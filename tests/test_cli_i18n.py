import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from plugbackup import __version__

ROOT = Path(__file__).resolve().parent.parent


def run_app(*args, env_extra=None):
    env = dict(os.environ, PYTHONPATH=str(ROOT), **(env_extra or {}))
    return subprocess.run([sys.executable, "-m", "plugbackup", *args],
                          capture_output=True, text=True, env=env, timeout=30)


class CliTest(unittest.TestCase):
    def test_version(self):
        result = run_app("--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"Plug & Backup {__version__}")

    def test_check_prints_report(self):
        result = run_app("--check")
        self.assertIn("rsync", result.stdout)
        self.assertIn(result.returncode, (0, 1))


class TranslationTest(unittest.TestCase):
    def test_russian_catalog_is_complete_and_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            mo_dir = Path(tmp) / "ru" / "LC_MESSAGES"
            mo_dir.mkdir(parents=True)
            subprocess.run(["msgfmt", "--check", "-o", str(mo_dir / "plugbackup.mo"),
                            str(ROOT / "po" / "ru.po")], check=True)
            code = "from plugbackup.i18n import _; print(_('Mount'))"
            env = dict(os.environ, PYTHONPATH=str(ROOT), PLUGBACKUP_LOCALEDIR=tmp,
                       LANGUAGE="ru", LANG="ru_RU.UTF-8")
            out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                                 text=True, env=env, check=True).stdout.strip()
        self.assertEqual(out, "Подключить")

    def test_no_untranslated_or_fuzzy_messages(self):
        result = subprocess.run(["msgfmt", "--statistics", "-o", "/dev/null", str(ROOT / "po" / "ru.po")],
                                capture_output=True, text=True, check=True)
        stats = result.stderr
        self.assertNotIn("неточн", stats)
        self.assertNotIn("fuzzy", stats)
        self.assertNotIn("непереведен", stats)
        self.assertNotIn("untranslated", stats)


if __name__ == "__main__":
    unittest.main()
