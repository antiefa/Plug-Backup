from __future__ import annotations

import os
import tempfile
import unittest

from plugbackup.core import marker
from plugbackup.core.auto import CONFLICT, RUN, decide
from plugbackup.core.profiles import AutoSettings, DeviceBinding, Job, Profile, ProfilesStore, profile_for_volume
from plugbackup.core.volume import Volume, uuid_matches


def _volume(root, partial=True):
    return Volume("v", "d", "12345678", "", "ntfs", "", "", 1, (root,), False, False,
                  "", "", "", "", "", "", "usb", True, "", False, "", uuid_partial=partial)


def _volume_with_uuid(value):
    return Volume("v", "d", value, "", "ntfs", "", "", 1, ("/missing",), False, False,
                  "", "", "", "", "", "", "usb", True, "", False, "")


class PartialUuidTests(unittest.TestCase):
    def test_partial_ntfs_requires_matching_marker_for_profile_lookup(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Profile(id="profile", device=DeviceBinding(uuid="ABCDEF0012345678"), root="Backup")
            store = ProfilesStore(os.path.join(folder, "profiles.json"))
            store.profiles = [profile]
            volume = _volume(folder)
            self.assertTrue(uuid_matches(volume, profile.device.uuid))
            self.assertEqual(store.find_by_volume(volume), [])
            self.assertIsNone(profile_for_volume([profile], volume))
            profile.auto = AutoSettings(enabled=True)
            profile.jobs = [Job(id="job")]
            self.assertEqual(decide(profile, volume, set(), 0, None, None).reason, CONFLICT)
            os.makedirs(os.path.join(folder, "Backup"), exist_ok=True)
            marker.write_marker(os.path.join(folder, "Backup"), profile)
            self.assertEqual(store.find_by_volume(volume), [profile])
            self.assertIs(profile_for_volume([profile], volume), profile)
            self.assertEqual(decide(profile, volume, set(), 0, None, None).reason, RUN)

    def test_full_uuid_does_not_need_marker(self):
        volume = _volume("/missing", partial=False)
        self.assertTrue(uuid_matches(volume, "12345678"))
        self.assertFalse(uuid_matches(volume, "00000000"))

    def test_full_uuid_comparison_is_nonempty_and_exact(self):
        volume = _volume("/missing", partial=False)
        self.assertFalse(uuid_matches(volume, ""))
        self.assertFalse(uuid_matches(_volume_with_uuid(""), "12345678"))
        self.assertTrue(uuid_matches(_volume_with_uuid("ABCDEF12"), "ABCDEF12"))
        self.assertFalse(uuid_matches(_volume_with_uuid("ABCDEF12"), "abcdef12"))
        self.assertFalse(uuid_matches(_volume_with_uuid("1234-5678"), "12345678"))


if __name__ == "__main__":
    unittest.main()
