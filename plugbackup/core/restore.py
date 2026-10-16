"""Восстановление из копии на носителе.

Восстанавливается ТЕКУЩЕЕ содержимое копии задания. На компьютере ничего не удаляется никогда.
При восстановлении в исходную папку файлы, которые будут перезаписаны, сначала откладываются в
~/.local/share/plugbackup/restore-backup/<время>/<задание>/ (rsync --backup --backup-dir), а рядом
кладётся README с описанием, откуда они и как вернуть.
"""

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field

from plugbackup.core import journal
from plugbackup.core import rsync as R
from plugbackup.core.journal import CANCELLED, ERROR, OK, WARNINGS, JobResult, RunRecord
from plugbackup.core.profiles import Job, normalize_dest
from plugbackup.i18n import _

SAMPLE = 50
TS_FORMAT = "%Y-%m-%d_%H-%M-%S"


def restore_backup_root() -> str:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "plugbackup", "restore-backup")


def default_other_base() -> str:
    """Папка для восстановления «в другое место» по умолчанию: ~/Восстановлено (на языке интерфейса)."""
    # TRANSLATORS: folder name in the home folder for files restored into another place
    return os.path.join(os.path.expanduser("~"), _("Restored"))


def default_other_target(job: Job, when: float | None = None) -> str:
    """Папка «в другое место» по умолчанию: ~/Восстановлено/<задание>_<дата>."""
    stamp = time.strftime("%Y-%m-%d", time.localtime(when or time.time()))
    name = normalize_dest(job.dest).replace("/", "_") or "copy"
    return os.path.join(default_other_base(), f"{name}_{stamp}")


@dataclass
class RestoreItem:
    job: Job
    copy_dir: str        # папка копии задания на носителе (проверенная вызывающим)
    target_dir: str      # куда восстанавливать (исходная папка или другая)


@dataclass
class RestorePlan:
    new_files: list[str] = field(default_factory=list)
    overwritten: list[str] = field(default_factory=list)
    transfer_bytes: int = 0


def build_command(item: RestoreItem, fs_type: str, backup_dir: str | None, dry_run: bool,
                  skip_newer: bool = True) -> list[str]:
    """rsync копия → папка на ПК. Без --delete: на компьютере ничего не удаляется.

    skip_newer — не заменять файлы, которые на компьютере новее, чем в копии (--update).
    """
    args = ["rsync", "--recursive", "--times", "--itemize-changes", "--no-human-readable",
            "--exclude=/" + R.PARTIAL_DIR + "/", "--exclude=" + R.PARTIAL_DIR + "/"]
    if fs_type in R.POSIX_FS:
        args += ["--perms", "--links"]
    if fs_type in R.FAT_LIKE:
        args.append("--modify-window=1")
    if skip_newer:
        args.append("--update")
    if backup_dir is not None:
        args += ["--backup", "--backup-dir=" + backup_dir]
    if dry_run:
        args += ["--dry-run", "--stats"]
    else:
        args += ["--no-inc-recursive", "--info=progress2", "--stats"]
    return args + [item.copy_dir.rstrip("/") + "/", item.target_dir.rstrip("/") + "/"]


def plan(item: RestoreItem, fs_type: str, skip_newer: bool = True) -> RestorePlan:
    """Что сделает восстановление: какие файлы появятся и какие будут перезаписаны."""
    done = subprocess.run(build_command(item, fs_type, None, True, skip_newer), capture_output=True, text=True,
                          env=R.rsync_env(), encoding="utf-8", errors="surrogateescape")
    if done.returncode not in (R.RC_OK, R.RC_VANISHED):
        raise OSError(f"rsync {done.returncode}: {done.stderr.strip()[:300]}")
    summary = R.ItemizeSummary()
    for line in done.stdout.splitlines():
        R.parse_itemize_line(line, summary)
    return RestorePlan(summary.new_files, summary.changed_files, R.parse_stats(done.stdout).transferred_size)


def write_readme(backup_dir: str, item: RestoreItem) -> None:
    os.makedirs(backup_dir, exist_ok=True)
    with open(os.path.join(backup_dir, "README.txt"), "w", encoding="utf-8") as f:
        title = _("Plug & Backup — files set aside during a restore")
        f.write(
            f"{title}\n{'=' * len(title)}\n\n"
            + _("Restored at: %s") % time.strftime("%d.%m.%Y %H:%M:%S") + "\n"
            + _("From the copy: %s") % item.copy_dir + "\n"
            + _("Into the folder: %s") % item.target_dir + "\n\n"
            + _("These are the versions of files that were in the folder on this computer BEFORE the restore and "
                "that the restore overwrote. Paths inside this folder are the same as in the target folder. To get "
                "a file back, copy it from here to the target folder.") + "\n"
        )


