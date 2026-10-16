"""Интеграционные проверки UDisks на временных loop-образах.

Намеренно отключены в обычном прогоне: для их запуска требуется явно задать
PLUGBACKUP_IMAGE_TESTS=1. Все блочные устройства создаются через udisksctl.
"""

from __future__ import annotations

import getpass
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid as uuidlib
from pathlib import Path

try:
    import gi

    gi.require_version("UDisks", "2.0")
    from gi.repository import GLib, UDisks
except (ImportError, ValueError) as exc:  # pragma: no cover - environment guard
    GLib = UDisks = None
    _GI_ERROR = str(exc)
    DeviceMonitor = collect_volumes = free_space = mount_sync = None
    unmount_sync = uuid_conflicts = find_mount = read_mountinfo = verify_mount = None
else:
    _GI_ERROR = ""
    from plugbackup.plat.linux.devices import (
        DeviceMonitor,
        collect_volumes,
        free_space,
        mount_sync,
        unmount_sync,
        uuid_conflicts,
    )
    from plugbackup.plat.linux.mounts import find_mount, read_mountinfo, verify_mount


_ENABLED = os.environ.get("PLUGBACKUP_IMAGE_TESTS") == "1"


def NO_AUTH_PROMPT():
    """Опции UDisks2: никогда не показывать окно запроса пароля (polkit)."""
    return GLib.Variant("a{sv}", {"auth.no_user_interaction": GLib.Variant("b", True)})


def _run(args: list[str], *, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=check, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout)


def _available(*commands: str) -> bool:
    return all(shutil.which(name) for name in commands)


