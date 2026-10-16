import json
import os
import tempfile
import unittest
from dataclasses import replace

from plugbackup import plat
mounts = plat.mounts
from plugbackup.core import config, fsinfo, storage
from plugbackup.core.volume import Volume, uuid_conflicts

MOUNTINFO_SAMPLE = """\
26 1 259:5 / / rw,relatime shared:1 - ext4 /dev/nvme0n1p5 rw,errors=remount-ro
30 26 259:1 / /boot/efi rw,relatime shared:6 - vfat /dev/nvme0n1p1 rw,fmask=0077
512 26 7:0 / /media/user/MY\\040DISK rw,nosuid,nodev,relatime shared:300 - vfat /dev/loop0 rw,uid=1000
513 26 7:1 /sub /mnt/bind rw,relatime shared:301 - ext4 /dev/loop1 rw
514 26 7:2 / /media/user/RO ro,relatime shared:302 - exfat /dev/loop2 ro
"""


class MountinfoTest(unittest.TestCase):
    def setUp(self):
        self.entries = mounts.parse_mountinfo(MOUNTINFO_SAMPLE)

    def test_parses_all_lines(self):
        self.assertEqual(len(self.entries), 5)
        root = self.entries[0]
        self.assertEqual((root.mount_point, root.fs_type, root.source, root.dev), ("/", "ext4", "/dev/nvme0n1p5", (259, 5)))

    def test_unescapes_space_in_mount_point(self):
        self.assertEqual(self.entries[2].mount_point, "/media/user/MY DISK")
        self.assertIs(mounts.find_mount("/media/user/MY DISK", self.entries), self.entries[2])

    def test_bind_root_and_read_only_are_kept(self):
        self.assertEqual(self.entries[3].root, "/sub")
        self.assertEqual(self.entries[4].options.split(",")[0], "ro")

    def test_find_mount_takes_topmost_entry(self):
        stacked = MOUNTINFO_SAMPLE + "600 512 7:3 / /media/user/MY\\040DISK rw - ext4 /dev/loop3 rw\n"
        entry = mounts.find_mount("/media/user/MY DISK/", mounts.parse_mountinfo(stacked))
        self.assertEqual(entry.source, "/dev/loop3")

    def test_unescape_only_octal_triplets(self):
        self.assertEqual(mounts._unescape("a\\134b\\0x"), "a\\b\\0x")

    def test_verify_rejects_bad_uuid_and_unmounted_path(self):
        self.assertEqual(mounts.verify_mount("/", "").reason, "invalid-uuid")
        self.assertEqual(mounts.verify_mount("/", "../etc").reason, "invalid-uuid")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(mounts.verify_mount(tmp, "ABCD-1234").reason, "not-mounted")

    def test_parent_writable(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(mounts.parent_is_user_writable(os.path.join(tmp, "drive")))


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "sub", "config.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_is_none(self):
        self.assertIsNone(storage.load_json(self.path))

    def test_save_creates_backup_of_previous_version(self):
        storage.save_json(self.path, {"a": 1})
        storage.save_json(self.path, {"a": 2})
        self.assertEqual(storage.load_json(self.path), {"a": 2})
        self.assertEqual(storage.load_json(self.path + ".bak"), {"a": 1})
        leftovers = [n for n in os.listdir(os.path.dirname(self.path)) if n.startswith(".tmp-")]
        self.assertEqual(leftovers, [])

    def test_damaged_file_raises_and_is_not_overwritten_by_store(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            f.write("{broken")
        store = config.ConfigStore(self.path)
        self.assertIsNotNone(store.error)
        store.config.show_all_disks = True
        self.assertFalse(store.save())
        with open(self.path) as f:
            self.assertEqual(f.read(), "{broken")

    def test_restore_backup(self):
        storage.save_json(self.path, {"show_all_disks": True})
        storage.save_json(self.path, {"show_all_disks": False})
        with open(self.path, "w") as f:
            f.write("not json")
        self.assertTrue(storage.restore_backup(self.path))
        self.assertEqual(storage.load_json(self.path), {"show_all_disks": True})
        self.assertTrue(os.path.exists(self.path + ".broken"))

    def test_config_round_trip_with_schema(self):
        store = config.ConfigStore(self.path)
        store.config.show_all_disks = True
        self.assertTrue(store.save())
        with open(self.path) as f:
            data = json.load(f)
            self.assertEqual((data["schema"], data["show_all_disks"]), (1, True))
            self.assertIn("*.tmp", data["global_excludes"])
        self.assertTrue(config.ConfigStore(self.path).config.show_all_disks)

    def test_config_dir_follows_xdg(self):
        old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.tmp.name
        try:
            self.assertEqual(config.config_path(), os.path.join(self.tmp.name, "plugbackup", "config.json"))
        finally:
            if old is None:
                del os.environ["XDG_CONFIG_HOME"]
            else:
                os.environ["XDG_CONFIG_HOME"] = old


class FsInfoTest(unittest.TestCase):
    def test_display_names(self):
        self.assertEqual(fsinfo.display_name("vfat", "FAT32"), "FAT32")
        self.assertEqual(fsinfo.display_name("exfat"), "exFAT")
        self.assertEqual(fsinfo.display_name("ntfs"), "NTFS")

    def test_limitations(self):
        self.assertEqual(len(fsinfo.limitations("vfat")), 2)
        self.assertEqual(fsinfo.limitations("ext4"), [])
        self.assertEqual(len(fsinfo.limitations("btrfs")), 1)
        self.assertEqual(fsinfo.FAT32_MAX_FILE, 4294967295)


def _volume(path, uuid):
    return Volume(
        object_path=path, device="/dev/x", uuid=uuid, inner_uuid="", fs_type="vfat", fs_version="FAT32",
        label="", size=1, mount_points=(), encrypted=False, locked=False, filesystem_path=path,
        drive_path="", drive_vendor="", drive_model="", drive_serial="", drive_id="", connection_bus="usb",
        removable=True, sibling_id="", is_loop=False, loop_backing_file="",
    )


class VolumeTest(unittest.TestCase):
    def test_uuid_conflicts(self):
        vols = [_volume("/a", "AAAA-0001"), _volume("/b", "AAAA-0001"), _volume("/c", "BBBB-0002"), _volume("/d", "")]
        self.assertEqual(uuid_conflicts(vols), {"AAAA-0001"})

    def test_display_name_fallbacks(self):
        v = _volume("/a", "X")
        self.assertEqual(replace(v, label="WORK").display_name, "WORK")
        self.assertEqual(replace(v, drive_vendor="Kingston", drive_model="DT").display_name, "Kingston DT")
        self.assertEqual(replace(v, is_loop=True, loop_backing_file="/tmp/a.img").display_name, "a.img")
        self.assertEqual(v.display_name, "x")

    def test_fs_uuid_for_luks_uses_inner(self):
        v = replace(_volume("/a", "LUKS-UUID"), encrypted=True, inner_uuid="INNER")
        self.assertEqual(v.fs_uuid, "INNER")


if __name__ == "__main__":
    unittest.main()