class Restorer:
    """Восстановление нескольких заданий подряд; блокирующий run() вызывается в отдельном потоке."""

    def __init__(self, profile_id: str, profile_name: str, items: list[RestoreItem], fs_type: str,
                 keep_overwritten: bool, skip_newer: bool = True, progress=None):
        self.profile_id, self.profile_name = profile_id, profile_name
        self.items, self.fs_type = items, fs_type
        self.keep_overwritten = keep_overwritten
        self.skip_newer = skip_newer
        self.progress = progress or (lambda info: None)
        self._cancel = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self.backup_root = os.path.join(restore_backup_root(), time.strftime(TS_FORMAT))

    def cancel(self) -> None:
        self._cancel.set()
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()

    def run(self) -> RunRecord:
        record = RunRecord(run_id=journal.new_run_id(), profile_id=self.profile_id,
                           profile_name=self.profile_name, trigger="restore", started=time.time())
        os.makedirs(journal.log_dir(), exist_ok=True)
        with open(record.log_path, "w", encoding="utf-8", errors="surrogateescape") as log:
            for index, item in enumerate(self.items):
                result = JobResult(job_id=item.job.id, source=item.copy_dir, dest=item.target_dir)
                record.jobs.append(result)
                if self._cancel.is_set():
                    result.status, result.reason = CANCELLED, "user"
                    continue
                self._restore_one(item, result, log, index)
        record.finished = time.time()
        record.status = journal.overall_status(record.jobs) if record.jobs else OK
        if record.status == CANCELLED:
            record.reason = "user"
        journal.append(record)
        return record

    def _restore_one(self, item: RestoreItem, result: JobResult, log, index: int) -> None:
        if not os.path.isdir(item.copy_dir):
            result.status, result.reason = ERROR, "no-copy"
            return
        os.makedirs(item.target_dir, exist_ok=True)
        backup_dir = None
        if self.keep_overwritten:
            backup_dir = os.path.join(self.backup_root, normalize_dest(item.job.dest) or item.job.id)
            write_readme(backup_dir, item)
        command = build_command(item, self.fs_type, backup_dir, False, self.skip_newer)
        log.write(f"\n=== {item.copy_dir} → {item.target_dir}\n$ {' '.join(command)}\n")
        summary = R.ItemizeSummary()
        with self._lock:
            if self._cancel.is_set():
                result.status, result.reason = CANCELLED, "user"
                return
            self._proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                          env=R.rsync_env(), bufsize=0)
        buffer, current = b"", ""
        stream = self._proc.stdout
        try:
            self._read_output(stream, item, index, summary, log)
        finally:
            stream.close()
        rc = self._proc.wait()
        self._finish_job(rc, item, result, summary, backup_dir)

    def _read_output(self, stream, item, index, summary, log) -> None:
        buffer, current = b"", ""
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            buffer += chunk
            while True:
                cut = min((i for i in (buffer.find(b"\n"), buffer.find(b"\r")) if i >= 0), default=-1)
                if cut < 0:
                    break
                line, buffer = buffer[:cut].decode("utf-8", "surrogateescape"), buffer[cut + 1:]
                if not line.strip():
                    continue
                progress = R.parse_progress(line)
                if progress is not None:
                    self.progress({"index": index, "count": len(self.items), "job": item.job,
                                   "percent": progress.percent, "speed": progress.speed, "eta": progress.eta,
                                   "file": current})
                    continue
                log.write(line + "\n")
                path = R.parse_itemize_line(line, summary)
                if path:
                    current = path

    def _finish_job(self, rc, item, result, summary, backup_dir) -> None:
        with self._lock:
            self._proc = None
        result.rsync_code = rc
        result.copied_files = len(summary.new_files) + len(summary.changed_files)
        result.new_files, result.replaced_files = len(summary.new_files), len(summary.changed_files)
        result.deleted_files = 0
        result.errors = [m for m in summary.other_messages if m.startswith(("rsync:", "rsync error:"))][:SAMPLE]
        if self._cancel.is_set() or rc == R.RC_SIGNAL or rc < 0:
            result.status, result.reason = CANCELLED, "user"
        elif rc == R.RC_OK:
            result.status = OK
        elif rc in (R.RC_VANISHED, R.RC_PARTIAL):
            result.status, result.reason = (WARNINGS, "files-vanished") if rc == R.RC_VANISHED \
                else (ERROR, "partial-transfer")
        else:
            result.status, result.reason = ERROR, f"rsync-{rc}"
        if backup_dir is not None and summary.changed_files:
            result.message = backup_dir
        elif backup_dir is not None:
            # Ничего не перезаписано — откладывать было нечего: убрать README и опустевшие папки вверх до корня.
            try:
                os.unlink(os.path.join(backup_dir, "README.txt"))
            except OSError:
                return
            path = backup_dir
            stop = os.path.dirname(self.backup_root)
            while path.startswith(self.backup_root) and path != stop:
                try:
                    os.rmdir(path)
                except OSError:
                    break
                path = os.path.dirname(path)
