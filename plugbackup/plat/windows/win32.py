"""Тонкие обёртки Win32. Парсеры и структуры доступны для тестов на любой ОС."""

from __future__ import annotations

import ctypes
import os
import struct
import sys
import uuid
from ctypes import wintypes
from dataclasses import dataclass
from typing import Optional


class Win32Unavailable(OSError):
    """Вызов Win32 сделан вне Windows."""


def _require_windows():
    if sys.platform != "win32":
        raise Win32Unavailable("Вызов доступен только в Windows")


_DLL_CACHE = {}
_FUNCTION_CACHE = {}


def _dll(name: str, *, last_error: bool = True):
    _require_windows()
    key = (name, last_error)
    if key not in _DLL_CACHE:
        _DLL_CACHE[key] = ctypes.WinDLL(name, use_last_error=last_error)
    return _DLL_CACHE[key]


def _fn(dll, name, args, result):
    key = (id(dll), name)
    cached = _FUNCTION_CACHE.get(key)
    if cached is not None and cached[0] is dll:
        return cached[1]
    f = getattr(dll, name)
    f.argtypes, f.restype = args, result
    _FUNCTION_CACHE[key] = (dll, f)
    return f


DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
UINT = ctypes.c_uint32
BOOL = ctypes.c_int32
HANDLE = ctypes.c_void_p
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MAX_PATH = 260
ERROR_MORE_DATA = 234
ERROR_NO_MORE_FILES = 18
ERROR_NOT_READY = 21
ERROR_UNRECOGNIZED_VOLUME = 1005
FVE_E_LOCKED_VOLUME = 0x80310000

FILE_CASE_SENSITIVE_SEARCH = 0x00000001
FILE_CASE_PRESERVED_NAMES = 0x00000002
FILE_READ_ONLY_VOLUME = 0x00080000
DRIVE_UNKNOWN, DRIVE_NO_ROOT_DIR, DRIVE_REMOVABLE, DRIVE_FIXED = 0, 1, 2, 3
DRIVE_REMOTE, DRIVE_CDROM, DRIVE_RAMDISK = 4, 5, 6


class GUID(ctypes.Structure):
    _fields_ = [("Data1", DWORD), ("Data2", WORD), ("Data3", WORD), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def from_uuid(cls, value):
        return cls.from_buffer_copy(uuid.UUID(str(value)).bytes_le)

    def as_uuid(self):
        return str(uuid.UUID(bytes_le=bytes(self)))


class PROPVARIANT_VALUE(ctypes.Union):
    _fields_ = [("uint64", ctypes.c_uint64), ("pointer", HANDLE),
                ("reserved", ctypes.c_ubyte * (16 if ctypes.sizeof(ctypes.c_void_p) == 8 else 8))]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", WORD), ("reserved1", WORD), ("reserved2", WORD),
                ("reserved3", WORD), ("value", PROPVARIANT_VALUE)]


class STORAGE_PROPERTY_QUERY(ctypes.Structure):
    _fields_ = [("PropertyId", DWORD), ("QueryType", DWORD), ("AdditionalParameters", ctypes.c_ubyte * 1)]


class STORAGE_DEVICE_DESCRIPTOR(ctypes.Structure):
    _fields_ = [("Version", DWORD), ("Size", DWORD), ("DeviceType", ctypes.c_ubyte),
                ("DeviceTypeModifier", ctypes.c_ubyte), ("RemovableMedia", ctypes.c_ubyte),
                ("CommandQueueing", ctypes.c_ubyte), ("VendorIdOffset", DWORD),
                ("ProductIdOffset", DWORD), ("ProductRevisionOffset", DWORD),
                ("SerialNumberOffset", DWORD), ("BusType", DWORD), ("RawPropertiesLength", DWORD),
                ("RawDeviceProperties", ctypes.c_ubyte * 1)]


class DISK_EXTENT(ctypes.Structure):
    _fields_ = [("DiskNumber", DWORD), ("StartingOffset", ctypes.c_int64), ("ExtentLength", ctypes.c_int64)]


class NTFS_VOLUME_DATA_BUFFER(ctypes.Structure):
    _fields_ = [("VolumeSerialNumber", ctypes.c_int64), ("NumberSectors", ctypes.c_int64),
                ("TotalClusters", ctypes.c_int64), ("FreeClusters", ctypes.c_int64),
                ("TotalReserved", ctypes.c_int64), ("BytesPerSector", DWORD),
                ("BytesPerCluster", DWORD), ("BytesPerFileRecordSegment", DWORD),
                ("ClustersPerFileRecordSegment", DWORD), ("MftValidDataLength", ctypes.c_int64),
                ("MftStartLcn", ctypes.c_int64), ("Mft2StartLcn", ctypes.c_int64),
                ("MftZoneStart", ctypes.c_int64), ("MftZoneEnd", ctypes.c_int64)]


