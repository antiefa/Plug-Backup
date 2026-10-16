"""Проверки причин пропуска имён и объектов при резервном копировании."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from plugbackup import plat
from plugbackup.core import binding, fsinfo, journal, rsync, runner
from plugbackup.core.profiles import Job, Profile, Thresholds


class InvalidReasonTests(unittest.TestCase):
    def test_each_forbidden_character_and_control_character(self):
        for char in '"*:<>?\\|':
            with self.subTest(char=char):
                self.assertEqual(fsinfo.invalid_reason(f"a{char}b", "exfat"),
                                 f"{fsinfo.SKIP_INVALID_CHARS}:{char}")
        self.assertEqual(fsinfo.invalid_reason("control\x01", "ntfs"),
                         f"{fsinfo.SKIP_INVALID_CHARS}:\x01")

    def test_multiple_invalid_characters_are_sorted_and_unique(self):
        self.assertEqual(fsinfo.invalid_reason("x|?:*|?", "exfat"),
                         f"{fsinfo.SKIP_INVALID_CHARS}:*:?|")

    def test_trailing_names_reserved_fat_names_and_filesystem_scope(self):
        for name in ("trailing ", "trailing."):
            self.assertEqual(fsinfo.invalid_reason(name, "ntfs"), fsinfo.SKIP_TRAILING)
        for name in ("CON", "con.txt", "PRN.log", "AUX", "NUL", "COM1", "LPT9.txt"):
            self.assertEqual(fsinfo.invalid_reason(name, "vfat"), fsinfo.SKIP_RESERVED, name)
            self.assertEqual(fsinfo.invalid_reason(name, "exfat"), "", name)
        for fs_type in ("ext4", "unknown-fs"):
            self.assertEqual(fsinfo.invalid_reason('bad:*?"', fs_type), "")

    def test_check_names_reasons_suppress_descendants_and_encryption(self):
        entries = [
            rsync.ListEntry("d", 0, "bad:"),
            rsync.ListEntry("f", 1, "bad:/child"),
            rsync.ListEntry("f", 1, "other?name"),
        ]
        report = fsinfo.check_names(entries, "exfat", copy_symlinks=False)
        self.assertEqual(report.invalid, ["bad:", "other?name"])
        self.assertEqual(report.reasons, {
            "bad:": f"{fsinfo.SKIP_INVALID_CHARS}::",
            "other?name": f"{fsinfo.SKIP_INVALID_CHARS}:?",
        })
        encrypted = fsinfo.check_names(entries, "exfat", copy_symlinks=False, encrypted=True)
        self.assertEqual(encrypted.invalid, [])
        self.assertEqual(encrypted.reasons, {})


class SkipReasonsIntegrationTests(unittest.TestCase):
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
        self.profile = Profile(
            id="skip-reasons-profile", name="Skip reasons", root="PlugBackup",
            mass_delete_threshold=Thresholds(percent=30, files=500, hard_max=5000),
            jobs=[Job(id="skip-reasons-job", source=str(self.source), dest="Data",
                      mode="mirror_versions", use_global_excludes=False)],
        )
        self.volume = SimpleNamespace(uuid="U1", fs_type="ext4", mount_point=str(self.drive),
                                      mounted=True, locked=False, fs_uuid="U1")
        self.stack = mock.patch.dict(os.environ, {
            "XDG_STATE_HOME": str(self.state), "XDG_RUNTIME_DIR": str(self.runtime),
        })
        self.stack.start()
        self.addCleanup(self.stack.stop)
        self.mount_patch = mock.patch.object(binding, "_verified_mount_point",
                                             lambda profile, volume: str(self.drive))
        self.verify_patch = mock.patch.object(plat.mounts, "verify_mount",
                                              lambda *a, **kw: SimpleNamespace(ok=True))
        self.sleep_patch = mock.patch.object(plat.system, "inhibit_sleep", lambda reason: None)
        for patcher in (self.mount_patch, self.verify_patch, self.sleep_patch):
            patcher.start()
            self.addCleanup(patcher.stop)

    @property
    def dest(self):
        return self.drive / "PlugBackup" / "Data"

    def run_job(self):
        return runner.Runner(runner.RunRequest(self.profile, self.volume)).run()

    def add_link_and_file(self):
        (self.source / "target.txt").write_text("target", encoding="utf-8")
        os.symlink("target.txt", self.source / "shortcut")

    def test_posix_auto_preserves_symlink_and_job_is_ok_without_other_skips(self):
        self.add_link_and_file()
        outcome = self.run_job()
        result = outcome.record.jobs[0]
        self.assertEqual(result.status, journal.OK)
        self.assertNotIn("shortcut", outcome.plans[self.profile.jobs[0].id].skipped)
        self.assertTrue((self.dest / "shortcut").is_symlink())
        self.assertEqual(os.readlink(self.dest / "shortcut"), "target.txt")

    def test_exfat_reports_symlink_invalid_name_and_fifo_reasons(self):
        self.volume.fs_type = "exfat"
        self.add_link_and_file()
        (self.source / "bad:name").write_text("invalid", encoding="utf-8")
        os.mkfifo(self.source / "pipe")
        outcome = self.run_job()
        result = outcome.record.jobs[0]
        paired = dict(zip(result.skipped_files, result.skipped_reasons))
        self.assertEqual(paired, {
            "shortcut": fsinfo.SKIP_SYMLINK,
            "bad:name": f"{fsinfo.SKIP_INVALID_CHARS}::",
            "pipe": fsinfo.SKIP_NON_REGULAR,
        })
        self.assertEqual(len(result.skipped_reasons), len(result.skipped_files))

    def test_copy_target_never_skips_symlink_on_supported_filesystems(self):
        for fs_type in ("ext4", "vfat", "exfat", "ntfs", "ntfs3"):
            with self.subTest(fs_type=fs_type):
                self.volume.fs_type = fs_type
                self.add_link_and_file()
                self.profile.jobs[0].symlinks = "copy_target"
                outcome = self.run_job()
                result = outcome.record.jobs[0]
                self.assertNotIn("shortcut", outcome.plans[self.profile.jobs[0].id].skipped)
                self.assertNotIn("shortcut", result.skipped_files)
                self.assertEqual((self.dest / "shortcut").read_text(encoding="utf-8"), "target")
                self.assertFalse((self.dest / "shortcut").is_symlink())
                (self.dest / "shortcut").unlink()
                (self.dest / "target.txt").unlink()
                (self.source / "shortcut").unlink()
                (self.source / "target.txt").unlink()


class JournalSkipReasonsTests(unittest.TestCase):
    def test_old_journal_record_defaults_and_new_reasons_round_trip(self):
        with tempfile.TemporaryDirectory() as state:
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": state}):
                path = Path(journal.log_dir()) / "runs.jsonl"
                path.parent.mkdir(parents=True)
                old = journal.RunRecord(
                    "old", "p", "Profile", "manual", time.time(),
                    jobs=[journal.JobResult("j", "source", "dest", skipped_files=["old.txt"])],
                )
                old_data = json.loads(json.dumps(old, default=lambda value: value.__dict__))
                old_data["jobs"][0].pop("skipped_reasons")
                path.write_text(json.dumps(old_data) + "\n", encoding="utf-8")
                loaded_old = journal.read_runs("p")[0]
                self.assertEqual(loaded_old.jobs[0].skipped_reasons, [])

                new = journal.RunRecord(
                    "new", "p", "Profile", "manual", time.time(),
                    jobs=[journal.JobResult("j", "source", "dest", skipped_files=["bad:name"],
                                            skipped_reasons=["invalid-chars::"])],
                )
                journal.append(new)
                loaded = journal.read_runs("p")[0]
                self.assertEqual(loaded.jobs[0].skipped_reasons, ["invalid-chars::"])


if __name__ == "__main__":
    unittest.main()
