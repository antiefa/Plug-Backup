"""Восстановление; всё — во временных каталогах."""

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import unicodedata
from unittest import mock

from plugbackup.core import restore
from plugbackup.core.journal import CANCELLED, ERROR, OK
from plugbackup.core.profiles import Job


@unittest.skipUnless(shutil.which("rsync"), "нужен установленный rsync")
class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="plugbackup-restore-")
        self.addCleanup(self.tmp.cleanup)
        self.base = self.tmp.name
        self.state = os.path.join(self.base, "state")
        self.data = os.path.join(self.base, "data")
        self.env_patch = mock.patch.dict(os.environ, {
            "XDG_STATE_HOME": self.state,
            "XDG_DATA_HOME": self.data,
        })
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def path(self, *parts):
        return os.path.join(self.base, *parts)

    @staticmethod
    def put(path, content, mtime=None):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as stream:
            stream.write(content if isinstance(content, bytes) else content.encode("utf-8"))
        if mtime is not None:
            os.utime(path, (mtime, mtime))

    @staticmethod
    def read(path):
        with open(path, "rb") as stream:
            return stream.read()

    def item(self, copy_dir, target_dir, dest="Работа/Документы", job_id="job"):
        return restore.RestoreItem(Job(id=job_id, source="unused", dest=dest), copy_dir, target_dir)

    def run_restorer(self, items, **kwargs):
        runner = restore.Restorer("profile", "Профиль", items, "ext4", **kwargs)
        record = runner.run()
        return runner, record

    def test_command_is_copy_only_and_excludes_partial_directories_at_any_depth(self):
        """Проверяет формат команды rsync; выполнение восстановления проверяется отдельно."""
        item = self.item(self.path("copy"), self.path("target"))
        command = restore.build_command(item, "ext4", None, False, skip_newer=False)
        self.assertNotIn("--delete", command)
        self.assertFalse(any(arg.startswith("--delete=") for arg in command))
        self.assertNotIn("--update", command)
        self.assertIn("--exclude=/.rsync-partial/", command)
        self.assertIn("--exclude=.rsync-partial/", command)
        self.assertEqual(command[-2:], [item.copy_dir + "/", item.target_dir + "/"])
        self.assertIn("--update", restore.build_command(item, "ext4", None, True, skip_newer=True))

    def test_restore_preserves_pc_only_files_and_copies_nested_unicode_names(self):
        copy_dir, target = self.path("copy"), self.path("target")
        nfd_name = unicodedata.normalize("NFD", "é"
        ) + " заметки.txt"
        self.put(os.path.join(copy_dir, "папка с пробелом", "вложено", "кириллица.txt"), "данные")
        self.put(os.path.join(copy_dir, "папка с пробелом", nfd_name), "NFD")
        self.put(os.path.join(copy_dir, ".rsync-partial", "top-secret"), "служебное")
        self.put(os.path.join(copy_dir, "уровень", ".rsync-partial", "partial"), "служебное")
        self.put(os.path.join(copy_dir, "уровень", "глубже", ".rsync-partial", "partial"), "служебное")
        self.put(os.path.join(target, "только-на-ПК.txt"), "не удалять")
        _, record = self.run_restorer([self.item(copy_dir, target)], keep_overwritten=False)
        self.assertEqual(record.jobs[0].status, OK)
        self.assertEqual(self.read(os.path.join(target, "папка с пробелом", "вложено", "кириллица.txt")), b"\xd0\xb4\xd0\xb0\xd0\xbd\xd0\xbd\xd1\x8b\xd0\xb5")
        self.assertEqual(self.read(os.path.join(target, "папка с пробелом", nfd_name)), b"NFD")
        self.assertEqual(self.read(os.path.join(target, "только-на-ПК.txt")), "не удалять".encode())
        self.assertFalse(any(".rsync-partial" in root for root, _, _ in os.walk(target)))

    def test_skip_newer_controls_replacement_and_new_target_is_created(self):
        copy_dir, target = self.path("copy"), self.path("new-parent", "other target")
        os.makedirs(copy_dir)
        self.put(os.path.join(copy_dir, "fresh.txt"), "из копии", mtime=1_600_000_000)
        _, record = self.run_restorer([self.item(copy_dir, target)], keep_overwritten=False, skip_newer=True)
        self.assertEqual(record.jobs[0].status, OK)
        self.assertTrue(os.path.isdir(target))
        self.assertEqual(self.read(os.path.join(target, "fresh.txt")), "из копии".encode())

        newer_copy, newer_target = self.path("copy-newer"), self.path("existing")
        self.put(os.path.join(newer_copy, "same.txt"), "старая копия", mtime=1_600_000_000)
        self.put(os.path.join(newer_target, "same.txt"), "новее на ПК", mtime=1_700_000_000)
        _, skipped = self.run_restorer([self.item(newer_copy, newer_target)], keep_overwritten=False,
                                       skip_newer=True)
        self.assertEqual(skipped.jobs[0].status, OK)
        self.assertEqual(self.read(os.path.join(newer_target, "same.txt")), "новее на ПК".encode())

        _, replaced = self.run_restorer([self.item(newer_copy, newer_target)], keep_overwritten=False,
                                        skip_newer=False)
        self.assertEqual(replaced.jobs[0].status, OK)
        self.assertEqual(self.read(os.path.join(newer_target, "same.txt")), "старая копия".encode())

    def test_keep_overwritten_saves_prior_contents_and_readme(self):
        copy_dir, target = self.path("copy"), self.path("target")
        self.put(os.path.join(copy_dir, "вложенная папка", "файл.txt"), "копия")
        self.put(os.path.join(target, "вложенная папка", "файл.txt"), "прежняя версия")
        item = self.item(copy_dir, target, dest="Работа/Документы")
        runner, record = self.run_restorer([item], keep_overwritten=True, skip_newer=False)
        backup_dir = os.path.join(runner.backup_root, "Работа", "Документы")
        self.assertEqual(record.jobs[0].status, OK)
        self.assertEqual(self.read(os.path.join(target, "вложенная папка", "файл.txt")), "копия".encode())
        self.assertEqual(self.read(os.path.join(backup_dir, "вложенная папка", "файл.txt")), "прежняя версия".encode())
        readme = self.read(os.path.join(backup_dir, "README.txt")).decode("utf-8")
        self.assertIn(item.copy_dir, readme)
        self.assertIn(item.target_dir, readme)
        self.assertIn("Plug & Backup", readme)   # текст — на языке интерфейса
        self.assertEqual(record.jobs[0].message, backup_dir)

    def test_keep_overwritten_removes_empty_deferred_folder(self):
        copy_dir, target = self.path("copy"), self.path("target")
        # Исправлено при приёмке: на ПК файл НОВЕЕ копии — --update его пропускает, перезаписи нет.
        self.put(os.path.join(copy_dir, "same.txt"), "старая копия", mtime=1_600_000_000)
        self.put(os.path.join(target, "same.txt"), "локальная версия", mtime=1_700_000_000)
        runner, record = self.run_restorer([self.item(copy_dir, target)], keep_overwritten=True,
                                           skip_newer=True)
        self.assertEqual(record.jobs[0].status, OK)
        self.assertEqual(self.read(os.path.join(target, "same.txt")), "локальная версия".encode())
        self.assertFalse(os.path.exists(runner.backup_root))

    def test_plan_matches_real_run_for_same_parameters(self):
        copy_dir, target = self.path("copy"), self.path("target")
        self.put(os.path.join(copy_dir, "new.txt"), "новый")
        self.put(os.path.join(copy_dir, "over.txt"), "из копии", mtime=1_600_000_000)
        self.put(os.path.join(copy_dir, "skip.txt"), "из копии", mtime=1_600_000_000)
        self.put(os.path.join(target, "over.txt"), "старое", mtime=1_500_000_000)
        self.put(os.path.join(target, "skip.txt"), "новее", mtime=1_700_000_000)
        item = self.item(copy_dir, target)
        planned = restore.plan(item, "ext4", skip_newer=True)
        _, record = self.run_restorer([item], keep_overwritten=False, skip_newer=True)
        self.assertEqual(record.jobs[0].status, OK)
        self.assertCountEqual(planned.new_files, ["new.txt"])
        self.assertCountEqual(planned.overwritten, ["over.txt"])
        self.assertEqual(record.jobs[0].copied_files, len(planned.new_files) + len(planned.overwritten))
        self.assertEqual(self.read(os.path.join(target, "skip.txt")), b"\xd0\xbd\xd0\xbe\xd0\xb2\xd0\xb5\xd0\xb5")

    def test_missing_copy_is_job_error_and_later_jobs_still_run(self):
        missing, source, target = self.path("missing-copy"), self.path("valid-copy"), self.path("target")
        self.put(os.path.join(source, "file.txt"), "успешно")
        _, record = self.run_restorer([
            self.item(missing, self.path("unused-target"), job_id="missing"),
            self.item(source, target, job_id="valid"),
        ], keep_overwritten=False)
        self.assertEqual([(job.job_id, job.status, job.reason) for job in record.jobs],
                         [("missing", ERROR, "no-copy"), ("valid", OK, "")])
        self.assertEqual(self.read(os.path.join(target, "file.txt")), "успешно".encode())

    def test_cancel_before_run_is_journaled_as_restore(self):
        source, target = self.path("copy"), self.path("target")
        self.put(os.path.join(source, "file.txt"), "data")
        runner = restore.Restorer("profile", "Профиль", [self.item(source, target)], "ext4", False)
        runner.cancel()
        record = runner.run()
        self.assertEqual(record.status, CANCELLED)
        self.assertEqual(record.jobs[0].status, CANCELLED)
        self.assertEqual(record.trigger, "restore")
        with open(os.path.join(self.state, "plugbackup", "log", "runs.jsonl"), encoding="utf-8") as stream:
            rows = [json.loads(line) for line in stream]
        self.assertEqual(rows[-1]["trigger"], "restore")
        self.assertEqual(rows[-1]["jobs"][0]["status"], CANCELLED)
        self.assertTrue(os.path.isfile(record.log_path))

    def test_cancel_during_real_rsync_transfer_is_journaled(self):
        source, target = self.path("copy"), self.path("target")
        self.put(os.path.join(source, "large.bin"), b"x" * (32 * 1024 * 1024))
        entered_progress = threading.Event()
        runner = None

        def on_progress(info):
            entered_progress.set()
            runner.cancel()

        runner = restore.Restorer("profile", "Профиль", [self.item(source, target)], "ext4", False,
                                  progress=on_progress)
        result_box = []
        thread = threading.Thread(target=lambda: result_box.append(runner.run()))
        thread.start()
        thread.join(20)
        self.assertFalse(thread.is_alive(), "восстановление не завершилось после cancel()")
        self.assertTrue(entered_progress.is_set(), "rsync не сообщил прогресс передачи для проверки cancel()")
        record = result_box[0]
        self.assertEqual(record.status, CANCELLED)
        self.assertEqual(record.jobs[0].status, CANCELLED)
        self.assertEqual(record.trigger, "restore")
        self.assertTrue(os.path.isfile(record.log_path))


