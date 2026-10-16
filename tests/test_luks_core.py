"""Поддержка LUKS; все внешние интерфейсы подменены."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import gi

gi.require_version("UDisks", "2.0")
from gi.repository import GLib, UDisks

from plugbackup.core import auto, devices, profiles, secrets
from plugbackup.core.profiles import AutoSettings, DeviceBinding, Encryption, Job, Profile


def glib_error(message="failure", code=UDisks.Error.FAILED):
    return GLib.Error.new_literal(UDisks.error_quark(), message, code)


class FakeSecret:
    class SchemaFlags:
        NONE = 0

    class SchemaAttributeType:
        STRING = "string"

    class CollectionFlags:
        NONE = 0

    class ServiceFlags:
        OPEN_SESSION = 1

    COLLECTION_DEFAULT = "default"

    class Schema:
        @staticmethod
        def new(name, flags, attributes):
            return (name, flags, attributes)


class SecretTests(unittest.TestCase):
    def setUp(self):
        self.secret = FakeSecret
        self.secret.lookups = []
        self.secret.stores = []
        self.secret.clears = []
        self.secret.lookup_result = "saved-pass"
        self.secret.store_result = True
        self.secret.clear_result = True
        self.secret.lookup_error = None
        self.secret.store_error = None
        self.secret.clear_error = None

        def lookup(schema, attrs, cancellable):
            self.secret.lookups.append((schema, attrs, cancellable))
            if self.secret.lookup_error:
                raise self.secret.lookup_error
            return self.secret.lookup_result

        def store(schema, attrs, collection, label, password, cancellable):
            self.secret.stores.append((schema, attrs, collection, label, password, cancellable))
            if self.secret.store_error:
                raise self.secret.store_error
            return self.secret.store_result

        def clear(schema, attrs, cancellable):
            self.secret.clears.append((schema, attrs, cancellable))
            if self.secret.clear_error:
                raise self.secret.clear_error
            return self.secret.clear_result

        self.secret.password_lookup_sync = lookup
        self.secret.password_store_sync = store
        self.secret.password_clear_sync = clear
        self.patch_secret = patch.object(secrets, "Secret", self.secret)
        self.patch_secret.start()
        self.addCleanup(self.patch_secret.stop)
        self.old_schema = secrets._schema
        secrets._schema = None
        self.addCleanup(setattr, secrets, "_schema", self.old_schema)

    def test_attributes_and_password_store_lookup_clear(self):
        self.assertEqual(secrets.lookup("profile-1"), "saved-pass")
        self.assertEqual(self.secret.lookups[-1][1], {"app": "plugbackup", "profile": "profile-1"})
        self.assertTrue(secrets.store("profile-1", "Drive", "private-password"))
        args = self.secret.stores[-1]
        self.assertEqual(args[1], {"app": "plugbackup", "profile": "profile-1"})
        self.assertEqual(args[4], "private-password")
        self.assertTrue(secrets.clear("profile-1"))
        self.assertEqual(self.secret.clears[-1][1], {"app": "plugbackup", "profile": "profile-1"})

    def test_empty_profile_id_does_not_call_secret_service(self):
        self.assertIsNone(secrets.lookup(""))
        self.assertFalse(secrets.store("", "Drive", "secret"))
        self.assertFalse(secrets.clear(""))
        self.assertEqual(self.secret.lookups, [])
        self.assertEqual(self.secret.stores, [])
        self.assertEqual(self.secret.clears, [])

    def test_secret_service_errors_are_suppressed_without_password_disclosure(self):
        sentinel = "highly-sensitive-passphrase"
        for operation, set_error, call, empty in (
            ("lookup", "lookup_error", lambda: secrets.lookup("p"), None),
            ("store", "store_error", lambda: secrets.store("p", "Drive", sentinel), False),
            ("clear", "clear_error", lambda: secrets.clear("p"), False),
        ):
            with self.subTest(operation=operation):
                setattr(self.secret, set_error, glib_error("backend failure"))
                try:
                    result = call()
                except Exception as exc:  # pragma: no cover - assertion documents no exception should escape
                    self.fail(f"{operation} leaked an exception: {exc!r}")
                self.assertEqual(result, empty)
                self.assertNotIn(sentinel, repr(result))
                setattr(self.secret, set_error, None)

    def test_secret_absence_disables_all_password_operations(self):
        with patch.object(secrets, "Secret", None):
            self.assertEqual(secrets.service_state(), secrets.UNAVAILABLE)
            self.assertIsNone(secrets.lookup("p"))
            self.assertFalse(secrets.store("p", "Drive", "secret"))
            self.assertFalse(secrets.clear("p"))

    def test_service_state_ok_locked_and_missing_collection(self):
        def set_service(collection):
            self.secret.Service = SimpleNamespace(
                get_sync=lambda *_args: object())
            self.secret.Collection = SimpleNamespace(
                for_alias_sync=lambda *_args: collection)

        set_service(SimpleNamespace(get_locked=lambda: False))
        self.assertEqual(secrets.service_state(), secrets.OK)
        set_service(SimpleNamespace(get_locked=lambda: True))
        self.assertEqual(secrets.service_state(), secrets.LOCKED)
        set_service(None)
        self.assertEqual(secrets.service_state(), secrets.UNAVAILABLE)

    def test_service_state_service_or_collection_errors_are_unavailable(self):
        self.secret.Service = SimpleNamespace(get_sync=lambda *_args: (_ for _ in ()).throw(glib_error()))
        self.assertEqual(secrets.service_state(), secrets.UNAVAILABLE)
        self.secret.Service = SimpleNamespace(get_sync=lambda *_args: object())
        self.secret.Collection = SimpleNamespace(
            for_alias_sync=lambda *_args: (_ for _ in ()).throw(glib_error()))
        self.assertEqual(secrets.service_state(), secrets.UNAVAILABLE)

    def test_service_state_locked_query_error_is_unavailable(self):
        self.secret.Service = SimpleNamespace(get_sync=lambda *_args: object())
        self.secret.Collection = SimpleNamespace(
            for_alias_sync=lambda *_args: SimpleNamespace(
                get_locked=lambda: (_ for _ in ()).throw(glib_error("locked state unavailable"))))
        self.assertEqual(secrets.service_state(), secrets.UNAVAILABLE)


class AutoLuksTests(unittest.TestCase):
    def setUp(self):
        self.profile = Profile(
            id="p", auto=AutoSettings(enabled=True, min_interval_hours=2),
            jobs=[Job(id="auto-a", in_auto=True), Job(id="manual", in_auto=False)])
        self.vol = SimpleNamespace(uuid="container", object_path="/fake/luks",
                                    mounted=False, locked=True)

    def decide(self, **kwargs):
        args = dict(conflicts=set(), now=10000, last_success=None, paused_until=None)
        args.update(kwargs)
        return auto.decide(self.profile, self.vol, **args)

    def test_locked_is_lowest_priority_and_returns_automatic_job_ids(self):
        self.assertEqual(self.decide(paused_until=10001).reason, auto.PAUSED)
        self.profile.auto.enabled = False
        self.assertEqual(self.decide().reason, auto.AUTO_OFF)
        self.profile.auto.enabled = True
        self.assertEqual(self.decide(conflicts={"container"}).reason, auto.CONFLICT)
        self.profile.jobs = [Job(id="disabled", enabled=False, in_auto=True)]
        self.assertEqual(self.decide().reason, auto.NO_AUTO_JOBS)
        self.profile.jobs = [Job(id="auto-a", in_auto=True)]
        self.assertEqual(self.decide(last_success=9900).reason, auto.TOO_SOON)
        result = self.decide()
        self.assertEqual((result.run, result.reason, result.job_ids),
                         (False, auto.LOCKED, ("auto-a",)))

    def test_unlock_mount_lock_unlock_and_forget_lifecycle(self):
        tracker = auto.InsertionTracker()
        self.assertEqual(tracker.update([self.vol], 1), [(auto.READY, self.vol)])
        opened = SimpleNamespace(**(vars(self.vol) | {"locked": False}))
        self.assertEqual(tracker.update([opened], 2), [])
        self.assertEqual(tracker.update([opened], 2 + auto.MOUNT_WAIT_SECONDS),
                         [(auto.NEEDS_MOUNT, opened)])
        mounted = SimpleNamespace(**(vars(opened) | {"mounted": True}))
        self.assertEqual(tracker.update([mounted], 8), [(auto.READY, mounted)])
        # Переход обратно в закрытое состояние при извлечении не порождает READY.
        closed = SimpleNamespace(**(vars(mounted) | {"mounted": False, "locked": True}))
        self.assertEqual(tracker.update([closed], 9), [])
        reopened = SimpleNamespace(**(vars(closed) | {"locked": False}))
        self.assertEqual(tracker.update([reopened], 10), [])
        self.assertEqual(tracker.update([reopened], 10 + auto.MOUNT_WAIT_SECONDS),
                         [(auto.NEEDS_MOUNT, reopened)])
        tracker.forget(reopened.object_path)
        self.assertNotIn(reopened.object_path, tracker.handled)
        self.assertNotIn(reopened.object_path, tracker.handled_locked)
        self.assertEqual(tracker.update([self.vol], 20), [(auto.READY, self.vol)])

    def test_removal_clears_handled_locked_and_allows_reinsertion(self):
        tracker = auto.InsertionTracker()
        self.assertEqual(tracker.update([self.vol], 1), [(auto.READY, self.vol)])
        self.assertIn(self.vol.object_path, tracker.handled_locked)
        self.assertEqual(tracker.update([], 2), [])
        self.assertNotIn(self.vol.object_path, tracker.handled_locked)
        self.assertEqual(tracker.update([self.vol], 3), [(auto.READY, self.vol)])


class ProfileLuksTests(unittest.TestCase):
    def setUp(self):
        self.profile = Profile(id="profile", name="Test", encryption=Encryption("gocryptfs", True))
        self.luks = SimpleNamespace(uuid="container-uuid", fs_type="ext4", label="encrypted",
                                    drive_serial="serial", drive_vendor="Acme", drive_model="Disk",
                                    inner_uuid="inner-uuid", encrypted=True)
        self.plain = SimpleNamespace(uuid="plain-uuid", fs_type="vfat", label="plain",
                                     drive_serial="serial-2", drive_vendor="", drive_model="USB",
                                     inner_uuid="", encrypted=False)

    def test_binding_luks_and_then_plain_resets_luks_encryption(self):
        profiles.bind_to_volume(self.profile, self.luks)
        self.assertEqual(self.profile.encryption.mode, "luks")
        self.assertEqual(self.profile.device.uuid, "container-uuid")
        self.assertEqual(self.profile.device.luks_inner_uuid, "inner-uuid")
        profiles.bind_to_volume(self.profile, self.plain)
        self.assertEqual(self.profile.encryption, Encryption())
        self.assertIsNone(self.profile.device.luks_inner_uuid)

    def test_uses_luks_for_current_and_legacy_profiles(self):
        self.profile.encryption.mode = "luks"
        self.assertTrue(profiles.uses_luks(self.profile))
        self.profile.encryption.mode = "none"
        self.profile.device.luks_inner_uuid = "legacy-inner"
        self.assertTrue(profiles.uses_luks(self.profile))
        self.profile.device.luks_inner_uuid = None
        self.assertFalse(profiles.uses_luks(self.profile))

    def test_clone_clears_binding_encryption_and_password_preference(self):
        self.profile.device = DeviceBinding(uuid="uuid", luks_inner_uuid="inner")
        self.profile.encryption = Encryption("luks", True)
        clone = profiles.clone_for_other_device(self.profile, "Clone")
        self.assertEqual(clone.name, "Clone")
        self.assertEqual(clone.device, DeviceBinding())
        self.assertEqual(clone.encryption, Encryption())

    def test_profile_dict_round_trip_preserves_encryption(self):
        self.profile.encryption = Encryption("luks", True)
        serialized = profiles.profile_to_dict(self.profile)
        self.assertEqual(serialized["encryption"], {"mode": "luks", "remember_password": True})
        self.assertEqual(profiles.profile_from_dict(serialized).encryption, self.profile.encryption)


def drain_main_context(predicate, limit=1000):
    context = GLib.MainContext.default()
    for _ in range(limit):
        if predicate():
            return
        context.iteration(False)
    raise AssertionError("GLib callback did not arrive within the iteration limit")


class UnlockAsyncTests(unittest.TestCase):
    def test_success_passes_passphrase_quiet_options_and_result_to_callback(self):
        observed = {}

        class Encrypted:
            def call_unlock(self, password, options, cancellable, callback):
                observed.update(password=password, options=options, cancellable=cancellable)
                callback(self, "result")

            def call_unlock_finish(self, result):
                return "/org/freedesktop/UDisks2/block_devices/cleartext"

        encrypted = Encrypted()
        obj = SimpleNamespace(get_encrypted=lambda: encrypted)
        client = SimpleNamespace(get_object=lambda _path: obj)
        volume = SimpleNamespace(object_path="/fake/luks")
        calls = []
        devices.unlock_async(client, volume, "passphrase-value", lambda *args: calls.append(args))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], ("/org/freedesktop/UDisks2/block_devices/cleartext", None))
        self.assertEqual(observed["password"], "passphrase-value")
        self.assertTrue(observed["options"].unpack()["auth.no_user_interaction"])
        self.assertIsNone(observed["cancellable"])

    def test_unlock_failure_reaches_callback(self):
        failure = glib_error("unlock failed")

        class Encrypted:
            def call_unlock(self, _password, _options, _cancellable, callback):
                callback(self, "result")

            def call_unlock_finish(self, _result):
                raise failure

        client = SimpleNamespace(get_object=lambda _path: SimpleNamespace(get_encrypted=lambda: Encrypted()))
        calls = []
        devices.unlock_async(client, SimpleNamespace(object_path="/fake"), "pw", lambda *args: calls.append(args))
        self.assertEqual(calls, [(None, failure)])

    def test_object_without_encrypted_interface_reports_callback_error(self):
        client = SimpleNamespace(get_object=lambda _path: SimpleNamespace(get_encrypted=lambda: None))
        calls = []
        devices.unlock_async(client, SimpleNamespace(object_path="/fake"), "pw", lambda *args: calls.append(args))
        drain_main_context(lambda: bool(calls))
        self.assertIsNone(calls[0][0])
        self.assertIsInstance(calls[0][1], GLib.Error)


class WrongPassphraseTests(unittest.TestCase):
    def test_recognizes_cryptsetup_text_and_failed_error(self):
        self.assertTrue(devices.is_wrong_passphrase(glib_error("No key available with this passphrase")))
        self.assertTrue(devices.is_wrong_passphrase(glib_error("bad password")))
        self.assertFalse(devices.is_wrong_passphrase(glib_error("I/O error")))

    def test_not_authorized_codes_are_not_misreported_as_wrong_password(self):
        for code in (UDisks.Error.NOT_AUTHORIZED, UDisks.Error.NOT_AUTHORIZED_CAN_OBTAIN):
            self.assertFalse(devices.is_wrong_passphrase(glib_error("bad password", code)))


if __name__ == "__main__":
    unittest.main()
