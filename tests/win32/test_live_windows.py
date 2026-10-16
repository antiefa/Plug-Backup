from __future__ import annotations

import os
import sys
import tempfile
import unittest
import uuid

from plugbackup.plat.windows import win32 as w
from plugbackup.plat.windows import devices, mounts, system


@unittest.skipUnless(sys.platform == "win32", "Требуется Windows")
class LiveWindowsTests(unittest.TestCase):
    def test_live_platform_volume_paths_and_mount_verification(self):
        volumes = devices.list_volumes(show_all=True)
        self.assertTrue(any(volume.access_paths for volume in volumes))
        system_path = os.environ.get("SystemDrive", "C:") + "\\"
        info = next(v for v in w.enumerate_volumes() if any(p.casefold() == system_path.casefold()
                                                             for p in v.access_points))
        if info.filesystem.upper() == "NTFS":
            serial = w.ntfs_volume_serial(info.path)
            expected = w.format_serial64(serial) if serial is not None else "{:08X}".format(info.serial32)
        else:
            expected = w.format_serial32(info.serial32)
        self.assertTrue(mounts.verify_mount(info.path, expected, writable=False).ok)

    def test_volume_path_realpath_comparison(self):
        volume = next(v for v in devices.list_volumes(show_all=True) if v.mount_points)
        child = os.path.join(os.path.realpath(volume.mount_point), "x")
        expected = os.path.join(os.path.realpath(volume.mount_point), "x")
        self.assertEqual(os.path.realpath(os.path.join(volume.mount_point, "x")), child)
        self.assertEqual(child, expected)

    def test_platform_sleep_token_and_profile_lock(self):
        token = system.inhibit_sleep("backup test")
        self.addCleanup(system.release_inhibit, token)
        system.release_inhibit(token)
        first = system.ProfileLock("live-test")
        second = system.ProfileLock("live-test")
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
        finally:
            first.release()
            second.release()

    def test_volumes_system_info_and_free_space(self):
        volumes = w.enumerate_volumes()
        self.assertTrue(volumes)
        system = os.environ.get("SystemDrive", "C:") + "\\"
        item = next(v for v in volumes if any(p.casefold() == system.casefold() for p in v.access_points))
        self.assertIsNone(item.error)
        self.assertTrue(item.filesystem)
        self.assertIsNotNone(item.free_bytes)

    def test_sleep_toggle(self):
        self.assertTrue(w.inhibit_sleep(True))
        try:
            self.assertTrue(w.inhibit_sleep(False))
        finally:
            w.inhibit_sleep(False)

    def test_startup_temp_value(self):
        name = "PlugBackupTest_" + uuid.uuid4().hex
        self.addCleanup(w.startup_delete, name)
        try:
            w.startup_set('"C:\\PlugBackupTest.exe" --hidden', name)
            self.assertEqual(w.startup_get(name), '"C:\\PlugBackupTest.exe" --hidden')
        finally:
            w.startup_delete(name)
        self.assertIsNone(w.startup_get(name))

    def test_credential_roundtrip(self):
        profile = "test-" + uuid.uuid4().hex
        self.addCleanup(w.credential_delete, profile)
        try:
            w.credential_write(profile, "пароль-test")
            self.assertEqual(w.credential_read(profile), "пароль-test")
        finally:
            w.credential_delete(profile)
        self.assertIsNone(w.credential_read(profile))

    def test_lock_temp_dir(self):
        with tempfile.TemporaryDirectory() as folder:
            one = w.lock_profile("test", folder)
            self.assertIsNotNone(one)
            three = None
            try:
                two = w.lock_profile("test", folder)
                self.assertIsNone(two)
                w.unlock_profile(one)
                one = None
                three = w.lock_profile("test", folder)
                self.assertIsNotNone(three)
                w.unlock_profile(three)
                three = None
            finally:
                if one is not None:
                    w.unlock_profile(one)
                if three is not None:
                    w.unlock_profile(three)

    def test_known_folder_and_bitlocker(self):
        self.assertTrue(w.roaming_app_data())
        self.assertTrue(w.local_app_data())
        system = os.environ.get("SystemDrive", "C:") + "\\"
        vol = next(v.path for v in w.enumerate_volumes() if any(p.casefold() == system.casefold() for p in v.access_points))
        w.bitlocker_raw(vol)


if __name__ == "__main__":
    unittest.main()
