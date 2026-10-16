"""Публичный интерфейс кроссплатформенного движка копирования."""
from __future__ import annotations

import os
import threading
from typing import Callable, Dict, List, Optional

from plugbackup.core import rsync
from plugbackup.core.profiles import Job, normalize_dest
from plugbackup.engine.executor import execute_plan
from plugbackup.engine.models import CopySpec, EngineResult, RC_NO_SPACE
from plugbackup.engine.planner import build_plan
from plugbackup.engine.scan import list_entries


def spec_from_job(job: Job, source: str, dest_dir: str, options: rsync.CommandOptions) -> CopySpec:
    """Собрать параметры движка из задания и тех же опций, что использует rsync."""
    patterns = list(options.global_excludes) if job.use_global_excludes else []
    patterns.extend(job.excludes)
    if job.skip_hidden:
        patterns.append(".*")
    sizes = []
    if job.max_size_mb:
        sizes.append(job.max_size_mb * 1024 * 1024)
    if options.fs_type == "vfat":
        sizes.append(4 * 1024 ** 3 - 1)
    dest = normalize_dest(job.dest)
    backup_dir = None
    if job.mode == "mirror_versions":
        if not options.timestamp:
            raise ValueError("timestamp is required for version history")
        root = os.path.abspath(dest_dir)
        for _part in dest.split("/"):
            root = os.path.dirname(root)
        backup_dir = os.path.join(root, ".versions", options.timestamp, *dest.split("/"))
    return CopySpec(source, dest_dir, job.mode, job.compare, job.symlinks, options.fs_type,
                    [p.strip() for p in patterns if p.strip()], list(options.extra_excludes),
                    min(sizes) if sizes else None, job.one_file_system, backup_dir,
                    options.hard_max_delete, bool(job.skip_hidden and os.name == "nt"))


def spec_for_restore(source: str, dest_dir: str, fs_type: str, skip_newer: bool = True,
                     backup_dir: Optional[str] = None) -> CopySpec:
    """Параметры восстановления из копии на носителе в папку компьютера."""
    return CopySpec(source, dest_dir, "accumulate", "size_time", "auto", fs_type,
                    ["/" + rsync.PARTIAL_DIR + "/", rsync.PARTIAL_DIR + "/"],
                    backup_dir=backup_dir, one_file_system=False, update_only=skip_newer)


def list_source(spec: CopySpec, cancel: Optional[threading.Event] = None) -> List[rsync.ListEntry]:
    """Вернуть список объектов источника с применёнными правилами исключения."""
    return list_entries(spec, cancel)


def plan(spec: CopySpec, cancel: Optional[threading.Event] = None) -> EngineResult:
    """Вернуть планируемые изменения, не записывая данные на диск."""
    try:
        return build_plan(spec, cancel).result
    except Exception as exc:
        result = EngineResult(rc=rsync.RC_PARTIAL)
        result.summary.other_messages.append(str(exc))
        return result


def execute(spec: CopySpec, cancel: Optional[threading.Event] = None,
            progress: Optional[Callable[[Dict[str, object]], None]] = None,
            log: Optional[Callable[[str], None]] = None) -> EngineResult:
    """Выполнить копирование и сообщать о ходе через заданные callback-функции."""
    return execute_plan(spec, cancel, progress, log)

__all__ = ["CopySpec", "EngineResult", "RC_NO_SPACE", "spec_from_job", "list_source", "plan", "execute"]
