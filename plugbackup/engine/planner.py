"""Построение плана изменений без записи на диск."""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from plugbackup.core import rsync
from plugbackup.engine.compare import case_insensitive, compare_entries, name_key
from plugbackup.engine.models import CopySpec, EngineResult
from plugbackup.engine.scan import ScanResult, scan_tree


@dataclass
class Action:
    """Одно действие над относительным путём назначения."""

    op: str
    path: str
    entry: object = None
    old_path: Optional[str] = None
    kind: str = "f"


@dataclass
class CopyPlan:
    """План и результаты обхода, повторно используемые исполнителем."""

    result: EngineResult
    actions: List[Action]
    transfer_bytes: int
    source_scan: ScanResult = field(default_factory=ScanResult)
    dest_scan: ScanResult = field(default_factory=ScanResult)
    dest_protected_ancestors: Set[str] = field(default_factory=set)


def _ancestors(path: str) -> Set[str]:
    """Получить все родительские относительные пути за время O(глубина)."""
    result = set()
    parent = path.rpartition("/")[0]
    while parent:
        result.add(parent)
        parent = parent.rpartition("/")[0]
    return result


def _ancestor_index(paths: Set[str]) -> Set[str]:
    """Собрать предков путей для последующих проверок за O(1)."""
    ancestors = set()
    for path in paths:
        ancestors.update(_ancestors(path))
    return ancestors


def _children_index(entries: Dict[str, object]) -> Dict[str, List[str]]:
    """Построить индекс непосредственных детей каждого каталога."""
    children = {}
    for path in entries:
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        children.setdefault(parent, []).append(path)
    return children


def _same_object(left: str, right: str) -> bool:
    """Проверить, обозначают ли два пути один существующий объект."""
    try:
        return os.path.lexists(right) and os.path.samefile(left, right)
    except OSError:
        return False


