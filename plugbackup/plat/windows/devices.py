"""Перечисление томов и периодический монитор Windows."""
from __future__ import annotations

import threading
from typing import Callable, Optional

from plugbackup.core.volume import EjectOutcome, Volume, uuid_conflicts
from plugbackup.i18n import _
from plugbackup.plat.windows import win32

_BUS = {1: "scsi", 2: "atapi", 3: "ata", 4: "1394", 5: "ssa", 6: "fibre", 7: "usb",
        8: "raid", 9: "iscsi", 10: "sas", 11: "sata", 12: "sd", 13: "mmc",
        14: "virtual", 15: "filebackedvirtual", 16: "spaces", 17: "nvme", 18: "scm", 19: "ufs"}
_VOLUME_CACHE = {}
_CACHE_LOCK = threading.RLock()


def _uuid(info, serial64):
    fs = info.filesystem.upper()
    if fs in ("FAT", "FAT12", "FAT16", "FAT32", "EXFAT"):
        return win32.format_serial32(info.serial32), False
    if fs == "NTFS":
        if serial64 is None:
            return f"{info.serial32 & 0xffffffff:08X}", True
        return win32.format_serial64(serial64), False
    return win32.format_serial32(info.serial32), False


def _fs(info):
    fs = (info.filesystem or "").lower()
    if fs in ("fat", "fat12", "fat16", "fat32"):
        return "vfat", info.filesystem.upper()
    if fs == "exfat":
        return "exfat", ""
    if fs == "ntfs":
        return "ntfs", ""
    return fs, ""


def _display_paths(points):
    return tuple((p if p.endswith("\\") else p + "\\") if len(p) == 2 and p[1] == ":" else p
                 for p in points)


def _query_signature(info):
    return (info.label, info.filesystem, info.serial32, info.error, tuple(info.access_points))


def _cached_details(info):
    signature = _query_signature(info)
    with _CACHE_LOCK:
        previous = _VOLUME_CACHE.get(info.path)
        if previous is not None and previous["signature"] == signature:
            return previous["storage"], previous["serial64"], previous["bitlocker"]
        known_encrypted = previous is not None and previous["bitlocker"] in ("locked", "unlocked")
        try:
            storage = win32.query_storage(info.path)
        except OSError:
            storage = win32.StorageInfo(error=1)
        serial64 = None
        if info.error is None and info.filesystem.upper() == "NTFS":
            serial64 = win32.ntfs_volume_serial(info.path)
        bitlocker = previous["bitlocker"] if known_encrypted else "unknown"
        if info.error is not None or known_encrypted:
            bitlocker = win32.interpret_bitlocker(win32.bitlocker_raw(info.path))
        _VOLUME_CACHE[info.path] = {"signature": signature, "storage": storage,
                                    "serial64": serial64, "bitlocker": bitlocker}
        return storage, serial64, bitlocker


