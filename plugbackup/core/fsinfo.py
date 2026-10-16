"""Особенности файловых систем носителя: название для показа и ограничения копии."""

import unicodedata
from dataclasses import dataclass, field

from plugbackup.i18n import _

FAT32_MAX_FILE = 4 * 1024 ** 3 - 1  # 4 ГиБ − 1 байт
# В зашифрованной папке (gocryptfs) файл на носителе больше исходного: заголовок 18 байт и 32 байта
# на каждый блок 4096 байт. Наибольший исходный размер, который ещё помещается на FAT32:
FAT32_MAX_FILE_ENCRYPTED = (FAT32_MAX_FILE - 18) // 4128 * 4096

_NAMES = {
    "vfat": "FAT",
    "exfat": "exFAT",
    "ntfs": "NTFS",
    "ntfs3": "NTFS",
    "ext4": "ext4",
    "ext3": "ext3",
    "ext2": "ext2",
    "btrfs": "Btrfs",
    "xfs": "XFS",
    "f2fs": "F2FS",
}

SUPPORTED = frozenset({"vfat", "exfat", "ntfs", "ntfs3", "ext4"})


def display_name(fs_type: str, fs_version: str = "") -> str:
    if fs_type == "vfat" and fs_version:
        return fs_version  # FAT32 / FAT16 / FAT12 — так отдаёт UDisks2
    return _NAMES.get(fs_type, fs_type or "?")


def limitations(fs_type: str, fs_version: str = "") -> list[str]:
    """Короткие предупреждения о том, что копия на этой ФС не сохранит или не вместит."""
    if fs_type == "vfat":
        return [
            _("Files of 4 GiB (4,294,967,296 bytes) and larger do not fit."),
            _("Linux permissions and symbolic links are not preserved."),
        ]
    if fs_type == "exfat":
        return [_("Linux permissions and symbolic links are not preserved.")]
    if fs_type in ("ntfs", "ntfs3"):
        return [_("Linux permissions are not preserved.")]
    if fs_type == "ext4":
        return []
    if fs_type:
        return [_("This file system has not been tested with Plug & Backup.")]
    return []


# ---------- предпроверка имён и размеров ----------


WINDOWS_FS = frozenset({"vfat", "exfat", "ntfs", "ntfs3"})
CASE_INSENSITIVE_FS = frozenset({"vfat", "exfat"})
INVALID_CHARS = frozenset('"*:<>?\\|')
FAT_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)}
                         | {f"LPT{i}" for i in range(1, 10)})


@dataclass
class NameReport:
    invalid: list[str] = field(default_factory=list)            # пропускаются с отчётом
    collisions: list[tuple[str, str]] = field(default_factory=list)  # блокируют задание
    too_big: list[tuple[str, int]] = field(default_factory=list)     # больше предела FAT32
    symlinks: list[str] = field(default_factory=list)            # ссылки, которые не будут скопированы


def invalid_name(name: str, fs_type: str) -> bool:
    if fs_type not in WINDOWS_FS:
        return False
    if any(c in INVALID_CHARS or ord(c) < 32 for c in name):
        return True
    if name.endswith((" ", ".")):
        return True
    if fs_type == "vfat" and name.split(".", 1)[0].upper() in FAT_RESERVED:
        return True
    return False


def check_names(entries, fs_type: str, copy_symlinks: bool, encrypted: bool = False) -> NameReport:
    """entries — rsync.ListEntry из полного списка источника (с учётом исключений).

    encrypted — копия в зашифрованной папке: имена на носителе зашифрованы (только латиница, цифры, «-», «_»),
    поэтому запрещённых символов и совпадений без учёта регистра не бывает; остаётся предел размера FAT32.
    """
    max_file = FAT32_MAX_FILE_ENCRYPTED if encrypted else FAT32_MAX_FILE
    report = NameReport()
    invalid_dirs: list[str] = []
    seen: dict[str, str] = {}
    for entry in entries:
        path = entry.path.rstrip("/")
        if any(path == d or path.startswith(d + "/") for d in invalid_dirs):
            continue  # вся папка уже пропускается
        name = path.rsplit("/", 1)[-1]
        if not encrypted and invalid_name(name, fs_type):
            report.invalid.append(path)
            if entry.kind == "d":
                invalid_dirs.append(path)
            continue
        if fs_type in CASE_INSENSITIVE_FS and not encrypted:
            key = unicodedata.normalize("NFC", path).casefold()
            other = seen.get(key)
            if other is not None and other != path:
                report.collisions.append((other, path))
            else:
                seen[key] = path
        if entry.kind == "f" and fs_type == "vfat" and entry.size > max_file:
            report.too_big.append((path, entry.size))
        if entry.kind == "l" and not copy_symlinks:
            report.symlinks.append(path)
    return report
