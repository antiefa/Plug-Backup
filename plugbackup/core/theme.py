"""Выбор темы окон приложения: как в системе, светлая или тёмная.

Тему системы приложение не меняет — только свои окна (Gtk.Settings процесса).
Для тем Mint (Mint-L, Mint-Y, Mint-X) подбирается светлая/тёмная пара с тем же акцентом:
Mint-L-Darker-Aqua → светлая Mint-L-Aqua, тёмная Mint-L-Dark-Aqua. Для прочих тем — пары вида
«Тема» / «Тема-dark», а если их нет — просьба к GTK использовать тёмный вариант темы.
"""
from __future__ import annotations

import os
import re

from plugbackup import plat
SYSTEM = "system"
LIGHT = "light"
DARK = "dark"
MODES = (SYSTEM, LIGHT, DARK)

_MINT = re.compile(r"^(Mint-[LXY])(?:-(Darker|Dark))?(?:-(.+))?$")


def theme_dirs() -> list[str]:
    return plat.paths.theme_dirs()


def installed_gtk3_themes(dirs: list[str] | None = None) -> set[str]:
    found = set()
    for base in dirs if dirs is not None else theme_dirs():
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for name in names:
            if os.path.isdir(os.path.join(base, name, "gtk-3.0")):
                found.add(name)
    return found


def _mint_pair(name: str) -> tuple[str, str] | None:
    m = _MINT.match(name)
    if not m:
        return None
    family, _shade, accent = m.groups()
    suffix = f"-{accent}" if accent else ""
    return f"{family}{suffix}", f"{family}-Dark{suffix}"


def _generic_pair(name: str, installed: set[str]) -> tuple[str | None, str | None]:
    lower = name.lower()
    for tail in ("-darker", "-dark"):
        if lower.endswith(tail):
            light = name[: -len(tail)]
            return (light if light in installed else None), name
    for tail in ("-dark", "-Dark"):
        if name + tail in installed:
            return name, name + tail
    return name, None


def resolve(mode: str, system_theme: str, installed: set[str]) -> tuple[str, bool]:
    """Какую тему поставить окнам и просить ли у GTK тёмный вариант.

    Возвращает (имя темы, prefer_dark). Для SYSTEM — тема системы без изменений.
    """
    if mode not in (LIGHT, DARK) or not system_theme:
        return system_theme, False
    pair = _mint_pair(system_theme)
    if pair is not None:
        light, dark = pair
        target = light if mode == LIGHT else dark
        if target in installed:
            return target, False
        return system_theme, mode == DARK
    light, dark = _generic_pair(system_theme, installed)
    if mode == LIGHT:
        return (light or system_theme), False
    if dark:
        return dark, False
    return system_theme, True


def can_switch(mode: str, system_theme: str, installed: set[str]) -> bool:
    """Получится ли нужный вид сменой темы. False — остаётся только просьба к GTK, результат зависит от темы."""
    if mode == SYSTEM:
        return True
    if not system_theme:
        return False
    name, _prefer = resolve(mode, system_theme, installed)
    if name != system_theme:
        return True
    darkish = "dark" in system_theme.lower()
    return darkish if mode == DARK else not darkish