class DEV_BROADCAST_HDR(ctypes.Structure):
    _fields_ = [("dbch_size", DWORD), ("dbch_devicetype", DWORD), ("dbch_reserved", DWORD)]


class DEV_BROADCAST_VOLUME(ctypes.Structure):
    _fields_ = DEV_BROADCAST_HDR._fields_ + [("dbcv_unitmask", DWORD), ("dbcv_flags", WORD)]


class DEV_BROADCAST_HANDLE(ctypes.Structure):
    _fields_ = DEV_BROADCAST_HDR._fields_ + [("dbch_handle", HANDLE), ("dbch_hdevnotify", HANDLE),
                ("dbch_eventguid", GUID), ("dbch_nameoffset", DWORD), ("dbch_data", ctypes.c_ubyte * 1)]


@dataclass(frozen=True)
class VolumeInfo:
    path: str
    access_points: tuple
    drive_type: int
    label: str
    filesystem: str
    serial32: int
    max_component_length: int
    filesystem_flags: int
    total_bytes: Optional[int]
    free_bytes: Optional[int]
    error: Optional[int] = None


@dataclass(frozen=True)
class StorageInfo:
    bus_type: Optional[int] = None
    removable_media: Optional[bool] = None
    vendor: str = ""
    product: str = ""
    serial: str = ""
    disk_number: Optional[int] = None
    error: Optional[int] = None


@dataclass(frozen=True)
class EjectResult:
    success: bool
    veto_type: Optional[int] = None
    veto_name: str = ""
    configret: Optional[int] = 0
    not_found: bool = False


def format_serial32(serial: int) -> str:
    n = int(serial) & 0xffffffff
    return f"{n >> 16:04X}-{n & 0xffff:04X}"


def format_serial64(serial: int) -> str:
    return f"{int(serial) & 0xffffffffffffffff:016X}"


def parse_multisz(data, char_width=ctypes.sizeof(ctypes.c_wchar)) -> tuple[str, ...]:
    """Разобрать MULTI_SZ, включая подготовленные UTF-16LE-буферы на Linux."""
    if isinstance(data, str):
        return tuple(x for x in data.split("\0") if x)
    raw = bytes(data)
    if char_width == 2:
        text = raw.decode("utf-16-le", "replace")
    else:
        # Linux c_wchar имеет 4 байта; Win32 WCHAR всегда 2.
        text = raw.decode("utf-16-le", "replace") if len(raw) % 2 == 0 else raw.decode("utf-32-le", "replace")
    return tuple(x for x in text.split("\0") if x)


def parse_storage_descriptor(data) -> dict:
    """Поля StorageDeviceProperty из STORAGE_DEVICE_DESCRIPTOR."""
    b = bytes(data)
    if len(b) < 36:
        raise ValueError("Короткий STORAGE_DEVICE_DESCRIPTOR")
    version, size, device_type, modifier, removable, queueing = struct.unpack_from("<II4B", b, 0)
    vendor_off, product_off, revision_off, serial_off = struct.unpack_from("<4I", b, 12)
    bus_type, raw_len = struct.unpack_from("<2I", b, 28)

    def string_at(offset):
        if not offset or offset >= len(b):
            return ""
        end = b.find(b"\0", offset)
        if end < 0:
            end = len(b)
        return b[offset:end].decode("ascii", "replace").strip()
    return {"bus_type": bus_type, "removable_media": bool(removable), "vendor": string_at(vendor_off),
            "product": string_at(product_off), "revision": string_at(revision_off), "serial": string_at(serial_off)}


def parse_volume_mask(mask) -> tuple[str, ...]:
    return tuple(chr(65 + i) for i in range(26) if int(mask) & (1 << i))


def _read_utf16z(address: int) -> str:
    raw = bytearray()
    while True:
        pair = ctypes.string_at(address + len(raw), 2)
        if pair == b"\0\0":
            break
        raw.extend(pair)
    return bytes(raw).decode("utf-16-le", "replace")


GUID_IO_VOLUME_UNLOCK = GUID.from_uuid("9a8c3d68-d0cb-4f2d-8d6d-4bda4e6f2b8b")
GUID_IO_VOLUME_LOCK = GUID.from_uuid("50708874-c9af-11d1-8fef-00a0c9a06d32")
GUID_IO_VOLUME_MOUNT = GUID.from_uuid("b5804878-1a96-11d2-8ffd-00a0c9a06d32")
GUID_IO_VOLUME_DISMOUNT = GUID.from_uuid("d16a55e8-1059-11d2-8ffd-00a0c9a06d32")


