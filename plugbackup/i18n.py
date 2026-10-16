"""Локализация через gettext.

Исходные строки в коде — на английском; русский перевод — po/ru.po.
Каталог переводов: /usr/share/locale (установленный пакет) или
PLUGBACKUP_LOCALEDIR (сборка и тесты из исходников).
"""

import gettext
import os

from plugbackup import GETTEXT_DOMAIN

LOCALEDIR = os.environ.get("PLUGBACKUP_LOCALEDIR", "/usr/share/locale")

_translation = gettext.translation(GETTEXT_DOMAIN, LOCALEDIR, fallback=True)


def _(message: str) -> str:
    return _translation.gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    return _translation.ngettext(singular, plural, n)


def language() -> str:
    """Язык перевода интерфейса («ru») или "" — показываются исходные английские строки."""
    info = _translation.info() if hasattr(_translation, "info") else {}
    return (info.get("language") or "").split("_")[0]