def list_volumes(show_all: bool = True) -> list[Volume]:
    system = win32.system_volume_paths()
    result = []
    for info in win32.enumerate_volumes():
        if info.error == win32.ERROR_NOT_READY:
            with _CACHE_LOCK:
                _VOLUME_CACHE.pop(info.path, None)
            continue
        if info.path in system or info.drive_type in (win32.DRIVE_REMOTE, win32.DRIVE_CDROM, win32.DRIVE_RAMDISK,
                                                       win32.DRIVE_NO_ROOT_DIR):
            continue
        storage, serial64, bitlocker = _cached_details(info)
        bus = _BUS.get(storage.bus_type, "") if storage.bus_type is not None else ""
        removable = info.drive_type == win32.DRIVE_REMOVABLE or storage.removable_media is True
        usb_sd = bus in ("usb", "sd", "mmc")
        bus_unknown = storage.error is not None or storage.bus_type is None or bus == ""
        if not (removable or (info.drive_type == win32.DRIVE_FIXED and usb_sd) or bus_unknown or show_all):
            continue
        if info.error is None:
            fs_uuid, partial = _uuid(info, serial64)
            fs_type, fs_version = _fs(info)
        else:
            fs_uuid, partial = "", False
            fs_type, fs_version = "", ""
        encrypted = bitlocker in ("locked", "unlocked")
        locked = bitlocker == "locked"
        points = _display_paths(info.access_points)
        volume_uuid = info.path[:-1] if info.path.endswith("\\") else info.path
        result.append(Volume(
            object_path=info.path, device="\\\\.\\" + volume_uuid[4:], uuid=fs_uuid, inner_uuid="",
            fs_type=fs_type, fs_version=fs_version, label=info.label, size=info.total_bytes or 0,
            mount_points=(info.path,), encrypted=encrypted, locked=locked, filesystem_path="",
            drive_path=f"\\\\.\\PhysicalDrive{storage.disk_number}" if storage.disk_number is not None else "",
            drive_vendor=storage.vendor, drive_model=storage.product, drive_serial=storage.serial,
            drive_id="", connection_bus=bus, removable=removable, sibling_id="",
            is_loop=bus == "filebackedvirtual", loop_backing_file="", access_paths=points,
            uuid_partial=partial, bus_unknown=bus_unknown,
        ))
    result.sort(key=lambda v: (v.drive_path or "~", v.object_path))
    return result


class DeviceMonitor:
    """Опрос томов каждые две секунды; подписчики вызываются из потока монитора."""

    def __init__(self, show_all: bool = False) -> None:
        self._show_all = show_all
        self._volumes = {v.object_path: v for v in list_volumes(show_all)}
        self._callbacks: dict[int, Callable] = {}
        self._next_callback = 0
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="PlugBackupDeviceMonitor", daemon=True)
        self._thread.start()

    def volumes(self) -> list[Volume]:
        with self._lock:
            return list(self._volumes.values())

    def get(self, identifier: str) -> Optional[Volume]:
        with self._lock:
            return self._volumes.get(identifier)

    def find_by_uuid(self, uuid: str) -> list[Volume]:
        with self._lock:
            return [v for v in self._volumes.values() if uuid and v.uuid == uuid]

    def subscribe(self, callback: Callable[["DeviceMonitor"], None]) -> Callable[[], None]:
        with self._lock:
            self._next_callback += 1
            key = self._next_callback
            self._callbacks[key] = callback
        def unsubscribe() -> None:
            with self._lock:
                self._callbacks.pop(key, None)
        return unsubscribe

    def refresh(self) -> None:
        current = {v.object_path: v for v in list_volumes(self._show_all)}
        with self._lock:
            changed = current != self._volumes
            self._volumes = current
            callbacks = tuple(self._callbacks.values()) if changed else ()
        for callback in callbacks:
            callback(self)

    def notify_changed(self) -> None:
        self._wake.set()

    def set_show_all(self, enabled: bool) -> None:
        self._show_all = enabled
        self.notify_changed()

    def _run(self) -> None:
        while not self._closed:
            self._wake.wait(2.0)
            self._wake.clear()
            if not self._closed:
                try:
                    self.refresh()
                except OSError:
                    pass

    def close(self) -> None:
        self._closed = True
        self._wake.set()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=3.0)
        with self._lock:
            self._callbacks.clear()


def eject(volume: Volume) -> EjectOutcome:
    if not volume.drive_path:
        return EjectOutcome(False, False, _("Physical disk number is unknown"), "")
    try:
        disk_number = int(volume.drive_path.rsplit("PhysicalDrive", 1)[1])
        result = win32.eject_disk(disk_number)
    except (OSError, ValueError) as exc:
        return EjectOutcome(False, False, str(exc), "")
    blocked = result.veto_name if result.veto_name else ""
    if result.success:
        message = ""
    elif blocked:
        message = _("Ejection was blocked")
    else:
        message = _("Could not eject the device")
    return EjectOutcome(bool(result.success), bool(result.success), message, blocked)
