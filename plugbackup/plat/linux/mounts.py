"""Проверка, что путь — действительно точка монтирования нужного носителя.

Источник истины — ядро: /proc/self/mountinfo и номера устройств (st_dev),
а не только данные UDisks2. Используется перед записью на носитель и во время запуска.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

MOUNTINFO = "/proc/self/mountinfo"
BY_UUID = "/dev/disk/by-uuid"


@dataclass(frozen=True)
class MountEntry:
    mount_id: int
    parent_id: int
    dev: tuple[int, int]
    root: str
    mount_point: str
    options: str
    fs_type: str
    source: str
    super_options: str


@dataclass(frozen=True)
class MountCheck:
    ok: bool
    reason: str = ""


def _unescape(value: str) -> str:
    """mountinfo экранирует пробел, табуляцию, перевод строки и \\ восьмеричными кодами."""
    out, i = [], 0
    while i < len(value):
        code = value[i + 1:i + 4]
        if value[i] == "\\" and len(code) == 3 and all(c in "01234567" for c in code):
            out.append(chr(int(value[i + 1:i + 4], 8)))
            i += 4
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


def parse_mountinfo(text: str) -> list[MountEntry]:
    entries = []
    for line in text.splitlines():
        if not line.strip():
            continue
        left, _sep, right = line.partition(" - ")
        fields = left.split()
        tail = right.split()
        if len(fields) < 6 or len(tail) < 3:
            continue
        major, minor = fields[2].split(":")
        entries.append(MountEntry(
            mount_id=int(fields[0]),
            parent_id=int(fields[1]),
            dev=(int(major), int(minor)),
            root=_unescape(fields[3]),
            mount_point=_unescape(fields[4]),
            options=fields[5],
            fs_type=tail[0],
            source=_unescape(tail[1]),
            super_options=tail[2],
        ))
    return entries


def read_mountinfo(path: str = MOUNTINFO) -> list[MountEntry]:
    with open(path, encoding="utf-8", errors="surrogateescape") as f:
        return parse_mountinfo(f.read())


def find_mount(mount_point: str, entries: list[MountEntry] | None = None) -> MountEntry | None:
    """Последняя (верхняя) запись монтирования для пути — она и действует."""
    entries = read_mountinfo() if entries is None else entries
    target = os.path.normpath(mount_point)
    found = None
    for entry in entries:
        if entry.mount_point == target:
            found = entry
    return found


def device_for_uuid(uuid: str) -> str | None:
    path = os.path.join(BY_UUID, uuid)
    return os.path.realpath(path) if os.path.exists(path) else None


def verify_mount(mount_point: str, expected_uuid: str, writable: bool = True) -> MountCheck:
    """Путь смонтирован, на нём файловая система с expected_uuid, и (если нужно) он доступен на запись."""
    if not expected_uuid or "/" in expected_uuid:
        return MountCheck(False, "invalid-uuid")
    entry = find_mount(mount_point)
    if entry is None or not os.path.ismount(mount_point):
        return MountCheck(False, "not-mounted")
    device = device_for_uuid(expected_uuid)
    if device is None:
        return MountCheck(False, "uuid-not-found")
    try:
        mount_dev = os.stat(mount_point).st_dev
        block_rdev = os.stat(device).st_rdev
    except OSError as exc:
        return MountCheck(False, f"stat-failed: {exc.strerror}")
    if mount_dev != block_rdev or entry.dev != (os.major(block_rdev), os.minor(block_rdev)):
        return MountCheck(False, "uuid-mismatch")
    if entry.root != "/":
        # Смонтирована не вся файловая система, а подпапка (bind) — для копии не годится.
        return MountCheck(False, "not-filesystem-root")
    if writable:
        if "ro" in entry.options.split(",") or "ro" in entry.super_options.split(","):
            return MountCheck(False, "read-only")
        if not os.access(mount_point, os.W_OK):
            return MountCheck(False, "not-writable")
    return MountCheck(True)


def parent_is_user_writable(mount_point: str) -> bool:
    """Родитель точки монтирования доступен пользователю на запись — без носителя туда можно что-то записать."""
    return os.access(os.path.dirname(os.path.normpath(mount_point)) or "/", os.W_OK)