def parse_device_broadcast(address) -> dict:
    """Разобрать lParam WM_DEVICECHANGE по его объявленному размеру."""
    if isinstance(address, (bytes, bytearray, memoryview)):
        raw_in = bytes(address)
        if len(raw_in) < ctypes.sizeof(DEV_BROADCAST_HDR):
            raise ValueError("Короткий DEV_BROADCAST_HDR")
        owner = ctypes.create_string_buffer(raw_in)
        address = ctypes.addressof(owner)
    hdr = ctypes.cast(address, ctypes.POINTER(DEV_BROADCAST_HDR)).contents
    if hdr.dbch_size < ctypes.sizeof(DEV_BROADCAST_HDR):
        return {"type": hdr.dbch_devicetype}
    if hdr.dbch_devicetype == 2 and hdr.dbch_size >= ctypes.sizeof(DEV_BROADCAST_VOLUME):
        value = ctypes.cast(address, ctypes.POINTER(DEV_BROADCAST_VOLUME)).contents
        return {"type": 2, "letters": parse_volume_mask(value.dbcv_unitmask), "flags": value.dbcv_flags}
    if hdr.dbch_devicetype == DBT_DEVTYP_HANDLE and hdr.dbch_size >= DEV_BROADCAST_HANDLE.dbch_eventguid.offset + 16:
        raw = ctypes.string_at(address, hdr.dbch_size)
        off = DEV_BROADCAST_HANDLE.dbch_eventguid.offset
        ev = uuid.UUID(bytes_le=raw[off:off + 16])
        known = {str(x.as_uuid()): name for x, name in ((GUID_IO_VOLUME_UNLOCK, "unlock"),
                (GUID_IO_VOLUME_LOCK, "lock"), (GUID_IO_VOLUME_MOUNT, "mount"),
                (GUID_IO_VOLUME_DISMOUNT, "dismount"))}
        return {"type": 6, "event": known.get(str(ev), "unknown") if ev else "unknown", "event_guid": ev}
    return {"type": hdr.dbch_devicetype}


WM_DEVICECHANGE = 0x0219
DBT_DEVICEARRIVAL, DBT_DEVICEREMOVECOMPLETE, DBT_CUSTOMEVENT = 0x8000, 0x8004, 0x8006
DBT_DEVTYP_VOLUME, DBT_DEVTYP_HANDLE = 2, 6


def to_extended(path: str) -> str:
    if path.startswith(("\\\\?\\", "\\\\.\\")):
        return path
    if len(path) >= 3 and path[1:3] in (":\\", ":/"):
        normalized = path[0] + ":\\" + path[3:].replace("/", "\\")
        return "\\\\?\\" + normalized
    p = path if path.startswith("\\\\") else os.path.abspath(path)
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    if len(p) >= 3 and p[1:3] in (":\\", ":/"):
        return "\\\\?\\" + p[0] + ":\\" + p[3:].replace("/", "\\")
    return p


def from_extended(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\") and not path.startswith("\\\\?\\Volume{"):
        return path[4:]
    return path


def _api():
    return _dll("kernel32")


def enumerate_volume_paths() -> tuple[str, ...]:
    k = _api()
    first = _fn(k, "FindFirstVolumeW", [wintypes.LPWSTR, DWORD], HANDLE)
    nxt = _fn(k, "FindNextVolumeW", [HANDLE, wintypes.LPWSTR, DWORD], BOOL)
    close = _fn(k, "FindVolumeClose", [HANDLE], BOOL)
    buf = ctypes.create_unicode_buffer(1024)
    h = first(buf, len(buf))
    if h == INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), "FindFirstVolumeW failed")
    result = []
    try:
        while True:
            result.append(buf.value)
            if not nxt(h, buf, len(buf)):
                err = ctypes.get_last_error()
                if err != ERROR_NO_MORE_FILES:
                    raise OSError(err, "FindNextVolumeW failed")
                break
    finally:
        close(h)
    return tuple(result)


