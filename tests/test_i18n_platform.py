import importlib
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from plugbackup import GETTEXT_DOMAIN

i18n = importlib.import_module("plugbackup.i18n")
ROOT = Path(__file__).resolve().parent.parent


class PlatformI18nTest(unittest.TestCase):
    def tearDown(self):
        importlib.reload(i18n)

    def reload_i18n(self, environ, platform=None, frozen=False, meipass=None,
                    ui_language=None):
        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, environ, clear=True))
            if platform:
                stack.enter_context(mock.patch.object(sys, "platform", platform))
            stack.enter_context(mock.patch.object(sys, "frozen", frozen, create=True))
            if meipass is not None:
                stack.enter_context(mock.patch.object(sys, "_MEIPASS", str(meipass),
                                                       create=True))
            elif hasattr(sys, "_MEIPASS"):
                stack.enter_context(mock.patch.object(sys, "_MEIPASS", None))
            if ui_language is not None:
                kernel = SimpleNamespace(GetUserDefaultUILanguage=lambda: ui_language)
                stack.enter_context(mock.patch.object(
                    i18n.ctypes, "windll", SimpleNamespace(kernel32=kernel), create=True))
            return importlib.reload(i18n)

    def compile_russian_catalog(self, locale_root):
        mo_path = Path(locale_root) / "ru" / "LC_MESSAGES" / (GETTEXT_DOMAIN + ".mo")
        mo_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["msgfmt", "--check", "-o", str(mo_path),
                        str(ROOT / "po" / "ru.po")], check=True)
        return mo_path

    def test_environment_variables_keep_selecting_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.compile_russian_catalog(tmp)
            environments = (
                {"LANGUAGE": "ru", "LANG": "C.UTF-8"},
                {"LC_ALL": "ru_RU.UTF-8"},
                {"LC_MESSAGES": "ru_RU.UTF-8"},
                {"LANG": "ru_RU.UTF-8"},
            )
            for environment in environments:
                with self.subTest(environment=environment):
                    environment["PLUGBACKUP_LOCALEDIR"] = tmp
                    module = self.reload_i18n(environment)
                    self.assertEqual(module.language(), "ru")
                    self.assertEqual(module._("Mount"), "Подключить")

    def test_windows_ui_language_selects_supported_languages(self):
        cases = ((0x0419, "ru"), (0x0409, "en"), (0x0407, "en"))
        for language_id, expected in cases:
            with self.subTest(language_id=language_id):
                module = self.reload_i18n({}, platform="win32", ui_language=language_id)
                self.assertEqual(module.language(), expected)

    def test_frozen_bundle_catalog_is_loaded_from_meipass(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.compile_russian_catalog(Path(tmp) / "locale")
            module = self.reload_i18n({}, platform="win32", frozen=True, meipass=tmp,
                                      ui_language=0x0419)
            self.assertEqual(Path(module.LOCALEDIR), Path(tmp) / "locale")
            self.assertEqual(module.language(), "ru")
            self.assertEqual(module._("Mount"), "Подключить")

    def test_locale_directory_search_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            override = root / "override"
            source_locale = root / "project" / "locale"
            bundle_locale = root / "bundle" / "locale"
            source_locale.mkdir(parents=True)
            bundle_locale.mkdir(parents=True)
            self.assertEqual(
                i18n._find_locale_dir(root / "project" / "plugbackup", False, None,
                                      sys.executable, str(override)),
                str(override),
            )
            self.assertEqual(
                i18n._find_locale_dir(root / "project" / "plugbackup", True,
                                      str(root / "bundle"), sys.executable, None),
                str(bundle_locale),
            )
            self.assertEqual(
                i18n._find_locale_dir(root / "project" / "plugbackup", False, None,
                                      sys.executable, None),
                str(source_locale),
            )
            self.assertEqual(
                i18n._find_locale_dir(root / "absent" / "plugbackup", False, None,
                                      sys.executable, None),
                "/usr/share/locale",
            )


if __name__ == "__main__":
    unittest.main()
