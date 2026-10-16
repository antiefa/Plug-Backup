"""Проверка границы между ядром и системными интерфейсами."""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_IMPORTS = {"gi", "PyQt5", "PyQt6", "fcntl"}


def imported_root(name):
    return name.split(".", 1)[0]


class BoundaryTests(unittest.TestCase):
    def test_core_and_engine_do_not_import_platform_apis(self):
        folders = [ROOT / "plugbackup" / "core"]
        engine = ROOT / "plugbackup" / "engine"
        if engine.is_dir():
            folders.append(engine)
        violations = []
        for folder in folders:
            for path in folder.rglob("*.py"):
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(path))
                for node in ast.walk(tree):
                    names = []
                    if isinstance(node, ast.Import):
                        names = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        names = [node.module or ""]
                    for name in names:
                        if imported_root(name) in FORBIDDEN_IMPORTS or name == "plugbackup.plat.linux" or name.startswith("plugbackup.plat.linux."):
                            violations.append("{}:{}: import {}".format(path.relative_to(ROOT), node.lineno, name))
                    if isinstance(node, ast.Attribute) and node.attr == "windll":
                        violations.append("{}:{}: windll".format(path.relative_to(ROOT), node.lineno))
                if "/proc/" in source:
                    violations.append("{}: строка /proc/".format(path.relative_to(ROOT)))
        self.assertEqual(violations, [], "\n".join(violations))

    def test_appcore_does_not_import_ui_or_os_specific_apis(self):
        folder = ROOT / "plugbackup" / "appcore"
        forbidden = FORBIDDEN_IMPORTS | {"ctypes"}
        violations = []
        for path in folder.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                for name in names:
                    if imported_root(name) in forbidden:
                        violations.append("{}:{}: import {}".format(path.relative_to(ROOT), node.lineno, name))
                if isinstance(node, ast.Attribute) and node.attr == "windll":
                    violations.append("{}:{}: windll".format(path.relative_to(ROOT), node.lineno))
        self.assertEqual(violations, [], "\n".join(violations))
