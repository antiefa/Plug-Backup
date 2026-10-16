from __future__ import annotations

import os
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from plugbackup import plat
from plugbackup import cli
from plugbackup.core import binding, journal, rsync, timeshift
from plugbackup.core.copier import EngineCopier, RsyncCopier
from plugbackup.core.profiles import Job, Profile, Thresholds
from plugbackup.core import runner


class TimeShiftFunctionTests(unittest.TestCase):
    def samples(self, count=20, hours=3, seconds=0):
        return [(10, 100000 + index, 10, 100000 + index - hours * 3600 + seconds)
                for index in range(count)]

    def test_requires_twenty_pairs_and_ninety_percent(self):
        self.assertIsNone(timeshift.detect_shift(self.samples(19)))
        samples = self.samples(20)
        samples[0] = (10, 100000, 10, 100000)
        self.assertEqual(timeshift.detect_shift(samples), timeshift.Shift(3, 20, 19))
        samples[1] = (10, 100001, 10, 100001)
        samples[2] = (10, 100002, 10, 100002)
        self.assertIsNone(timeshift.detect_shift(samples))

    def test_mixed_shifts_do_not_match(self):
        samples = self.samples(20)
        samples[0] = (10, 100000, 10, 100000 - 3600)
        samples[1] = (10, 100001, 10, 100001 - 7200)
        samples[2] = (10, 100002, 10, 100002 - 7200)
        self.assertIsNone(timeshift.detect_shift(samples))

    def test_zero_shift_and_out_of_range_shift_do_not_match(self):
        self.assertIsNone(timeshift.detect_shift(self.samples(hours=0)))
        self.assertIsNone(timeshift.detect_shift(self.samples(hours=15)))

    def test_two_second_tolerance_and_negative_hours(self):
        self.assertEqual(timeshift.detect_shift(self.samples(hours=4, seconds=2)),
                         timeshift.Shift(4, 20, 20))
        self.assertEqual(timeshift.detect_shift(self.samples(hours=-7, seconds=-2)),
                         timeshift.Shift(-7, 20, 20))
        self.assertIsNone(timeshift.detect_shift(self.samples(hours=4, seconds=3)))

    def test_different_sizes_are_not_pairs(self):
        samples = self.samples()
        samples[0] = (11, samples[0][1], 10, samples[0][3])
        self.assertIsNone(timeshift.detect_shift(samples))
        samples.append((10, 100020, 10, 100020 - 3 * 3600))
        self.assertEqual(timeshift.detect_shift(samples), timeshift.Shift(3, 20, 20))


class RecordingCallbacks(runner.Callbacks):
    def __init__(self, answer=False):
        self.answer = answer
        self.kinds = []

    def confirm(self, kind, title, text, details):
        self.kinds.append((kind, title, text, details))
        return self.answer


