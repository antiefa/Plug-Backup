from __future__ import annotations

import os
import errno
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import time

from plugbackup.core import rsync
from plugbackup.core.profiles import Job
from plugbackup.engine import CopySpec, execute, list_source, plan, spec_from_job
from plugbackup.engine import windows
from plugbackup.engine.progress import ProgressTracker, format_eta, format_speed

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.src = self.base / "source"
        self.dst = self.base / "drive" / "task"
        self.src.mkdir()
        self.dst.mkdir(parents=True)

    def spec(self, **kwargs):
        values = dict(source=str(self.src), dest_dir=str(self.dst), mode="mirror", compare="size_mtime",
                      symlinks="skip", fs_type="ext4", one_file_system=False)
        values.update(kwargs)
        return CopySpec(**values)

    def test_copy_plan_and_mirror_deletions(self):
        (self.src / "folder").mkdir()
        (self.src / "folder" / "new.txt").write_text("new")
        (self.dst / "old.txt").write_text("old")
        result = plan(self.spec())
        self.assertEqual(result.summary.new_files, ["folder/new.txt"])
        self.assertTrue((self.dst / "old.txt").exists())
        result = execute(self.spec())
        self.assertEqual(result.rc, rsync.RC_OK)
        self.assertEqual((self.dst / "folder" / "new.txt").read_text(), "new")
        self.assertFalse((self.dst / "old.txt").exists())

    def test_filters_protect_excluded_content_and_limit(self):
        (self.src / "keep.tmp").write_text("x")
        (self.src / "large").write_bytes(b"12345")
        (self.dst / "keep.tmp").write_text("old")
        (self.dst / "large").write_bytes(b"old")
        result = execute(self.spec(excludes=["*.tmp"], max_size=3))
        self.assertEqual(result.rc, rsync.RC_OK)
        self.assertEqual((self.dst / "keep.tmp").read_text(), "old")
        self.assertEqual((self.dst / "large").read_bytes(), b"old")

    def test_excluded_only_directory_and_excluded_child_protect_deletion(self):
        (self.dst / "only-excluded").mkdir()
        (self.dst / "only-excluded" / "old").write_text("keep")
        (self.dst / "parent").mkdir()
        (self.dst / "parent" / "important.tmp").write_text("keep")
        (self.dst / "parent" / "remove").write_text("remove")
        result = execute(self.spec(excludes=["only-excluded/", "*.tmp"]))
        self.assertEqual(result.rc, rsync.RC_OK)
        self.assertTrue((self.dst / "only-excluded" / "old").exists())
        self.assertTrue((self.dst / "parent" / "important.tmp").exists())
        self.assertFalse((self.dst / "parent" / "remove").exists())

    def test_partial_cleanup_removes_only_matching_source_names(self):
        (self.src / "owned").write_text("new")
        partial = self.dst / ".rsync-partial"
        partial.mkdir()
        (partial / "owned").write_text("interrupted")
        (partial / "foreign").write_text("untouched")
        result = execute(self.spec())
        self.assertEqual(result.rc, rsync.RC_OK)
        self.assertEqual((self.dst / "owned").read_text(), "new")
        self.assertTrue((partial / "foreign").exists())

    def test_checksum_rewrites_same_size_and_time_content(self):
        src = self.src / "x"
        dst = self.dst / "x"
        src.write_text("new")
        dst.write_text("old")
        os.utime(dst, (src.stat().st_atime, src.stat().st_mtime))
        res = execute(self.spec(compare="checksum"))
        self.assertEqual(res.summary.changed_files, ["x"])
        self.assertEqual(dst.read_text(), "new")

    def test_update_only_keeps_a_newer_destination(self):
        source = self.src / "update.txt"
        target = self.dst / "update.txt"
        source.write_text("source")
        target.write_text("newer destination")
        os.utime(source, (100, 100))
        os.utime(target, (200, 200))
        result = plan(self.spec(update_only=True))
        self.assertEqual(result.summary.changed_files, [])
        result = execute(self.spec(update_only=True))
        self.assertEqual(result.rc, rsync.RC_OK)
        self.assertEqual(target.read_text(), "newer destination")

    def test_delete_excluded_is_plan_only(self):
        (self.src / "keep.txt").write_text("source")
        (self.dst / "legacy.tmp").write_text("old")
        normal = plan(self.spec(excludes=["*.tmp"]))
        include_excluded = plan(self.spec(excludes=["*.tmp"], delete_excluded=True))
        self.assertEqual(normal.summary.deleted_files, [])
        self.assertEqual(include_excluded.summary.deleted_files, ["legacy.tmp"])
        with self.assertRaises(ValueError):
            execute(self.spec(excludes=["*.tmp"], delete_excluded=True))

    def test_backup_directory_archives_accumulate_replacement_after_exdev(self):
        from plugbackup.engine import executor
        from plugbackup.engine.models import EngineResult
        old = self.dst / "replace.txt"
        old.write_text("previous")
        backup = self.base / "other-volume" / "backup"
        spec = self.spec(mode="accumulate", backup_dir=str(backup))
        real_replace = os.replace
        def exdev_once(src, dest):
            if os.fspath(src) == str(old):
                raise OSError(errno.EXDEV, "cross-device link")
            return real_replace(src, dest)
        with mock.patch.object(executor.os, "replace", side_effect=exdev_once):
            self.assertTrue(executor._remove_existing(spec, str(old), "replace.txt", "f",
                                                      EngineResult(), None))
        self.assertFalse(old.exists())
        self.assertEqual((backup / "replace.txt").read_text(), "previous")

    def test_casefold_renames_existing_name_and_duplicate_source_blocks(self):
        """NFC и регистр сопоставляются на Windows-ФС; неоднозначный источник блокируется без записи."""
        (self.src / "README").write_text("new")
        (self.dst / "Readme").write_text("old")
        res = execute(self.spec(fs_type="ntfs3"))
        self.assertTrue((self.dst / "README").exists())
        self.assertFalse((self.dst / "Readme").exists())
        (self.src / "readme").write_text("collision")
        res = execute(self.spec(fs_type="ntfs3"))
        self.assertEqual(res.rc, rsync.RC_PARTIAL)
        self.assertEqual((self.dst / "README").read_text(), "new")

    def test_hard_delete_limit_and_versions_collision(self):
        for name in ("a", "b"):
            (self.dst / name).write_text(name)
        planned = plan(self.spec(hard_max_delete=1))
        res = execute(self.spec(hard_max_delete=1))
        self.assertEqual(planned.rc, rsync.RC_MAX_DELETE)
        self.assertEqual(planned.stats.deleted, 1)
        self.assertEqual(planned.summary.deleted_files, res.summary.deleted_files)
        self.assertEqual(res.rc, rsync.RC_MAX_DELETE)
        self.assertEqual(res.stats.deleted, 1)
        self.assertEqual(sum((self.dst / n).exists() for n in ("a", "b")), 1)

    def test_plan_lists_directory_deletions_without_mutating(self):
        (self.dst / "old").mkdir()
        (self.dst / "old" / "child").write_text("x")
        result = plan(self.spec())
        self.assertEqual(result.summary.deleted_files, ["old/child"])
        self.assertEqual(result.summary.deleted_dirs, ["old/"])
        self.assertTrue((self.dst / "old" / "child").exists())

    def test_cancel_before_execution(self):
        (self.src / "x").write_text("x")
        event = threading.Event(); event.set()
        res = execute(self.spec(), cancel=event)
        self.assertEqual(res.rc, rsync.RC_SIGNAL)
        self.assertFalse((self.dst / "x").exists())

    def test_destination_symlink_is_not_followed(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "keep").write_text("safe")
        (self.dst / "escape").symlink_to(outside, target_is_directory=True)
        (self.src / "escape").mkdir()
        (self.src / "escape" / "new").write_text("bad")
        result = execute(self.spec())
        self.assertEqual(result.rc, rsync.RC_OK)
        self.assertTrue((self.dst / "escape" / "new").exists())
        self.assertEqual((outside / "keep").read_text(), "safe")
        self.assertFalse((outside / "new").exists())

    def test_list_and_job_options(self):
        (self.src / "a").write_text("x")
        entries = list_source(self.spec())
        self.assertEqual([(e.kind, e.path) for e in entries], [("f", "a")])
        job = Job(dest="A/B", mode="mirror_versions", max_size_mb=2, use_global_excludes=False)
        spec = spec_from_job(job, str(self.src), str(self.dst), rsync.CommandOptions(fs_type="vfat", timestamp="T"))
        self.assertEqual(spec.max_size, 2 * 1024 * 1024)
        self.assertIn(".versions", spec.backup_dir)

    def test_windows_attribute_helpers(self):
        self.assertTrue(windows.is_hidden(SimpleNamespace(st_file_attributes=0x2)))
        self.assertTrue(windows.is_reparse_point(SimpleNamespace(st_file_attributes=0x400, st_reparse_tag=0)))
        self.assertTrue(windows.is_cloud_only(SimpleNamespace(st_file_attributes=0x1000)))

    def test_scan_skips_fake_hidden_cloud_and_reparse_objects(self):
        from plugbackup.engine import scan
        source_root = self.src
        class Entry:
            def __init__(self, name, mode, attrs=0, dev=None):
                self.name = name
                self.path = str(source_root / name)
                self.value = SimpleNamespace(st_mode=mode, st_size=1, st_mtime=1.0, st_atime=1.0,
                                             st_ino=1, st_dev=dev or source_root.stat().st_dev,
                                             st_file_attributes=attrs, st_reparse_tag=0)
            def stat(self, follow_symlinks=False): return self.value
        cases = [
            ("hidden", stat.S_IFREG | 0o644, 0x2, True, "hidden attribute"),
            ("cloud", stat.S_IFREG | 0o644, 0x1000, False, "файл только в облаке"),
            ("junction", stat.S_IFDIR | 0o755, 0x400, False, "точка соединения"),
        ]
        for name, mode, attrs, hidden, message in cases:
            with self.subTest(name=name):
                entry = Entry(name, mode, attrs, self.src.stat().st_dev + 1 if name == "junction" else None)
                spec = self.spec(skip_hidden_attr=hidden, one_file_system=True)
                with mock.patch.object(scan.os, "scandir", return_value=[entry]):
                    result = scan.scan_tree(spec, True)
                self.assertIn(name, result.skipped)
                if name == "junction":
                    self.assertEqual(result.entries[name][0], "l")

    def test_fake_other_filesystem_directory_is_not_descended(self):
        from plugbackup.engine import scan
        source_root = self.src
        class Entry:
            name = "mounted"
            path = str(source_root / name)
            def stat(self, follow_symlinks=False):
                return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_size=0, st_mtime=1.0, st_atime=1.0,
                                       st_ino=2, st_dev=source_root.stat().st_dev + 1,
                                       st_file_attributes=0, st_reparse_tag=0)
        with mock.patch.object(scan.os, "scandir", return_value=[Entry()]):
            result = scan.scan_tree(self.spec(one_file_system=True), True)
        self.assertEqual(result.skipped["mounted"], "different filesystem")
        self.assertEqual(list(result.entries), ["mounted"])

    def test_source_changes_during_read_is_not_published(self):
        from plugbackup.engine import executor
        source = self.src / "changing"
        target = self.dst / "changing"
        source.write_bytes(b"old-data")
        target.write_bytes(b"previous")
        original = os.stat
        def changed(path, *args, **kwargs):
            if os.fspath(path) == str(source):
                source.write_bytes(b"new-data")
            return original(path, *args, **kwargs)
        with mock.patch.object(executor.os, "stat", side_effect=changed):
            result = execute(self.spec(compare="checksum"))
        self.assertEqual(result.rc, rsync.RC_PARTIAL)
        self.assertEqual(target.read_bytes(), b"previous")
        self.assertFalse((self.dst / ".rsync-partial" / "changing").exists())

    def test_cancel_inside_large_file_preserves_old_and_cleans_partial(self):
        from plugbackup.engine import executor
        source = self.src / "large"
        target = self.dst / "large"
        source.write_bytes(b"n" * (3 * 1024 * 1024))
        target.write_bytes(b"old")
        event = threading.Event()
        real_open = open
        class Reader:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): self.stream.__enter__(); return self
            def __exit__(self, *args): return self.stream.__exit__(*args)
            def read(self, amount=-1):
                block = self.stream.read(min(amount, 1024 * 1024))
                if block: event.set()
                return block
        def wrapped_open(path, mode="r", *args, **kwargs):
            stream = real_open(path, mode, *args, **kwargs)
            return Reader(stream) if os.fspath(path) == str(source) and "r" in mode else stream
        with mock.patch("builtins.open", side_effect=wrapped_open):
            result = execute(self.spec(), cancel=event)
        self.assertEqual(result.rc, rsync.RC_SIGNAL)
        self.assertEqual(target.read_bytes(), b"old")
        self.assertFalse((self.dst / ".rsync-partial" / "large").exists())

    def test_failed_publish_preserves_the_previous_file(self):
        from plugbackup.engine import executor
        source = self.src / "atomic"
        target = self.dst / "atomic"
        source.write_text("new")
        target.write_text("old")
        with mock.patch.object(executor.os, "replace", side_effect=OSError("publish failed")):
            result = execute(self.spec(compare="checksum"))
        self.assertEqual(result.rc, rsync.RC_PARTIAL)
        self.assertEqual(target.read_text(), "old")
        self.assertFalse((self.dst / ".rsync-partial" / "atomic").exists())

    def test_busy_versions_path_is_not_overwritten(self):
        source = self.src / "x"
        target = self.dst / "x"
        source.write_text("new")
        target.write_text("old")
        backup_dir = self.base / "drive" / ".versions" / "T" / "task"
        backup_dir.mkdir(parents=True)
        (backup_dir / "x").write_text("keep backup")
        result = execute(self.spec(mode="mirror_versions", compare="checksum", backup_dir=str(backup_dir)))
        self.assertEqual(result.rc, rsync.RC_PARTIAL)
        self.assertEqual(target.read_text(), "old")
        self.assertEqual((backup_dir / "x").read_text(), "keep backup")

    def test_casefold_rename_in_accumulate_keeps_distinct_old_name(self):
        (self.src / "README").write_text("new")
        (self.dst / "Readme").write_text("old")
        result = execute(self.spec(mode="accumulate", fs_type="ntfs3"))
        self.assertEqual(result.rc, rsync.RC_OK)
        self.assertEqual((self.dst / "README").read_text(), "new")
        self.assertEqual((self.dst / "Readme").read_text(), "old")

    def test_casefold_rename_in_versions_archives_distinct_old_name(self):
        (self.src / "README").write_text("new")
        (self.dst / "Readme").write_text("old")
        backup = self.base / "drive" / ".versions" / "T" / "task"
        backup.mkdir(parents=True)
        spec = self.spec(mode="mirror_versions", fs_type="ntfs3", backup_dir=str(backup))
        planned = plan(spec)
        result = execute(spec)
        self.assertEqual(planned.stats.deleted, 1)
        self.assertEqual(result.stats.deleted, 1)
        self.assertEqual((backup / "Readme").read_text(), "old")
        self.assertEqual((self.dst / "README").read_text(), "new")
        self.assertFalse((self.dst / "Readme").exists())

    def test_enospc_stops_execution_and_cleans_temporary_file(self):
        from plugbackup.engine import RC_NO_SPACE, executor
        first_source = self.src / "a"
        second_source = self.src / "b"
        first_target = self.dst / "a"
        first_source.write_bytes(b"new")
        second_source.write_bytes(b"later")
        first_target.write_bytes(b"old")
        real_open = open

        class FullWriter:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                self.stream.__enter__()
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def write(self, block):
                raise OSError(errno.ENOSPC, "disk full")

        def failing_open(path, mode="r", *args, **kwargs):
            stream = real_open(path, mode, *args, **kwargs)
            return FullWriter(stream) if "w" in mode else stream

        with mock.patch("builtins.open", side_effect=failing_open):
            result = execute(self.spec(compare="checksum"))
        self.assertEqual(result.rc, RC_NO_SPACE)
        self.assertIn("no space left on device", result.summary.other_messages)
        self.assertEqual(first_target.read_bytes(), b"old")
        self.assertFalse((self.dst / "b").exists())
        self.assertFalse((self.dst / ".rsync-partial" / "a").exists())

    def test_destination_reparse_directory_is_removed_with_rmdir(self):
        from plugbackup.engine import executor
        fake = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_file_attributes=0x400, st_reparse_tag=0)
        with mock.patch.object(executor.os, "lstat", return_value=fake), \
                mock.patch.object(executor.os, "rmdir") as rmdir, \
                mock.patch.object(executor.os, "unlink") as unlink, \
                mock.patch.object(executor.shutil, "rmtree") as rmtree:
            executor._remove_path(str(self.dst / "junction"))
        rmdir.assert_called_once()
        unlink.assert_not_called()
        rmtree.assert_not_called()

    def test_source_scan_error_suppresses_deletion_but_copies_readable_files(self):
        from plugbackup.engine import scan
        blocked = self.src / "blocked"
        blocked.mkdir()
        (blocked / "unreadable").write_text("hidden")
        (self.src / "readable").write_text("new")
        (self.dst / "stale").write_text("keep")
        real_scandir = os.scandir

        def fail_blocked(path):
            if os.path.abspath(os.fspath(path)) == str(blocked):
                raise PermissionError("permission denied")
            return real_scandir(path)

        with mock.patch.object(scan.os, "scandir", side_effect=fail_blocked):
            result = execute(self.spec())
        self.assertEqual(result.rc, rsync.RC_PARTIAL)
        self.assertIn("IO error encountered -- skipping file deletion", result.summary.other_messages)
        self.assertEqual((self.dst / "stale").read_text(), "keep")
        self.assertEqual((self.dst / "readable").read_text(), "new")

    def test_copy_target_dangling_directory_and_cycle(self):
        target_dir = self.base / "target"
        target_dir.mkdir()
        (target_dir / "inside").write_text("copied")
        (self.src / "dir-link").symlink_to(target_dir, target_is_directory=True)
        (self.src / "dangling").symlink_to(self.base / "absent")
        (self.src / "loop").symlink_to(self.src, target_is_directory=True)
        result = execute(self.spec(symlinks="copy_target"))
        self.assertEqual(result.rc, rsync.RC_PARTIAL)
        self.assertEqual((self.dst / "dir-link" / "inside").read_text(), "copied")
        self.assertFalse((self.dst / "dangling").exists())
        self.assertFalse((self.dst / "loop").exists())

    def test_nfd_names_are_preserved_and_casefold_matching_uses_nfc(self):
        nfd = "cafe\u0301.txt"
        nfc = "café.txt"
        (self.src / nfd).write_text("new")
        (self.dst / nfc).write_text("old")
        result = execute(self.spec(fs_type="ntfs3"))
        self.assertEqual(result.rc, rsync.RC_OK)
        self.assertTrue((self.dst / nfd).exists())
        self.assertFalse((self.dst / nfc).exists())

    def test_hard_limit_counts_type_replacements_in_both_mirror_modes(self):
        """В требованиях лимит включает смену типа; rsync 3.2.7 не показывает её удалением в itemize."""
        for mode in ("mirror", "mirror_versions"):
            with self.subTest(mode=mode):
                for name in ("first", "second"):
                    (self.dst / name).write_text("old")
                    (self.src / name).mkdir()
                    (self.src / name / "child").write_text("new")
                backup = self.base / mode / ".versions" / "T" / "task"
                backup.mkdir(parents=True)
                result = execute(self.spec(mode=mode, backup_dir=str(backup), hard_max_delete=1))
                self.assertEqual(result.rc, rsync.RC_MAX_DELETE)
                self.assertEqual(sum((self.dst / n).is_dir() for n in ("first", "second")), 1)
                for name in ("first", "second"):
                    (self.dst / name).unlink() if (self.dst / name).is_file() else None
                    if (self.dst / name).is_dir():
                        import shutil
                        shutil.rmtree(str(self.dst / name))
                    source = self.src / name
                    for child in source.iterdir(): child.unlink()
                    source.rmdir()

    def test_progress_formats_and_throttles_at_five_updates_per_second(self):
        now = [0.0]
        received = []
        tracker = ProgressTracker(received.append, 100, clock=lambda: now[0])
        for i in range(11):
            now[0] = i * 0.05
            tracker.update(i * 10, "x")
        self.assertLessEqual(len(received), 4)
        self.assertEqual(format_speed(12340000), "12.34MB/s")
        self.assertEqual(format_eta(83), "0:01:23")
        self.assertIn("percent", received[-1])

    def test_posix_mode_bits_are_copied(self):
        if os.name == "nt":
            self.skipTest("права POSIX проверяются на POSIX")
        source = self.src / "executable"
        target = self.dst / "executable"
        source.write_text("run")
        source.chmod(0o751)
        result = execute(self.spec())
        self.assertEqual(result.rc, rsync.RC_OK)
        self.assertEqual(target.stat().st_mode & 0o777, 0o751)

if __name__ == "__main__":
    unittest.main()
