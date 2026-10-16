"""Сопоставление правил исключения подмножества фильтров rsync."""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Pattern

from plugbackup.core import rsync
from plugbackup.engine.models import CopySpec


@lru_cache(maxsize=1024)
def _translate(pattern: str) -> Pattern[str]:
    """Преобразовать подмножество шаблона rsync в регулярное выражение."""
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            i += 1
            out.append(re.escape(pattern[i]))
        elif ch == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                while i + 1 < len(pattern) and pattern[i + 1] == "*":
                    i += 1
                if i + 1 < len(pattern) and pattern[i + 1] == "/":
                    out.append("(?:.*/)?")
                    i += 1
                else:
                    out.append(".*")
            else:
                out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch == "[":
            end = i + 1
            if end < len(pattern) and pattern[end] in "!^":
                end += 1
            if end < len(pattern) and pattern[end] == "]":
                end += 1
            while end < len(pattern) and pattern[end] != "]":
                end += 1
            if end >= len(pattern):
                out.append(r"\[")
            else:
                body = pattern[i + 1:end]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", r"\\") + "]")
                i = end
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def rule_matches(path: str, is_dir: bool, pattern: str) -> bool:
    """Проверить правило по пути с учётом привязки и типа объекта."""
    rule = pattern.strip()
    if not rule:
        return False
    anchored = rule.startswith("/")
    if anchored:
        rule = rule[1:]
    directory_only = rule.endswith("/")
    if directory_only:
        rule = rule[:-1]
    if not rule or (directory_only and not is_dir):
        return False
    compiled = _translate(rule)
    components = path.split("/")
    contains_slash = "/" in rule
    candidates = []
    upper = len(components) if is_dir or not directory_only else len(components) - 1
    for end in range(1, upper + 1):
        prefix = components[:end]
        if not contains_slash and "**" not in rule:
            candidates.append(prefix[-1])
        elif anchored:
            candidates.append("/".join(prefix))
        else:
            candidates.extend("/".join(prefix[i:]) for i in range(len(prefix)))
    return any(compiled.match(candidate) for candidate in candidates)


def excluded(path: str, is_dir: bool, spec: CopySpec) -> bool:
    """Проверить пользовательские и служебные исключения."""
    path = path.strip("/")
    if path == rsync.PARTIAL_DIR or path.startswith(rsync.PARTIAL_DIR + "/"):
        return True
    if path == ".versions" or path.startswith(".versions/") or path == ".plugbackup" or path.startswith(".plugbackup/"):
        return True
    for exact in spec.extra_excludes:
        if rule_matches(path, is_dir, "/" + rsync.escape_pattern(exact)):
            return True
    return any(rule_matches(path, is_dir, rule) for rule in spec.excludes)
