"""Проверка тома Windows перед записью."""
from __future__ import annotations

import os
from dataclasses import dataclass

from plugbackup.core.volume import uuid_matches
from plugbackup.plat.windows import win32


@dataclass(frozen=True)
class MountCheck:
    ok: bool
    reason: str = ""


def verify_mount(mount_point: str, expected_uuid: str, writable: bool = True) -> MountCheck:
    if not expected_uuid or "/" in expected_uuid or "\\" in expected_uuid:
        return MountCheck(False, "invalid-uuid")
    try:
        info = win32.query_volume(mount_point)
    except OSError:
        return MountCheck(False, "not-mounted")
    if info.error is not None:
        return MountCheck(False, "not-mounted")
    fs = info.filesystem.upper()
    if fs in ("FAT", "FAT12", "FAT16", "FAT32", "EXFAT"):
        actual = win32.format_serial32(info.serial32)
        if actual.replace("-", "") != expected_uuid.replace("-", "").upper():
            return MountCheck(False, "uuid-mismatch")
    elif fs == "NTFS":
        serial64 = win32.ntfs_volume_serial(info.path)
        if serial64 is not None:
            if win32.format_serial64(serial64) != expected_uuid.upper():
                return MountCheck(False, "uuid-mismatch")
        elif f"{info.serial32:08X}" != expected_uuid.replace("-", "")[-8:].upper():
            return MountCheck(False, "uuid-mismatch")
    else:
        actual = win32.format_serial32(info.serial32)
        if actual != expected_uuid.upper():
            return MountCheck(False, "uuid-mismatch")
    if writable:
        if info.filesystem_flags & win32.FILE_READ_ONLY_VOLUME:
            return MountCheck(False, "read-only")
        if not os.access(mount_point, os.W_OK):
            return MountCheck(False, "not-writable")
    return MountCheck(True)
