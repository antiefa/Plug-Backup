import re
import unittest
import xml.etree.ElementTree as ET

try:
    import gi

    gi.require_version("Pango", "1.0")
    from gi.repository import Pango
except (ImportError, ValueError):
    Pango = None

from plugbackup import help_en, help_ru


class HelpTest(unittest.TestCase):
    def _assert_markup_valid(self, markup):
        # Pango accepts these XML-style tags. ElementTree keeps this check usable
        # on Windows and on Python installations without GTK/PyGObject.
        ET.fromstring("<root>" + markup + "</root>")
        if Pango is not None:
            Pango.parse_markup(markup, -1, "\0")

    def test_platform_sections_match_in_both_languages(self):
        for platform in ("linux", "win32"):
            ru = help_ru.sections(platform)
            en = help_en.sections(platform)
            ru_keys = [key for key, _title, _markup in ru]
            en_keys = [key for key, _title, _markup in en]
            with self.subTest(platform=platform):
                self.assertEqual(ru_keys, en_keys)
                self.assertEqual(len(ru_keys), len(set(ru_keys)))

    def test_markup_is_valid_without_gtk(self):
        for module in (help_ru, help_en):
            for platform in ("linux", "win32"):
                for key, title, markup in module.sections(platform):
                    with self.subTest(language=module.__name__, platform=platform, section=key):
                        self.assertTrue(title.strip())
                        self._assert_markup_valid(markup)

    def test_english_sections_have_no_russian_text(self):
        for platform in ("linux", "win32"):
            for key, title, markup in help_en.sections(platform):
                with self.subTest(platform=platform, section=key):
                    self.assertNotRegex(title + markup, "[А-Яа-яЁё]")

    def test_covers_main_topics_for_each_platform(self):
        for platform, expected in (
            ("linux", {"start", "profiles", "jobs", "backup", "auto", "maintenance",
                        "restore", "journal", "filesystems", "luks", "gocryptfs", "cli",
                        "files", "faq", "cross-os"}),
            ("win32", {"start", "profiles", "jobs", "backup", "auto", "maintenance",
                        "restore", "journal", "filesystems", "bitlocker", "cli", "files",
                        "faq", "cross-os", "install"}),
        ):
            with self.subTest(platform=platform):
                keys = {key for key, _title, _markup in help_ru.sections(platform)}
                self.assertTrue(expected <= keys)

    def test_windows_help_has_no_linux_only_terms(self):
        help_text = "\n".join(title + "\n" + markup for _key, title, markup in help_ru.sections("win32") + help_en.sections("win32"))
        for term in ("Nemo", "UDisks", "udisksctl", "/media", "~/.config", "cryptsetup", "LUKS"):
            with self.subTest(term=term):
                self.assertNotRegex(help_text, re.compile(re.escape(term), re.IGNORECASE))

    def test_linux_help_has_no_windows_only_terms(self):
        help_text = "\n".join(title + "\n" + markup for _key, title, markup in help_ru.sections("linux") + help_en.sections("linux"))
        self.assertNotIn("%APPDATA%", help_text)
        self.assertNotRegex(help_text, re.compile("BitLocker", re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()
