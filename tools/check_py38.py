#!/usr/bin/env python3
"""Проверка синтаксиса и конструкций, отсутствующих в Python 3.8."""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = sorted((ROOT / "plugbackup" / "core").rglob("*.py"))
FILES += sorted((ROOT / "plugbackup" / "plat").rglob("*.py"))
FILES += sorted((ROOT / "plugbackup" / "engine").rglob("*.py"))
FILES += sorted((ROOT / "plugbackup" / "qt").rglob("*.py"))
FILES += sorted((ROOT / "plugbackup" / "appcore").rglob("*.py"))
FILES += sorted((ROOT / "plugbackup" / "plat" / "windows").rglob("*.py"))
FILES += [ROOT / path for path in (
        "plugbackup/plat/__init__.py", "plugbackup/plat/base.py", "plugbackup/cli.py",
        "plugbackup/i18n.py", "plugbackup/help_ru.py", "plugbackup/help_en.py", "plugbackup/__init__.py",
    )]
FILES = sorted(set(FILES))


def descendants(node):
    result = set()
    for child in ast.walk(node):
        result.add(id(child))
    return result


def dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted(node.value)
        return "{}.{}".format(prefix, node.attr) if prefix else node.attr
    return ""


def check_file(path):
    problems = []
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path), feature_version=(3, 8))
    except SyntaxError as exc:
        return ["{}:{}: синтаксис Python 3.8: {}".format(path.relative_to(ROOT), exc.lineno, exc.msg)]

    annotations = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.arg, ast.AnnAssign)) and getattr(node, "annotation", None) is not None:
            annotations.update(descendants(node.annotation))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
            annotations.update(descendants(node.returns))
    if annotations and not any(
        isinstance(node, ast.ImportFrom) and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    ):
        problems.append("{}: нет from __future__ import annotations".format(path.relative_to(ROOT)))

    assigned_dicts = set()
    dataclass_names = {"dataclass"}
    cache_names = {"cache"}
    utc_names = {"UTC"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if node.module == "dataclasses" and alias.name == "dataclass":
                    dataclass_names.add(alias.asname or alias.name)
                if node.module == "functools" and alias.name == "cache":
                    cache_names.add(alias.asname or alias.name)
                if node.module == "datetime" and alias.name == "UTC":
                    utc_names.add(alias.asname or alias.name)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if isinstance(value, ast.Dict):
                assigned_dicts.update(t.id for t in targets if isinstance(t, ast.Name))

    for node in ast.walk(tree):
        line = getattr(node, "lineno", 1)
        label = "{}:{}".format(path.relative_to(ROOT), line)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            if any(name == "zoneinfo" or name == "tomllib" for name in names):
                problems.append("{}: импорт модуля после Python 3.8".format(label))
            if isinstance(node, ast.ImportFrom) and node.module == "typing" and any(a.name in ("TypeAlias", "ParamSpec") for a in node.names):
                problems.append("{}: TypeAlias/ParamSpec недоступны в Python 3.8".format(label))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr) and id(node) not in annotations:
            left_dict = isinstance(node.left, ast.Dict) or isinstance(node.left, ast.Name) and node.left.id in assigned_dicts
            right_dict = isinstance(node.right, ast.Dict) or isinstance(node.right, ast.Name) and node.right.id in assigned_dicts
            if left_dict and right_dict:
                problems.append("{}: объединение словарей через |".format(label))
        if isinstance(node, ast.AugAssign) and isinstance(node.op, ast.BitOr) and isinstance(node.target, ast.Name) and node.target.id in assigned_dicts:
            problems.append("{}: |= для словаря".format(label))
        if isinstance(node, ast.Subscript) and id(node) not in annotations and isinstance(node.value, ast.Name) and node.value.id in ("list", "dict", "set", "tuple", "frozenset", "type"):
            problems.append("{}: обобщение встроенного типа вне аннотации".format(label))
        if isinstance(node, ast.Call):
            function = dotted(node.func)
            if isinstance(node.func, ast.Attribute) and node.func.attr in ("removeprefix", "removesuffix", "bit_count"):
                problems.append("{}: метод {} недоступен в Python 3.8".format(label, node.func.attr))
            if function in ("functools.cache",) or isinstance(node.func, ast.Name) and node.func.id in cache_names:
                problems.append("{}: functools.cache недоступен в Python 3.8".format(label))
            if function == "zip" and any(keyword.arg == "strict" for keyword in node.keywords):
                problems.append("{}: zip(strict=...) недоступен в Python 3.8".format(label))
            if function in ("math.lcm", "lcm"):
                problems.append("{}: math.lcm недоступен в Python 3.8".format(label))
            if (function.rsplit(".", 1)[-1] in dataclass_names or isinstance(node.func, ast.Name) and node.func.id in dataclass_names) and any(k.arg in ("slots", "kw_only") for k in node.keywords):
                problems.append("{}: параметр dataclass недоступен в Python 3.8".format(label))
            if function == "isinstance" and len(node.args) > 1 and isinstance(node.args[1], ast.BinOp) and isinstance(node.args[1].op, ast.BitOr):
                problems.append("{}: isinstance с объединением типов недоступен в Python 3.8".format(label))
        if isinstance(node, ast.Attribute) and dotted(node) == "datetime.UTC":
            problems.append("{}: datetime.UTC недоступен в Python 3.8".format(label))
        if isinstance(node, ast.Name) and node.id in utc_names and isinstance(getattr(node, "ctx", None), ast.Load):
            parent_is_import = False
            if node.id == "UTC":
                parent_is_import = any(isinstance(item, ast.ImportFrom) and item.module == "datetime"
                                       and any(alias.name == "UTC" for alias in item.names) for item in tree.body)
            if parent_is_import and node is not None and not isinstance(node.ctx, ast.Store):
                problems.append("{}: datetime.UTC недоступен в Python 3.8".format(label))
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and node.attr == "cache" and dotted(node.value) == "functools":
            problems.append("{}: functools.cache недоступен в Python 3.8".format(label))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Name) and decorator.id in cache_names:
                    problems.append("{}: functools.cache недоступен в Python 3.8".format(label))
        if isinstance(node, ast.Attribute) and node.attr == "lcm" and dotted(node.value) == "math":
            problems.append("{}: math.lcm недоступен в Python 3.8".format(label))
    return problems


def main():
    problems = []
    for path in FILES:
        problems.extend(check_file(path))
    if problems:
        print("\n".join(problems))
        return 1
    print("Проверено файлов: {}; нарушений нет.".format(len(FILES)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