class TimeShiftRunnerTests(unittest.TestCase):
    copiers = (RsyncCopier, EngineCopier)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "source"
        self.drive = self.base / "drive"
        self.source.mkdir()
        self.drive.mkdir()
        self.state = self.base / "state"
        self.runtime = self.base / "runtime"
        self.state.mkdir()
        self.runtime.mkdir()
        self.profile = Profile(id="timeshift-test", name="Time shift",
                               root="PlugBackup",
                               mass_delete_threshold=Thresholds(percent=30, files=500, hard_max=5000),
                               jobs=[Job(id="job", source=str(self.source), dest="Data",
                                         mode="mirror_versions", use_global_excludes=False)])
        self.volume = SimpleNamespace(uuid="U1", fs_type="vfat", mount_point=str(self.drive),
                                      mounted=True, locked=False, fs_uuid="U1")
        self.dest = self.drive / "PlugBackup" / "Data"
        self.dest.mkdir(parents=True)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.state),
                                                               "XDG_RUNTIME_DIR": str(self.runtime)}))
        self.stack.enter_context(mock.patch.object(binding, "_verified_mount_point",
                                                    lambda profile, volume: str(self.drive)))
        self.stack.enter_context(mock.patch.object(plat.mounts, "verify_mount",
                                                    lambda *args, **kwargs: SimpleNamespace(ok=True)))
        self.stack.enter_context(mock.patch.object(plat.system, "inhibit_sleep", lambda reason: None))
        binding.write_device_files(self.profile, self.volume, accept_existing=True)

    def seed_shifted_files(self, hours=5, count=20):
        for index in range(count):
            source = self.source / ("file-%02d.txt" % index)
            dest = self.dest / source.name
            source.write_text("content-%02d" % index, encoding="utf-8")
            source_time = 1700000000 + index * 10
            os.utime(str(source), (source_time, source_time))
            dest.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            os.utime(str(dest), (source_time - hours * 3600, source_time - hours * 3600))

    def run_job(self, copier, callback=None, trigger="manual", preview=False):
        self.stack.enter_context(mock.patch.object(runner, "default_copier", lambda: copier()))
        request = runner.RunRequest(self.profile, self.volume, trigger=trigger, preview=preview)
        return runner.Runner(request, callback).run()

    def test_manual_yes_updates_timestamps_without_copy_or_archive_for_both_copiers(self):
        for copier in self.copiers:
            with self.subTest(copier=copier.__name__):
                self.seed_shifted_files()
                before = sorted(path.read_bytes() for path in self.dest.iterdir())
                callback = RecordingCallbacks(True)
                outcome = self.run_job(copier, callback)
                self.assertEqual([item[0] for item in callback.kinds], ["timeshift"])
                self.assertEqual(outcome.record.jobs[0].status, journal.OK)
                self.assertEqual(outcome.record.jobs[0].copied_files, 0)
                self.assertEqual(outcome.record.jobs[0].deleted_files, 0)
                self.assertEqual(sorted(path.read_bytes() for path in self.dest.iterdir()), before)
                for source in self.source.iterdir():
                    self.assertEqual(os.lstat(str(source)).st_mtime, os.lstat(str(self.dest / source.name)).st_mtime)
                self.assertFalse((self.drive / "PlugBackup" / ".versions").exists())
                self.dest = self.drive / "PlugBackup" / "Data"
                self.dest.mkdir(parents=True, exist_ok=True)

    def test_manual_no_and_automatic_skip_for_both_copiers(self):
        for copier in self.copiers:
            with self.subTest(copier=copier.__name__, trigger="manual"):
                self.seed_shifted_files()
                callback = RecordingCallbacks(False)
                outcome = self.run_job(copier, callback)
                self.assertEqual((outcome.record.jobs[0].status, outcome.record.jobs[0].reason),
                                 (journal.SKIPPED, "timeshift"))
                self.assertEqual(len(callback.kinds), 1)
                self.assertNotEqual(os.lstat(str(self.dest / "file-00.txt")).st_mtime,
                                    os.lstat(str(self.source / "file-00.txt")).st_mtime)
                self._reset_destination()
            for trigger in ("auto", "cli"):
                with self.subTest(copier=copier.__name__, trigger=trigger):
                    self.seed_shifted_files()
                    callback = RecordingCallbacks(True)
                    outcome = self.run_job(copier, callback, trigger=trigger)
                    self.assertEqual((outcome.record.jobs[0].status, outcome.record.jobs[0].reason),
                                     (journal.SKIPPED, "timeshift"))
                    self.assertEqual(callback.kinds, [])
                    if trigger == "cli":
                        self.assertEqual(cli.exit_code(outcome.record), cli.EXIT_NEEDS_CONFIRMATION)
                    self._reset_destination()

    def test_preview_marks_shift_without_confirmation_or_mutation(self):
        for copier in self.copiers:
            with self.subTest(copier=copier.__name__):
                self.seed_shifted_files()
                old_time = os.lstat(str(self.dest / "file-00.txt")).st_mtime
                callback = RecordingCallbacks(True)
                outcome = self.run_job(copier, callback, preview=True)
                self.assertEqual(outcome.plans["job"].timeshift, 5)
                self.assertEqual(callback.kinds, [])
                self.assertEqual(os.lstat(str(self.dest / "file-00.txt")).st_mtime, old_time)
                self._reset_destination()

    def test_non_vfat_filesystems_skip_detection(self):
        self.seed_shifted_files()
        for fs_type in ("ext4", "exfat"):
            with self.subTest(fs_type=fs_type):
                self.volume.fs_type = fs_type
                with mock.patch.object(timeshift, "detect", side_effect=AssertionError("unexpected scan")):
                    result = self.run_job(EngineCopier, RecordingCallbacks(False))
                self.assertNotEqual(result.record.jobs[0].reason, "timeshift")
                self._reset_destination()
                self.seed_shifted_files()

    def _reset_destination(self):
        for path in self.dest.iterdir():
            path.unlink()


if __name__ == "__main__":
    unittest.main()
