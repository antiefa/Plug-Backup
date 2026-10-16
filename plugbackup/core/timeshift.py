"""Обнаружение сдвига времени файлов на FAT32."""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass


HOUR_SECONDS = 3600
TIME_TOLERANCE = 2
MIN_PAIRS = 20
MATCH_RATIO = 0.90


@dataclass(frozen=True)
class Shift:
    hours: int
    pairs: int
    matched: int


def detect_shift(samples: list[tuple[int, float, int, float]]) -> Shift | None:
    """Определить общий целочасовой сдвиг по парам (размер, время, размер, время)."""
    samples = [sample for sample in samples if sample[0] == sample[2]]
    pairs = len(samples)
    if pairs < MIN_PAIRS:
        return None
    counts: dict[int, int] = {}
    for _source_size, source_mtime, _dest_size, dest_mtime in samples:
        difference = source_mtime - dest_mtime
        hours = int(round(difference / HOUR_SECONDS))
        if hours == 0 or abs(hours) > 14:
            continue
        if abs(difference - hours * HOUR_SECONDS) <= TIME_TOLERANCE:
            counts[hours] = counts.get(hours, 0) + 1
    if not counts:
        return None
    hours, matched = max(counts.items(), key=lambda item: item[1])
    if matched / pairs < MATCH_RATIO:
        return None
    return Shift(hours, pairs, matched)


def _safe_regular(path: str, root: str) -> os.stat_result | None:
    """Получить lstat только для обычного файла внутри дерева без ссылок."""
    root = os.path.abspath(root)
    path = os.path.abspath(path)
    try:
        if os.path.commonpath([root, path]) != root:
            return None
    except ValueError:
        return None
    try:
        root_stat = os.lstat(root)
    except OSError:
        return None
    if not stat.S_ISDIR(root_stat.st_mode):
        return None
    relative = os.path.relpath(path, root)
    if relative == ".":
        return None
    current = root
    parts = relative.split(os.sep)
    for part in parts[:-1]:
        current = os.path.join(current, part)
        try:
            current_stat = os.lstat(current)
        except OSError:
            return None
        if not stat.S_ISDIR(current_stat.st_mode):
            return None
    try:
        result = os.lstat(path)
    except OSError:
        return None
    return result if stat.S_ISREG(result.st_mode) else None


def _entry_path(root: str, relative: str) -> str | None:
    if not relative or os.path.isabs(relative):
        return None
    root = os.path.abspath(root)
    path = os.path.abspath(os.path.join(root, relative.replace("/", os.sep)))
    try:
        if os.path.commonpath([root, path]) != root:
            return None
    except ValueError:
        return None
    return path


def collect_pairs(entries, source_root: str, dest_root: str) -> list[tuple[str, int, float, int, float]]:
    """Собрать безопасные совпадающие обычные файлы из списка источника."""
    pairs = []
    for entry in entries:
        if entry.kind != "f":
            continue
        source_path = _entry_path(source_root, entry.path)
        dest_path = _entry_path(dest_root, entry.path)
        if source_path is None or dest_path is None:
            continue
        source_stat = _safe_regular(source_path, source_root)
        dest_stat = _safe_regular(dest_path, dest_root)
        if source_stat is None or dest_stat is None or source_stat.st_size != dest_stat.st_size:
            continue
        pairs.append((entry.path, source_stat.st_size, source_stat.st_mtime,
                      dest_stat.st_size, dest_stat.st_mtime))
    return pairs


def detect(entries, source_root: str, dest_root: str) -> Shift | None:
    """Обнаружить сдвиг времени в совпадающих файлах задания."""
    samples = collect_pairs(entries, source_root, dest_root)
    return detect_shift([(source_size, source_mtime, dest_size, dest_mtime)
                         for _path, source_size, source_mtime, dest_size, dest_mtime in samples])


def matching_paths(entries, source_root: str, dest_root: str, shift: Shift) -> list[tuple[str, float, float]]:
    """Вернуть пути и времена только для пар с указанным сдвигом."""
    matches = []
    for path, _source_size, source_mtime, _dest_size, dest_mtime in collect_pairs(
            entries, source_root, dest_root):
        difference = source_mtime - dest_mtime
        if abs(difference - shift.hours * HOUR_SECONDS) <= TIME_TOLERANCE:
            matches.append((path, source_mtime, dest_mtime))
    return matches
