"""Профили и метка. Все файловые операции — во временных каталогах."""

import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from plugbackup.core.marker import (
    MARKER_FORMAT,
    PROFILE_COPY_FILE,
    ROOT_CORRUPT,
    ROOT_EMPTY,
    ROOT_FOREIGN_DATA,
    ROOT_MATCH,
    ROOT_MISSING,
    ROOT_OTHER_PROFILE,
    MarkerError,
    read_marker,
    read_profile_copy,
    root_state,
    sync_device_files,
    write_marker,
)
from plugbackup.core.profiles import (
    EXPORT_FORMAT,
    EXPORT_VERSION,
    SCHEMA,
    AutoSettings,
    DeviceBinding,
    Job,
    Profile,
    ProfileFormatError,
    ProfilesStore,
    clone_for_other_device,
    default_dest,
    export_profile,
    has_errors,
    import_profile,
    job_from_dict,
    normalize_dest,
    path_within,
    profile_for_volume,
    profile_from_dict,
    profile_to_dict,
    rebind_candidates,
    validate_dest,
    validate_job,
    validate_name_component,
    validate_profile,
)


def issue_codes(issues):
    return {issue.code for issue in issues}


class ProfileSerializationTests(unittest.TestCase):
    def test_job_and_profile_round_trip(self):
        profile = Profile(
            id="profile-α", name="Работа — 東京", device=DeviceBinding(uuid="UUID-1", serial="SER-1"),
            auto=AutoSettings(enabled=True, countdown_sec=0),
            jobs=[Job(id="job-1", source="/tmp/é", dest="Проекты/資料", excludes=["*.tmp"])],
        )
        self.assertEqual(profile_from_dict(profile_to_dict(profile)), profile)
        self.assertEqual(job_from_dict(profile_to_dict(profile)["jobs"][0]), profile.jobs[0])

    def test_unknown_keys_are_ignored_and_missing_fields_use_defaults(self):
        data = {"id": "p", "unexpected": {"anything": True}, "device": {"unknown": 1},
                "auto": {"unknown": 2}, "encryption": {"unknown": 3},
                "mass_delete_threshold": {"unknown": 4}, "jobs": [{"id": "j", "extra": 5}]}
        profile = profile_from_dict(data)
        self.assertEqual(profile.name, "")
        self.assertEqual(profile.root, "PlugBackup")
        self.assertEqual(profile.auto.countdown_sec, 10)
        self.assertEqual(profile.jobs[0].mode, "mirror_versions")
        self.assertEqual(profile.jobs[0].compare, "size_mtime")

    def test_unknown_modes_fall_back_to_defaults(self):
        job = job_from_dict({"id": "j", "mode": "future", "compare": "future", "symlinks": "future"})
        profile = profile_from_dict({"id": "p", "encryption": {"mode": "future"}})
        self.assertEqual((job.mode, job.compare, job.symlinks), ("mirror_versions", "size_mtime", "auto"))
        self.assertEqual(profile.encryption.mode, "none")

    def test_malformed_structures_and_excludes_are_rejected(self):
        cases = [
            (lambda: job_from_dict([]), "job object"),
            (lambda: job_from_dict({"id": "j", "excludes": "*.tmp"}), "exclude list"),
            (lambda: job_from_dict({"id": "j", "excludes": ["ok", 3]}), "exclude item"),
            (lambda: profile_from_dict([]), "profile object"),
            (lambda: profile_from_dict({"id": "p", "jobs": {}}), "jobs list"),
            (lambda: profile_from_dict({"id": "p", "device": 4}), "device object"),
            (lambda: profile_from_dict({"id": "p", "device": []}), "empty device list"),
            (lambda: profile_from_dict({"id": "p", "auto": []}), "empty auto list"),
            (lambda: profile_from_dict({"id": "p", "jobs": [{"id": "j"}, {"id": "j"}]}), "duplicate jobs"),
        ]
        for operation, label in cases:
            with self.subTest(label=label), self.assertRaises(ProfileFormatError):
                operation()

    def test_duplicate_profile_ids_are_rejected_by_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "profiles.json")
            with open(path, "w", encoding="utf-8") as stream:
                json.dump({"schema": SCHEMA, "profiles": [{"id": "p"}, {"id": "p"}]}, stream)
            store = ProfilesStore(path)
            self.assertIsNotNone(store.error)
            self.assertEqual(store.profiles, [])


