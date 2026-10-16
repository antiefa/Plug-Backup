from __future__ import annotations

import ctypes
import struct
import sys
import unittest
import uuid
from unittest import mock

from plugbackup.plat.windows import win32 as w


class ParserTests(unittest.TestCase):
    def test_descriptor_offsets_strings_and_missing(self):
        b = bytearray(128)
        struct.pack_into("<II4B4I2I", b, 0, 128, 128, 0, 0, 1, 1, 36, 42, 0, 49, 7, 0)
        b[36:42] = b"ACME\0\0"
        b[42:49] = b"Disk X\0"
        b[49:57] = b"SN-123\0\0"
        got = w.parse_storage_descriptor(b)
        self.assertEqual((got["vendor"], got["product"], got["serial"], got["bus_type"]), ("ACME", "Disk X", "SN-123", 7))
        struct.pack_into("<4I", b, 12, 0, 0, 0, 0)
        got = w.parse_storage_descriptor(b)
        self.assertEqual((got["vendor"], got["product"], got["serial"]), ("", "", ""))

    def test_volume_mask(self):
        self.assertEqual(w.parse_volume_mask((1 << 0) | (1 << 3) | (1 << 25)), ("A", "D", "Z"))

    def test_volume_broadcast_struct(self):
        x = w.DEV_BROADCAST_VOLUME()
        x.dbch_size = ctypes.sizeof(x)
        x.dbch_devicetype = w.DBT_DEVTYP_VOLUME
        x.dbcv_unitmask = 5
        self.assertEqual(w.parse_device_broadcast(bytes(x)), {"type": 2, "letters": ("A", "C"), "flags": 0})

    def test_handle_event_guid(self):
        x = w.DEV_BROADCAST_HANDLE()
        x.dbch_size = ctypes.sizeof(x)
        x.dbch_devicetype = w.DBT_DEVTYP_HANDLE
        ctypes.memmove(ctypes.addressof(x) + w.DEV_BROADCAST_HANDLE.dbch_eventguid.offset,
                       bytes(w.GUID_IO_VOLUME_UNLOCK), ctypes.sizeof(w.GUID))
        got = w.parse_device_broadcast(bytes(x))
        self.assertEqual(got["event"], "unlock")

    def test_multisz_utf16(self):
        self.assertEqual(w.parse_multisz("E:\\\x00C:\\mount\x00\x00".encode("utf-16-le"), 2), ("E:\\", "C:\\mount"))

    def test_ntfs_buffer_layout(self):
        x = w.NTFS_VOLUME_DATA_BUFFER()
        x.VolumeSerialNumber = 0x123456789
        self.assertEqual(ctypes.sizeof(x), 96)
        self.assertEqual(w.NTFS_VOLUME_DATA_BUFFER.BytesPerSector.offset, 40)
        raw = bytes(x)
        self.assertEqual(struct.unpack_from("<q", raw, 0)[0], 0x123456789)

    def test_sizes_x64(self):
        self.assertEqual(ctypes.sizeof(w.PROPVARIANT), 24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16)
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual(ctypes.sizeof(w.GUID), 16)
            self.assertEqual(ctypes.sizeof(w.DEV_BROADCAST_HDR), 12)
            self.assertEqual(ctypes.sizeof(w.DEV_BROADCAST_VOLUME), 20)
            self.assertEqual(w.DEV_BROADCAST_HANDLE.dbch_eventguid.offset, 32)
            self.assertEqual(ctypes.sizeof(w.DEV_BROADCAST_HANDLE), 56)
            self.assertEqual(ctypes.sizeof(w.STORAGE_PROPERTY_QUERY), 12)
            self.assertEqual(ctypes.sizeof(w.STORAGE_DEVICE_DESCRIPTOR), 40)
            self.assertEqual(w.STORAGE_DEVICE_DESCRIPTOR.VendorIdOffset.offset, 12)
            self.assertEqual(w.STORAGE_DEVICE_DESCRIPTOR.BusType.offset, 28)
            self.assertEqual(w.STORAGE_DEVICE_DESCRIPTOR.RawDeviceProperties.offset, 36)

    def test_paths_and_serials(self):
        self.assertEqual(w.to_extended("C:\\a"), "\\\\?\\C:\\a")
        self.assertEqual(w.to_extended("C:/a"), "\\\\?\\C:\\a")
        self.assertEqual(w.to_extended("\\\\?\\C:\\a"), "\\\\?\\C:\\a")
        self.assertEqual(w.to_extended("\\\\?\\Volume{abc}\\a"), "\\\\?\\Volume{abc}\\a")
        self.assertEqual(w.to_extended("\\\\?\\UNC\\s\\x"), "\\\\?\\UNC\\s\\x")
        self.assertEqual(w.to_extended("\\\\.\\PhysicalDrive0"), "\\\\.\\PhysicalDrive0")
        self.assertEqual(w.to_extended("\\\\server\\share\\a"), "\\\\?\\UNC\\server\\share\\a")
        self.assertEqual(w.to_extended("\\\\s\\x"), "\\\\?\\UNC\\s\\x")
        self.assertEqual(w.from_extended("\\\\?\\UNC\\server\\share\\a"), "\\\\server\\share\\a")
        vol = "\\\\?\\Volume{abc}\\"
        self.assertEqual(w.to_extended(vol), vol)
        self.assertEqual(w.from_extended("\\\\?\\C:\\a"), "C:\\a")
        with mock.patch.object(w.os.path, "abspath", return_value="C:\\cwd\\relative"):
            self.assertEqual(w.to_extended("relative"), "\\\\?\\C:\\cwd\\relative")
        self.assertEqual(w.format_serial32(0x5A3C91F2), "5A3C-91F2")
        self.assertEqual(w.format_serial64(0x123456789ABCDEF0), "123456789ABCDEF0")

    def test_bitlocker_values(self):
        self.assertEqual(w.interpret_bitlocker(2), "none")
        self.assertEqual(w.interpret_bitlocker(3), "unlocked")
        self.assertEqual(w.interpret_bitlocker(6), "locked")
        self.assertEqual(w.interpret_bitlocker(999), "unknown")

    def test_import_and_non_windows_call(self):
        if sys.platform != "win32":
            with self.assertRaisesRegex(w.Win32Unavailable, "только в Windows"):
                w.enumerate_volume_paths()


if __name__ == "__main__":
    unittest.main()
