from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from plugbackup.core.volume import Volume
from plugbackup.plat.windows import autostart, deps, devices, mounts, paths, secrets, system, win32


def _volume(path="\\\\?\\Volume{A}\\", uuid="1234-5678"):
    return Volume(path, "device", uuid, "", "vfat", "FAT32", "USB", 100,
                  (path,), False, False, "", "", "", "", "", "", "usb", True, "", False, "")


class WindowsPlatformTests(unittest.TestCase):
    def setUp(self):
        devices._VOLUME_CACHE.clear()

    def test_paths_delegate_to_known_folders(self):
        with patch.object(paths.win32, "roaming_app_data", return_value="R"), \
                patch.object(paths.win32, "local_app_data", return_value="L"):
            self.assertEqual(paths.config_dir(), "R")
            self.assertEqual(paths.state_dir(), "L/state")
            self.assertEqual(paths.runtime_dir(), "L/run")
            self.assertEqual(paths.restore_backup_root(), "L/restore-backup")

    def test_volume_filter_and_fields(self):
        removable = win32.VolumeInfo("v1", ("E:",), win32.DRIVE_REMOVABLE, "Data", "FAT32", 0x12345678,
                                     255, 0, 100, 50)
        internal = win32.VolumeInfo("v2", ("C:",), win32.DRIVE_FIXED, "System", "NTFS", 1, 255, 0, 200, 100)
        network = win32.VolumeInfo("v3", ("Z:",), win32.DRIVE_REMOTE, "Net", "NTFS", 1, 255, 0, 200, 100)
        storages = {"v1": win32.StorageInfo(7, True, "Vendor", "Model", "Serial", 2),
                    "v2": win32.StorageInfo(3, False, disk_number=0), "v3": win32.StorageInfo(11)}
        with patch.object(devices.win32, "system_volume_paths", return_value=frozenset({"v2"})), \
                patch.object(devices.win32, "enumerate_volumes", return_value=(removable, internal, network)), \
                patch.object(devices.win32, "query_storage", side_effect=lambda p: storages[p]), \
                patch.object(devices.win32, "ntfs_volume_serial", return_value=None), \
                patch.object(devices.win32, "bitlocker_raw", return_value=None):
            result = devices.list_volumes(False)
        self.assertEqual(len(result), 1)
        item = result[0]
        self.assertEqual((item.uuid, item.fs_type, item.fs_version), ("1234-5678", "vfat", "FAT32"))
        self.assertEqual(item.mount_points, ("v1",))
        self.assertEqual(item.access_paths, ("E:\\",))
        self.assertEqual(item.drive_path, "\\\\.\\PhysicalDrive2")

    def test_partial_ntfs_and_unknown_bus(self):
        info = win32.VolumeInfo("v", (), win32.DRIVE_FIXED, "", "NTFS", 0x1234, 255, 0, 1, 1)
        with patch.object(devices.win32, "system_volume_paths", return_value=frozenset()), \
                patch.object(devices.win32, "enumerate_volumes", return_value=(info,)), \
                patch.object(devices.win32, "query_storage", return_value=win32.StorageInfo(error=5)), \
                patch.object(devices.win32, "ntfs_volume_serial", return_value=None), \
                patch.object(devices.win32, "bitlocker_raw", return_value=None):
            item = devices.list_volumes(False)[0]
        self.assertEqual(item.uuid, "00001234")
        self.assertTrue(item.uuid_partial)
        self.assertTrue(item.bus_unknown)

    def test_volume_information_errors_do_not_create_zero_uuid(self):
        not_ready = win32.VolumeInfo("empty", (), win32.DRIVE_REMOVABLE, "", "", 0, 0, 0, None, None,
                                     win32.ERROR_NOT_READY)
        locked = win32.VolumeInfo("locked", (), win32.DRIVE_REMOVABLE, "", "", 0, 0, 0, None, None,
                                  win32.FVE_E_LOCKED_VOLUME)
        unreadable = win32.VolumeInfo("bad", (), win32.DRIVE_REMOVABLE, "", "", 0, 0, 0, None, None,
                                      win32.ERROR_UNRECOGNIZED_VOLUME)
        with patch.object(devices.win32, "system_volume_paths", return_value=frozenset()), \
                patch.object(devices.win32, "enumerate_volumes", return_value=(not_ready, locked, unreadable)), \
                patch.object(devices.win32, "query_storage", return_value=win32.StorageInfo(7, True)), \
                patch.object(devices.win32, "bitlocker_raw", side_effect=(6, None)) as bitlocker:
            result = devices.list_volumes(False)
        by_path = {volume.object_path: volume for volume in result}
        self.assertEqual(set(by_path), {"locked", "bad"})
        self.assertEqual((by_path["locked"].uuid, by_path["locked"].fs_type,
                          by_path["locked"].encrypted, by_path["locked"].locked),
                         ("", "", True, True))
        self.assertEqual((by_path["bad"].uuid, by_path["bad"].fs_type,
                          by_path["bad"].encrypted, by_path["bad"].locked),
                         ("", "", False, False))
        self.assertEqual(bitlocker.call_count, 2)

    def test_volume_metadata_cache_skips_repeated_storage_and_bitlocker_queries(self):
        locked = win32.VolumeInfo("locked-cache", (), win32.DRIVE_REMOVABLE, "", "", 0, 0, 0, None, None,
                                  win32.ERROR_UNRECOGNIZED_VOLUME)
        with patch.object(devices.win32, "system_volume_paths", return_value=frozenset()), \
                patch.object(devices.win32, "enumerate_volumes", return_value=(locked,)), \
                patch.object(devices.win32, "query_storage", return_value=win32.StorageInfo(7, True)) as storage, \
                patch.object(devices.win32, "bitlocker_raw", return_value=6) as bitlocker:
            first = devices.list_volumes(False)
            second = devices.list_volumes(False)
        self.assertEqual(first, second)
        storage.assert_called_once_with("locked-cache")
        bitlocker.assert_called_once_with("locked-cache")

    def test_windows_version_requirement(self):
        Version = type("Version", (), {})
        def version(major, minor, service_pack_major=0):
            item = Version()
            item.major = major
            item.minor = minor
            item.service_pack_major = service_pack_major
            return item
        self.assertFalse(deps._windows_supported(version(6, 1, 0)))
        self.assertTrue(deps._windows_supported(version(6, 1, 1)))
        self.assertTrue(deps._windows_supported(version(6, 2)))
        self.assertTrue(deps._windows_supported(version(6, 3)))
        self.assertTrue(deps._windows_supported(version(10, 0)))

    def test_fixed_usb_is_shown_but_internal_disk_is_hidden(self):
        usb = win32.VolumeInfo("usb", (), win32.DRIVE_FIXED, "External", "NTFS", 0x1234, 255, 0, 1, 1)
        sata = win32.VolumeInfo("sata", (), win32.DRIVE_FIXED, "Internal", "NTFS", 0x5678, 255, 0, 1, 1)
        storage = {"usb": win32.StorageInfo(7, False, disk_number=1),
                   "sata": win32.StorageInfo(11, False, disk_number=0)}
        with patch.object(devices.win32, "system_volume_paths", return_value=frozenset()), \
                patch.object(devices.win32, "enumerate_volumes", return_value=(usb, sata)), \
                patch.object(devices.win32, "query_storage", side_effect=lambda p: storage[p]), \
                patch.object(devices.win32, "ntfs_volume_serial", return_value=0x1234567800001234), \
                patch.object(devices.win32, "bitlocker_raw", return_value=None):
            result = devices.list_volumes(False)
        self.assertEqual([v.object_path for v in result], ["usb"])
        self.assertEqual(result[0].connection_bus, "usb")

    def test_mount_check_reason_codes(self):
        info = win32.VolumeInfo("v", (), win32.DRIVE_REMOVABLE, "", "FAT32", 0x12345678, 0, 0, 1, 1)
        with patch.object(mounts.win32, "query_volume", side_effect=OSError("missing")):
            self.assertEqual(mounts.verify_mount("v", "1234-5678").reason, "not-mounted")
        with patch.object(mounts.win32, "query_volume", return_value=info), \
                patch("plugbackup.plat.windows.mounts.os.access", return_value=True):
            self.assertTrue(mounts.verify_mount("v", "1234-5678").ok)
            self.assertEqual(mounts.verify_mount("v", "0000-0000").reason, "uuid-mismatch")
        readonly = win32.VolumeInfo("v", (), win32.DRIVE_REMOVABLE, "", "FAT32", 0x12345678, 0,
                                    win32.FILE_READ_ONLY_VOLUME, 1, 1)
        with patch.object(mounts.win32, "query_volume", return_value=readonly):
            self.assertEqual(mounts.verify_mount("v", "1234-5678").reason, "read-only")
        with patch.object(mounts.win32, "query_volume", return_value=info), \
                patch("plugbackup.plat.windows.mounts.os.access", return_value=False):
            self.assertEqual(mounts.verify_mount("v", "1234-5678").reason, "not-writable")

    def test_inhibit_enable_and_release_use_same_thread(self):
        calls = []
        with patch.object(system.win32, "inhibit_sleep", side_effect=lambda enabled: calls.append((enabled, threading.get_ident()))):
            token = system.inhibit_sleep("backup")
            self.assertTrue(token.thread.is_alive() or calls)
            system.release_inhibit(token)
        self.assertEqual([x[0] for x in calls], [True, False])
        self.assertEqual(calls[0][1], calls[1][1])

    def test_monitor_notifies_and_stops(self):
        old = _volume(uuid="old")
        new = _volume(uuid="new")
        with patch.object(devices, "list_volumes", side_effect=([old], [new], [new])):
            monitor = devices.DeviceMonitor()
            events = threading.Event()
            seen = []
            monitor.subscribe(lambda value: (seen.append(value.volumes()[0].uuid), events.set()))
            monitor.notify_changed()
            self.assertTrue(events.wait(2))
            monitor.close()
            self.assertFalse(monitor._thread.is_alive())
        self.assertEqual(seen, ["new"])

    def test_autostart_and_credentials(self):
        with patch.object(autostart.win32, "startup_get", return_value=autostart._command()) as get, \
                patch.object(autostart.win32, "startup_set") as set_value:
            self.assertTrue(autostart.is_enabled())
            autostart.set_enabled(True)
            self.assertEqual(get.call_count, 1)
            self.assertIn("--hidden", set_value.call_args.args[0])
        with patch.object(secrets.win32, "credential_read", return_value="secret"):
            self.assertEqual(secrets.lookup("p"), "secret")
        with patch.object(secrets.win32, "credential_write") as write:
            self.assertTrue(secrets.store("p", "label", "secret"))
            write.assert_called_once_with("p", "secret")


if __name__ == "__main__":
    unittest.main()