class ProfilesStoreTests(unittest.TestCase):
    def test_create_upsert_get_find_remove_and_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nested", "profiles.json")
            store = ProfilesStore(path)
            self.assertIsNone(store.error)
            self.assertTrue(store.save())
            first = Profile(id="p1", name="Первый", device=DeviceBinding(uuid="u1"))
            second = Profile(id="p2", name="Второй", device=DeviceBinding(uuid="u1"))
            store.upsert(first)
            store.upsert(second)
            store.upsert(Profile(id="p1", name="Обновлённый", device=DeviceBinding(uuid="u2")))
            self.assertEqual(len(store.profiles), 2)
            self.assertEqual(store.get("p1").name, "Обновлённый")
            self.assertEqual([p.id for p in store.find_by_uuid("u1")], ["p2"])
            self.assertEqual(store.find_by_uuid(""), [])
            self.assertTrue(store.save())
            with open(path, encoding="utf-8") as stream:
                stored = json.load(stream)
            self.assertEqual(stored["schema"], SCHEMA)
            self.assertEqual([p["id"] for p in stored["profiles"]], ["p1", "p2"])
            reloaded = ProfilesStore(path)
            self.assertEqual([p.id for p in reloaded.profiles], ["p1", "p2"])
            reloaded.remove("p1")
            self.assertEqual([p.id for p in reloaded.profiles], ["p2"])

    def test_corrupt_file_is_preserved_and_save_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "profiles.json")
            original = "{broken json"
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(original)
            store = ProfilesStore(path)
            self.assertIsNotNone(store.error)
            store.upsert(Profile(id="new"))
            self.assertFalse(store.save())
            with open(path, encoding="utf-8") as stream:
                self.assertEqual(stream.read(), original)


