"""Выполнение плана с временными файлами и безопасным удалением."""
from __future__ import annotations

import errno
import os
import shutil
import stat
import threading
from typing import Callable, Dict, Optional, Union

from plugbackup.core import rsync
from plugbackup.engine.models import CopySpec, EngineResult, RC_NO_SPACE
from plugbackup.engine.planner import build_plan
from plugbackup.engine.progress import ProgressTracker
from plugbackup.engine.scan import linkish, safe_path


class SourceReadError(Exception):
    """Ошибка чтения источника, которую можно ограничить одним файлом."""

    def __init__(self, message: str, vanished: bool = False, cause: Optional[OSError] = None) -> None:
        super().__init__(message)
        self.vanished = vanished
        self.cause = cause


class DiskFullError(Exception):
    """Ошибка записи, вызванная исчерпанием места на носителе."""


def _log(callback: Optional[Callable[[str], None]], line: str) -> None:
    """Передать строку журнала, если вызывающий код указал callback."""
    if callback is not None:
        callback(line)


def _is_disk_full(error: OSError) -> bool:
    """Распознать ENOSPC и коды Windows для заполненного тома."""
    return error.errno == errno.ENOSPC or getattr(error, "winerror", None) in (39, 112)


def _raise_destination_error(error: OSError) -> None:
    """Преобразовать ошибку записи в отдельный сигнал нехватки места."""
    if _is_disk_full(error):
        raise DiskFullError("no space left on device") from error
    raise error


def _ensure_directory(path: str, root: str) -> None:
    """Создать каталог назначения после проверки каждого компонента пути."""
    safe_path(path, root)
    os.makedirs(path, exist_ok=True)


def _make_temp(source: str, target: str, before: os.stat_result,
               cancel: Optional[threading.Event], spec: CopySpec,
               tracker: Optional[ProgressTracker], name: str, done: int) -> Union[str, bool, None]:
    """Скопировать один файл во временную папку и проверить источник после чтения."""
    parent = os.path.dirname(target)
    partial = os.path.join(parent, rsync.PARTIAL_DIR)
    _ensure_directory(partial, spec.dest_dir)
    temporary = os.path.join(partial, os.path.basename(target))
    keep = False
    try:
        try:
            inp = open(source, "rb")
        except OSError as exc:
            raise SourceReadError(str(exc), isinstance(exc, FileNotFoundError), exc) from exc
        with inp:
            try:
                out = open(temporary, "wb")
            except OSError as exc:
                _raise_destination_error(exc)
            with out:
                copied = 0
                while True:
                    if cancel is not None and cancel.is_set():
                        return None
                    try:
                        block = inp.read(1024 * 1024)
                    except OSError as exc:
                        raise SourceReadError(str(exc), cause=exc) from exc
                    if not block:
                        break
                    try:
                        out.write(block)
                    except OSError as exc:
                        _raise_destination_error(exc)
                    copied += len(block)
                    if tracker is not None:
                        tracker.update(done + copied, name)
                try:
                    after = os.stat(source)
                except OSError as exc:
                    raise SourceReadError(str(exc), isinstance(exc, FileNotFoundError), exc) from exc
                before_id = (before.st_size, before.st_mtime, getattr(before, "st_ino", 0))
                after_id = (after.st_size, after.st_mtime, getattr(after, "st_ino", 0))
                if before_id != after_id:
                    return False
                try:
                    if spec.fs_type in rsync.POSIX_FS:
                        mode = stat.S_IMODE(before.st_mode)
                        if hasattr(os, "fchmod"):
                            os.fchmod(out.fileno(), mode)
                        else:
                            os.chmod(temporary, mode)
                    out.flush()
                    os.utime(temporary, (before.st_atime, before.st_mtime))
                    os.fsync(out.fileno())
                except OSError as exc:
                    _raise_destination_error(exc)
        keep = True
        return temporary
    finally:
        if not keep:
            _discard_temp(temporary)


def _discard_temp(path: str) -> None:
    """Удалить временный файл и его пустую служебную папку."""
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass
        try:
            os.rmdir(os.path.dirname(path))
        except OSError:
            pass