def volume_access_points(volume_path: str) -> tuple[str, ...]:
    k = _api()
    fn = _fn(k, "GetVolumePathNamesForVolumeNameW", [wintypes.LPCWSTR, wintypes.LPWSTR, DWORD, ctypes.POINTER(DWORD)], BOOL)
    size = DWORD(0)
    fn(volume_path, None, 0, ctypes.byref(size))
    err = ctypes.get_last_error()
    if err not in (0, ERROR_MORE_DATA):
        raise OSError(err, "GetVolumePathNamesForVolumeNameW failed")
    buf = ctypes.create_unicode_buffer(max(2, size.value))
    if not fn(volume_path, buf, len(buf), ctypes.byref(size)):
        raise OSError(ctypes.get_last_error(), "GetVolumePathNamesForVolumeNameW failed")
    return parse_multisz(buf[:size.value] if size.value else buf.value + "\0\0")


def query_volume(volume_path: str) -> VolumeInfo:
    k = _api()
    label, fs = ctypes.create_unicode_buffer(261), ctypes.create_unicode_buffer(64)
    serial, maxcomp, flags = DWORD(), DWORD(), DWORD()
    getinfo = _fn(k, "GetVolumeInformationW", [wintypes.LPCWSTR, wintypes.LPWSTR, DWORD,
        ctypes.POINTER(DWORD), ctypes.POINTER(DWORD), ctypes.POINTER(DWORD), wintypes.LPWSTR, DWORD], BOOL)
    ok = getinfo(volume_path, label, len(label), ctypes.byref(serial), ctypes.byref(maxcomp), ctypes.byref(flags), fs, len(fs))
    err = None if ok else ctypes.get_last_error()
    # Буква и тип диска известны и без читаемой ФС (закрытый BitLocker, нераспознанная ФС).
    try:
        points = volume_access_points(volume_path)
    except OSError:
        points = ()
    root = points[0] if points else volume_path
    drive = _fn(k, "GetDriveTypeW", [wintypes.LPCWSTR], UINT)(root)
    if err is not None:
        return VolumeInfo(volume_path, points, drive, "", "", 0, 0, 0, None, None, err)
    total, free = ctypes.c_ulonglong(), ctypes.c_ulonglong()
    diskfree = _fn(k, "GetDiskFreeSpaceExW", [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.POINTER(ctypes.c_ulonglong), ctypes.POINTER(ctypes.c_ulonglong)], BOOL)
    if not diskfree(root, ctypes.byref(free), ctypes.byref(total), None):
        err = ctypes.get_last_error()
        return VolumeInfo(volume_path, points, drive, label.value, fs.value, serial.value, maxcomp.value, flags.value, None, None, err)
    return VolumeInfo(volume_path, points, drive, label.value, fs.value, serial.value, maxcomp.value, flags.value, total.value, free.value)


def enumerate_volumes() -> tuple[VolumeInfo, ...]:
    return tuple(query_volume(p) for p in enumerate_volume_paths())


def system_volume_paths() -> frozenset:
    k = _api()
    getwin = _fn(k, "GetWindowsDirectoryW", [wintypes.LPWSTR, DWORD], DWORD)
    win = ctypes.create_unicode_buffer(32768)
    getwin(win, len(win))
    home = os.environ.get("USERPROFILE", "")
    candidates = [win.value, home]
    paths = enumerate_volume_paths()
    chosen = set()
    for candidate in candidates:
        if not candidate:
            continue
        for vol in paths:
            try:
                if _path_belongs(candidate, vol):
                    chosen.add(vol)
                    break
            except OSError:
                pass
    return frozenset(chosen)


def _path_belongs(path, volume):
    k = _api()
    get_mount = _fn(k, "GetVolumePathNameW", [wintypes.LPCWSTR, wintypes.LPWSTR, DWORD], BOOL)
    get_name = _fn(k, "GetVolumeNameForVolumeMountPointW", [wintypes.LPCWSTR, wintypes.LPWSTR, DWORD], BOOL)
    mount, name = ctypes.create_unicode_buffer(1024), ctypes.create_unicode_buffer(1024)
    if not get_mount(path, mount, len(mount)):
        raise OSError(ctypes.get_last_error(), "GetVolumePathNameW failed")
    if not get_name(mount, name, len(name)):
        raise OSError(ctypes.get_last_error(), "GetVolumeNameForVolumeMountPointW failed")
    return name.value.casefold() == volume.casefold()