class PathValidationTests(unittest.TestCase):
    def test_path_helpers_handle_unicode_and_components(self):
        self.assertEqual(normalize_dest(r"Проекты\\./東京//Отчёты/"), "Проекты/東京/Отчёты")
        self.assertEqual(default_dest("/tmp/Каталог/"), "Каталог")
        with tempfile.TemporaryDirectory() as tmp:
            parent = os.path.join(tmp, "a", "b")
            os.makedirs(parent)
            sibling = os.path.join(tmp, "a", "bc")
            os.makedirs(sibling)
            self.assertTrue(path_within(parent, os.path.join(tmp, "a")))
            self.assertFalse(path_within(sibling, parent))
            link = os.path.join(tmp, "link")
            os.symlink(parent, link)
            self.assertTrue(path_within(os.path.join(link, "child"), os.path.join(tmp, "a")))

    def test_source_validation_empty_relative_missing_and_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = os.path.join(tmp, "file")
            with open(file_path, "w", encoding="utf-8") as stream:
                stream.write("x")
            for source, expected in [("", "source-empty"), ("relative", "source-relative"),
                                     (os.path.join(tmp, "missing"), "source-missing"),
                                     (file_path, "source-not-dir")]:
                with self.subTest(source=source):
                    self.assertIn(expected, issue_codes(validate_job(Job(id="j", source=source, dest="ok"), Profile())))

    def test_forbidden_system_path_rule_with_temporary_fixture(self):
        # Подменяем список запретов тестовым путём, чтобы не обращаться к системным каталогам.
        with tempfile.TemporaryDirectory() as tmp, patch("plugbackup.core.profiles.FORBIDDEN_SOURCES", {tmp}):
            self.assertIn("source-system", issue_codes(validate_job(Job(id="j", source=tmp, dest="ok"), Profile())))

    def test_source_on_drive_and_drive_inside_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "source")
            drive = os.path.join(tmp, "drive")
            os.makedirs(source)
            os.makedirs(drive)
            on_drive = os.path.join(drive, "source")
            os.makedirs(on_drive)
            self.assertIn("source-on-drive", issue_codes(validate_job(Job(id="j", source=on_drive, dest="ok"), Profile(), drive)))
            self.assertIn("source-contains-drive", issue_codes(validate_job(Job(id="j", source=tmp, dest="ok"), Profile(), drive)))

    def test_dest_empty_absolute_parent_and_reserved_names_at_any_depth(self):
        cases = [("", "dest-empty"), ("/absolute", "dest-absolute"), (r"\absolute", "dest-absolute"),
                 ("a/../b", "dest-parent"), ("safe/.versions/x", "dest-reserved"),
                 (".plugbackup", "dest-reserved"), ("safe\\.rsync-partial\\x", "dest-reserved")]
        for dest, expected in cases:
            with self.subTest(dest=dest):
                self.assertIn(expected, issue_codes(validate_dest(dest)))
        self.assertEqual(validate_dest("Проекты//2026/./Июнь/"), [])

    def test_dest_overlap_is_component_aware_and_detects_nesting(self):
        current = Job(id="current", source="", dest="Проекты")
        profile = Profile(jobs=[current, Job(id="child", source="", dest="Проекты/2026")])
        self.assertIn("dest-overlap", issue_codes(validate_job(current, profile)))
        current.dest = "Проекты2"
        profile.jobs[1].dest = "Проекты"
        self.assertNotIn("dest-overlap", issue_codes(validate_job(current, profile)))
        profile.jobs[1].dest = "Проекты2"
        self.assertIn("dest-overlap", issue_codes(validate_job(current, profile)))

    def test_source_overlap_warns_mirror_warns_and_bad_size_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = os.path.join(tmp, "Проекты")
            child = os.path.join(parent, "подпапка")
            os.makedirs(child)
            job = Job(id="j1", source=parent, dest="one", mode="mirror", max_size_mb=0)
            other = Job(id="j2", source=child, dest="two")
            issues = validate_job(job, Profile(jobs=[job, other]))
            codes = issue_codes(issues)
            self.assertIn("source-overlap", codes)
            self.assertIn("mirror-irreversible", codes)
            self.assertIn("max-size-invalid", codes)
            self.assertTrue(all(i.level == "warning" for i in issues if i.code in {"source-overlap", "mirror-irreversible"}))

    def test_exclude_newline_is_rejected(self):
        issues = validate_job(Job(id="j", source="", dest="ok", excludes=["good", "bad\nline"]), Profile())
        self.assertIn("exclude-newline", issue_codes(issues))

    def test_name_component_and_profile_validation(self):
        for name in ("", "  \t", ".", "..", "a/b", "a\\b"):
            with self.subTest(name=name):
                self.assertTrue(has_errors(validate_name_component(name)))
        for name in (".versions", ".plugbackup", ".rsync-partial"):
            with self.subTest(name=name):
                self.assertIn("root-reserved", issue_codes(validate_name_component(name)))
        self.assertEqual(validate_name_component("Работа 東京"), [])
        profile = Profile(name=" \t", auto=AutoSettings(countdown_sec=601))
        self.assertEqual(issue_codes(validate_profile(profile)), {"name-empty", "countdown-invalid"})


class BindingSelectionAndExportTests(unittest.TestCase):
    def test_profile_for_volume_and_rebind_candidates(self):
        same = Profile(id="same", device=DeviceBinding(uuid="uuid-a", serial="ser"))
        old = Profile(id="old", device=DeviceBinding(uuid="uuid-old", serial="ser"))
        unrelated = Profile(id="other", device=DeviceBinding(uuid="uuid-x", serial="other"))
        volume = SimpleNamespace(uuid="uuid-a", drive_serial="ser")
        self.assertIs(profile_for_volume([old, same], volume), same)
        new_volume = SimpleNamespace(uuid="uuid-new", drive_serial="ser")
        self.assertEqual(rebind_candidates([old, unrelated], new_volume, set()), [old])
        self.assertEqual(rebind_candidates([old], new_volume, {"uuid-old"}), [])
        self.assertEqual(rebind_candidates([old], SimpleNamespace(uuid="uuid-new", drive_serial=""), set()), [])
        self.assertEqual(rebind_candidates([same, old], volume, set()), [])

    def test_clone_has_new_ids_no_binding_and_does_not_mutate_source(self):
        original = Profile(id="p", name="Исходный", device=DeviceBinding(uuid="uuid", serial="serial"),
                           device_sync_pending=True, jobs=[Job(id="j1"), Job(id="j2")])
        before = profile_to_dict(original)
        clone = clone_for_other_device(original, "Клон")
        self.assertNotEqual(clone.id, original.id)
        self.assertEqual(clone.name, "Клон")
        self.assertNotEqual({j.id for j in clone.jobs}, {j.id for j in original.jobs})
        self.assertEqual(clone.device, DeviceBinding())
        self.assertFalse(clone.device_sync_pending)
        self.assertEqual(profile_to_dict(original), before)

    def test_export_import_format_version_and_automation(self):
        profile = Profile(id="p", name="Работа", auto=AutoSettings(enabled=True), jobs=[Job(id="j")])
        exported = export_profile(profile)
        self.assertEqual((exported["format"], exported["version"]), (EXPORT_FORMAT, EXPORT_VERSION))
        self.assertNotIn("device_sync_pending", exported["profile"])
        imported = import_profile(exported)
        self.assertFalse(imported.auto.enabled)
        self.assertFalse(imported.device_sync_pending)
        with self.assertRaises(ProfileFormatError):
            import_profile({"format": "other", "version": 1, "profile": {"id": "p"}})
        with self.assertRaises(ProfileFormatError):
            import_profile({"format": EXPORT_FORMAT, "version": EXPORT_VERSION + 1, "profile": {"id": "p"}})


