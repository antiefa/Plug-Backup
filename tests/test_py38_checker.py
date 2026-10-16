"""Проверка инструмента совместимости с Python 3.8."""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Python38CheckerTests(unittest.TestCase):
    def test_repository_files_pass_checker(self):
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "check_py38.py")],
                                cwd=str(ROOT), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("нарушений нет", result.stdout)