def ntfs_volume_serial(volume_path: str) -> Optional[int]:
    """64-битный NTFS serial; None означает неподдерживаемый запрос/отказ."""
    k = _api()
    create = _fn(k, "CreateFileW", [wintypes.LPCWSTR, DWORD, DWORD, HANDLE, DWORD, DWORD, HANDLE], HANDLE)
    close = _fn(k, "CloseHandle", [HANDLE], BOOL)
    ioctl = _fn(k, "DeviceIoControl", [HANDLE, DWORD, HANDLE, DWORD, HANDLE, DWORD, ctypes.POINTER(DWORD), HANDLE], BOOL)
    path = volume_path.rstrip("\\")
    h = create(path, 0, 0x1 | 0x2, None, 3, 0, None)
    if h == INVALID_HANDLE_VALUE:
        return None
    try:
        out = NTFS_VOLUME_DATA_BUFFER()
        got = DWORD()
        if not ioctl(h, 0x00090064, None, 0, ctypes.byref(out), ctypes.sizeof(out), ctypes.byref(got), None):
            return None
        return out.VolumeSerialNumber & 0xffffffffffffffff
    finally:
        close(h)


def query_storage(volume_path: str) -> StorageInfo:
    """Запрос свойства и номера диска через дескриптор с нулевым доступом."""
    k = _api()
    create = _fn(k, "CreateFileW", [wintypes.LPCWSTR, DWORD, DWORD, HANDLE, DWORD, DWORD, HANDLE], HANDLE)
    close = _fn(k, "CloseHandle", [HANDLE], BOOL)
    ioctl = _fn(k, "DeviceIoControl", [HANDLE, DWORD, HANDLE, DWORD, HANDLE, DWORD, ctypes.POINTER(DWORD), HANDLE], BOOL)
    path = "\\\\.\\" + volume_path[4:].rstrip("\\") if volume_path.startswith("\\\\?\\Volume{") else volume_path
    h = create(path, 0, 0x1 | 0x2, None, 3, 0, None)
    if h == INVALID_HANDLE_VALUE:
        return StorageInfo(error=ctypes.get_last_error())
    try:
        query = STORAGE_PROPERTY_QUERY(0, 0, (ctypes.c_ubyte * 1)(0))
        buf = ctypes.create_string_buffer(4096)
        got = DWORD()
        if not ioctl(h, 0x002D1400, ctypes.byref(query), ctypes.sizeof(query), buf, len(buf), ctypes.byref(got), None):
            return StorageInfo(error=ctypes.get_last_error())
        fields = parse_storage_descriptor(buf.raw[:got.value])
        number_buf = ctypes.create_string_buffer(12)
        diskno = None
        if ioctl(h, 0x002D1080, None, 0, number_buf, len(number_buf), ctypes.byref(got), None):
            diskno = struct.unpack_from("<I", number_buf.raw, 4)[0]
        return StorageInfo(fields["bus_type"], fields["removable_media"], fields["vendor"], fields["product"], fields["serial"], diskno)
    finally:
        close(h)


def eject_disk(disk_number: int) -> EjectResult:
    """Извлечь родительское PnP-устройство, сопоставленное по номеру диска."""
    _require_windows()
    # SetupAPI/Configuration Manager требуют перечисления интерфейсов и связки DEVINST.
    # Реализация вынесена в отдельную функцию, чтобы отказ возвращался структурированно.
    return _eject_disk_setupapi(int(disk_number))


