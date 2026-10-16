"""Автоматика: только фиктивные тома и временные XDG-каталоги."""

import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from plugbackup.core import auto, autostart, journal
from plugbackup.core.profiles import AutoSettings, Job, Profile


def volume(uuid="uuid-a", path="/org/freedesktop/UDisks2/block_devices/fake",
           mounted=True, locked=False):
    return SimpleNamespace(uuid=uuid, object_path=path, mounted=mounted, locked=locked)


class DecideTests(unittest.TestCase):
    def setUp(self):
        self.profile = Profile(
            id="profile-a",
            auto=AutoSettings(enabled=True, min_interval_hours=4),
            jobs=[
                Job(id="auto-enabled", enabled=True, in_auto=True),
                Job(id="auto-disabled", enabled=False, in_auto=True),
                Job(id="manual-only", enabled=True, in_auto=False),
            ],
        )
        self.volume = volume()

    def decide(self, **kwargs):
        args = dict(conflicts=set(), now=20_000, last_success=None, paused_until=None)
        args.update(kwargs)
        return auto.decide(self.profile, self.volume, **args)

    def test_all_blocking_reasons_and_priority(self):
        self.assertEqual(self.decide(paused_until=20_001).reason, auto.PAUSED)
        self.profile.auto.enabled = False
        self.assertEqual(self.decide(paused_until=20_001).reason, auto.PAUSED)
        self.assertEqual(self.decide().reason, auto.AUTO_OFF)
        self.profile.auto.enabled = True
        self.assertEqual(self.decide(conflicts={self.volume.uuid}).reason, auto.CONFLICT)
        self.volume.locked = True
        self.assertEqual(self.decide(conflicts={self.volume.uuid}).reason, auto.CONFLICT)
        self.assertEqual(self.decide().reason, auto.LOCKED)
        self.volume.locked = False
        self.profile.jobs = [Job(id="off", enabled=False, in_auto=True),
                             Job(id="manual", enabled=True, in_auto=False)]
        self.assertEqual(self.decide().reason, auto.NO_AUTO_JOBS)

    def test_too_soon_but_runs_at_exact_interval_boundary(self):
        last = 10_000
        self.assertEqual(self.decide(now=last + 4 * 3600 - 0.001, last_success=last).reason, auto.TOO_SOON)
        result = self.decide(last_success=last, now=last + 4 * 3600)
        self.assertTrue(result.run)
        self.assertEqual(result.reason, auto.RUN)
        self.assertEqual(result.job_ids, ("auto-enabled",))

    def test_pause_expires_at_boundary(self):
        result = self.decide(paused_until=20_000)
        self.assertTrue(result.run)


class LastSuccessTests(unittest.TestCase):
    def test_uses_latest_ok_or_warnings_from_any_trigger(self):
        with tempfile.TemporaryDirectory() as state_home, patch.dict(os.environ, {"XDG_STATE_HOME": state_home}):
            now = time.time()
            records = [
                journal.RunRecord("old", "p", "Profile", "manual", now - 30, status=journal.OK),
                journal.RunRecord("failed", "p", "Profile", "auto", now - 20, status=journal.ERROR),
                journal.RunRecord("good", "p", "Profile", "cli", now - 10, status=journal.WARNINGS),
                journal.RunRecord("other-profile", "q", "Other", "manual", now - 5, status=journal.OK),
            ]
            for record in records:
                journal.append(record)
            self.assertEqual(auto.last_success_time("p"), now - 10)
            self.assertIsNone(auto.last_success_time("missing"))


class InsertionTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = auto.InsertionTracker()

    def test_already_mounted_at_first_poll_emits_ready_once(self):
        v = volume()
        self.assertEqual(self.tracker.update([v], 1), [(auto.READY, v)])
        self.assertEqual(self.tracker.update([v], 2), [])

    def test_unmounted_volume_requests_mount_once_after_wait(self):
        v = volume(mounted=False)
        self.assertEqual(self.tracker.update([v], 100), [])
        self.assertEqual(self.tracker.update([v], 100 + auto.MOUNT_WAIT_SECONDS - 0.001), [])
        self.assertEqual(self.tracker.update([v], 100 + auto.MOUNT_WAIT_SECONDS), [(auto.NEEDS_MOUNT, v)])
        self.assertEqual(self.tracker.update([v], 200), [])
        v.mounted = True
        self.assertEqual(self.tracker.update([v], 201), [(auto.READY, v)])
        self.assertEqual(self.tracker.update([v], 202), [])

    def test_removal_and_reinsertion_emit_new_ready(self):
        v = volume()
        self.assertEqual(self.tracker.update([v], 1), [(auto.READY, v)])
        self.assertEqual(self.tracker.update([], 2), [])
        self.assertEqual(self.tracker.update([v], 3), [(auto.READY, v)])

    def test_different_uuid_at_same_path_is_a_new_medium(self):
        first, second = volume(uuid="first"), volume(uuid="second")
        self.assertEqual(self.tracker.update([first], 1), [(auto.READY, first)])
        self.assertEqual(self.tracker.update([second], 2), [(auto.READY, second)])

    def test_forget_allows_same_inserted_medium_to_be_handled_again(self):
        v = volume()
        self.tracker.update([v], 1)
        self.tracker.forget(v.object_path)
        self.assertEqual(self.tracker.update([v], 2), [(auto.READY, v)])

    def test_locked_luks_medium_is_ready_without_mount_delay(self):
        v = volume(mounted=False, locked=True)
        self.assertEqual(self.tracker.update([v], 1), [(auto.READY, v)])
        self.assertEqual(self.tracker.update([v], 100), [])

    def test_multiple_media_are_tracked_independently(self):
        mounted = volume(uuid="mounted", path="/fake/a")
        waiting = volume(uuid="waiting", path="/fake/b", mounted=False)
        self.assertEqual(self.tracker.update([mounted, waiting], 10), [(auto.READY, mounted)])
        self.assertEqual(self.tracker.update([mounted, waiting], 10 + auto.MOUNT_WAIT_SECONDS),
                         [(auto.NEEDS_MOUNT, waiting)])
        waiting.mounted = True
        self.assertEqual(self.tracker.update([mounted, waiting], 16), [(auto.READY, waiting)])


class AutostartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"XDG_CONFIG_HOME": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_path_and_enable_create_expected_desktop_entry_atomically(self):
        path = autostart.autostart_path()
        self.assertEqual(path, os.path.join(self.temp.name, "autostart", "io.github.plugbackup.PlugBackup.desktop"))
        self.assertFalse(autostart.is_enabled())
        autostart.set_enabled(True)
        with open(path, encoding="utf-8") as f:
            contents = f.read()
        self.assertIn("Exec=plugbackup --hidden", contents)
        self.assertTrue(autostart.is_enabled())
        self.assertFalse(os.path.exists(path + ".tmp"))

    def test_repeated_enable_and_disable(self):
        autostart.set_enabled(True)
        path = autostart.autostart_path()
        with open(path, encoding="utf-8") as f:
            first = f.read()
        autostart.set_enabled(True)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), first)
        self.assertFalse(os.path.exists(path + ".tmp"))
        autostart.set_enabled(False)
        self.assertFalse(os.path.exists(path))
        self.assertFalse(autostart.is_enabled())
        autostart.set_enabled(False)
        self.assertFalse(os.path.exists(path))

    def test_gnome_disabled_and_hidden_entries_are_disabled(self):
        path = autostart.autostart_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        for entry in ("X-GNOME-Autostart-enabled=false", "Hidden=true"):
            with self.subTest(entry=entry):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("[Desktop Entry]\n" + entry + "\n")
                self.assertFalse(autostart.is_enabled())

    def test_disabled_text_in_comment_does_not_disable_entry(self):
        path = autostart.autostart_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("[Desktop Entry]\n# Old value: X-GNOME-Autostart-enabled=false\n"
                    "X-GNOME-Autostart-enabled=true\nExec=plugbackup --hidden\n")
        self.assertTrue(autostart.is_enabled())


if __name__ == "__main__":
    unittest.main()
