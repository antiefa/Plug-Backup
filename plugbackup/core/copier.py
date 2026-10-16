"""Общий интерфейс планирования и выполнения операций копирования."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import dataclasses
from typing import Callable, Optional

from plugbackup.core import rsync as R
from plugbackup.engine import CopySpec, execute as engine_execute, list_source as engine_list
from plugbackup.engine import plan as engine_plan, spec_for_restore, spec_from_job


class RsyncCopier:
    """Операции копирования через системный rsync."""

    name = "rsync"

    @staticmethod
    def run(command: list[str], cancel: Optional[threading.Event] = None) -> tuple[int, str]:
        if cancel is not None and cancel.is_set():
            return R.RC_SIGNAL, ""
        done = subprocess.run(command, capture_output=True, text=True, env=R.rsync_env(),
                              encoding="utf-8", errors="surrogateescape")
        return done.returncode, done.stdout + done.stderr

    def list_source(self, job, source: str, options: R.CommandOptions, capture=None):
        captured = capture(R.build_list_command(job, source, options)) if capture else self.run(
            R.build_list_command(job, source, options))
        if captured is None:
            return None
        rc, output = captured
        return rc, R.parse_list(output), output

    def plan_job(self, job, source: str, dest: str, options: R.CommandOptions, capture=None):
        dry_options = dataclasses.replace(options, dry_run=True)
        command = R.build_command(job, source, dest, dry_options)
        captured = capture(command) if capture else self.run(command)
        if captured is None:
            return None
        rc, output = captured
        summary = R.ItemizeSummary()
        for line in output.splitlines():
            R.parse_itemize_line(line, summary)
        return rc, summary, R.parse_stats(output), output

    def execute_job(self, job, source: str, dest: str, options: R.CommandOptions,
                    cancel, progress, log, command_runner=None):
        command = R.build_command(job, source, dest, options)
        if command_runner:
            return command_runner(command, log, job, progress)
        rc, output = self.run(command, cancel)
        summary = R.ItemizeSummary()
        for line in output.splitlines():
            R.parse_itemize_line(line, summary)
            if log:
                log.write(line + "\n")
        return rc, summary

    def restore_spec(self, item, fs_type, skip_newer, backup_dir):
        return None

    def plan_restore(self, item, fs_type, skip_newer):
        command = self.restore_command(item, fs_type, None, True, skip_newer)
        done = subprocess.run(command, capture_output=True, text=True, env=R.rsync_env(), encoding="utf-8",
                              errors="surrogateescape")
        summary = R.ItemizeSummary()
        for line in done.stdout.splitlines():
            R.parse_itemize_line(line, summary)
        return done.returncode, summary, R.parse_stats(done.stdout), done.stderr

    def restore_command(self, item, fs_type, backup_dir, dry_run, skip_newer):
        from plugbackup.core.restore import build_command
        return build_command(item, fs_type, backup_dir, dry_run, skip_newer)

    def dry_deletions(self, job, dest_dir, fs_type, global_excludes, delete_excluded):
        options = R.CommandOptions(fs_type=fs_type, global_excludes=global_excludes, timestamp="maintenance")
        args = ["rsync"] + R.file_options(job, fs_type) + R.filter_options(job, options)
        args += ["--dry-run", "--itemize-changes", "--no-human-readable", "--delete"]
        if delete_excluded:
            args.append("--delete-excluded")
        args += [job.source.rstrip("/") + "/", dest_dir.rstrip("/") + "/"]
        rc, output = self.run(args)
        summary = R.ItemizeSummary()
        for line in output.splitlines():
            R.parse_itemize_line(line, summary)
        if rc not in (R.RC_OK, R.RC_VANISHED):
            raise OSError(f"rsync {rc}: {output.strip()[:300]}")
        return summary.deleted_files + summary.deleted_dirs


class EngineCopier(RsyncCopier):
    """Операции копирования через движок Plug & Backup."""

    name = "engine"

    def list_source(self, job, source: str, options: R.CommandOptions, capture=None):
        spec = spec_from_job(job, source, "", options)
        try:
            entries = engine_list(spec)
            return R.RC_OK, entries, ""
        except (OSError, ValueError) as exc:
            return R.RC_PARTIAL, [], str(exc)

    def plan_job(self, job, source: str, dest: str, options: R.CommandOptions, capture=None, cancel=None):
        result = engine_plan(spec_from_job(job, source, dest, options), cancel)
        return result.rc, result.summary, result.stats, "\n".join(result.summary.other_messages)

    def execute_job(self, job, source: str, dest: str, options: R.CommandOptions,
                    cancel, progress, log, command_runner=None):
        spec = spec_from_job(job, source, dest, options)
        def emit(info):
            progress(info)
        def write(line):
            if log:
                log.write(line + "\n")
        result = engine_execute(spec, cancel, emit, write)
        return result.rc, result.summary

    def restore_spec(self, item, fs_type, skip_newer, backup_dir):
        return spec_for_restore(item.copy_dir, item.target_dir, fs_type, skip_newer, backup_dir)

    def plan_restore(self, item, fs_type, skip_newer):
        result = engine_plan(self.restore_spec(item, fs_type, skip_newer, None))
        return result.rc, result.summary, result.stats, "; ".join(result.summary.other_messages)

    def dry_deletions(self, job, dest_dir, fs_type, global_excludes, delete_excluded):
        options = R.CommandOptions(fs_type=fs_type, global_excludes=global_excludes, timestamp="maintenance")
        spec = spec_from_job(job, job.source, dest_dir, options)
        spec.mode = "mirror"
        spec.delete_excluded = delete_excluded
        result = engine_plan(spec)
        if result.rc not in (R.RC_OK, R.RC_VANISHED):
            raise OSError("engine planning failed: " + "; ".join(result.summary.other_messages))
        return result.summary.deleted_files + result.summary.deleted_dirs


def default_copier():
    """Выбрать копировщик по ОС или PLUGBACKUP_COPIER."""
    selected = os.environ.get("PLUGBACKUP_COPIER", "").strip().lower()
    if selected == "engine":
        return EngineCopier()
    if selected == "rsync":
        return RsyncCopier()
    if selected:
        raise ValueError("PLUGBACKUP_COPIER must be engine or rsync")
    return RsyncCopier() if sys.platform.startswith("linux") else EngineCopier()


__all__ = ["RsyncCopier", "EngineCopier", "default_copier"]
