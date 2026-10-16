"""Safety and behavior tests for the copy engine.

Every integration run uses real rsync between temporary source and destination
directories. Device verification and logind are replaced before Runner.run().
"""

import os
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from plugbackup.core import binding, fsinfo, journal, rsync, runner
from plugbackup.core.profiles import Job, Profile, Thresholds


class RsyncHelpersTests(unittest.TestCase):
    def test_relative_backup_dir_depth_and_normalization(self):
        self.assertEqual(rsync.relative_backup_dir("Projects", "2026-10-15T12-00-00Z"),
                         "../.versions/2026-10-15T12-00-00Z/Projects")
        self.assertEqual(rsync.relative_backup_dir("Archive/Photos", "T"),
                         "../../.versions/T/Archive/Photos")

    def test_escape_pattern_quotes_each_rsync_meta_character(self):
        self.assertEqual(rsync.escape_pattern(r"a*[b]?\c"), r"a\*\[b\]\?\\c")

    def test_commands_cover_modes_filesystems_and_filtering(self):
        base = Job(source="/tmp/source", dest="Photos")
        opts = rsync.CommandOptions(fs_type="ext4", global_excludes=[" *.tmp "])
        self.assertEqual(rsync.file_options(base, "ext4"),
                         ["--recursive", "--times", "--perms", "--links", "--one-file-system"])
        self.assertEqual(rsync.filter_options(base, opts), ["--exclude=*.tmp"])
        for mode, expected in (("mirror", ["--delete", "--max-delete=5000"]),
                               ("accumulate", []),
                               ("mirror_versions", ["--delete", "--max-delete=5000", "--backup",
                                                    "--backup-dir=../.versions/T/Photos"])):
            job = Job(source="/tmp/source", dest="Photos", mode=mode)
            cmd = rsync.build_command(job, "/tmp/source", "/tmp/dest", rsync.CommandOptions(
                fs_type="exfat", timestamp="T", global_excludes=["*.tmp"], progress=False))
            for arg in expected:
                self.assertIn(arg, cmd)
            self.assertTrue(cmd[-2].endswith("/"))
            self.assertTrue(cmd[-1].endswith("/"))
            self.assertIn("--modify-window=1", cmd)
        with self.assertRaises(ValueError):
            rsync.build_command(Job(dest="x", mode="mirror_versions"), "s", "d",
                                rsync.CommandOptions(fs_type="ext4"))
        fat = Job(dest="x", max_size_mb=8192, skip_hidden=True)
        self.assertIn("--max-size=4294967295", rsync.filter_options(fat, rsync.CommandOptions(fs_type="vfat")))
        listed = rsync.build_list_command(fat, "/tmp/source", rsync.CommandOptions(fs_type="vfat"))
        self.assertFalse(any(arg.startswith("--max-size=") for arg in listed))

    def test_parse_list_unescapes_names_and_preserves_link_target_boundary(self):
        text = ("drwxr-xr-x          0 2026/10/15 12:00:00 .\n"
                "-rw-r--r--          7 2026/10/15 12:00:00 folder/hello world\n"
                "lrwxrwxrwx          4 2026/10/15 12:00:00 link -> target name\n"
                "-rw-r--r--          1 2026/10/15 12:00:00 octal\\#303\\#251\n")
        entries = rsync.parse_list(text)
        self.assertEqual([(e.kind, e.size, e.path) for e in entries], [
            ("f", 7, "folder/hello world"), ("l", 4, "link"), ("f", 1, "octal\u00e9")])

    def test_parse_list_discards_rsync_root_directory_spelling(self):
        # rsync --list-only emits "./" for its root on supported rsync 3.2.7.
        text = "drwxr-xr-x          0 2026/10/15 12:00:00 ./\n"
        self.assertEqual(rsync.parse_list(text), [])

    def test_parse_itemize_stats_and_progress(self):
        summary = rsync.ItemizeSummary()
        for line in (">f+++++++++ new.txt", ">f.st...... changed.txt", "*deleting old.txt",
                     "*deleting folder/", 'skipping non-regular file "socket"', "custom notice"):
            rsync.parse_itemize_line(line, summary)
        self.assertEqual(summary.new_files, ["new.txt"])
        self.assertEqual(summary.changed_files, ["changed.txt"])
        self.assertEqual(summary.deleted_files, ["old.txt"])
        self.assertEqual(summary.deleted_dirs, ["folder/"])
        self.assertEqual(summary.skipped_nonregular, ["socket"])
        self.assertEqual(summary.other_messages, ["custom notice"])
        stats = rsync.parse_stats("Total transferred file size: 1,234 bytes\nTotal file size: 9 bytes\n"
                                  "Number of regular files transferred: 2\nNumber of deleted files: 1\n")
        self.assertEqual((stats.transferred_size, stats.total_size, stats.files_transferred, stats.deleted),
                         (1234, 9, 2, 1))
        self.assertEqual(rsync.parse_progress("  1,234  50%  2.5MB/s  0:00:01"),
                         rsync.Progress(1234, 50, "2.5MB/s", "0:00:01"))
        self.assertIsNone(rsync.parse_progress("not progress"))


