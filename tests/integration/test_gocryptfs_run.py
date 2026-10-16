"""Сквозная проверка на настоящем gocryptfs: создание папки, резервирование движком, перенос копии.

Монтирует FUSE только во временной папке; запускается лишь с PLUGBACKUP_GOCRYPTFS_TESTS=1.
Носитель — обычная временная папка: проверка точки монтирования подменена.
"""

import os
import tempfile
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock

try:
    import gi  # noqa: F401
except ImportError as exc:
    _GI_ERROR = str(exc)
else:
    _GI_ERROR = ""

from plugbackup import plat
gocrypt = None if _GI_ERROR else plat.vault
from plugbackup.core import binding, marker, runner, transfer
from plugbackup.core.profiles import Encryption, Job, Profile
from plugbackup.core.runner import Runner, RunRequest

PASSWORD = "integration-only-password"


@unittest.skipUnless(os.environ.get("PLUGBACKUP_GOCRYPTFS_TESTS") == "1",
                     "set PLUGBACKUP_GOCRYPTFS_TESTS=1 to run real gocryptfs/FUSE tests")
@unittest.skipUnless(not _GI_ERROR, "GI недоступен: {}".format(_GI_ERROR))
class GocryptfsRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="plugbackup-vault-run-")
        self.addCleanup(self.tmp.cleanup)
        base = self.tmp.name
        self.drive = os.path.join(base, "drive")
        self.source = os.path.join(base, "source")
        for path in (self.drive, self.source, os.path.join(base, "state"), os.path.join(base, "runtime")):
            os.makedirs(path)
        with open(os.path.join(self.source, "Отчёт: итоги?.txt"), "w") as f:   # недопустимо для exFAT без шифрования
            f.write("секретное содержимое")
        os.makedirs(os.path.join(self.source, "sub"))
        with open(os.path.join(self.source, "sub", "a.txt"), "w") as f:
            f.write("a")
        self.profile = Profile(id="vault-run-test", name="Test", root="PlugBackup",
                               encryption=Encryption(mode="gocryptfs"),
                               jobs=[Job(id="job", source=self.source, dest="Data", use_global_excludes=False)])
        self.profile.device.uuid = "U1"
        self.volume = SimpleNamespace(uuid="U1", fs_type="exfat", mount_point=self.drive, mounted=True,
                                      locked=False, fs_uuid="U1")
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.dict(os.environ, {"XDG_STATE_HOME": os.path.join(base, "state"),
                                                         "XDG_RUNTIME_DIR": os.path.join(base, "runtime")}))
        stack.enter_context(mock.patch.object(binding, "_verified_mount_point", lambda p, v: self.drive))
        stack.enter_context(mock.patch.object(plat.mounts, "verify_mount", lambda *a, **k: SimpleNamespace(ok=True)))
        stack.enter_context(mock.patch.object(plat.system, "inhibit_sleep", lambda reason: None))
        self.plain = gocrypt.plain_dir(self.profile.id)
        self.cipher = binding.cipher_dir_for(self.profile, self.volume)
        self.addCleanup(self._unmount)

    def _unmount(self):
        plain = os.path.realpath(self.plain)
        assert plain.startswith(os.path.realpath(self.tmp.name) + os.sep), plain   # только своя точка
        if gocrypt.mounted_source(self.plain) is not None:
            gocrypt.unmount(self.plain)

    def test_backup_into_encrypted_folder(self):
        with self.assertRaises(binding.BindingError) as missing:
            binding.root_dir_for(self.profile, self.volume)
        self.assertEqual(missing.exception.code, "vault-missing")
        key = gocrypt.init(self.cipher, PASSWORD)
        self.assertEqual(len(key.split("-")), 8)
        with self.assertRaises(binding.BindingError) as closed:
            binding.root_dir_for(self.profile, self.volume)
        self.assertEqual(closed.exception.code, "vault-closed")

        gocrypt.mount(self.cipher, self.plain, PASSWORD)
        self.assertEqual(binding.root_dir_for(self.profile, self.volume), self.plain)
        outcome = Runner(RunRequest(profile=self.profile, volume=self.volume)).run()
        self.assertEqual(outcome.record.status, "ok", outcome.record)
        self.assertEqual(outcome.record.jobs[0].copied_files, 2)
        self.assertEqual(marker.read_marker(self.plain).profile_id, self.profile.id)
        with open(os.path.join(self.plain, "Data", "Отчёт: итоги?.txt")) as f:
            self.assertEqual(f.read(), "секретное содержимое")

        # На «носителе» — только зашифрованное: ни имён, ни содержимого в открытом виде.
        names = []
        for _root, dirs, files in os.walk(self.cipher):
            names += dirs + files
        self.assertFalse({"Data", ".plugbackup", "Отчёт: итоги?.txt", "sub"} & set(names))
        for root, _dirs, files in os.walk(self.cipher):
            for name in files:
                with open(os.path.join(root, name), "rb") as f:
                    self.assertNotIn("секретное".encode(), f.read())

        gocrypt.unmount(self.plain)
        with self.assertRaises(binding.BindingError):
            binding.root_dir_for(self.profile, self.volume)

    def test_transfer_plain_copy_into_vault_and_back(self):
        plain_profile = Profile(id=self.profile.id, name="Test", root="PlugBackup", jobs=self.profile.jobs)
        plain_profile.device.uuid = "U1"
        outcome = Runner(RunRequest(profile=plain_profile, volume=self.volume)).run()
        self.assertEqual(outcome.record.status, "warnings")   # имя с «:» и «?» на exFAT пропущено
        old_root = binding.plain_root_dir_for(plain_profile, self.volume)

        gocrypt.init(self.cipher, PASSWORD)
        gocrypt.mount(self.cipher, self.plain, PASSWORD)
        job = transfer.Transfer(old_root, self.plain, "exfat")
        self.assertTrue(job.run(), job.output_tail)
        self.assertEqual(transfer.tree_size(old_root), transfer.tree_size(self.plain))
        transfer.remove_tree(old_root)
        self.assertEqual(marker.root_state(self.plain, self.profile.id), marker.ROOT_MATCH)
        outcome = Runner(RunRequest(profile=self.profile, volume=self.volume)).run()
        self.assertEqual(outcome.record.status, "ok", outcome.record)   # в шифрованной папке имя допустимо

        back = binding.plain_root_dir_for(self.profile, self.volume)
        job = transfer.Transfer(self.plain, back, "exfat")
        self.assertTrue(job.run(), job.output_tail)
        self.assertTrue(os.path.isfile(os.path.join(back, "Data", "sub", "a.txt")))


if __name__ == "__main__":
    unittest.main()