def _remove_path(path: str) -> None:
    """Удалить объект назначения без обхода ссылки или точки соединения."""
    st = os.lstat(path)
    if linkish(st):
        if stat.S_ISDIR(st.st_mode) or windows_is_directory(st):
            os.rmdir(path)
        else:
            os.unlink(path)
    elif stat.S_ISDIR(st.st_mode):
        shutil.rmtree(path)
    else:
        os.unlink(path)


def windows_is_directory(st: object) -> bool:
    """Проверить атрибут каталога reparse point на Windows."""
    return bool(getattr(st, "st_file_attributes", 0) & 0x10)


def _remove_existing(spec: CopySpec, old_path: str, rel: str, kind: str, result: EngineResult,
                     cancel: Optional[threading.Event], count_delete: bool = True,
                     record_delete: bool = True) -> bool:
    """Удалить объект или переместить его в корзину, соблюдая предел удалений."""
    safe_path(old_path, spec.dest_dir, allow_leaf=True)
    if kind in ("f", "l") and count_delete and result.stats.deleted >= max(1, spec.hard_max_delete):
        return False
    if spec.backup_dir:
        backup = os.path.join(spec.backup_dir, *rel.split("/"))
        backup_root = os.path.commonpath([os.path.abspath(spec.dest_dir), os.path.abspath(backup)])
        safe_path(backup, backup_root, allow_leaf=True)
        if os.path.lexists(backup):
            raise OSError(f"backup path is already occupied: {backup}")
        os.makedirs(os.path.dirname(backup), exist_ok=True)
        try:
            os.replace(old_path, backup)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            if kind == "f":
                shutil.copy2(old_path, backup)
            elif kind == "l":
                os.symlink(os.readlink(old_path), backup)
            else:
                raise
            os.unlink(old_path)
    else:
        _remove_path(old_path)
    if kind in ("f", "l") and count_delete:
        result.stats.deleted += 1
        if record_delete:
            result.summary.deleted_files.append(rel)
    return True


def _publish_link(path: str, target: str, spec: CopySpec) -> str:
    """Создать временную символическую ссылку для последующей публикации."""
    parent = os.path.dirname(target)
    partial = os.path.join(parent, rsync.PARTIAL_DIR)
    _ensure_directory(partial, spec.dest_dir)
    temporary = os.path.join(partial, os.path.basename(target))
    try:
        os.symlink(os.readlink(path), temporary)
        return temporary
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _clean_partial(spec: CopySpec, source_entries: Dict[str, object]) -> None:
    """Удалить только временные файлы, имена которых есть в том же каталоге источника."""
    own = {}
    for rel in source_entries:
        parent, name = rel.rsplit("/", 1) if "/" in rel else ("", rel)
        own.setdefault(parent, set()).add(name)
    for parent, names in own.items():
        destination_parent = os.path.join(spec.dest_dir, *parent.split("/")) if parent else spec.dest_dir
        partial = os.path.join(destination_parent, rsync.PARTIAL_DIR)
        try:
            safe_path(partial, spec.dest_dir, allow_leaf=True)
            st = os.lstat(partial)
            if not stat.S_ISDIR(st.st_mode) or linkish(st):
                continue
            entries = list(os.scandir(partial))
        except OSError:
            continue
        for item in entries:
            if item.name not in names:
                continue
            try:
                item_st = item.stat(follow_symlinks=False)
                if stat.S_ISREG(item_st.st_mode):
                    safe_path(item.path, spec.dest_dir, allow_leaf=True)
                    os.unlink(item.path)
            except OSError:
                continue
        try:
            os.rmdir(partial)
        except OSError:
            pass


