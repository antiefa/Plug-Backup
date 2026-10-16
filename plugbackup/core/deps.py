"""Общая модель и форматирование результатов проверки компонентов."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    key: str
    feature: str
    component: str
    ok: bool
    required: bool
    detail: str = ""


def missing_required(checks: list[Check]) -> list[Check]:
    return [check for check in checks if check.required and not check.ok]


def format_report(checks: list[Check]) -> str:
    lines = []
    for check in checks:
        mark = "OK " if check.ok else ("ERR" if check.required else "-- ")
        detail = f" — {check.detail}" if check.detail else ""
        lines.append(f"[{mark}] {check.feature}: {check.component}{detail}")
    return "\n".join(lines)


def run_checks():
    from plugbackup import plat
    return plat.deps.run_checks()
