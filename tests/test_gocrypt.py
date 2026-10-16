"""Ядро зашифрованной папки (gocryptfs).

Модульные проверки не запускают внешние команды. Интеграционные проверки
запускаются только с PLUGBACKUP_GOCRYPTFS_TESTS=1 и работают внутри tempdir.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from plugbackup.core import binding, fsinfo, gocrypt
from plugbackup.core.mounts import MountCheck, MountEntry
from plugbackup.core.profiles import DeviceBinding, Encryption, Profile
from plugbackup.core.rsync import ListEntry


def done(rc=0, stdout="", stderr="", args=None):
    return subprocess.CompletedProcess(args or [], rc, stdout=stdout, stderr=stderr)


class GocryptUnitTests(unittest.TestCase):
    def test_password_is_stdin_for_run_and_background_and_not_argv(self):
        secret = "password-DO-NOT-LEAK"
        completed = done(args=["gocryptfs", "-init"])
        with tempfile.TemporaryDirectory() as root:
            cipher, plain = os.path.join(root, "cipher"), os.path.join(root, "plain")
            with patch.object(gocrypt.subprocess, "run", return_value=completed) as run:
                self.assertIs(gocrypt._run(["gocryptfs", "-init", "--", cipher], secret), completed)
        args, kwargs = run.call_args
        self.assertNotIn(secret, repr(args))
        self.assertNotIn(secret, repr({key: value for key, value in kwargs.items() if key != "input"}))
        self.assertEqual(kwargs["input"], secret + "\n")

        writes = []

        class FakeStdin:
            def write(self, value):
                writes.append(value)

            def close(self):
                writes.append("<closed>")

        class FakeProc:
            stdin = FakeStdin()

            def wait(self, timeout=None):
                return 0

        with patch.object(gocrypt.subprocess, "Popen", return_value=FakeProc()) as popen, \
                patch.object(tempfile, "TemporaryFile") as temp_file:
            # tempfile is imported in _run; patch the shared module's constructor.
            file_obj = SimpleNamespace(seek=lambda *_: None, read=lambda: "")
            temp_file.return_value.__enter__.return_value = file_obj
            gocrypt._run(["gocryptfs", "--", cipher, plain], secret, background=True)
        popen_args, popen_kwargs = popen.call_args
        self.assertNotIn(secret, repr(popen_args))
        self.assertNotIn(secret, repr(popen_kwargs))
        self.assertEqual(writes, [secret + "\n", "<closed>"])

    def test_gocrypt_exit_codes_map_to_safe_error_codes(self):
        with tempfile.TemporaryDirectory() as root:
            cipher, plain = os.path.join(root, "cipher"), os.path.join(root, "plain")
            os.mkdir(cipher)
            with patch.object(gocrypt, "_run", return_value=done(6, stderr="not empty")):
                with self.assertRaises(gocrypt.GocryptError) as caught:
                    gocrypt.init(cipher, "pw")
            self.assertEqual(caught.exception.code, gocrypt.NOT_EMPTY)

            open(os.path.join(cipher, gocrypt.CONF_FILE), "w").close()
            with patch.object(gocrypt, "is_open", return_value=False), \
                    patch.object(gocrypt, "mounted_source", return_value=None):
                with patch.object(gocrypt, "_run", return_value=done(12)):
                    with self.assertRaises(gocrypt.GocryptError) as caught:
                        gocrypt.mount(cipher, plain, "pw")
                self.assertEqual(caught.exception.code, gocrypt.WRONG_PASSWORD)
                with patch.object(gocrypt, "_run", return_value=done(10, stderr="occupied")):
                    with self.assertRaises(gocrypt.GocryptError) as caught:
                        gocrypt.mount(cipher, plain, "pw")
                self.assertEqual(caught.exception.code, gocrypt.NOT_EMPTY)

            empty_cipher = os.path.join(root, "empty-cipher")
            os.mkdir(empty_cipher)
            with patch.object(gocrypt, "_run", return_value=done(22)):
                with self.assertRaises(gocrypt.GocryptError) as caught:
                    gocrypt.init(empty_cipher, "pw")
            self.assertEqual(caught.exception.code, gocrypt.FAILED)

    def test_availability_missing_old_and_supported_components(self):
        with patch.object(gocrypt.shutil, "which", return_value=None):
            self.assertEqual(gocrypt.availability(), (False, "no-gocryptfs"))
        with patch.object(gocrypt.shutil, "which", side_effect=lambda name: "/usr/bin/gocryptfs" if name == "gocryptfs" else None):
            self.assertEqual(gocrypt.availability(), (False, "no-fuse"))
        with patch.object(gocrypt.shutil, "which", side_effect=lambda name: "/usr/bin/" + name), \
                patch.object(gocrypt.subprocess, "run", return_value=done(stdout="gocryptfs v2.3.0")):
            self.assertEqual(gocrypt.availability(), (False, "old-gocryptfs"))
        with patch.object(gocrypt.shutil, "which", side_effect=lambda name: "/usr/bin/" + name), \
                patch.object(gocrypt.subprocess, "run", return_value=done(stdout="gocryptfs v2.4.0")):
            self.assertEqual(gocrypt.availability(), (True, "ok"))

    def test_availability_rejects_nonzero_version_command(self):
        with patch.object(gocrypt.shutil, "which", side_effect=lambda name: "/usr/bin/" + name), \
                patch.object(gocrypt.subprocess, "run", return_value=done(1, stdout="gocryptfs v2.4.0")):
            self.assertEqual(gocrypt.availability(), (False, "no-gocryptfs"))

    def test_format_master_key_normalizes_and_groups(self):
        raw = "01234567-89ABCDEF-01234567-89ABCDEF-01234567-89ABCDEF-01234567-89ABCDEF"
        self.assertEqual(gocrypt.format_master_key(raw),
                         "01234567-89abcdef-01234567-89abcdef-01234567-89abcdef-01234567-89abcdef")
        self.assertEqual(gocrypt.format_master_key("0123 xyz"), "0123")

    def test_init_rejects_nonempty_directory_and_symlink(self):
        with tempfile.TemporaryDirectory() as root:
            cipher = os.path.join(root, "cipher")
            os.mkdir(cipher)
            with open(os.path.join(cipher, "occupied"), "w") as f:
                f.write("x")
            with self.assertRaises(gocrypt.GocryptError) as caught:
                gocrypt.init(cipher, "pw")
            self.assertEqual(caught.exception.code, gocrypt.NOT_EMPTY)
            link = os.path.join(root, "link")
            os.symlink(cipher, link)
            with self.assertRaises(gocrypt.GocryptError) as caught:
                gocrypt.init(link, "pw")
            self.assertEqual(caught.exception.code, gocrypt.FAILED)

    def test_mount_rejects_foreign_mount_and_nonempty_plain_directory(self):
        with tempfile.TemporaryDirectory() as root:
            cipher, plain = os.path.join(root, "cipher"), os.path.join(root, "plain")
            os.mkdir(cipher)
            open(os.path.join(cipher, gocrypt.CONF_FILE), "w").close()
            with patch.object(gocrypt, "is_open", return_value=False), \
                    patch.object(gocrypt, "mounted_source", return_value="/other/cipher"):
                with self.assertRaises(gocrypt.GocryptError) as caught:
                    gocrypt.mount(cipher, plain, "pw")
            self.assertEqual(caught.exception.code, gocrypt.FAILED)
            with patch.object(gocrypt, "is_open", return_value=False), \
                    patch.object(gocrypt, "mounted_source", return_value=None):
                os.mkdir(plain)
                open(os.path.join(plain, "occupied"), "w").close()
                with self.assertRaises(gocrypt.GocryptError) as caught:
                    gocrypt.mount(cipher, plain, "pw")
            self.assertEqual(caught.exception.code, gocrypt.NOT_EMPTY)

    def test_unmount_lazy_and_fusermount_failure(self):
        with tempfile.TemporaryDirectory() as root:
            plain = os.path.join(root, "plain")
            os.mkdir(plain)
            with patch.object(gocrypt, "mounted_source", return_value=os.path.join(root, "cipher")), \
                    patch.object(gocrypt, "fusermount", return_value="/usr/bin/fusermount3"), \
                    patch.object(gocrypt, "_run", return_value=done()) as run:
                gocrypt.unmount(plain, lazy=True)
            self.assertEqual(run.call_args.args[0], ["/usr/bin/fusermount3", "-u", "-z", "--", plain])
            self.assertFalse(os.path.exists(plain))
            with patch.object(gocrypt, "mounted_source", return_value=os.path.join(root, "cipher")), \
                    patch.object(gocrypt, "fusermount", return_value="/usr/bin/fusermount3"), \
                    patch.object(gocrypt, "_run", return_value=done(1, stderr="busy")):
                with self.assertRaises(gocrypt.GocryptError) as caught:
                    gocrypt.unmount(plain)
            self.assertEqual(caught.exception.code, gocrypt.FAILED)

    def test_stale_mounts_only_direct_gocryptfs_children_of_runtime(self):
        with tempfile.TemporaryDirectory() as root:
            base = os.path.join(root, "runtime", "plugbackup")
            foreign = os.path.join(root, "foreign", "plugbackup", "item")
            entries = [
                MountEntry(1, 0, (0, 1), "/", base + "/profile", "rw", gocrypt.FS_TYPE,
                           os.path.join(root, "cipher"), "rw"),
                MountEntry(2, 0, (0, 1), "/", base + "/nested/profile", "rw", gocrypt.FS_TYPE,
                           os.path.join(root, "cipher2"), "rw"),
                MountEntry(3, 0, (0, 1), "/", foreign, "rw", gocrypt.FS_TYPE,
                           os.path.join(root, "foreign-cipher"), "rw"),
                MountEntry(4, 0, (0, 1), "/", base + "/other", "rw", "fuse.sshfs",
                           os.path.join(root, "remote"), "rw"),
            ]
            with patch.object(gocrypt, "runtime_dir", return_value=base), \
                    patch.object(gocrypt, "read_mountinfo", return_value=entries):
                self.assertEqual(gocrypt.stale_mounts(), [base + "/profile"])

    def test_exception_details_redact_password_and_master_key(self):
        password = "SENSITIVE-PASSWORD"
        master_key = "0123456789abcdef" * 4
        with tempfile.TemporaryDirectory() as root:
            cipher = os.path.join(root, "cipher")
            with patch.object(gocrypt, "_run", return_value=done(1, stderr=f"bad {password} {master_key}")):
                with self.assertRaises(gocrypt.GocryptError) as caught:
                    gocrypt.init(cipher, password)
        for secret in (password, master_key):
            with self.subTest(secret_kind="password" if secret == password else "master-key"):
                self.assertNotIn(secret, str(caught.exception) + caught.exception.detail)

    def test_init_returns_empty_when_xray_fails(self):
        with tempfile.TemporaryDirectory() as root:
            cipher = os.path.join(root, "cipher")
            results = [done(0), done(1, stdout="masterkey=" + "a" * 64)]
            with patch.object(gocrypt, "_run", side_effect=results):
                self.assertEqual(gocrypt.init(cipher, "pw"), "")


class BindingGocryptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.volume = SimpleNamespace(uuid="volume-uuid", fs_uuid="fs-uuid", locked=False,
                                      mounted=True, mount_point=self.tmp.name)
        self.profile = Profile(id="profile-id", name="Test", device=DeviceBinding(uuid="volume-uuid"),
                              root="Archive", encryption=Encryption(mode="gocryptfs"))
        self.verify = patch.object(binding, "verify_mount", return_value=MountCheck(True))
        self.verify.start()
        self.addCleanup(self.verify.stop)

    def root_result(self):
        return binding.root_dir_for(self.profile, self.volume)

    def test_vault_missing_and_vault_closed(self):
        with self.assertRaises(binding.BindingError) as caught:
            self.root_result()
        self.assertEqual(caught.exception.code, "vault-missing")
        cipher = binding.cipher_dir_for(self.profile, self.volume)
        os.mkdir(cipher)
        open(os.path.join(cipher, gocrypt.CONF_FILE), "w").close()
        with patch.object(gocrypt, "is_initialized", return_value=True), \
                patch.object(gocrypt, "plain_dir", return_value=os.path.join(self.tmp.name, "plain")), \
                patch.object(gocrypt, "is_open", return_value=False):
            with self.assertRaises(binding.BindingError) as caught:
                self.root_result()
        self.assertEqual(caught.exception.code, "vault-closed")

    def test_cipher_link_file_and_invalid_root_are_rejected(self):
        cipher = binding.cipher_dir_for(self.profile, self.volume)
        os.symlink(self.tmp.name, cipher)
        with self.assertRaises(binding.BindingError) as caught:
            binding.cipher_dir_for(self.profile, self.volume)
        self.assertEqual(caught.exception.code, "root-is-link")
        os.unlink(cipher)
        open(cipher, "w").close()
        with self.assertRaises(binding.BindingError) as caught:
            binding.cipher_dir_for(self.profile, self.volume)
        self.assertEqual(caught.exception.code, "root-not-folder")
        self.profile.root = "bad/name"
        with self.assertRaises(binding.BindingError) as caught:
            binding.cipher_dir_for(self.profile, self.volume)
        self.assertEqual(caught.exception.code, "root-invalid")

    def test_plain_mode_still_returns_plain_root(self):
        self.profile.encryption.mode = "none"
        with patch.object(gocrypt, "is_initialized", side_effect=AssertionError("gocrypt called")):
            self.assertEqual(self.root_result(), os.path.join(self.tmp.name, "Archive"))


class EncryptedNamesTests(unittest.TestCase):
    def test_encrypted_vfat_and_exfat_skip_name_rules_but_keep_size_limit(self):
        entries = [ListEntry("f", 10, "bad:name"), ListEntry("f", 10, "Name"),
                   ListEntry("f", 10, "name"), ListEntry("f", 10, "CON.txt")]
        for fs_type in ("vfat", "exfat"):
            with self.subTest(fs_type=fs_type):
                report = fsinfo.check_names(entries, fs_type, False, encrypted=True)
                self.assertEqual(report.invalid, [])
                self.assertEqual(report.collisions, [])
        self.assertEqual(fsinfo.FAT32_MAX_FILE_ENCRYPTED,
                         (fsinfo.FAT32_MAX_FILE - 18) // 4128 * 4096)
        n = fsinfo.FAT32_MAX_FILE_ENCRYPTED
        self.assertLessEqual(18 + n + 32 * ((n + 4095) // 4096), fsinfo.FAT32_MAX_FILE)
        self.assertGreater(18 + n + 4096 + 32 * (((n + 4096) + 4095) // 4096), fsinfo.FAT32_MAX_FILE)
        report = fsinfo.check_names([ListEntry("f", n, "at-limit"),
                                     ListEntry("f", n + 4096, "over-limit")],
                                    "vfat", False, encrypted=True)
        self.assertEqual(report.too_big, [("over-limit", n + 4096)])


@unittest.skipUnless(os.environ.get("PLUGBACKUP_GOCRYPTFS_TESTS") == "1",
                     "set PLUGBACKUP_GOCRYPTFS_TESTS=1 to run real gocryptfs/FUSE tests")
class GocryptIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="plugbackup-gocrypt-")
        self.addCleanup(self.temp.cleanup)
        self.runtime_base = os.path.join(self.temp.name, "runtime")
        os.mkdir(self.runtime_base)
        self.old_runtime = os.environ.get("XDG_RUNTIME_DIR")
        os.environ["XDG_RUNTIME_DIR"] = self.runtime_base
        self.addCleanup(self._restore_runtime)
        self.cipher = os.path.join(self.temp.name, "vault.enc")
        self.profile_id = "vault-integration"
        self.plain = gocrypt.plain_dir(self.profile_id)
        self.addCleanup(self._cleanup_mount)

    def _restore_runtime(self):
        if self.old_runtime is None:
            os.environ.pop("XDG_RUNTIME_DIR", None)
        else:
            os.environ["XDG_RUNTIME_DIR"] = self.old_runtime

    def _assert_inside_temp(self, path):
        real = os.path.realpath(path)
        root = os.path.realpath(self.temp.name)
        self.assertEqual(os.path.commonpath([root, real]), root, f"path escaped tempdir: {path}")

    def _cleanup_mount(self):
        self._assert_inside_temp(self.plain)
        if gocrypt.is_open(self.cipher, self.plain):
            self._assert_inside_temp(self.plain)  # Проверка непосредственно перед unmount.
            subprocess.run(["fusermount3", "-u", "--", self.plain], check=True,
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if os.path.isdir(self.plain) and not os.path.ismount(self.plain):
            shutil.rmtree(self.plain)

    def test_init_wrong_password_mount_files_names_reopen_unmount_and_stale(self):
        password = "test-only-password-0.8"
        wrong = "definitely-wrong-password"
        os.mkdir(self.cipher)
        key = gocrypt.init(self.cipher, password)
        self.assertRegex(key, r"^(?:[0-9a-f]{8}-){7}[0-9a-f]{8}$")
        with self.assertRaises(gocrypt.GocryptError) as caught:
            gocrypt.mount(self.cipher, self.plain, wrong)
        self.assertEqual(caught.exception.code, gocrypt.WRONG_PASSWORD)
        gocrypt.mount(self.cipher, self.plain, password)
        self.assertTrue(gocrypt.is_open(self.cipher, self.plain))
        self.assertIn(self.plain, gocrypt.stale_mounts())
        with open(os.path.join(self.plain, "private report.txt"), "w", encoding="utf-8") as f:
            f.write("private content")
        self.assertEqual(open(os.path.join(self.plain, "private report.txt"), encoding="utf-8").read(),
                         "private content")
        self.assertNotIn("private report.txt", os.listdir(self.cipher))
        gocrypt.mount(self.cipher, self.plain, password)  # Повторное открытие уже открытого тома.
        self._assert_inside_temp(self.plain)
        gocrypt.unmount(self.plain)
        self.assertFalse(os.path.exists(self.plain))
        self._assert_inside_temp(self.plain)
        gocrypt.mount(self.cipher, self.plain, password)
        self.assertEqual(open(os.path.join(self.plain, "private report.txt"), encoding="utf-8").read(),
                         "private content")
        self._assert_inside_temp(self.plain)
        subprocess.run(["fusermount3", "-u", "--", self.plain], check=True,
                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertFalse(os.path.ismount(self.plain))
        if os.path.isdir(self.plain):
            os.rmdir(self.plain)