def execute_plan(spec: CopySpec, cancel: Optional[threading.Event] = None,
                 progress: Optional[Callable[[Dict[str, object]], None]] = None,
                 log: Optional[Callable[[str], None]] = None) -> EngineResult:
    """Выполнить построенный план и вернуть результат в формате движка."""
    if spec.delete_excluded:
        raise ValueError("delete_excluded is supported only for planning")
    try:
        planned = build_plan(spec, cancel)
    except Exception as exc:
        result = EngineResult(rc=rsync.RC_PARTIAL)
        result.summary.other_messages.append(str(exc))
        _log(log, str(exc))
        return result
    result = EngineResult(rc=planned.result.rc,
                          stats=rsync.Stats(total_size=planned.result.stats.total_size))
    result.summary.skipped_nonregular.extend(planned.result.summary.skipped_nonregular)
    result.summary.other_messages.extend(planned.result.summary.other_messages)
    if planned.result.rc == rsync.RC_SIGNAL:
        return result

    source_scan = planned.source_scan
    try:
        _clean_partial(spec, source_scan.entries)
    except OSError as exc:
        result.rc = rsync.RC_PARTIAL
        result.summary.other_messages.append(str(exc))
        _log(log, str(exc))
        return result
    tracker = ProgressTracker(progress, planned.transfer_bytes)
    completed = 0
    source_dirs = [(os.path.join(spec.dest_dir, *name.split("/")), st, name)
                   for name, (kind, st, _path) in source_scan.entries.items()
                   if kind == "d" and name not in source_scan.errors]
    dest_entries = planned.dest_scan.entries

    for action in planned.actions:
        temporary = None
        if cancel is not None and cancel.is_set():
            result.rc = rsync.RC_SIGNAL
            break
        target = os.path.join(spec.dest_dir, *action.path.split("/"))
        try:
            safe_path(target, spec.dest_dir, allow_leaf=True)
            if action.op == "delete":
                if not _remove_existing(spec, target, action.path, action.kind, result, cancel):
                    result.rc = rsync.RC_MAX_DELETE
                    continue
                if action.kind == "d":
                    result.summary.deleted_dirs.append(action.path + "/")
                    _log(log, f"*deleting {action.path}/")
                else:
                    _log(log, f"*deleting {action.path}")
                continue
            if action.op == "rmdir":
                try:
                    os.rmdir(target)
                except OSError as exc:
                    result.rc = rsync.RC_PARTIAL
                    result.summary.other_messages.append(f"failed to remove directory for replacement: {exc}")
                continue
            if action.op == "touch":
                st = action.entry[1]
                os.utime(target, (st.st_atime, st.st_mtime), follow_symlinks=False)
                if action.kind in ("d", "f") and spec.fs_type in rsync.POSIX_FS:
                    os.chmod(target, stat.S_IMODE(st.st_mode))
                continue

            kind, st, source = action.entry
            _ensure_directory(os.path.dirname(target), spec.dest_dir)
            old_path = (os.path.join(spec.dest_dir, *action.old_path.split("/"))
                        if action.old_path else None)
            old_kind = dest_entries.get(action.old_path, (None,))[0] if action.old_path else None
            if (old_kind == "d" and kind != "d" and
                    (action.old_path in planned.dest_scan.protected or
                     action.old_path in planned.dest_protected_ancestors)):
                result.rc = rsync.RC_PARTIAL
                result.summary.other_messages.append(
                    f"cannot replace directory containing protected data: {action.path}")
                continue

            if kind == "d":
                if old_path and os.path.lexists(old_path):
                    count = old_kind in ("f", "l") and old_kind != kind
                    if old_kind == "d" and old_path != target:
                        safe_path(old_path, spec.dest_dir, allow_leaf=True)
                        os.replace(old_path, target)
                    elif old_kind != "d":
                        if not _remove_existing(spec, old_path, action.old_path, old_kind, result, cancel,
                                                count, False):
                            result.rc = rsync.RC_MAX_DELETE
                            continue
                os.makedirs(target, exist_ok=True)
                _log(log, f"cd+++++++++ {action.path}/")
                continue

            if kind == "l" and spec.symlinks != "copy_target":
                if spec.symlinks == "auto" and spec.fs_type in rsync.POSIX_FS:
                    temporary = _publish_link(source, target, spec)
                else:
                    result.summary.skipped_nonregular.append(action.path)
                    _log(log, f"skipping non-regular file {action.path}")
                    continue
            elif kind == "f":
                try:
                    temporary = _make_temp(source, target, st, cancel, spec, tracker, action.path, completed)
                except SourceReadError as exc:
                    result.rc = rsync.RC_VANISHED if exc.vanished else rsync.RC_PARTIAL
                    cause = exc.cause
                    if (isinstance(cause, PermissionError) or
                            getattr(cause, "winerror", None) in (32, 33)):
                        message = f"file is busy with another program: {action.path}"
                    else:
                        message = f"skipping {action.path}: {exc}"
                    result.summary.other_messages.append(message)
                    _log(log, result.summary.other_messages[-1])
                    continue
                if temporary is None:
                    result.rc = rsync.RC_SIGNAL
                    break
                if temporary is False:
                    result.rc = rsync.RC_PARTIAL
                    message = f"source file changed while being read: {action.path}"
                    result.summary.other_messages.append(message)
                    _log(log, message)
                    continue
            else:
                result.summary.skipped_nonregular.append(action.path)
                continue

            if old_path and os.path.lexists(old_path):
                replacement = old_kind != kind
                archive_old = not source_scan.errors and bool(spec.backup_dir)
                if replacement or archive_old:
                    if not _remove_existing(spec, old_path, action.old_path, old_kind, result, cancel,
                                            replacement, False):
                        _discard_temp(temporary)
                        result.rc = rsync.RC_MAX_DELETE
                        continue
            safe_path(target, spec.dest_dir, allow_leaf=True)
            os.replace(temporary, target)

            if old_path and old_path != target and os.path.lexists(old_path):
                same_object = False
                try:
                    same_object = os.path.samefile(old_path, target)
                except OSError:
                    pass
                if not same_object and spec.mode != "accumulate" and not source_scan.errors:
                    if not _remove_existing(spec, old_path, action.old_path, old_kind, result, cancel):
                        result.rc = rsync.RC_MAX_DELETE
                    else:
                        _log(log, f"*deleting {action.old_path}")

            try:
                os.rmdir(os.path.dirname(temporary))
            except OSError:
                pass
            if kind == "f":
                completed += st.st_size
                result.stats.transferred_size += st.st_size
                result.stats.files_transferred += 1
            path_action = "new" if action.op == "new" else "change"
            if kind == "f":
                (result.summary.new_files if path_action == "new" else result.summary.changed_files).append(action.path)
            if kind == "l":
                _log(log, ("cL+++++++++ " if path_action == "new" else "cL..t...... ") + action.path)
            else:
                _log(log, (">f+++++++++ " if path_action == "new" else ">f.st...... ") + action.path)
            if tracker is not None:
                tracker.update(completed, action.path)
        except DiskFullError as exc:
            if temporary:
                _discard_temp(temporary)
            result.rc = RC_NO_SPACE
            result.summary.other_messages.append("no space left on device")
            _log(log, "no space left on device")
            break
        except OSError as exc:
            if temporary:
                _discard_temp(temporary)
            if _is_disk_full(exc):
                result.rc = RC_NO_SPACE
                result.summary.other_messages.append("no space left on device")
                _log(log, "no space left on device")
                break
            result.rc = rsync.RC_PARTIAL
            result.summary.other_messages.append(str(exc))
            _log(log, str(exc))
        except Exception as exc:
            if temporary:
                _discard_temp(temporary)
            result.rc = rsync.RC_PARTIAL
            result.summary.other_messages.append(str(exc))
            _log(log, str(exc))

    if result.rc != RC_NO_SPACE:
        for target, st, rel in sorted(source_dirs, key=lambda item: item[2].count("/"), reverse=True):
            try:
                if os.path.isdir(target) and not linkish(os.lstat(target)):
                    safe_path(target, spec.dest_dir)
                    os.utime(target, (st.st_atime, st.st_mtime))
                    if spec.fs_type in rsync.POSIX_FS:
                        os.chmod(target, stat.S_IMODE(st.st_mode))
            except OSError as exc:
                result.rc = rsync.RC_PARTIAL
                result.summary.other_messages.append(str(exc))
    if result.summary.other_messages and result.rc == rsync.RC_OK:
        result.rc = rsync.RC_PARTIAL
    return result