def _eject_disk_setupapi(disk_number):
    """Найти интерфейс диска и запросить извлечение его родительского устройства."""
    setup, cfg, k = _dll("setupapi"), _dll("cfgmgr32"), _api()
    class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
        _fields_ = [("cbSize", DWORD), ("InterfaceClassGuid", GUID), ("Flags", DWORD), ("Reserved", ctypes.c_size_t)]
    class SP_DEVINFO_DATA(ctypes.Structure):
        _fields_ = [("cbSize", DWORD), ("ClassGuid", GUID), ("DevInst", DWORD), ("Reserved", ctypes.c_size_t)]
    disk_guid = GUID.from_uuid("53f56307-b6bf-11d0-94f2-00a0c91efb8b")
    get_devs = _fn(setup, "SetupDiGetClassDevsW", [ctypes.POINTER(GUID), wintypes.LPCWSTR, HANDLE, DWORD], HANDLE)
    enum = _fn(setup, "SetupDiEnumDeviceInterfaces", [HANDLE, ctypes.POINTER(SP_DEVINFO_DATA), ctypes.POINTER(GUID), DWORD, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)], BOOL)
    detail = _fn(setup, "SetupDiGetDeviceInterfaceDetailW", [HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), HANDLE, DWORD, ctypes.POINTER(DWORD), ctypes.POINTER(SP_DEVINFO_DATA)], BOOL)
    destroy = _fn(setup, "SetupDiDestroyDeviceInfoList", [HANDLE], BOOL)
    create = _fn(k, "CreateFileW", [wintypes.LPCWSTR, DWORD, DWORD, HANDLE, DWORD, DWORD, HANDLE], HANDLE)
    close = _fn(k, "CloseHandle", [HANDLE], BOOL)
    ioctl = _fn(k, "DeviceIoControl", [HANDLE, DWORD, HANDLE, DWORD, HANDLE, DWORD, ctypes.POINTER(DWORD), HANDLE], BOOL)
    parent_fn = _fn(cfg, "CM_Get_Parent", [ctypes.POINTER(DWORD), DWORD, DWORD], DWORD)
    eject = _fn(cfg, "CM_Request_Device_EjectW", [DWORD, ctypes.POINTER(DWORD), wintypes.LPWSTR, DWORD, DWORD], DWORD)
    info_set = get_devs(ctypes.byref(disk_guid), None, None, 0x12)  # PRESENT | DEVICEINTERFACE
    if info_set == INVALID_HANDLE_VALUE:
        return EjectResult(False, configret=ctypes.get_last_error())
    selected = None
    try:
        index = 0
        while True:
            iface = SP_DEVICE_INTERFACE_DATA()
            iface.cbSize = ctypes.sizeof(iface)
            if not enum(info_set, None, ctypes.byref(disk_guid), index, ctypes.byref(iface)):
                if ctypes.get_last_error() == 259:
                    break
                return EjectResult(False, configret=ctypes.get_last_error())
            devinfo = SP_DEVINFO_DATA()
            devinfo.cbSize = ctypes.sizeof(devinfo)
            required = DWORD()
            detail(info_set, ctypes.byref(iface), None, 0, ctypes.byref(required), ctypes.byref(devinfo))
            if not required.value:
                index += 1
                continue
            raw = ctypes.create_string_buffer(required.value)
            struct.pack_into("<I", raw, 0, 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6)
            if not detail(info_set, ctypes.byref(iface), raw, required.value, ctypes.byref(required), ctypes.byref(devinfo)):
                index += 1
                continue
            path = _read_utf16z(ctypes.addressof(raw) + 4)
            h = create(path, 0, 0x1 | 0x2, None, 3, 0, None)
            if h != INVALID_HANDLE_VALUE:
                try:
                    number, got = ctypes.create_string_buffer(12), DWORD()
                    if ioctl(h, 0x002D1080, None, 0, number, len(number), ctypes.byref(got), None):
                        if struct.unpack_from("<I", number.raw, 4)[0] == disk_number:
                            selected = int(devinfo.DevInst)
                            break
                finally:
                    close(h)
            index += 1
    finally:
        destroy(info_set)
    if selected is None:
        return EjectResult(False, configret=None, not_found=True)
    parent = DWORD()
    cr = parent_fn(ctypes.byref(parent), selected, 0)
    if cr != 0:
        return EjectResult(False, configret=cr)
    veto, veto_name = DWORD(), ctypes.create_unicode_buffer(256)
    cr = eject(parent.value, ctypes.byref(veto), veto_name, len(veto_name), 0)
    return EjectResult(cr == 0, veto.value if cr else None, veto_name.value if cr else "", cr)


def bitlocker_raw(volume_path: str) -> Optional[int]:
    """Сырое System.Volume.BitLockerProtection для тома с буквой; None при недоступности."""
    drive_path = next((p for p in volume_access_points(volume_path) if len(p) >= 2 and p[1] == ":"), None)
    if not drive_path:
        return None
    # Вызовы IPropertyStore выполняются через vtable; возвращаем VT_UI4/VT_I4.
    ole = _dll("ole32", last_error=False)
    props = _dll("propsys", last_error=False)
    coinit = _fn(ole, "CoInitializeEx", [HANDLE, DWORD], ctypes.c_long)
    couninit = _fn(ole, "CoUninitialize", [], None)
    shget = _fn(_dll("shell32", last_error=False), "SHGetPropertyStoreFromParsingName",
        [wintypes.LPCWSTR, HANDLE, DWORD, ctypes.POINTER(GUID), ctypes.POINTER(HANDLE)], ctypes.c_long)
    pskey = _fn(props, "PSGetPropertyKeyFromName", [wintypes.LPCWSTR, ctypes.POINTER(GUID)], ctypes.c_long)
    pvclear = _fn(props, "PropVariantClear", [ctypes.POINTER(PROPVARIANT)], ctypes.c_long)
    store = HANDLE()
    key = GUID()
    hr = coinit(None, 2)
    if hr < 0:
        return None
    try:
        if pskey("System.Volume.BitLockerProtection", ctypes.byref(key)) < 0:
            return None
        if shget(drive_path, None, 0, ctypes.byref(IID_IPROPERTY_STORE), ctypes.byref(store)) < 0:
            return None
        vtable = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(HANDLE))).contents
        getvalue = ctypes.WINFUNCTYPE(ctypes.c_long, HANDLE, ctypes.POINTER(GUID), ctypes.POINTER(PROPVARIANT))(vtable[5])
        value = PROPVARIANT()
        if getvalue(store, ctypes.byref(key), ctypes.byref(value)) < 0:
            return None
        try:
            if value.vt == 19:
                return int(value.value.uint64 & 0xffffffff)  # VT_UI4
            if value.vt == 3:
                return ctypes.c_int32(value.value.uint64 & 0xffffffff).value  # VT_I4
            return None
        finally:
            pvclear(ctypes.byref(value))
    finally:
        if store:
            vtable = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(HANDLE))).contents
            release = ctypes.WINFUNCTYPE(ctypes.c_ulong, HANDLE)(vtable[2])
            release(store)
        couninit()