class FsInfoTests(unittest.TestCase):
    def test_invalid_names_reserved_names_trailing_and_platform_scope(self):
        for name in ('bad:name', 'bad*name', 'bad?name', 'bad"name', 'bad<name', 'bad>name',
                     'bad|name', "bad\\name", "control\x1f", "trail.", "trail "):
            self.assertTrue(fsinfo.invalid_name(name, "exfat"), name)
            self.assertTrue(fsinfo.invalid_name(name, "ntfs"), name)
        for name in ("CON", "con.txt", "PRN.log", "AUX", "NUL", "COM1", "LPT9.txt"):
            self.assertTrue(fsinfo.invalid_name(name, "vfat"), name)
        self.assertFalse(fsinfo.invalid_name("CON", "exfat"))
        self.assertFalse(fsinfo.invalid_name("bad:name", "ext4"))

    def test_case_and_unicode_collisions_and_fat_limit_and_links(self):
        entries = [rsync.ListEntry("f", 1, "Name"), rsync.ListEntry("f", 1, "name"),
                   rsync.ListEntry("f", 1, "caf\u00e9"), rsync.ListEntry("f", 1, "cafe\u0301"),
                   rsync.ListEntry("f", fsinfo.FAT32_MAX_FILE + 1, "large.bin"),
                   rsync.ListEntry("l", 0, "shortcut")]
        fat = fsinfo.check_names(entries, "vfat", copy_symlinks=False)
        self.assertEqual(fat.collisions, [("Name", "name"), ("caf\u00e9", "cafe\u0301")])
        self.assertEqual(fat.too_big, [("large.bin", 4 * 1024 ** 3)])
        self.assertEqual(fat.symlinks, ["shortcut"])
        copied = fsinfo.check_names(entries, "ext4", copy_symlinks=True)
        self.assertEqual(copied.collisions, [])
        self.assertEqual(copied.too_big, [])
        self.assertEqual(copied.symlinks, [])

    def test_invalid_directory_suppresses_descendants(self):
        report = fsinfo.check_names([rsync.ListEntry("d", 0, "bad."),
                                     rsync.ListEntry("f", 2, "bad./child")], "exfat", False)
        self.assertEqual(report.invalid, ["bad."])


class JournalTests(unittest.TestCase):
    def test_append_read_last_and_prune_in_isolated_state_home(self):
        with tempfile.TemporaryDirectory() as state:
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": state}):
                old = journal.RunRecord("old", "p", "P", "manual", time.time() - 100 * 86400)
                recent = journal.RunRecord("recent", "p", "P", "manual", time.time())
                journal.append(old)
                journal.append(recent)
                self.assertEqual([r.run_id for r in journal.read_runs("p")], ["recent"])
                self.assertEqual(journal.last_run("p").run_id, "recent")
                journal.append(journal.RunRecord("other", "q", "Q", "cli", time.time()))
                self.assertEqual(journal.last_run("q").run_id, "other")

    def test_read_skips_malformed_lines_and_overall_status_priority(self):
        with tempfile.TemporaryDirectory() as state:
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": state}):
                path = Path(journal.log_dir()) / "runs.jsonl"
                path.parent.mkdir(parents=True)
                path.write_text("{broken}\n", encoding="utf-8")
                self.assertEqual(journal.read_runs(), [])
        statuses = [journal.JobResult("1", "s", "d", status=journal.WARNINGS),
                    journal.JobResult("2", "s", "d", status=journal.SKIPPED)]
        self.assertEqual(journal.overall_status(statuses), journal.SKIPPED)
        self.assertEqual(journal.overall_status([]), journal.OK)


