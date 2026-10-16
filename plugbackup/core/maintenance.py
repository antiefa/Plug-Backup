"""Ручное обслуживание копии: очистка корзины, удаление исключённого из копии, очистка копии задания.

Каждое действие выполняется только по подтверждению пользователя (подтверждение — в интерфейсе)
и только когда корень копии на носителе помечен меткой этого профиля. Удаляемое из копии задания
в режиме «зеркало с корзиной» не стирается, а переносится в корзину версий.

Функции нижнего уровня (list_versions, delete_versions, excluded_in_copy, remove_paths, clear_dir)
работают с уже проверенными путями; prepare() делает проверки носителя и метки.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass

from plugbackup.core import binding, marker
from plugbackup.core import rsync as R
from plugbackup.core.copier import default_copier
from plugbackup.core.binding import BindingError
from plugbackup.core.profiles import Job, Profile, normalize_dest
from plugbackup.core.runner import Runner, unique_versions_name, versions_dirs


@dataclass(frozen=True)
class VersionFolder:
    name: str
    path: str
    stamp: float
    size: int
    files: int


def _tree_size(path: str) -> tuple[int, int]:
    size = files = 0
    for current, _dirs, names in os.walk(path):
        for name in names:
            try:
                size += os.lstat(os.path.join(current, name)).st_size
                files += 1
            except OSError:
                pass
    return size, files


def _within(path: str, base: str) -> bool:
    real, real_base = os.path.realpath(path), os.path.realpath(base)
    return real == real_base or real.startswith(real_base + os.sep)


# ---------- проверки перед действием ----------

def prepare(profile: Profile, volume, job: Job | None = None) -> tuple[str, str | None]:
    """Корень копии и (для задания) его папка — только если метка на носителе принадлежит профилю."""
    root = binding.root_dir_for(profile, volume)
    state = marker.root_state(root, profile.id)
    if state != marker.ROOT_MATCH:
        raise BindingError(state)
    if job is None:
        return root, None
    dest = normalize_dest(job.dest)
    current = marker.read_marker(root)
    if current is None or job.id not in current.jobs:
        raise BindingError("job-no-marker")
    Runner._check_dest_path(root, dest)
    return root, os.path.join(root, dest)


def versions_target(root: str, dest: str) -> str:
    """Папка корзины для того, что убирается из копии задания сейчас."""
    return os.path.join(root, R.VERSIONS_DIR, unique_versions_name(root, time.gmtime()), normalize_dest(dest))


# ---------- корзина ----------

def list_versions(root: str) -> list[VersionFolder]:
    found = []
    for stamp, path in versions_dirs(root):
        size, files = _tree_size(path)
        found.append(VersionFolder(os.path.basename(path), path, stamp, size, files))
    return found


def delete_versions(root: str, names: list[str]) -> list[str]:
    """Удалить выбранные папки корзины. Удаляется только то, что действительно является папкой корзины."""
    allowed = {os.path.basename(path): path for _stamp, path in versions_dirs(root)}
    removed = []
    for name in names:
        path = allowed.get(name)
        if path is None or os.path.islink(path):
            continue
        shutil.rmtree(path)
        removed.append(name)
    base = os.path.join(root, R.VERSIONS_DIR)
    if os.path.isdir(base) and not os.path.islink(base) and not os.listdir(base):
        os.rmdir(base)
    return removed


# ---------- исключённое в копии ----------

def _dry_deletions(job: Job, dest_dir: str, fs_type: str, global_excludes: list[str],
                   delete_excluded: bool) -> list[str]:
    return default_copier().dry_deletions(job, dest_dir, fs_type, global_excludes, delete_excluded)


def excluded_in_copy(job: Job, dest_dir: str, fs_type: str, global_excludes: list[str]) -> list[str]:
    """Пути в копии, которые остались там только потому, что подпадают под исключения задания."""
    if not os.path.isdir(dest_dir):
        return []
    with_excluded = _dry_deletions(job, dest_dir, fs_type, global_excludes, True)
    plain = set(_dry_deletions(job, dest_dir, fs_type, global_excludes, False))
    return [p for p in with_excluded if p not in plain and not p.startswith(R.PARTIAL_DIR)]


def free_target(path: str) -> str:
    """Путь в корзине, который ничего не перезапишет: занятое имя получает суффикс ~2, ~3 …"""
    if not os.path.lexists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.lexists(f"{base}~{n}{ext}"):
        n += 1
    return f"{base}~{n}{ext}"


def remove_paths(dest_dir: str, paths: list[str], move_to: str | None) -> tuple[int, int]:
    """Убрать из копии перечисленные пути (файлы — затем опустевшие папки).

    move_to — папка корзины: файлы переносятся туда с сохранением путей; None — удаляются.
    Возвращает (файлов, папок). Пути вне dest_dir и переходы по ссылкам игнорируются.
    """
    files = [p for p in paths if not p.endswith("/")]
    dirs = [p.rstrip("/") for p in paths if p.endswith("/")]
    # Папка, переданная без «/» в конце, — тоже папка (по самой ФС, ссылки — не папки).
    for rel in list(files):
        path = os.path.join(dest_dir, rel)
        if os.path.isdir(path) and not os.path.islink(path):
            files.remove(rel)
            dirs.append(rel)
    dirs = sorted(set(dirs), key=lambda p: p.count("/"), reverse=True)
    removed_files = removed_dirs = 0
    for rel in files:
        path = os.path.join(dest_dir, rel)
        if not _within(os.path.dirname(path), dest_dir) or not os.path.lexists(path):
            continue
        if move_to is not None:
            target = free_target(os.path.join(move_to, rel))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            os.replace(path, target)
        else:
            os.unlink(path)
        removed_files += 1
    for rel in dirs:
        path = os.path.join(dest_dir, rel)
        if os.path.islink(path) or not os.path.isdir(path) or not _within(path, dest_dir):
            continue
        try:
            os.rmdir(path)  # только пустые: всё нужное уже перенесено или удалено выше
            removed_dirs += 1
        except OSError:
            pass
    return removed_files, removed_dirs


# ---------- очистка копии задания ----------

def copy_contents(dest_dir: str) -> tuple[list[str], int]:
    """Все файлы копии задания (относительные пути) и их общий объём."""
    paths, size = [], 0
    for current, dirs, names in os.walk(dest_dir):
        dirs.sort()
        for name in sorted(names):
            full = os.path.join(current, name)
            paths.append(os.path.relpath(full, dest_dir))
            try:
                size += os.lstat(full).st_size
            except OSError:
                pass
    return paths, size


def clear_dir(dest_dir: str, move_to: str | None) -> tuple[int, int]:
    """Очистить папку копии задания (сама папка остаётся)."""
    files, _size = copy_contents(dest_dir)
    dirs = []
    for current, subdirs, _names in os.walk(dest_dir):
        for name in subdirs:
            rel = os.path.relpath(os.path.join(current, name), dest_dir)
            # Ссылка на папку — это элемент копии, а не папка: убирается как ссылка, без перехода по ней.
            if os.path.islink(os.path.join(current, name)):
                files.append(rel)
            else:
                dirs.append(rel + "/")
    return remove_paths(dest_dir, files + dirs, move_to)
