"""Безопасный обход дерева источника и назначения."""
from __future__ import annotations

import os
import stat
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from plugbackup.core import rsync
from plugbackup.engine.filters import excluded
from plugbackup.engine.models import CopySpec
from plugbackup.engine import windows

Entry = Tuple[str, os.stat_result, str]


@dataclass
class ScanResult:
    """Записи обхода и пути, которые требуется защитить от удаления."""
    entries: Dict[str, Entry] = field(default_factory=dict)
    protected: Set[str] = field(default_factory=set)
    pruned: Set[str] = field(default_factory=set)
    skipped: Dict[str, str] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)


def linkish(st: object) -> bool:
    """Вернуть истину для ссылок и точек повторной обработки Windows."""
    return stat.S_ISLNK(st.st_mode) or windows.is_reparse_point(st)


def safe_path(path: str, root: str, allow_leaf: bool = False) -> None:
    """Проверить компоненты пути назначения, не переходя по ссылкам."""
    root = os.path.abspath(root)
    path = os.path.abspath(path)
    try:
        if os.path.commonpath([root, path]) != root:
            raise OSError("path is outside destination directory")
    except ValueError:
        raise OSError("path is on a different volume")
    current = root
    for index, part in enumerate([""] if path == root else os.path.relpath(path, root).split(os.sep)):
        if part:
            current = os.path.join(current, part)
        try:
            st = os.lstat(current)
        except OSError:
            continue
        leaf = current == path
        if linkish(st) and not (leaf and allow_leaf):
            raise OSError("link inside destination directory: " + current)


def _protect_ancestors(path: str, protected: Set[str]) -> None:
    parts = path.split("/")
    for end in range(1, len(parts)):
        protected.add("/".join(parts[:end]))


def scan_tree(spec: CopySpec, source: bool = True,
              cancel: Optional[threading.Event] = None, apply_filters: bool = True) -> ScanResult:
    root = os.path.abspath(spec.source if source else spec.dest_dir)
    if not os.path.isdir(root):
        raise OSError("directory not found: " + root)
    root_lstat = os.lstat(root)
    if linkish(root_lstat):
        raise OSError("scan root is a link or reparse point")
    root_dev = os.stat(root).st_dev
    result = ScanResult()
    stack = [(root, "", frozenset([os.path.realpath(root)]))]
    while stack:
        if cancel is not None and cancel.is_set():
            break
        here, relbase, ancestors = stack.pop()
        try:
            entries = sorted(os.scandir(here), key=lambda e: e.name)
        except OSError as exc:
            if not relbase:
                raise OSError("failed to read source root: " + str(exc))
            if relbase:
                result.skipped[relbase] = str(exc)
                if source:
                    result.errors[relbase] = str(exc)
                result.pruned.add(relbase)
                result.protected.add(relbase)
                _protect_ancestors(relbase, result.protected)
            continue
        for ent in entries:
            if cancel is not None and cancel.is_set():
                break
            rel = (relbase + "/" if relbase else "") + ent.name
            try:
                st = ent.stat(follow_symlinks=False)
            except OSError as exc:
                result.skipped[rel] = str(exc)
                if source:
                    result.errors[rel] = str(exc)
                result.pruned.add(rel)
                result.protected.add(rel)
                _protect_ancestors(rel, result.protected)
                continue
            link = linkish(st)
            is_dir = stat.S_ISDIR(st.st_mode) and not link
            if apply_filters and excluded(rel, is_dir, spec):
                result.pruned.add(rel)
                result.protected.add(rel)
                _protect_ancestors(rel, result.protected)
                continue
            if source and spec.skip_hidden_attr and windows.is_hidden(st):
                result.pruned.add(rel)
                result.skipped[rel] = "hidden attribute"
                result.protected.add(rel)
                _protect_ancestors(rel, result.protected)
                continue
            if source and windows.is_cloud_only(st):
                result.pruned.add(rel)
                result.skipped[rel] = "cloud-only file"
                result.protected.add(rel)
                _protect_ancestors(rel, result.protected)
                continue
            if source and link:
                if windows.is_reparse_point(st):
                    result.entries[rel] = ("l", st, os.path.join(here, ent.name))
                    result.skipped[rel] = "junction or reparse point"
                    result.pruned.add(rel)
                    continue
                if spec.symlinks == "copy_target":
                    try:
                        target = os.path.realpath(os.path.join(here, ent.name))
                        followed = os.stat(target)
                        if followed.st_dev != root_dev and spec.one_file_system:
                            result.skipped[rel] = "different filesystem"
                            result.pruned.add(rel)
                            continue
                        if stat.S_ISDIR(followed.st_mode):
                            result.entries[rel] = ("d", followed, target)
                            if target not in ancestors:
                                stack.append((target, rel, ancestors | frozenset([target])))
                            else:
                                result.skipped[rel] = "cyclic symlink"
                                result.pruned.add(rel)
                        elif stat.S_ISREG(followed.st_mode):
                            result.entries[rel] = ("f", followed, target)
                        else:
                            result.entries[rel] = ("o", st, os.path.join(here, ent.name))
                    except OSError as exc:
                        result.entries[rel] = ("l", st, os.path.join(here, ent.name))
                        result.skipped[rel] = "dangling symlink: " + str(exc)
                        result.pruned.add(rel)
                    continue
                result.entries[rel] = ("l", st, os.path.join(here, ent.name))
                continue
            if is_dir and source and spec.one_file_system and st.st_dev != root_dev:
                result.entries[rel] = ("d", st, os.path.join(here, ent.name))
                result.skipped[rel] = "different filesystem"
                continue
            kind = "d" if is_dir else "l" if link else "f" if stat.S_ISREG(st.st_mode) else "o"
            result.entries[rel] = (kind, st, os.path.join(here, ent.name))
            if is_dir:
                stack.append((os.path.join(here, ent.name), rel, ancestors))
    return result


def list_entries(spec: CopySpec, cancel: Optional[threading.Event] = None) -> List[rsync.ListEntry]:
    """Сформировать список источника в формате структур rsync."""
    tree = scan_tree(spec, True, cancel)
    output = []
    for name, (kind, st, path) in sorted(tree.entries.items()):
        if cancel is not None and cancel.is_set():
            break
        if kind == "l" and spec.symlinks == "copy_target":
            try:
                followed = os.stat(path)
                kind = "d" if stat.S_ISDIR(followed.st_mode) else "f"
                st = followed
            except OSError:
                pass
        output.append(rsync.ListEntry(kind, int(st.st_size), name))
    return output