IID_IPROPERTY_STORE = GUID.from_uuid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")
BITLOCKER_VALUES = {0: "unknown", 1: "unlocked", 2: "none", 3: "unlocked", 4: "unlocked",
                    5: "unlocked", 6: "locked", 7: "none", 8: "unlocked"}


def interpret_bitlocker(value: Optional[int]) -> str:
    return BITLOCKER_VALUES.get(value, "unknown")


def register_volume_notifications(hwnd, volume_path: str) -> tuple:
    k = _api()
    create = _fn(k, "CreateFileW", [wintypes.LPCWSTR, DWORD, DWORD, HANDLE, DWORD, DWORD, HANDLE], HANDLE)
    close = _fn(k, "CloseHandle", [HANDLE], BOOL)
    reg = _fn(_dll("user32"), "RegisterDeviceNotificationW", [HANDLE, HANDLE, DWORD], HANDLE)
    path = "\\\\.\\" + volume_path[4:].rstrip("\\")
    h = create(path, 0, 0x1 | 0x2, None, 3, 0, None)
    if h == INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), "CreateFileW failed")
    class FILTER(ctypes.Structure):
        _fields_ = [("size", DWORD), ("devicetype", DWORD), ("reserved", DWORD), ("handle", HANDLE),
                    ("hdevnotify", HANDLE), ("eventguid", GUID), ("nameoffset", DWORD), ("data", ctypes.c_ubyte * 1)]
    notifications = []
    try:
        for event_guid in (GUID_IO_VOLUME_UNLOCK, GUID_IO_VOLUME_LOCK, GUID_IO_VOLUME_MOUNT, GUID_IO_VOLUME_DISMOUNT):
            f = FILTER()
            f.size = ctypes.sizeof(FILTER)
            f.devicetype = DBT_DEVTYP_HANDLE
            f.handle = h
            f.eventguid = event_guid
            notification = reg(hwnd, ctypes.byref(f), 0)
            if not notification:
                raise OSError(ctypes.get_last_error(), "RegisterDeviceNotificationW failed")
            notifications.append(notification)
        return (tuple(notifications), h)
    except Exception:
        unreg = _fn(_dll("user32"), "UnregisterDeviceNotification", [HANDLE], BOOL)
        for notification in notifications:
            unreg(notification)
        close(h)
        raise


def unregister_device_notification(notification) -> None:
    fn = _fn(_dll("user32"), "UnregisterDeviceNotification", [HANDLE], BOOL)
    notifications, handle = notification if isinstance(notification, tuple) and len(notification) == 2 else ((notification,), None)
    error = None
    for item in notifications:
        if not fn(item):
            error = ctypes.get_last_error()
    if handle:
        _fn(_api(), "CloseHandle", [HANDLE], BOOL)(handle)
    if error is not None:
        raise OSError(error, "UnregisterDeviceNotification failed")


ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001


def inhibit_sleep(enable: bool = True) -> int:
    """Запретить/разрешить сон; включение и снятие вызываются в одном потоке."""
    fn = _fn(_api(), "SetThreadExecutionState", [DWORD], DWORD)
    result = fn(ES_CONTINUOUS | ES_SYSTEM_REQUIRED if enable else ES_CONTINUOUS)
    if not result:
        raise OSError(ctypes.get_last_error(), "SetThreadExecutionState failed")
    return result


def flush_file(fd: int) -> None:
    """os.fsync на Windows вызывает _commit CRT, транзитивно FlushFileBuffers."""
    os.fsync(fd)