@unittest.skipUnless(_ENABLED, "установите PLUGBACKUP_IMAGE_TESTS=1 для loop-интеграционных тестов")
@unittest.skipUnless(not _GI_ERROR, f"GI UDisks недоступен: {_GI_ERROR}")
class DeviceImageIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        needed = ("udisksctl", "losetup", "findmnt", "blkid", "mkfs.vfat",
                  "mkfs.exfat", "mkfs.ntfs", "mkfs.ext4")
        missing = [cmd for cmd in needed if not shutil.which(cmd)]
        if missing:
            raise unittest.SkipTest("не найдены команды: " + ", ".join(missing))
        cls.client = UDisks.Client.new_sync(None)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="plugbackup-image-test-")
        self.root = Path(self.tmp.name)
        self.images: list[dict] = []
        self.monitor = DeviceMonitor(client=self.client)
        self.events: list[tuple[str, object]] = []
        for signal in ("volume-added", "volume-removed", "volume-changed", "volumes-changed"):
            self.monitor.connect(signal, lambda _obj, *args, s=signal: self.events.append((s, args[0] if args else None)))
        media_root = Path("/media") / getpass.getuser()
        self.media_before = set(media_root.iterdir()) if media_root.is_dir() else set()
        self.addCleanup(self._cleanup)

    def _new_image(self, label: str, mkfs_args: list[str], *, size_mb: int = 96) -> dict:
        path = self.root / f"{len(self.images)}.img"
        with path.open("wb") as image:
            image.truncate(size_mb * 1024 * 1024)
        _run(mkfs_args + [str(path)])
        info = {"path": path, "loop": "", "uuid": "", "label": label}
        self.images.append(info)
        result = _run(["udisksctl", "loop-setup", "--file", str(path), "--no-user-interaction"])
        match = re.search(r"(/dev/loop\d+)", result.stdout + result.stderr)
        self.assertIsNotNone(match, f"udisksctl loop-setup не вернул loop-устройство: {result.stdout} {result.stderr}")
        info["loop"] = match.group(1)
        # The sandbox/container may not expose /dev/loopN even though UDisks owns
        # the device. The backing image has the same on-disk UUID and is readable.
        info["uuid"] = _run(["blkid", "-s", "UUID", "-o", "value", str(path)]).stdout.strip()
        self.assertTrue(info["uuid"], f"не удалось прочитать UUID для {info['loop']}")
        self._wait_for(lambda: self._volume(info["uuid"]))
        return info

    def _volume(self, wanted_uuid: str):
        self.client.settle()
        return next((v for v in collect_volumes(self.client) if v.uuid == wanted_uuid), None)

    def _wait_for(self, predicate, timeout: float = 15.0):
        context = GLib.MainContext.default()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            self.client.settle()
            result = predicate()
            if result:
                return result
            time.sleep(0.05)
        self.fail(f"таймаут ожидания условия ({timeout:.0f} с)")

    def _wait_event(self, signal: str, predicate=lambda _value: True, timeout: float = 15.0):
        return self._wait_for(lambda: (self.monitor.refresh() or True) and
                              next((value for name, value in self.events
                                    if name == signal and predicate(value)), None), timeout)

    @staticmethod
    def _uuid_links_available() -> bool:
        return os.path.isdir("/dev/disk/by-uuid")

    def _assert_own(self, volume):
        """Страховка: тест трогает только loop-устройства своих образов во временной папке."""
        self.assertTrue(volume.is_loop, f"попытка работать не с тестовым образом: {volume.device}")
        backing = os.path.realpath(volume.loop_backing_file)
        self.assertTrue(backing.startswith(os.path.realpath(self.tmp.name) + os.sep),
                        f"loop не из временной папки теста: {volume.loop_backing_file}")

    def _mount(self, volume):
        self._assert_own(volume)
        # Cinnamon может успеть смонтировать устройство до вызова mount_sync.
        point = mount_sync(self.client, volume)
        self.assertTrue(point)
        self._keep_loop_after_unmount(volume)
        self._wait_for(lambda: (self.monitor.refresh() or True) and
                       any(name == "volume-changed" and getattr(value, "uuid", None) == volume.uuid
                           for name, value in self.events))
        refreshed = self._volume(volume.uuid)
        return point, refreshed or volume

    def _keep_loop_after_unmount(self, volume):
        """UDisks2 включает autoclear при монтировании своего loop — после размонтирования
        устройство исчезло бы. Выключаем его только у своего образа, без окна пароля."""
        self._assert_own(volume)
        self.client.settle()
        obj = self.client.get_object(volume.object_path)
        loop = obj.get_loop() if obj is not None else None
        self.assertIsNotNone(loop, f"нет интерфейса Loop у {volume.device}")
        loop.call_set_autoclear_sync(False, NO_AUTH_PROMPT(), None)

    def _unmount(self, volume):
        current = self._volume(volume.uuid) or volume
        self._assert_own(current)
        if current.mounted:
            unmount_sync(self.client, current)
        self._wait_for(lambda: not any(e.source == current.device and e.root == "/"
                                       for e in read_mountinfo()))

    def _loop_is_ours(self, loop: str, image_path) -> bool:
        """За этим номером loop всё ещё стоит файл нашего образа (номер мог достаться другому)."""
        self.client.settle()
        obj = self.client.get_object("/org/freedesktop/UDisks2/block_devices/" + os.path.basename(loop))
        loop_iface = obj.get_loop() if obj is not None else None
        if loop_iface is None:
            return False
        backing = bytes(loop_iface.props.backing_file or b"").rstrip(b"\0").decode() \
            if not isinstance(loop_iface.props.backing_file, str) else loop_iface.props.backing_file
        return os.path.realpath(backing) == os.path.realpath(str(image_path))

    def _cleanup(self):
        # Tear down only paths and loop devices recorded from this test's own images.
        try:
            for info in reversed(self.images):
                loop = info.get("loop")
                if not loop:
                    continue
                loop_object_path = "/org/freedesktop/UDisks2/block_devices/" + os.path.basename(loop)
                # Unlock-created cleartext mappings and Cinnamon automounts are both
                # represented by the UDisks filesystem objects below.
                for _ in range(3):
                    self.client.settle()
                    objects = self.client.get_object_manager().get_objects()
                    for obj in objects:
                        block = obj.get_block()
                        if block is None:
                            continue
                        # Только сам loop этого теста и расшифрованное устройство поверх него.
                        # У прочих устройств crypto_backing_device равен "/", а не "" —
                        # раньше из-за этого очистка пыталась отключить все разделы системы.
                        device = str(block.props.preferred_device or block.props.device or "")
                        backing = str(block.props.crypto_backing_device or "")
                        own_loop = os.path.realpath(device) == os.path.realpath(loop)
                        over_own_loop = backing == loop_object_path
                        if not (own_loop or over_own_loop):
                            continue
                        filesystem = obj.get_filesystem()
                        if filesystem and filesystem.props.mount_points:
                            try:
                                filesystem.call_unmount_sync(NO_AUTH_PROMPT(), None)
                            except GLib.Error:
                                pass
                        encrypted = obj.get_encrypted()
                        if encrypted and encrypted.props.cleartext_device not in ("", "/"):
                            try:
                                encrypted.call_lock_sync(NO_AUTH_PROMPT(), None)
                            except GLib.Error:
                                pass
                if not self._loop_is_ours(loop, info["path"]):
                    continue  # уже освобождён (autoclear) или номер занят чужим образом — не трогать
                result = _run(["udisksctl", "loop-delete", "--block-device", loop,
                               "--no-user-interaction"], check=False)
                if result.returncode:
                    # UDisks loop devices have autoclear; disappearance is expected.
                    remaining = _run(["losetup", "-j", str(info["path"])], check=False)
                    if remaining.stdout.strip():
                        self.fail(f"не удалось удалить тестовый loop {loop}: {result.stderr.strip()}")
        finally:
            self.monitor.close()
            self.tmp.cleanup()
            media_root = Path("/media") / getpass.getuser()
            if media_root.is_dir():
                for child in set(media_root.iterdir()) - self.media_before:
                    try:
                        child.rmdir()
                    except OSError:
                        pass

    def test_filesystem_volumes_signals_mount_and_space(self):
        formats = (
            ("vfat", "V FAT", ["mkfs.vfat", "-F", "16", "-n", "V FAT"]),
            ("exfat", "EX FAT", ["mkfs.exfat", "-L", "EX FAT"]),
            ("ntfs", "NTFS TEST", ["mkfs.ntfs", "-F", "-L", "NTFS TEST"]),
            # Корень — пользователя, иначе запись невозможна (это отдельно проверяет test_ext4_root_ownership…).
            ("ext4", "EXT4 TEST", ["mkfs.ext4", "-F", "-L", "EXT4 TEST",
                                   "-E", f"root_owner={os.getuid()}:{os.getgid()}"]),
        )
        for fs_type, label, mkfs_args in formats:
            with self.subTest(fs_type=fs_type):
                info = self._new_image(label, mkfs_args)
                # Если номер loop только что освободился, UDisks2 переиспользует путь объекта —
                # тогда монитор сообщает «изменён», а не «добавлен».
                added = self._wait_for(lambda: (self.monitor.refresh() or True) and next(
                    (value for name, value in self.events
                     if name in ("volume-added", "volume-changed") and getattr(value, "uuid", None) == info["uuid"]),
                    None))
                self.assertIsNotNone(added)
                volume = self._volume(info["uuid"])
                self.assertIsNotNone(volume)
                self.assertEqual(volume.fs_type.lower(), fs_type)
                self.assertEqual(volume.label, label)
                self.assertEqual(volume.display_name, label)
                self.assertGreaterEqual(volume.size, 64 * 1024 * 1024)
                self.assertLessEqual(volume.size, 128 * 1024 * 1024)
                self.assertTrue(volume.is_loop)
                self.assertTrue(volume.removable)
                self.assertEqual(os.path.realpath(volume.loop_backing_file), os.path.realpath(info["path"]))
                point, mounted_volume = self._mount(volume)
                if self._uuid_links_available():
                    check = verify_mount(point, volume.fs_uuid)
                    self.assertTrue(check.ok, check.reason)
                free, total = free_space(point)
                self.assertGreater(free, 0)
                self.assertLessEqual(free, total)
                self.assertGreater(total, 64 * 1024 * 1024)
                self.assertLess(total, 128 * 1024 * 1024)
                self._unmount(mounted_volume)
                self._wait_event("volume-changed", lambda v: getattr(v, "uuid", None) == info["uuid"]
                                 and not v.mounted)
                if fs_type == "vfat":
                    self.assertIn(info["uuid"], [v.uuid for v in collect_volumes(self.client)])
                    self.assertEqual(uuid_conflicts(collect_volumes(self.client)), set())
        # Explicitly refresh after setup/deletion to exercise removal even if the
        # desktop's UDisks changed notification was coalesced.
        last = self.images[-1]
        self._cleanup_loop(last)
        self._wait_event("volume-removed", lambda path: isinstance(path, str))

    def _cleanup_loop(self, info):
        loop = info["loop"]
        if self._loop_is_ours(loop, info["path"]):
            _run(["udisksctl", "loop-delete", "--block-device", loop, "--no-user-interaction"], check=False)
        self._wait_for(lambda: not self._volume(info["uuid"]))
        self.monitor.refresh()

    def test_mount_verification_reasons_and_read_only(self):
        if not self._uuid_links_available():
            self.skipTest("/dev/disk/by-uuid скрыт изоляцией запуска; verify_mount не может сопоставить UUID устройству")
        first = self._new_image("MY DISK", ["mkfs.vfat", "-F", "16", "-n", "MY DISK"])
        second = self._new_image("OTHER DISK", ["mkfs.vfat", "-F", "16", "-n", "OTHER DISK"])
        volume = self._volume(first["uuid"])
        point, volume = self._mount(volume)
        check = verify_mount(point, first["uuid"])
        self.assertTrue(check.ok, check.reason)
        self.assertTrue(any(entry.mount_point == point for entry in read_mountinfo()))
        self.assertEqual(find_mount(point).mount_point, point)
        self.assertEqual(verify_mount(point, second["uuid"]).reason, "uuid-mismatch")
        self.assertEqual(verify_mount(point, str(uuidlib.uuid4())).reason, "uuid-not-found")
        self.assertEqual(verify_mount(point + "/does-not-exist", first["uuid"]).reason, "not-mounted")
        self.assertEqual(verify_mount(point, "").reason, "invalid-uuid")

        self._unmount(volume)
        self.assertEqual(verify_mount(point, first["uuid"]).reason, "not-mounted")
        # Use Filesystem.Mount's standard options dictionary for an explicit ro mount.
        volume = self._volume(first["uuid"])
        filesystem = self.client.get_object(volume.filesystem_path).get_filesystem()
        self._assert_own(volume)
        options = GLib.Variant("a{sv}", {"options": GLib.Variant("s", "ro"),
                                         "auth.no_user_interaction": GLib.Variant("b", True)})
        try:
            ro_point = filesystem.call_mount_sync(options, None)
        except GLib.Error:
            # In case Cinnamon won the race, remove its mount then immediately retry.
            self._unmount(volume)
            ro_point = filesystem.call_mount_sync(options, None)
        self.assertEqual(ro_point, point)
        self.assertEqual(verify_mount(ro_point, first["uuid"]).reason, "read-only")

    def test_ext4_root_ownership_controls_writability(self):
        if not self._uuid_links_available():
            self.skipTest("/dev/disk/by-uuid скрыт изоляцией запуска; verify_mount не может сопоставить UUID устройству")
        root_owned = self._new_image("ROOT OWNED", ["mkfs.ext4", "-F", "-L", "ROOT OWNED"])
        root_volume = self._volume(root_owned["uuid"])
        self._assert_own(root_volume)
        root_point = mount_sync(self.client, root_volume)
        self.assertEqual(verify_mount(root_point, root_owned["uuid"]).reason, "not-writable")
        self._unmount(root_volume)

        user_owned = self._new_image("USER OWNED", ["mkfs.ext4", "-F", "-L", "USER OWNED",
                                                   "-E", f"root_owner={os.getuid()}:{os.getgid()}"])
        user_volume = self._volume(user_owned["uuid"])
        self._assert_own(user_volume)
        point = mount_sync(self.client, user_volume)
        check = verify_mount(point, user_owned["uuid"])
        self.assertTrue(check.ok, check.reason)

    def test_duplicate_vfat_uuid_is_reported(self):
        first = self._new_image("DUP ONE", ["mkfs.vfat", "-F", "16", "-n", "DUP ONE",
                                             "-i", "1234ABCD"])
        second = self._new_image("DUP TWO", ["mkfs.vfat", "-F", "16", "-n", "DUP TWO",
                                              "-i", "1234ABCD"])
        volumes = self._wait_for(lambda: (found if len(found := [v for v in collect_volumes(self.client)
                                                                  if v.uuid == first["uuid"]]) >= 2 else None))
        self.assertGreaterEqual(len(volumes), 2)
        self.assertIn(first["uuid"], uuid_conflicts(volumes))

    def test_luks_discovery_and_unlock_or_skip_inner_filesystem(self):
        if not shutil.which("cryptsetup"):
            self.skipTest("cryptsetup недоступен")
        path = self.root / "encrypted.img"
        with path.open("wb") as image:
            image.truncate(128 * 1024 * 1024)
        key = self.root / "key"
        key.write_bytes(b"temporary-test-passphrase")
        os.chmod(key, 0o600)
        _run(["cryptsetup", "luksFormat", "--batch-mode", "--type", "luks2",
              "--key-file", str(key), str(path)], timeout=90)
        info = {"path": path, "loop": "", "uuid": "", "label": ""}
        self.images.append(info)
        result = _run(["udisksctl", "loop-setup", "--file", str(path), "--no-user-interaction"])
        info["loop"] = re.search(r"(/dev/loop\d+)", result.stdout + result.stderr).group(1)
        info["uuid"] = _run(["blkid", "-s", "UUID", "-o", "value", str(path)]).stdout.strip()
        volume = self._wait_for(lambda: self._volume(info["uuid"]))
        self.assertTrue(volume.encrypted)
        self.assertTrue(volume.locked)
        self.assertEqual(volume.fs_type, "")
        obj = self.client.get_object(volume.object_path)
        try:
            obj.get_encrypted().call_unlock_sync("temporary-test-passphrase", NO_AUTH_PROMPT(), None)
        except GLib.Error as exc:
            self.skipTest(f"UDisks не смог разблокировать временный LUKS: {exc.message}")
        volume = self._wait_for(lambda: (self.monitor.refresh() or True) and
                                (v if (v := self._volume(info["uuid"])) and not v.locked else None))
        self.assertFalse(volume.locked)
        if not volume.fs_type or not volume.inner_uuid:
            self.skipTest("создание ФС внутри LUKS требует root; внутри контейнера сейчас нет ФС")
        self._assert_own(volume)
        point = mount_sync(self.client, volume)
        if self._uuid_links_available():
            self.assertTrue(verify_mount(point, volume.fs_uuid).ok)


if __name__ == "__main__":
    unittest.main()
