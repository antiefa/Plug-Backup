from __future__ import annotations

import os
import stat
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from plugbackup.core import rsync
from plugbackup.core.profiles import Job
from plugbackup.engine import CopySpec, execute, list_source, plan, spec_from_job

@unittest.skipUnless(shutil.which("rsync"), "требуется rsync")
class RsyncDifferentialTests(unittest.TestCase):
    def _tree(self, root):
        out = {}
        for here, dirs, files in os.walk(root, followlinks=False):
            for name in dirs + files:
                p = Path(here) / name
                rel = p.relative_to(root).as_posix()
                if p.is_symlink():
                    out[rel] = ("l", os.readlink(str(p)))
                elif p.is_dir():
                    out[rel] = ("d", p.stat().st_mode & 0o7777, int(p.stat().st_mtime))
                else:
                    st = p.stat()
                    out[rel] = ("f", p.read_bytes(), int(st.st_mtime), st.st_mode & 0o7777)
        return out

    def _summary(self, text):
        summary = rsync.ItemizeSummary()
        for line in text.splitlines():
            rsync.parse_itemize_line(line, summary)
        return summary

    def _make_spec(self, job, source, dest, fs, timestamp, dry=False, **kwargs):
        options = rsync.CommandOptions(fs_type=fs, timestamp=timestamp, dry_run=dry, progress=False,
                                       hard_max_delete=kwargs.get("hard_max_delete", 5000),
                                       global_excludes=kwargs.get("global_excludes", []),
                                       extra_excludes=kwargs.get("extra_excludes", []))
        return spec_from_job(job, str(source), str(dest), options), options

    def _assert_step(self, source, left, right, job, fs, stamp, **kwargs):
        source_before, dest_before = self._tree(source), self._tree(left)
        has_type_transition = any(source_before[name][0] != dest_before[name][0]
                                  for name in source_before.keys() & dest_before.keys())
        spec, opts = self._make_spec(job, source, right, fs, stamp, **kwargs)
        listed = {(e.kind, e.size, e.path) for e in list_source(spec)}
        list_cmd = rsync.build_list_command(job, str(source), opts)
        listed_rsync = subprocess.run(list_cmd, capture_output=True, text=True, env=rsync.rsync_env())
        self.assertEqual(listed_rsync.returncode, 0, listed_rsync.stderr)
        self.assertEqual(listed, {(e.kind, e.size, e.path) for e in rsync.parse_list(listed_rsync.stdout)})

        dry_opts = rsync.CommandOptions(fs_type=fs, timestamp=stamp, dry_run=True, progress=False,
                                       hard_max_delete=opts.hard_max_delete,
                                       global_excludes=opts.global_excludes, extra_excludes=opts.extra_excludes)
        dry_cmd = rsync.build_command(job, str(source), str(left), dry_opts)
        dry = subprocess.run(dry_cmd, capture_output=True, text=True, env=rsync.rsync_env())
        planned = plan(spec)
        self.assertEqual(planned.rc, dry.returncode, dry.stderr)
        rsync_stats = rsync.parse_stats(dry.stdout)
        self.assertEqual(planned.stats.total_size, rsync_stats.total_size)
        self.assertEqual(planned.stats.transferred_size, rsync_stats.transferred_size)
        self.assertEqual(planned.stats.files_transferred, rsync_stats.files_transferred)
        rsummary = self._summary(dry.stdout)
        self.assertEqual(set(planned.summary.new_files), set(rsummary.new_files))
        self.assertEqual(set(planned.summary.changed_files), set(rsummary.changed_files))
        self.assertEqual(set(planned.summary.deleted_files), set(rsummary.deleted_files))

        engine_result = execute(spec)
        live_opts = rsync.CommandOptions(fs_type=fs, timestamp=stamp, progress=False,
                                         hard_max_delete=opts.hard_max_delete,
                                         global_excludes=opts.global_excludes, extra_excludes=opts.extra_excludes)
        live_cmd = rsync.build_command(job, str(source), str(left), live_opts)
        live = subprocess.run(live_cmd, capture_output=True, text=True, env=rsync.rsync_env())
        self.assertEqual(engine_result.rc, live.returncode, live.stderr)
        lsummary = self._summary(live.stdout)
        self.assertEqual(set(engine_result.summary.new_files), set(lsummary.new_files))
        self.assertEqual(set(engine_result.summary.changed_files), set(lsummary.changed_files))
        self.assertEqual(set(engine_result.summary.deleted_files), set(lsummary.deleted_files))
        if not has_type_transition:
            self.assertEqual(engine_result.stats.deleted, rsync_stats.deleted)
        self._assert_tree_equal(left, right, fs)
        if job.mode == "mirror_versions":
            rsync_backup = left.parent / ".versions" / stamp / job.dest
            engine_backup = right.parent / ".versions" / stamp / job.dest
            self._assert_tree_equal(rsync_backup, engine_backup, fs)

    def _assert_tree_equal(self, left, right, fs):
        lt, rt = self._tree(left), self._tree(right)
        self.assertEqual(set(lt), set(rt))
        for path in lt:
            a, b = lt[path], rt[path]
            self.assertEqual(a[0], b[0], path)
            if a[0] == "l":
                self.assertEqual(a[1], b[1], path)
            elif a[0] == "d":
                if fs in rsync.POSIX_FS:
                    self.assertEqual(a[1], b[1], path)
                self.assertLessEqual(abs(a[2] - b[2]), 1 if fs in rsync.FAT_LIKE else 0, path)
            if a[0] == "f":
                self.assertEqual(a[1], b[1], path)
                if fs in rsync.POSIX_FS:
                    self.assertEqual(a[3], b[3], path)
                window = 1 if fs in rsync.FAT_LIKE else 0
                self.assertLessEqual(abs(a[2] - b[2]), window, path)

    def test_first_run_repeat_and_plan_across_modes_and_filesystem_options(self):
        for mode in ("mirror", "mirror_versions", "accumulate"):
            for fs in ("ext4", "vfat", "exfat", "ntfs3"):
                with self.subTest(mode=mode, fs=fs), tempfile.TemporaryDirectory() as td:
                    base = Path(td)
                    source, root = base / "source", base / "drive" / "PlugBackup"
                    source.mkdir(); root.mkdir(parents=True)
                    (source / "nested").mkdir()
                    (source / "nested" / "hello world.txt").write_text("payload", encoding="utf-8")
                    (source / "empty").write_bytes(b"")
                    for entry in (source, source / "nested", source / "nested" / "hello world.txt", source / "empty"):
                        os.utime(entry, (1700000000, 1700000000))
                    left, right = root / "left", root / "right"
                    left.mkdir(); right.mkdir()
                    job = Job(source=str(source), dest="right", mode=mode, use_global_excludes=False,
                              one_file_system=False)
                    opts = rsync.CommandOptions(fs_type=fs, timestamp="2030-01-02T03-04-05Z", progress=False)
                    spec = spec_from_job(job, str(source), str(right), opts)

                    dry = rsync.build_command(job, str(source), str(left), rsync.CommandOptions(
                        fs_type=fs, timestamp="2030-01-02T03-04-05Z", dry_run=True, progress=False))
                    cp = subprocess.run(dry, capture_output=True, text=True, env=rsync.rsync_env())
                    self.assertEqual(cp.returncode, 0, cp.stderr)
                    engine_plan = plan(spec)
                    self.assertEqual(engine_plan.summary.new_files, self._summary(cp.stdout).new_files)
                    self.assertEqual(self._tree(left), {})
                    self.assertEqual(execute(spec).rc, 0)
                    live = rsync.build_command(job, str(source), str(left), rsync.CommandOptions(
                        fs_type=fs, timestamp="2030-01-02T03-04-05Z", progress=False))
                    cp = subprocess.run(live, capture_output=True, text=True, env=rsync.rsync_env())
                    self.assertEqual(cp.returncode, 0, cp.stderr)
                    self.assertEqual(self._tree(left), self._tree(right))
                    self.assertEqual(engine_plan.summary.new_files, self._summary(cp.stdout).new_files)

                    # Повтор без изменений: оба процесса должны считать файлы уже актуальными.
                    again_engine = plan(spec)
                    cp = subprocess.run(rsync.build_command(job, str(source), str(left), rsync.CommandOptions(
                        fs_type=fs, timestamp="2030-01-02T03-04-06Z", dry_run=True, progress=False)),
                        capture_output=True, text=True, env=rsync.rsync_env())
                    self.assertEqual(cp.returncode, 0, cp.stderr)
                    self.assertEqual(again_engine.summary.new_files, self._summary(cp.stdout).new_files)
                    self.assertEqual(again_engine.summary.changed_files, self._summary(cp.stdout).changed_files)

    def test_delete_rename_type_changes_and_exclusion_protection(self):
        """Сверяет три фазы синхронизации для удалений, переименований и исключённой папки."""
        for mode in ("mirror", "mirror_versions", "accumulate"):
            for fs in ("ext4", "vfat", "exfat", "ntfs3"):
                with self.subTest(mode=mode, fs=fs), tempfile.TemporaryDirectory() as td:
                    base = Path(td)
                    source = base / "source"
                    root = base / "drive" / "PlugBackup"
                    source.mkdir(); root.mkdir(parents=True)
                    left, right = root / "left", root / "right"
                    left.mkdir(); right.mkdir()
                    (source / "rename-old").write_text("first", encoding="utf-8")
                    (source / "type").write_text("file", encoding="utf-8")
                    (source / "cache").mkdir()
                    (source / "cache" / "keep.tmp").write_text("source hidden", encoding="utf-8")
                    (source / "protected-parent").mkdir()
                    for dest in (left, right):
                        (dest / "protected-parent").mkdir()
                        (dest / "protected-parent" / "secret.tmp").write_text("keep")
                        (dest / "protected-parent" / "remove").write_text("delete")
                        (dest / "only-excluded").mkdir()
                        (dest / "only-excluded" / "secret.tmp").write_text("keep")
                    job = Job(source=str(source), dest="right", mode=mode, use_global_excludes=False,
                              one_file_system=False, excludes=["cache/", "*.tmp"])
                    kw = {"global_excludes": []}
                    self._assert_step(source, left, right, job, fs, "2031-01-01T00-00-01Z", **kw)
                    (source / "rename-old").rename(source / "rename-new")
                    (source / "type").unlink()
                    (source / "type").mkdir()
                    (source / "type" / "child").write_text("directory", encoding="utf-8")
                    (source / "new-empty").mkdir()
                    self._assert_step(source, left, right, job, fs, "2031-01-01T00-00-02Z", **kw)
                    self._assert_step(source, left, right, job, fs, "2031-01-01T00-00-02Z", **kw)
                    (source / "rename-new").unlink()
                    self._assert_step(source, left, right, job, fs, "2031-01-01T00-00-03Z", **kw)
                    shutil.rmtree(str(source / "type"))
                    (source / "type").write_text("back to file", encoding="utf-8")
                    self._assert_step(source, left, right, job, fs, "2031-01-01T00-00-04Z", **kw)

    def test_hard_delete_limit_deletes_same_count_as_rsync(self):
        """Проверяет фактический предел удалений в обоих зеркальных режимах."""
        for mode in ("mirror", "mirror_versions"):
            for fs in ("ext4", "vfat", "exfat", "ntfs3"):
                with self.subTest(mode=mode, fs=fs), tempfile.TemporaryDirectory() as td:
                    base = Path(td); source = base / "source"; root = base / "drive" / "PlugBackup"
                    source.mkdir(); root.mkdir(parents=True)
                    left, right = root / "left", root / "right"
                    left.mkdir(); right.mkdir()
                    for name in ("a", "b", "c"):
                        (left / name).write_text(name)
                        (right / name).write_text(name)
                    job = Job(source=str(source), dest="right", mode=mode, use_global_excludes=False,
                              one_file_system=False)
                    for stamp in ("2032-02-02T00-00-01Z", "2032-02-02T00-00-02Z",
                                  "2032-02-02T00-00-03Z"):
                        self._assert_step(source, left, right, job, fs, stamp, hard_max_delete=1)

    def test_checksum_mtime_windows_and_special_names(self):
        """Сверяет checksum, временные окна и имена, содержащие пробелы и метасимволы."""
        cases = [("ext4", "checksum"), ("vfat", "size_mtime"), ("exfat", "size_mtime"),
                 ("ext4", "size_mtime")]
        for fs, compare in cases:
            with self.subTest(fs=fs, compare=compare), tempfile.TemporaryDirectory() as td:
                base = Path(td); source = base / "source"; root = base / "drive" / "PlugBackup"
                source.mkdir(); root.mkdir(parents=True)
                left, right = root / "left", root / "right"
                left.mkdir(); right.mkdir()
                for name in ("space name", "юникод-é", "nfd-cafe\u0301", "[square]", "star*", "question?",
                             "back\\slash", "-leading"):
                    (source / name).write_bytes(b"new")
                    (left / name).write_bytes(b"old")
                    (right / name).write_bytes(b"old")
                    os.utime(source / name, (1700000002, 1700000002))
                    os.utime(left / name, (1700000001, 1700000001))
                    os.utime(right / name, (1700000001, 1700000001))
                job = Job(source=str(source), dest="right", mode="mirror", compare=compare,
                          use_global_excludes=False, one_file_system=False)
                self._assert_step(source, left, right, job, fs, "2032-02-02T00-00-02Z")
                self._assert_step(source, left, right, job, fs, "2032-02-02T00-00-02Z")

    def test_mirror_versions_move_deleted_files_and_compare_basket(self):
        """Проверяет, что удалённые и заменённые файлы попадают в одинаковую корзину."""
        for fs in ("ext4", "vfat", "exfat", "ntfs3"):
            with self.subTest(fs=fs), tempfile.TemporaryDirectory() as td:
                base = Path(td); source = base / "source"; root = base / "drive" / "PlugBackup"
                source.mkdir(); root.mkdir(parents=True)
                left, right = root / "left", root / "right"
                left.mkdir(); right.mkdir()
                (source / "change").write_text("new")
                (left / "change").write_text("old")
                (right / "change").write_text("old")
                (left / "removed").write_text("removed")
                (right / "removed").write_text("removed")
                job = Job(source=str(source), dest="right", mode="mirror_versions", compare="checksum",
                          use_global_excludes=False, one_file_system=False)
                self._assert_step(source, left, right, job, fs, "2032-02-02T00-00-03Z")
                self._assert_step(source, left, right, job, fs, "2032-02-02T00-00-03Z")

    def test_checksum_content_and_time_only_changes(self):
        """Равный размер с разным содержимым передаётся, а одинаковые данные получают только mtime."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); source = base / "source"; root = base / "drive" / "PlugBackup"
            source.mkdir(); root.mkdir(parents=True)
            left, right = root / "left", root / "right"
            left.mkdir(); right.mkdir()
            (source / "same").write_bytes(b"new")
            for dest in (left, right):
                (dest / "same").write_bytes(b"old")
                os.utime(dest / "same", (1700000000, 1700000000))
            os.utime(source / "same", (1700000000, 1700000000))
            job = Job(source=str(source), dest="right", mode="mirror", compare="checksum",
                      use_global_excludes=False, one_file_system=False)
            self._assert_step(source, left, right, job, "ext4", "2032-02-02T00-00-06Z")
            os.utime(source / "same", (1700000010, 1700000010))
            self._assert_step(source, left, right, job, "ext4", "2032-02-02T00-00-07Z")
            self._assert_step(source, left, right, job, "ext4", "2032-02-02T00-00-07Z")

    def test_update_only_does_not_replace_a_newer_destination(self):
        """Поведение обновления должно совпадать с rsync --update."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source, dest = base / "source", base / "dest"
            source.mkdir()
            dest.mkdir()
            source_file, dest_file = source / "newer", dest / "newer"
            source_file.write_text("source")
            dest_file.write_text("destination")
            os.utime(source_file, (100, 100))
            os.utime(dest_file, (200, 200))
            spec = CopySpec(str(source), str(dest), "accumulate", "size_mtime", "skip", "ext4",
                            one_file_system=False, update_only=True)
            command = ["rsync", "--recursive", "--times", "--itemize-changes", "--no-human-readable",
                       "--update", "--dry-run", "--stats", str(source) + "/", str(dest) + "/"]
            checked = subprocess.run(command, capture_output=True, text=True, env=rsync.rsync_env())
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual(plan(spec).summary.changed_files, self._summary(checked.stdout).changed_files)
            self.assertEqual(execute(spec).rc, 0)
            self.assertEqual(dest_file.read_text(), "destination")

    def test_delete_excluded_plan_matches_rsync(self):
        """План удаляет скрытые фильтром записи только при delete_excluded."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source, dest = base / "source", base / "dest"
            source.mkdir()
            dest.mkdir()
            (source / "keep.txt").write_text("source")
            (dest / "hidden.tmp").write_text("stale")
            job = Job(source=str(source), dest="dest", mode="mirror", excludes=["*.tmp"],
                      use_global_excludes=False, one_file_system=False)
            spec = CopySpec(str(source), str(dest), "mirror", "size_mtime", "skip", "ext4",
                            excludes=["*.tmp"], one_file_system=False, delete_excluded=True)
            command = ["rsync", "--recursive", "--times", "--delete", "--delete-excluded", "--dry-run",
                       "--itemize-changes", "--no-human-readable", "--exclude=*.tmp",
                       str(source) + "/", str(dest) + "/"]
            checked = subprocess.run(command, capture_output=True, text=True, env=rsync.rsync_env())
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual(set(plan(spec).summary.deleted_files),
                             set(self._summary(checked.stdout).deleted_files))

    def test_link_folder_type_replacements_all_modes(self):
        """Проверяет ссылку↔каталог переходы и версионные корзины во всех режимах."""
        for mode in ("mirror", "mirror_versions", "accumulate"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as td:
                base = Path(td); source = base / "source"; root = base / "drive" / "PlugBackup"
                source.mkdir(); root.mkdir(parents=True)
                left, right = root / "left", root / "right"
                left.mkdir(); right.mkdir()
                referent = source / "referent"
                referent.write_text("value")
                (source / "object").symlink_to(referent)
                job = Job(source=str(source), dest="right", mode=mode, symlinks="auto",
                          use_global_excludes=False, one_file_system=False)
                self._assert_step(source, left, right, job, "ext4", "2033-03-03T00-00-03Z")
                (source / "object").unlink()
                (source / "object").mkdir()
                (source / "object" / "child").write_text("new")
                self._assert_step(source, left, right, job, "ext4", "2033-03-03T00-00-04Z")
                shutil.rmtree(str(source / "object"))
                (source / "object").symlink_to(referent)
                self._assert_step(source, left, right, job, "ext4", "2033-03-03T00-00-05Z")
                self._assert_step(source, left, right, job, "ext4", "2033-03-03T00-00-05Z")

    def test_max_size_preserves_stale_destination(self):
        """Файл выше лимита остаётся в назначении, как при `--max-size`."""
        for mode in ("mirror", "mirror_versions", "accumulate"):
            for fs in ("ext4", "vfat", "exfat", "ntfs3"):
                with self.subTest(mode=mode, fs=fs), tempfile.TemporaryDirectory() as td:
                    base = Path(td); source = base / "source"; root = base / "drive" / "PlugBackup"
                    source.mkdir(); root.mkdir(parents=True)
                    left, right = root / "left", root / "right"
                    left.mkdir(); right.mkdir()
                    (source / "large").write_bytes(b"s" * (1024 * 1024 + 1))
                    (left / "large").write_text("old")
                    (right / "large").write_text("old")
                    job = Job(source=str(source), dest="right", mode=mode, max_size_mb=1,
                              use_global_excludes=False, one_file_system=False)
                    self._assert_step(source, left, right, job, fs, "2032-02-02T00-00-04Z")
                    self._assert_step(source, left, right, job, fs, "2032-02-02T00-00-04Z")

    def test_posix_permissions_match_rsync(self):
        """Проверяет перенос POSIX mode bits для файлов и каталогов на POSIX-ФС."""
        if os.name == "nt":
            self.skipTest("для сравнения POSIX-прав требуется POSIX-система")
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); source = base / "source"; root = base / "drive" / "PlugBackup"
            source.mkdir(); root.mkdir(parents=True)
            left, right = root / "left", root / "right"
            left.mkdir(); right.mkdir()
            for path in (source / "exec",):
                path.write_text("run"); path.chmod(0o751)
                os.utime(path, (1700000000, 1700000000))
            (source / "folder").mkdir(); (source / "folder").chmod(0o750)
            (source / "folder" / "child").write_text("x")
            (left / "exec").write_text("run"); (left / "exec").chmod(0o600)
            (right / "exec").write_text("run"); (right / "exec").chmod(0o600)
            os.utime(left / "exec", (1700000000, 1700000000)); os.utime(right / "exec", (1700000000, 1700000000))
            (left / "folder").mkdir(); (right / "folder").mkdir()
            (left / "folder" / "child").write_text("x"); (right / "folder" / "child").write_text("x")
            (left / "folder").chmod(0o700); (right / "folder").chmod(0o700)
            job = Job(source=str(source), dest="right", mode="mirror", use_global_excludes=False,
                      one_file_system=False)
            self._assert_step(source, left, right, job, "ext4", "2032-02-02T00-00-05Z")
            self._assert_step(source, left, right, job, "ext4", "2032-02-02T00-00-05Z")

    def test_type_replacement_budget_is_an_intentional_difference(self):
        """Задача требует учитывать замену типа в hard_max_delete; rsync не считает file→dir удалением."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); source = base / "source"; root = base / "drive" / "PlugBackup"
            source.mkdir(); root.mkdir(parents=True)
            left, right = root / "left", root / "right"
            left.mkdir(); right.mkdir()
            for name in ("a", "b"):
                (left / name).write_text("old")
                (right / name).write_text("old")
                (source / name).mkdir()
                (source / name / "child").write_text("new")
            job = Job(source=str(source), dest="right", mode="mirror", use_global_excludes=False,
                      one_file_system=False)
            opts = rsync.CommandOptions(fs_type="ext4", dry_run=True, progress=False, hard_max_delete=1)
            dry = subprocess.run(rsync.build_command(job, str(source), str(left), opts), capture_output=True,
                                 text=True, env=rsync.rsync_env())
            spec, _ = self._make_spec(job, source, right, "ext4", "", hard_max_delete=1)
            self.assertEqual(dry.returncode, rsync.RC_OK)
            self.assertEqual(plan(spec).rc, rsync.RC_MAX_DELETE)

    def test_casefold_name_matching_is_an_intentional_difference(self):
        """На регистронезависимой ФС движок считает смену регистра обновлением; rsync на Linux видит delete+new."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); source = base / "source"; root = base / "drive" / "PlugBackup"
            source.mkdir(); root.mkdir(parents=True)
            left, right = root / "left", root / "right"
            left.mkdir(); right.mkdir()
            (source / "README").write_text("new")
            for dest in (left, right):
                (dest / "Readme").write_text("old")
                os.utime(dest / "Readme", (1700000000, 1700000000))
            os.utime(source / "README", (1700000002, 1700000002))
            job = Job(source=str(source), dest="right", mode="mirror", use_global_excludes=False,
                      one_file_system=False)
            spec, _ = self._make_spec(job, source, right, "ntfs3", "")
            engine_plan = plan(spec)
            dry = subprocess.run(rsync.build_command(job, str(source), str(left), rsync.CommandOptions(
                fs_type="ntfs3", dry_run=True, progress=False)), capture_output=True, text=True,
                env=rsync.rsync_env())
            parsed = self._summary(dry.stdout)
            self.assertEqual(engine_plan.summary.changed_files, ["README"])
            self.assertEqual(parsed.new_files, ["README"])
            self.assertEqual(parsed.deleted_files, ["Readme"])
            engine_result = execute(spec)
            live = subprocess.run(rsync.build_command(job, str(source), str(left), rsync.CommandOptions(
                fs_type="ntfs3", progress=False)), capture_output=True, text=True, env=rsync.rsync_env())
            self.assertEqual(engine_result.rc, live.returncode)
            self.assertEqual((left / "README").read_text(), (right / "README").read_text())
            self.assertFalse((left / "Readme").exists())
            self.assertFalse((right / "Readme").exists())

    def test_nfc_nfd_name_matching_is_an_intentional_difference(self):
        """NFC-сопоставление нужно целевым macOS/Windows-ФС, а Linux rsync считает NFC и NFD разными именами."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); source = base / "source"; root = base / "drive" / "PlugBackup"
            source.mkdir(); root.mkdir(parents=True)
            left, right = root / "left", root / "right"
            left.mkdir(); right.mkdir()
            nfd, nfc = "cafe\u0301.txt", "café.txt"
            (source / nfd).write_text("new")
            for dest in (left, right):
                (dest / nfc).write_text("old")
                os.utime(dest / nfc, (1700000000, 1700000000))
            os.utime(source / nfd, (1700000002, 1700000002))
            job = Job(source=str(source), dest="right", mode="mirror", use_global_excludes=False,
                      one_file_system=False)
            spec, _ = self._make_spec(job, source, right, "ntfs3", "")
            engine_plan = plan(spec)
            dry = subprocess.run(rsync.build_command(job, str(source), str(left), rsync.CommandOptions(
                fs_type="ntfs3", dry_run=True, progress=False)), capture_output=True, text=True,
                env=rsync.rsync_env())
            parsed = self._summary(dry.stdout)
            self.assertEqual(engine_plan.summary.changed_files, [nfd])
            self.assertEqual(parsed.new_files, [nfd])
            self.assertEqual(parsed.deleted_files, [nfc])
            execute(spec)
            subprocess.run(rsync.build_command(job, str(source), str(left), rsync.CommandOptions(
                fs_type="ntfs3", progress=False)), check=True, capture_output=True, text=True,
                env=rsync.rsync_env())
            self.assertEqual((left / nfd).read_text(), (right / nfd).read_text())

    def test_symlink_modes_match_rsync_across_modes_and_filesystem_options(self):
        """Сверяет auto, skip и copy_target для файловых и каталоговых ссылок."""
        for mode in ("mirror", "mirror_versions", "accumulate"):
            for fs in ("ext4", "vfat", "exfat", "ntfs3"):
                for symlinks in ("auto", "skip", "copy_target"):
                    with self.subTest(mode=mode, fs=fs, symlinks=symlinks), tempfile.TemporaryDirectory() as td:
                        base = Path(td); source = base / "source"; root = base / "drive" / "PlugBackup"
                        source.mkdir(); root.mkdir(parents=True)
                        left, right = root / "left", root / "right"
                        left.mkdir(); right.mkdir()
                        (source / "target-file").write_text("one")
                        (source / "target-dir").mkdir()
                        (source / "target-dir" / "child").write_text("directory")
                        try:
                            (source / "link-file").symlink_to(source / "target-file")
                            (source / "link-dir").symlink_to(source / "target-dir", target_is_directory=True)
                        except (OSError, NotImplementedError) as exc:
                            self.skipTest("символические ссылки недоступны: " + str(exc))
                        job = Job(source=str(source), dest="right", mode=mode, symlinks=symlinks,
                                  use_global_excludes=False, one_file_system=False)
                        self._assert_step(source, left, right, job, fs, "2033-03-03T00-00-01Z")
                        (source / "target-file").write_text("two")
                        self._assert_step(source, left, right, job, fs, "2033-03-03T00-00-02Z")
                        self._assert_step(source, left, right, job, fs, "2033-03-03T00-00-02Z")

    def test_copy_target_cycle_error_matches_rsync(self):
        """Циклический copy-target пропускается; rsync завершает такой проход с частичной ошибкой."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); source = base / "source"; root = base / "drive" / "PlugBackup"
            source.mkdir(); root.mkdir(parents=True)
            left, right = root / "left", root / "right"
            left.mkdir(); right.mkdir()
            (source / "loop").symlink_to(source, target_is_directory=True)
            job = Job(source=str(source), dest="right", mode="mirror", symlinks="copy_target",
                      use_global_excludes=False, one_file_system=False)
            spec, _ = self._make_spec(job, source, right, "ext4", "")
            engine_result = execute(spec)
            cmd = rsync.build_command(job, str(source), str(left), rsync.CommandOptions(fs_type="ext4", progress=False))
            actual = subprocess.run(cmd, capture_output=True, text=True, env=rsync.rsync_env())
            self.assertEqual(engine_result.rc, rsync.RC_PARTIAL)
            self.assertNotEqual(actual.returncode, rsync.RC_OK)

    def test_source_read_error_disables_deletion_in_both_engines(self):
        """Ошибка scandir источника должна сохранить лишние файлы, как rsync."""
        if getattr(os, "geteuid", lambda: 0)() == 0:
            self.skipTest("permission test is not meaningful when run as root")
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); source = base / "source"; root = base / "drive" / "PlugBackup"
            source.mkdir(); root.mkdir(parents=True)
            blocked = source / "blocked"
            blocked.mkdir()
            (blocked / "unreadable").write_text("hidden from traversal")
            left, right = root / "left", root / "right"
            left.mkdir(); right.mkdir()
            for dest in (left, right):
                (dest / "blocked").mkdir()
                (dest / "blocked" / "stale").write_text("must remain")
            job = Job(source=str(source), dest="right", mode="mirror", use_global_excludes=False,
                      one_file_system=False)
            try:
                blocked.chmod(0)
                spec, _ = self._make_spec(job, source, right, "ext4", "")
                dry = subprocess.run(rsync.build_command(job, str(source), str(left), rsync.CommandOptions(
                    fs_type="ext4", dry_run=True, progress=False)), capture_output=True, text=True,
                    env=rsync.rsync_env())
                planned = plan(spec)
                self.assertEqual(dry.returncode, rsync.RC_PARTIAL)
                self.assertEqual(planned.rc, rsync.RC_PARTIAL)
                self.assertIn("IO error encountered -- skipping file deletion", planned.summary.other_messages)
                executed = execute(spec)
                actual = subprocess.run(rsync.build_command(job, str(source), str(left), rsync.CommandOptions(
                    fs_type="ext4", progress=False)), capture_output=True, text=True,
                    env=rsync.rsync_env())
                self.assertEqual(executed.rc, rsync.RC_PARTIAL)
                self.assertEqual(actual.returncode, rsync.RC_PARTIAL, actual.stderr)
                (left / "blocked").chmod(0o755)
                self.assertEqual((right / "blocked" / "stale").read_text(), "must remain")
                self.assertEqual((left / "blocked" / "stale").read_text(), "must remain")
            finally:
                blocked.chmod(stat.S_IRWXU)

if __name__ == "__main__":
    unittest.main()