def lock_profile(profile_id: str, directory: Optional[str] = None) -> Optional[int]:
    _require_windows()
    import msvcrt
    base = directory or os.path.join(local_app_data(), "run")
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, profile_id + ".lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return fd
    except Exception:
        os.close(fd)
        return None


def unlock_profile(fd: int) -> None:
    _require_windows()
    import msvcrt
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(fd)


def startup_get(name: str = "PlugBackup") -> Optional[str]:
    _require_windows()
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            return winreg.QueryValueEx(key, name)[0]
    except FileNotFoundError:
        return None


def startup_set(command: str, name: str = "PlugBackup") -> None:
    _require_windows()
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)


def startup_command(executable_path: str) -> str:
    """Команда автозапуска с корректным quoting пути и флагом скрытого старта."""
    import subprocess
    return subprocess.list2cmdline([executable_path, "--hidden"])


def startup_delete(name: str = "PlugBackup") -> None:
    _require_windows()
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass


def _cred_api():
    adv = _dll("advapi32")
    return (_fn(adv, "CredWriteW", [HANDLE, DWORD], BOOL), _fn(adv, "CredReadW", [wintypes.LPCWSTR, DWORD, DWORD, ctypes.POINTER(HANDLE)], BOOL),
            _fn(adv, "CredDeleteW", [wintypes.LPCWSTR, DWORD, DWORD], BOOL), _fn(adv, "CredFree", [HANDLE], None))


class CREDENTIAL(ctypes.Structure):
    _fields_ = [("Flags", DWORD), ("Type", DWORD), ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                ("LastWritten", ctypes.c_int64), ("CredentialBlobSize", DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", DWORD), ("AttributeCount", DWORD), ("Attributes", HANDLE), ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR)]


def credential_write(profile_id: str, password: str) -> None:
    write, _, _, _ = _cred_api()
    encoded = bytearray(password.encode("utf-16-le"))
    blob = (ctypes.c_ubyte * len(encoded)).from_buffer(encoded)
    cred = CREDENTIAL()
    cred.Type = 1
    cred.TargetName = "PlugBackup/" + profile_id
    cred.CredentialBlobSize = len(encoded)
    cred.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    cred.Persist = 2
    cred.UserName = "PlugBackup"
    try:
        if not write(ctypes.byref(cred), 0):
            raise OSError(ctypes.get_last_error(), "CredWriteW failed")
    finally:
        for i in range(len(encoded)):
            encoded[i] = 0


def credential_read(profile_id: str) -> Optional[str]:
    _, read, _, free = _cred_api()
    ptr = HANDLE()
    if not read("PlugBackup/" + profile_id, 1, 0, ctypes.byref(ptr)):
        err = ctypes.get_last_error()
        if err == 1168:
            return None
        raise OSError(err, "CredReadW failed")
    try:
        cred = ctypes.cast(ptr, ctypes.POINTER(CREDENTIAL)).contents
        raw = bytearray(ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize))
        try:
            return bytes(raw).decode("utf-16-le")
        finally:
            for i in range(len(raw)):
                raw[i] = 0
    finally:
        free(ptr)


def credential_delete(profile_id: str) -> None:
    _, _, delete, _ = _cred_api()
    if not delete("PlugBackup/" + profile_id, 1, 0):
        err = ctypes.get_last_error()
        if err != 1168:
            raise OSError(err, "CredDeleteW failed")


FOLDERID_RoamingAppData = GUID.from_uuid("3eb685db-65f9-4cf6-a03a-e3ef65729f3d")
FOLDERID_LocalAppData = GUID.from_uuid("f1b32785-6fba-4fcf-9d55-7b8e7f157091")


def known_folder(folder_id: GUID, fallback: str) -> str:
    if sys.platform == "win32":
        shell = _dll("shell32")
        fn = _fn(shell, "SHGetKnownFolderPath", [ctypes.POINTER(GUID), DWORD, HANDLE, ctypes.POINTER(wintypes.LPWSTR)], ctypes.c_long)
        ptr = wintypes.LPWSTR()
        if fn(ctypes.byref(folder_id), 0, None, ctypes.byref(ptr)) >= 0:
            try:
                return ptr.value
            finally:
                _fn(_dll("ole32", last_error=False), "CoTaskMemFree", [HANDLE], None)(ptr)
    return os.environ.get(fallback) or os.path.expanduser("~")


def roaming_app_data() -> str:
    return os.path.join(known_folder(FOLDERID_RoamingAppData, "APPDATA"), "PlugBackup")


def local_app_data() -> str:
    return os.path.join(known_folder(FOLDERID_LocalAppData, "LOCALAPPDATA"), "PlugBackup")
