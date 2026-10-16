"""Сравнение объектов и имён источника с учётом особенностей ФС."""
from __future__ import annotations

import hashlib
import os
import unicodedata
from typing import NamedTuple, Tuple

from plugbackup.core import fsinfo, rsync
from plugbackup.engine.models import CopySpec


class EntryComparison(NamedTuple):
    """Результат сравнения содержимого и метаданных пары объектов."""

    same: bool
    update_meta: bool


def case_insensitive(spec: CopySpec) -> bool:
    """Определить ФС, где имена сопоставляются без учёта регистра."""
    return spec.fs_type in fsinfo.WINDOWS_FS or spec.fs_type in ("apfs", "hfs", "hfsplus")


def name_key(name: str, spec: CopySpec) -> str:
    """Вернуть ключ имени с нужной нормализацией для ФС назначения."""
    return unicodedata.normalize("NFC", name).casefold() if case_insensitive(spec) else name


def _hash(path: str) -> bytes:
    """Вычислить BLAKE2b для файла блоками ограниченного размера."""
    digest = hashlib.blake2b()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.digest()


def compare_entries(src: Tuple[str, os.stat_result, str], dst: Tuple[str, os.stat_result, str],
                    spec: CopySpec) -> EntryComparison:
    """Сравнить тип, данные и метаданные двух просканированных объектов."""
    sk, ss, sp = src
    dk, ds, dp = dst
    if sk != dk:
        return EntryComparison(False, False)
    if sk == "d":
        mode_change = spec.fs_type in rsync.POSIX_FS and (ss.st_mode & 0o7777) != (ds.st_mode & 0o7777)
        return EntryComparison(True, mode_change or int(ss.st_mtime) != int(ds.st_mtime))
    if sk == "l":
        return EntryComparison(os.readlink(sp) == os.readlink(dp), False)
    if sk != "f":
        return EntryComparison(True, False)
    if ss.st_size != ds.st_size:
        return EntryComparison(False, False)
    if spec.compare == "checksum":
        if _hash(sp) != _hash(dp):
            return EntryComparison(False, False)
        mode_change = spec.fs_type in rsync.POSIX_FS and (ss.st_mode & 0o7777) != (ds.st_mode & 0o7777)
        return EntryComparison(True, mode_change or ss.st_mtime != ds.st_mtime)
    window = 1 if spec.fs_type in rsync.FAT_LIKE else 0
    same_time = abs(int(ss.st_mtime) - int(ds.st_mtime)) <= window
    mode_change = spec.fs_type in rsync.POSIX_FS and (ss.st_mode & 0o7777) != (ds.st_mode & 0o7777)
    return EntryComparison(same_time, same_time and mode_change)