class MarkerTests(unittest.TestCase):
    def test_all_root_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "missing")
            self.assertEqual(root_state(missing, "p"), ROOT_MISSING)
            root = os.path.join(tmp, "root")
            os.mkdir(root)
            self.assertEqual(root_state(root, "p"), ROOT_EMPTY)
            os.mkdir(os.path.join(root, ".plugbackup"))
            self.assertEqual(root_state(root, "p"), ROOT_EMPTY)
            with open(os.path.join(root, "data"), "w", encoding="utf-8") as stream:
                stream.write("data")
            self.assertEqual(root_state(root, "p"), ROOT_FOREIGN_DATA)
            profile = Profile(id="p", jobs=[Job(id="j", dest="Проекты")])
            write_marker(root, profile)
            self.assertEqual(root_state(root, "p"), ROOT_MATCH)
            self.assertEqual(root_state(root, "else"), ROOT_OTHER_PROFILE)
            with open(os.path.join(root, ".plugbackup", "id"), "w", encoding="utf-8") as stream:
                stream.write("{")
            self.assertEqual(root_state(root, "p"), ROOT_CORRUPT)

    def test_marker_has_job_destinations_but_no_pc_source_paths(self):
        with tempfile.TemporaryDirectory() as root:
            profile = Profile(id="p", jobs=[Job(id="j", source="/pc/private/資料", dest="Проекты/東京")])
            write_marker(root, profile)
            marker = read_marker(root)
            self.assertEqual(marker.profile_id, "p")
            self.assertEqual(marker.jobs, {"j": "Проекты/東京"})
            with open(os.path.join(root, ".plugbackup", "id"), encoding="utf-8") as stream:
                raw = stream.read()
            self.assertNotIn("/pc/private", raw)
            self.assertNotIn("資料", raw)
            self.assertEqual(json.loads(raw)["format"], MARKER_FORMAT)

    def test_sync_writes_and_removes_profile_copy_and_copy_can_be_imported(self):
        with tempfile.TemporaryDirectory() as root:
            profile = Profile(id="p", name="Работа", store_profile_on_device=True,
                              auto=AutoSettings(enabled=True), jobs=[Job(id="j", source="/pc/source", dest="Data")])
            sync_device_files(root, profile)
            raw = read_profile_copy(root)
            self.assertTrue(os.path.exists(os.path.join(root, ".plugbackup", PROFILE_COPY_FILE)))
            self.assertEqual(import_profile(raw).id, "p")
            self.assertFalse(import_profile(raw).auto.enabled)
            profile.store_profile_on_device = False
            sync_device_files(root, profile)
            self.assertIsNone(read_profile_copy(root))
            self.assertEqual(read_marker(root).profile_id, "p")

    def test_malformed_marker_and_copy_raise_marker_error(self):
        with tempfile.TemporaryDirectory() as root:
            service = os.path.join(root, ".plugbackup")
            os.mkdir(service)
            with open(os.path.join(service, "id"), "w", encoding="utf-8") as stream:
                json.dump({"format": MARKER_FORMAT, "version": 1, "profile_id": "p", "jobs": {"j": 7}}, stream)
            with self.assertRaises(MarkerError):
                read_marker(root)


if __name__ == "__main__":
    unittest.main()
