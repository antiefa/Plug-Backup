import unittest

import gi

gi.require_version("Pango", "1.0")
from gi.repository import Pango  # noqa: E402

from plugbackup import help_en, help_ru  # noqa: E402


class HelpTest(unittest.TestCase):
    def test_every_section_is_valid_pango_markup(self):
        keys = set()
        for key, title, markup in help_ru.SECTIONS:
            with self.subTest(section=key):
                self.assertNotIn(key, keys)
                keys.add(key)
                self.assertTrue(title.strip())
                Pango.parse_markup(markup, -1, "\0")  # бросает исключение при ошибке разметки

    def test_english_help_matches_russian_sections(self):
        self.assertEqual([k for k, _t, _m in help_en.SECTIONS], [k for k, _t, _m in help_ru.SECTIONS])
        for key, title, markup in help_en.SECTIONS:
            with self.subTest(section=key):
                self.assertTrue(title.strip())
                self.assertNotRegex(title + markup, "[А-Яа-яЁё]")   # без русских остатков
                Pango.parse_markup(markup, -1, "\0")

    def test_covers_main_topics(self):
        keys = {key for key, _t, _m in help_ru.SECTIONS}
        self.assertTrue({"start", "profiles", "jobs", "backup", "auto", "maintenance", "restore", "journal",
                         "filesystems", "cli", "files", "faq"} <= keys)


if __name__ == "__main__":
    unittest.main()