class RestorePathHelpers(unittest.TestCase):
    def test_backup_root_uses_xdg_data_home_and_default_target_only_returns_path(self):
        with tempfile.TemporaryDirectory(prefix="plugbackup-restore-paths-") as temp:
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": temp}):
                self.assertEqual(restore.restore_backup_root(), os.path.join(temp, "plugbackup", "restore-backup"))
            job = Job(dest="Папка/Файлы")
            with mock.patch("plugbackup.core.restore.os.path.expanduser", return_value="/home/isolated"):
                path = restore.default_other_target(job, when=1_700_000_000)
            # Имя папки — на языке интерфейса
            self.assertEqual(path, os.path.join("/home/isolated", restore._("Restored"), "Папка_Файлы_2023-11-15"))

    def test_write_readme_creates_description_only_under_supplied_temp_path(self):
        with tempfile.TemporaryDirectory(prefix="plugbackup-readme-") as temp:
            item = restore.RestoreItem(Job(id="j", dest="Документы"), "/copy", "/target")
            backup = os.path.join(temp, "restore-backup", "time", "Документы")
            restore.write_readme(backup, item)
            with open(os.path.join(backup, "README.txt"), encoding="utf-8") as stream:
                content = stream.read()
            self.assertIn("/copy", content)
            self.assertIn("/target", content)
            self.assertIn("Plug & Backup", content)   # текст — на языке интерфейса


if __name__ == "__main__":
    unittest.main()