class RunnerIntegrationTests(unittest.TestCase):
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
        self.profile = Profile(id="profile-test", name="Test", root="PlugBackup",
                               mass_delete_threshold=Thresholds(percent=30, files=500, hard_max=5000),
                               jobs=[Job(id="job-test", source=str(self.source), dest="Data",
                                         mode="mirror_versions", use_global_excludes=False)])
        self.volume = SimpleNamespace(uuid="U1", fs_type="exfat", mount_point=str(self.drive),
                                      mounted=True, locked=False, fs_uuid="U1")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.state),
                                                               "XDG_RUNTIME_DIR": str(self.runtime)}))
        self.stack.enter_context(mock.patch.object(binding, "_verified_mount_point",
                                                    lambda profile, volume: str(self.drive)))
        self.stack.enter_context(mock.patch.object(runner, "verify_mount",
                                                    lambda *a, **kw: SimpleNamespace(ok=True)))
        self.stack.enter_context(mock.patch.object(runner, "inhibit_sleep", lambda reason: None))

    @property
    def dest(self):
        return self.drive / "PlugBackup" / "Data"

    def run_job(self, *, callback=None, trigger="manual"):
        return runner.Runner(runner.RunRequest(self.profile, self.volume, trigger=trigger), callback).run()

    def test_real_rsync_copies_unicode_and_spaces_then_accumulate_retains_old(self):
        (self.source / "привет мир.txt").write_text("one", encoding="utf-8")
        first = self.run_job()
        self.assertEqual(first.record.status, journal.OK)
        self.assertEqual((self.dest / "привет мир.txt").read_text(encoding="utf-8"), "one")
        self.profile.jobs[0].mode = "accumulate"
        (self.source / "привет мир.txt").unlink()
        (self.source / "new file.txt").write_text("two", encoding="utf-8")
        second = self.run_job()
        self.assertEqual(second.record.jobs[0].status, journal.OK)
        self.assertTrue((self.dest / "привет мир.txt").exists())
        self.assertTrue((self.dest / "new file.txt").exists())

    def test_mirror_versions_moves_deleted_file_inside_versions_and_mirror_deletes(self):
        self.profile.mass_delete_threshold = Thresholds(percent=100, files=500, hard_max=5000)
        (self.source / "old.txt").write_text("original", encoding="utf-8")
        (self.source / "steady.txt").write_text("steady", encoding="utf-8")
        self.run_job()
        (self.source / "old.txt").unlink()
        second = self.run_job()
        self.assertEqual(second.record.jobs[0].deleted_files, 1)
        versions = self.drive / "PlugBackup" / ".versions"
        archived = list(versions.glob("*/Data/old.txt"))
        self.assertEqual(len(archived), 1, f"deleted={second.record.jobs[0].deleted_files}; files="
                         f"{[str(p.relative_to(self.drive)) for p in self.drive.rglob('*')]}")
        self.assertEqual(archived[0].read_text(encoding="utf-8"), "original")
        self.assertFalse((self.dest / "old.txt").exists())
        self.profile.jobs[0].mode = "mirror"
        (self.source / "new.txt").write_text("new", encoding="utf-8")
        self.run_job()
        (self.source / "new.txt").unlink()
        (self.source / "steady.txt").write_text("steady", encoding="utf-8")
        third = self.run_job()
        self.assertEqual(third.record.jobs[0].deleted_files, 1)
        self.assertFalse((self.dest / "new.txt").exists())

    def test_empty_source_and_rejected_mass_delete_leave_destination_untouched(self):
        (self.source / "keep.txt").write_text("safe", encoding="utf-8")
        self.run_job()
        before = (self.dest / "keep.txt").read_bytes()
        (self.source / "keep.txt").unlink()
        result = self.run_job()
        self.assertEqual((result.record.jobs[0].status, result.record.jobs[0].reason),
                         (journal.SKIPPED, "empty-source"))
        self.assertEqual((self.dest / "keep.txt").read_bytes(), before)

        self.profile.mass_delete_threshold = Thresholds(percent=100, files=500, hard_max=5000)
        (self.source / "replacement.txt").write_text("replacement", encoding="utf-8")
        self.run_job()
        (self.source / "replacement.txt").unlink()
        self.profile.mass_delete_threshold = Thresholds(percent=30, files=0, hard_max=5000)
        (self.source / "survivor.txt").write_text("survivor", encoding="utf-8")
        callback = RecordingCallbacks(False)
        result = self.run_job(callback=callback)
        self.assertEqual((result.record.jobs[0].status, result.record.jobs[0].reason),
                         (journal.SKIPPED, "mass-delete"))
        self.assertIn("mass-delete", callback.kinds)
        self.assertTrue((self.dest / "replacement.txt").exists())

    def test_mass_delete_thresholds_are_strict_at_exact_boundary(self):
        self.profile.mass_delete_threshold = Thresholds(percent=50, files=2, hard_max=5000)
        for index in range(4):
            (self.source / f"file-{index}.txt").write_text(str(index), encoding="utf-8")
        self.run_job()
        (self.source / "file-0.txt").unlink()
        (self.source / "file-1.txt").unlink()
        callback = RecordingCallbacks(False)
        outcome = self.run_job(callback=callback)
        self.assertEqual(outcome.record.jobs[0].status, journal.OK)
        self.assertEqual(outcome.record.jobs[0].deleted_files, 2)
        self.assertNotIn("mass-delete", callback.kinds)
        self.assertFalse((self.dest / "file-0.txt").exists())
        self.assertFalse((self.dest / "file-1.txt").exists())

    def test_excluded_destination_is_retained(self):
        (self.source / "keep.txt").write_text("keep", encoding="utf-8")
        (self.source / "excluded.tmp").write_text("excluded", encoding="utf-8")
        self.run_job()
        self.profile.jobs[0].excludes = ["excluded.tmp"]
        (self.source / "excluded.tmp").unlink()
        result = self.run_job()
        self.assertIn(result.record.jobs[0].status, (journal.OK, journal.WARNINGS))
        self.assertTrue((self.dest / "excluded.tmp").exists())

    def test_name_collision_skips_before_copy(self):
        self.volume.fs_type = "vfat"
        (self.source / "Report.txt").write_text("a", encoding="utf-8")
        (self.source / "report.txt").write_text("b", encoding="utf-8")
        result = self.run_job()
        self.assertEqual((result.record.jobs[0].status, result.record.jobs[0].reason),
                         (journal.SKIPPED, "name-collision"))
        self.assertFalse(self.dest.exists())

    def test_rejecting_mass_delete_is_skipped_in_auto_without_confirm(self):
        self.profile.mass_delete_threshold = Thresholds(percent=100, files=500, hard_max=5000)
        (self.source / "one.txt").write_text("one", encoding="utf-8")
        self.run_job()
        (self.source / "one.txt").unlink()
        (self.source / "other.txt").write_text("other", encoding="utf-8")
        self.run_job()
        self.profile.mass_delete_threshold = Thresholds(percent=30, files=1, hard_max=5000)
        (self.source / "other.txt").unlink()
        callback = RecordingCallbacks(True)
        (self.source / "sentinel.txt").write_text("s", encoding="utf-8")
        result = self.run_job(callback=callback, trigger="auto")
        self.assertEqual(callback.kinds, [])
        self.assertEqual((result.record.jobs[0].status, result.record.jobs[0].reason),
                         (journal.SKIPPED, "mass-delete"),
                         f"result={result.record.jobs[0]} callback={callback.kinds}")

    def test_versions_helpers_and_expiry_cleanup_only_old_directories(self):
        root = self.drive / "PlugBackup"
        versions = root / ".versions"
        old_name = "2020-01-01T00-00-00Z"
        recent_name = time.strftime(runner.VERSIONS_TS_FORMAT, time.gmtime())
        (versions / old_name / "Data").mkdir(parents=True)
        (versions / old_name / "Data" / "old.txt").write_text("old", encoding="utf-8")
        (versions / recent_name).mkdir(parents=True)
        moment = time.gmtime(1234567890)
        self.assertEqual(runner.unique_versions_name(str(root), moment),
                         time.strftime(runner.VERSIONS_TS_FORMAT, moment))
        self.assertEqual(runner.unique_versions_name(str(root), time.gmtime()),
                         recent_name + "-2")
        found = runner.versions_dirs(str(root))
        self.assertEqual([Path(path).name for _stamp, path in found], [old_name, recent_name])
        removed = runner.Runner(runner.RunRequest(self.profile, self.volume))._cleanup_versions(
            str(root), 30, "unused-current")
        self.assertEqual(removed, [old_name])
        self.assertFalse((versions / old_name).exists())
        self.assertTrue((versions / recent_name).exists())

    def test_make_space_requires_confirmation_then_removes_oldest_versions_only(self):
        root = self.drive / "PlugBackup"
        versions = root / ".versions"
        old = versions / "2020-01-01T00-00-00Z"
        newer = versions / "2021-01-01T00-00-00Z"
        (old / "Data").mkdir(parents=True)
        (newer / "Data").mkdir(parents=True)
        (old / "Data" / "old").write_text("old", encoding="utf-8")
        (newer / "Data" / "new").write_text("new", encoding="utf-8")
        run = runner.Runner(runner.RunRequest(self.profile, self.volume), RecordingCallbacks(False))
        with mock.patch.object(runner, "free_bytes", side_effect=[0, 10, 10, 10]):
            self.assertFalse(run._make_space(str(root), 10, "current", str(self.dest)))
        self.assertTrue(old.exists())
        run.cb = RecordingCallbacks(True)
        with mock.patch.object(runner, "free_bytes", side_effect=[0, 0, 10, 10]):
            self.assertTrue(run._make_space(str(root), 10, "current", str(self.dest)))
        self.assertFalse(old.exists())
        self.assertTrue(newer.exists())


class RecordingCallbacks(runner.Callbacks):
    def __init__(self, answer):
        self.answer = answer
        self.kinds = []

    def confirm(self, kind, title, text, details):
        self.kinds.append(kind)
        return self.answer


if __name__ == "__main__":
    unittest.main()
