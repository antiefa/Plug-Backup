"""Обслуживание копии и выбор темы; всё — во временных каталогах."""

import os
import shutil
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from plugbackup.core import maintenance, marker, theme
from plugbackup.core.binding import BindingError
from plugbackup.core.profiles import DeviceBinding, Job, Profile


class TemporaryTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="plugbackup-maint-")
        self.addCleanup(self.tmp.cleanup)
        self.base = self.tmp.name

    def path(self, *parts):
        return os.path.join(self.base, *parts)

    def put(self, path, data="x"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(data)

    def read(self, path):
        with open(path, encoding="utf-8") as stream:
            return stream.read()


class MaintenanceTests(TemporaryTree):
    def test_list_and_delete_versions_only_accept_valid_bucket_directories(self):
        root = self.path("root")
        stamp = "2026-10-15T14-30-05Z"
        valid = os.path.join(root, ".versions", stamp)
        self.put(os.path.join(valid, "Заметки и планы", "файл.txt"), "текст")
        self.put(os.path.join(root, ".versions", "not-a-version", "keep"))
        os.makedirs(os.path.join(root, ".versions", "2026-10-15T14-30-06Z"))
        versions = maintenance.list_versions(root)
        empty_stamp = "2026-10-15T14-30-06Z"
        self.assertEqual([v.name for v in versions], [stamp, empty_stamp])
        self.assertEqual(versions[0].files, 1)
        self.assertEqual(versions[0].size, len("текст".encode()))
        removed = maintenance.delete_versions(root, ["../not-a-version", "", "/", stamp])
        self.assertEqual(removed, [stamp])
        self.assertFalse(os.path.exists(valid))
        self.assertTrue(os.path.exists(os.path.join(root, ".versions", "not-a-version", "keep")))

    def test_delete_versions_ignores_symlink_version_folder(self):
        root, outside = self.path("root"), self.path("outside")
        stamp = "2026-10-15T14-30-05Z"
        self.put(os.path.join(outside, "keep"))
        os.makedirs(os.path.join(root, ".versions"), exist_ok=True)
        os.symlink(outside, os.path.join(root, ".versions", stamp))
        self.assertEqual(maintenance.delete_versions(root, [stamp]), [])
        self.assertTrue(os.path.exists(os.path.join(outside, "keep")))

    @unittest.skipUnless(shutil.which("rsync"), "нужен установленный rsync")
    def test_excluded_in_copy_uses_real_rsync_and_preserves_nonexcluded_deletions(self):
        source, dest = self.path("source"), self.path("copy")
        os.makedirs(source)
        os.makedirs(dest)
        self.put(os.path.join(source, "актуально.txt"), "new")
        self.put(os.path.join(dest, "актуально.txt"), "old")
        self.put(os.path.join(dest, "cache space", "оставить.bin"), "excluded")
        self.put(os.path.join(dest, "устарело.txt"), "stale")
        job = Job(source=source, dest="Задание", excludes=["cache space/"])
        excluded = maintenance.excluded_in_copy(job, dest, "ext4", [])
        self.assertIn("cache space/оставить.bin", excluded)
        self.assertNotIn("устарело.txt", excluded)
        self.assertTrue(os.path.exists(os.path.join(dest, "устарело.txt")))

    def test_remove_paths_unlinks_external_symlink_without_following_it(self):
        dest, outside = self.path("copy"), self.path("outside")
        os.makedirs(dest)
        self.put(os.path.join(outside, "precious.txt"), "safe")
        os.symlink(outside, os.path.join(dest, "ссылка"))
        self.assertEqual(maintenance.remove_paths(dest, ["ссылка"], None), (1, 0))
        self.assertFalse(os.path.lexists(os.path.join(dest, "ссылка")))
        self.assertEqual(self.read(os.path.join(outside, "precious.txt")), "safe")

    def test_remove_paths_moves_external_symlink_as_link(self):
        dest, basket, outside = self.path("copy"), self.path("basket"), self.path("outside")
        os.makedirs(dest)
        self.put(os.path.join(outside, "precious.txt"), "safe")
        os.symlink(outside, os.path.join(dest, "folder-link"))
        self.assertEqual(maintenance.remove_paths(dest, ["folder-link"], basket), (1, 0))
        moved = os.path.join(basket, "folder-link")
        self.assertTrue(os.path.islink(moved))
        self.assertEqual(os.readlink(moved), outside)
        self.assertEqual(self.read(os.path.join(outside, "precious.txt")), "safe")

    def test_remove_paths_rejects_parent_and_absolute_paths(self):
        dest, outside = self.path("copy"), self.path("outside.txt")
        os.makedirs(dest)
        self.put(outside, "untouched")
        self.put(os.path.join(dest, "inside.txt"))
        self.assertEqual(maintenance.remove_paths(dest, ["../outside.txt", outside], None), (0, 0))
        self.assertEqual(self.read(outside), "untouched")
        self.assertTrue(os.path.exists(os.path.join(dest, "inside.txt")))

    def test_remove_paths_moves_nested_unicode_names_and_removes_empty_dirs(self):
        dest, basket = self.path("copy"), self.path("basket")
        self.put(os.path.join(dest, "папка с пробелом", "вложенная", "файл ё.txt"), "payload")
        result = maintenance.remove_paths(
            dest,
            ["папка с пробелом/вложенная/файл ё.txt", "папка с пробелом/вложенная/", "папка с пробелом/"],
            basket,
        )
        self.assertEqual(result, (1, 2))
        self.assertEqual(self.read(os.path.join(basket, "папка с пробелом", "вложенная", "файл ё.txt")), "payload")
        self.assertFalse(os.path.exists(os.path.join(dest, "папка с пробелом")))

    def test_remove_paths_does_not_overwrite_existing_basket_file(self):
        # Поведение выбрано при приёмке: существующая версия сохраняется, новый файл получает суффикс ~2.
        dest, basket = self.path("copy"), self.path("basket")
        self.put(os.path.join(dest, "report.txt"), "new copy")
        self.put(os.path.join(basket, "report.txt"), "older version")
        maintenance.remove_paths(dest, ["report.txt"], basket)
        self.assertEqual(self.read(os.path.join(basket, "report.txt")), "older version")
        self.assertEqual(self.read(os.path.join(basket, "report~2.txt")), "new copy")

    def test_remove_paths_recognizes_directory_without_trailing_slash(self):
        dest = self.path("copy")
        os.makedirs(os.path.join(dest, "empty"))
        self.assertEqual(maintenance.remove_paths(dest, ["empty"], None), (0, 1))
        self.assertFalse(os.path.exists(os.path.join(dest, "empty")))

    def test_clear_dir_removes_external_directory_symlink_as_link(self):
        dest, outside = self.path("copy"), self.path("outside")
        os.makedirs(dest)
        self.put(os.path.join(outside, "keep.txt"), "safe")
        os.symlink(outside, os.path.join(dest, "linked-folder"))
        self.assertEqual(maintenance.clear_dir(dest, None), (1, 0))
        self.assertFalse(os.path.lexists(os.path.join(dest, "linked-folder")))
        self.assertTrue(os.path.exists(os.path.join(outside, "keep.txt")))

    def test_copy_contents_counts_files_symlinks_and_empty_directories_are_not_files(self):
        dest, outside = self.path("copy"), self.path("outside.txt")
        os.makedirs(os.path.join(dest, "empty"))
        self.put(outside, "external")
        self.put(os.path.join(dest, "текстовый файл.txt"), "1234")
        os.symlink(outside, os.path.join(dest, "link"))
        paths, size = maintenance.copy_contents(dest)
        self.assertEqual(paths, ["link", "текстовый файл.txt"])
        self.assertEqual(size, os.lstat(os.path.join(dest, "link")).st_size + 4)

    def test_clear_dir_moves_files_to_bucket_and_leaves_empty_destination_root(self):
        dest, basket = self.path("copy"), self.path("basket")
        self.put(os.path.join(dest, "a", "b", "файл.txt"), "data")
        self.assertEqual(maintenance.clear_dir(dest, basket), (1, 2))
        self.assertTrue(os.path.isdir(dest))
        self.assertEqual(self.read(os.path.join(basket, "a", "b", "файл.txt")), "data")

    def test_versions_target_uses_unique_utc_bucket_and_normalized_destination(self):
        root = self.path("root")
        moment = time.strptime("2026-10-15T14-30-05Z", "%Y-%m-%dT%H-%M-%SZ")
        with mock.patch.object(maintenance.time, "gmtime", return_value=moment):
            first = maintenance.versions_target(root, "folder\\subfolder")
            os.makedirs(os.path.dirname(first), exist_ok=True)
            second = maintenance.versions_target(root, "folder\\subfolder")
        self.assertTrue(first.endswith(".versions/2026-10-15T14-30-05Z/folder/subfolder"))
        self.assertTrue(second.endswith(".versions/2026-10-15T14-30-05Z-2/folder/subfolder"))

    def test_prepare_only_after_mocked_mount_and_matching_marker(self):
        mount = self.path("fake-mount")
        root = os.path.join(mount, "PlugBackup")
        os.makedirs(root)
        job = Job(id="job-id", dest="Documents/Проекты")
        profile = Profile(id="profile-id", root="PlugBackup", device=DeviceBinding(uuid="fake-uuid"), jobs=[job])
        marker.write_marker(root, profile)
        volume = SimpleNamespace(uuid="fake-uuid", fs_type="ext4", mount_point=mount,
                                  mounted=True, locked=False, fs_uuid="fake-fs-uuid")
        with mock.patch.object(maintenance.binding, "_verified_mount_point", lambda p, v: mount):
            self.assertEqual(maintenance.prepare(profile, volume, job), (root, os.path.join(root, "Documents/Проекты")))
            self.assertEqual(maintenance.prepare(profile, volume), (root, None))

    def test_prepare_rejects_job_missing_from_marker_with_mocked_mount(self):
        mount = self.path("fake-mount")
        root = os.path.join(mount, "PlugBackup")
        os.makedirs(root)
        profile = Profile(id="profile-id", root="PlugBackup", device=DeviceBinding(uuid="fake-uuid"),
                          jobs=[Job(id="other", dest="Documents")])
        marker.write_marker(root, Profile(id=profile.id, jobs=[]))
        volume = SimpleNamespace(uuid="fake-uuid", fs_type="ext4", mount_point=mount,
                                  mounted=True, locked=False, fs_uuid="fake-fs-uuid")
        with mock.patch.object(maintenance.binding, "_verified_mount_point", lambda p, v: mount):
            with self.assertRaises(BindingError) as error:
                maintenance.prepare(profile, volume, profile.jobs[0])
        self.assertEqual(error.exception.code, "job-no-marker")


class ThemeTests(TemporaryTree):
    def test_installed_gtk3_themes_reads_only_explicit_temporary_directory(self):
        base = self.path("themes")
        for name in ("Mint-L-Aqua", "Adwaita-dark", "Arc-Darker"):
            os.makedirs(os.path.join(base, name, "gtk-3.0"))
        os.makedirs(os.path.join(base, "NoGtk3"))
        self.assertEqual(theme.installed_gtk3_themes(dirs=[base]), {"Mint-L-Aqua", "Adwaita-dark", "Arc-Darker"})

    def test_mint_accent_and_shade_pairs(self):
        installed = {"Mint-L-Aqua", "Mint-L-Dark-Aqua", "Mint-Y-Teal", "Mint-Y-Dark-Teal",
                     "Mint-X-Pink", "Mint-X-Dark-Pink"}
        self.assertEqual(theme.resolve(theme.LIGHT, "Mint-L-Darker-Aqua", installed), ("Mint-L-Aqua", False))
        self.assertEqual(theme.resolve(theme.DARK, "Mint-L-Darker-Aqua", installed), ("Mint-L-Dark-Aqua", False))
        self.assertEqual(theme.resolve(theme.LIGHT, "Mint-Y-Dark-Teal", installed), ("Mint-Y-Teal", False))
        self.assertEqual(theme.resolve(theme.DARK, "Mint-Y-Teal", installed), ("Mint-Y-Dark-Teal", False))
        self.assertEqual(theme.resolve(theme.LIGHT, "Mint-X-Darker-Pink", installed), ("Mint-X-Pink", False))
        self.assertEqual(theme.resolve(theme.DARK, "Mint-X-Pink", installed), ("Mint-X-Dark-Pink", False))
        self.assertTrue(theme.can_switch(theme.DARK, "Mint-Y-Teal", installed))

    def test_mint_missing_variant_falls_back_to_system_theme_and_gtk_preference(self):
        installed = set()
        self.assertEqual(theme.resolve(theme.DARK, "Mint-L-Aqua", installed), ("Mint-L-Aqua", True))
        self.assertEqual(theme.resolve(theme.LIGHT, "Mint-L-Dark-Aqua", installed), ("Mint-L-Dark-Aqua", False))

    def test_generic_light_dark_and_darker_theme_pairs(self):
        installed = {"Adwaita", "Adwaita-dark", "Arc", "Arc-Darker"}
        self.assertEqual(theme.resolve(theme.DARK, "Adwaita", installed), ("Adwaita-dark", False))
        self.assertEqual(theme.resolve(theme.LIGHT, "Adwaita-dark", installed), ("Adwaita", False))
        self.assertEqual(theme.resolve(theme.LIGHT, "Arc-Darker", installed), ("Arc", False))
        self.assertEqual(theme.resolve(theme.DARK, "Arc-Darker", installed), ("Arc-Darker", False))
        self.assertTrue(theme.can_switch(theme.DARK, "Arc-Darker", installed))

    def test_system_mode_preserves_theme_and_empty_name_is_returned_safely(self):
        installed = {"Adwaita", "Adwaita-dark"}
        self.assertEqual(theme.resolve(theme.SYSTEM, "Adwaita-dark", installed), ("Adwaita-dark", False))
        self.assertEqual(theme.resolve(theme.DARK, "", installed), ("", False))

    def test_empty_theme_cannot_report_a_successful_light_switch(self):
        self.assertFalse(theme.can_switch(theme.LIGHT, "", set()))


if __name__ == "__main__":
    unittest.main()
