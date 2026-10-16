"""Форматирование объёма в десятичных единицах, как в GLib."""
from __future__ import annotations

from plugbackup.i18n import language


def format_size(size: int) -> str:
    units = ("Б", "КБ", "МБ", "ГБ", "ТБ", "ПБ") if language() == "ru" else (
        "B", "kB", "MB", "GB", "TB", "PB")
    value = float(max(0, size))
    index = 0
    while value >= 1000 and index < len(units) - 1:
        value /= 1000
        index += 1
    if index == 0:
        amount = str(int(value))
    else:
        amount = "%.1f" % value
        if language() == "ru":
            amount = amount.replace(".", ",")
    return "%s %s" % (amount, units[index])