def build_plan(spec: CopySpec, cancel: Optional[threading.Event] = None) -> CopyPlan:
    """Построить действия и сохранить сканы для выполнения без повторного обхода."""
    result = EngineResult()
    source = scan_tree(spec, True, cancel)
    dest = scan_tree(spec, False, apply_filters=not spec.delete_excluded) if os.path.isdir(spec.dest_dir) else ScanResult()
    if cancel is not None and cancel.is_set():
        result.rc = rsync.RC_SIGNAL
        return CopyPlan(result, [], 0, source, dest, _ancestor_index(dest.protected))

    insensitive = case_insensitive(spec)
    if insensitive:
        source_keys = {}
        dest_keys = {}
        for name in source.entries:
            key = name_key(name, spec)
            if key in source_keys and source_keys[key] != name:
                raise ValueError(f"source name collision: {source_keys[key]} / {name}")
            source_keys[key] = name
        for name in dest.entries:
            key = name_key(name, spec)
            if key in dest_keys and dest_keys[key] != name:
                raise ValueError(f"destination name collision: {dest_keys[key]} / {name}")
            dest_keys[key] = name
        pairs = {name: dest_keys.get(name_key(name, spec)) for name in source.entries}
        claimed = set(source_keys)
    else:
        pairs = {name: name if name in dest.entries else None for name in source.entries}
        claimed = set(source.entries)
    actions = []
    protected = set(source.protected) | set(dest.protected)
    pruned = set(source.pruned) | set(dest.pruned)
    protected_ancestors = _ancestor_index(protected)
    pruned_ancestors = _ancestor_index(pruned)
    children = _children_index(dest.entries)
    transfer_bytes = 0
    delete_count = 0
    blocked_roots = set()
    source_errors = bool(source.errors)
    if source_errors:
        result.rc = rsync.RC_PARTIAL
        result.summary.other_messages.append("IO error encountered -- skipping file deletion")

    result.stats.total_size = sum(st.st_size for kind, st, _path in source.entries.values()
                                  if kind in ("f", "l"))
    for name, entry in sorted(source.entries.items()):
        parent = name.rpartition("/")[0]
        blocked = False
        while parent:
            if parent in blocked_roots:
                blocked = True
                break
            parent = parent.rpartition("/")[0]
        if blocked:
            continue
        if cancel is not None and cancel.is_set():
            result.rc = rsync.RC_SIGNAL
            break
        kind, st, _path = entry
        old = pairs[name]
        if kind == "f" and spec.max_size is not None and st.st_size > spec.max_size:
            protected.add(name)
            protected_ancestors.update(_ancestors(name))
            pruned.add(name)
            pruned_ancestors.update(_ancestors(name))
            continue
        if kind == "o":
            result.summary.skipped_nonregular.append(name)
            continue
        if kind == "l" and not (spec.symlinks == "copy_target" or
                                (spec.symlinks == "auto" and spec.fs_type in rsync.POSIX_FS)):
            result.summary.skipped_nonregular.append(name)
            continue
        if name in source.skipped:
            result.summary.skipped_nonregular.append(name)
            result.summary.other_messages.append(f"{name}: {source.skipped[name]}")
            protected.add(name)
            protected_ancestors.update(_ancestors(name))
            pruned.add(name)
            pruned_ancestors.update(_ancestors(name))
            continue

        old_entry = dest.entries.get(old) if old is not None else None
        if spec.update_only and old_entry is not None and kind == "f" and old_entry[0] == "f":
            window = 1 if spec.fs_type in rsync.FAT_LIKE else 0
            if old_entry[1].st_mtime > st.st_mtime + window:
                continue
        target_path = os.path.join(spec.dest_dir, *name.split("/"))
        case_rename = old is not None and old != name
        old_is_target = case_rename and _same_object(
            os.path.join(spec.dest_dir, *old.split("/")), target_path)
        distinct_old_name = case_rename and not old_is_target

        if old_entry is not None and old_entry[0] == "d" and kind != "d":
            old_children = children.get(old, [])
            if spec.mode == "accumulate" and old_children:
                result.rc = rsync.RC_PARTIAL
                result.summary.other_messages.append(f"cannot replace non-empty directory with a file: {name}")
                protected.add(old)
                protected_ancestors.update(_ancestors(old))
                continue
            if old_children and not source_errors:
                actions.append(Action("rmdir", old, old_entry, old, "d"))

        if old_entry is None:
            actions.append(Action("new", name, entry, kind=kind))
            if kind == "f":
                result.summary.new_files.append(name)
                transfer_bytes += st.st_size
            continue

        if source_errors and old_entry[0] != kind:
            # Для замены типа требуется удаление старого объекта, запрещённое при ошибке обхода.
            continue

        comparison = compare_entries(entry, old_entry, spec)
        if not comparison.same or case_rename:
            if (old_entry[0] in ("f", "l") and old_entry[0] != kind and
                    delete_count >= max(1, spec.hard_max_delete)):
                result.rc = rsync.RC_MAX_DELETE
                blocked_roots.add(name)
                continue
            if old_entry[0] in ("f", "l") and old_entry[0] != kind:
                delete_count += 1
                result.stats.deleted += 1
            if distinct_old_name and not source_errors and spec.mode in ("mirror", "mirror_versions"):
                old_item = dest.entries[old]
                if old_item[0] in ("f", "l"):
                    if delete_count >= max(1, spec.hard_max_delete):
                        result.rc = rsync.RC_MAX_DELETE
                        blocked_roots.add(name)
                        continue
                    delete_count += 1
                    result.stats.deleted += 1
                    result.summary.deleted_files.append(old)
                    actions.append(Action("delete", old, old_item, kind=old_item[0]))
            op = "new" if old_entry[0] != kind else "change"
            old_path = None if distinct_old_name else old
            actions.append(Action(op, name, entry, old_path, kind))
            if kind == "f":
                (result.summary.new_files if op == "new" else result.summary.changed_files).append(name)
                transfer_bytes += st.st_size
        elif comparison.update_meta:
            actions.append(Action("touch", name, entry, old, kind))

    if spec.mode in ("mirror", "mirror_versions") and not source_errors:
        remove_names = set()
        for name in dest.entries:
            key = name_key(name, spec) if insensitive else name
            if key not in claimed and not _protected(
                    name, protected, protected_ancestors, pruned, pruned_ancestors):
                remove_names.add(name)
        for name in sorted(remove_names, key=lambda path: (path.count("/"), path), reverse=True):
            kind = dest.entries[name][0]
            if kind == "d" and any(child not in remove_names for child in children.get(name, [])):
                continue
            if kind != "d" and delete_count >= max(1, spec.hard_max_delete):
                result.rc = rsync.RC_MAX_DELETE
                continue
            actions.append(Action("delete", name, dest.entries[name], kind=kind))
            if kind == "d":
                result.summary.deleted_dirs.append(name + "/")
            else:
                delete_count += 1
                result.summary.deleted_files.append(name)
                result.stats.deleted += 1

    deleted_paths = {action.path for action in actions if action.op == "delete"}
    retained = set(dest.entries) - deleted_paths
    retained_ancestors = _ancestor_index(retained)
    blocked_type_roots = set()
    for action in actions:
        if action.op == "rmdir" and action.path in retained_ancestors:
            blocked_type_roots.add(action.path)
            if result.rc != rsync.RC_MAX_DELETE:
                result.rc = rsync.RC_PARTIAL
                result.summary.other_messages.append(f"directory contains protected or undeleted data: {action.path}")
    if blocked_type_roots:
        actions = [action for action in actions if action.op != "rmdir" and
                   not (action.op in ("new", "change") and action.path in blocked_type_roots)]

    actions.sort(key=lambda action: (
        0 if action.op == "delete" else 1 if action.op == "rmdir" else
        2 if action.op in ("new", "change") else 3,
        -action.path.count("/") if action.op in ("delete", "rmdir") else action.path,
    ))
    result.stats.transferred_size = transfer_bytes
    result.stats.files_transferred = sum(
        1 for action in actions if action.op in ("new", "change") and action.kind == "f")
    return CopyPlan(result, actions, transfer_bytes, source, dest, _ancestor_index(dest.protected))


def _protected(path: str, protected: Set[str], protected_ancestors: Set[str],
               pruned: Set[str], pruned_ancestors: Set[str]) -> bool:
    """Проверить защиту пути и предков за время, пропорциональное глубине."""
    if path in protected or path in protected_ancestors or path in pruned_ancestors:
        return True
    parent = path.rpartition("/")[0]
    while parent:
        if parent in pruned:
            return True
        parent = parent.rpartition("/")[0]
    return False
