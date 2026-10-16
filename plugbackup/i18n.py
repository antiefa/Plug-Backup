"""Локализация через gettext.

Исходные строки в коде — на английском; русский перевод — po/ru.po.
"""
from __future__ import annotations

import ctypes
import gettext
import locale
import os
import sys
from pathlib import Path
from typing import Mapping, Optional

from plugbackup import GETTEXT_DOMAIN


def _find_locale_dir(
    package_dir: Path,
    frozen: bool,
    meipass: Optional[str],
    executable: str,
    env_locale: Optional[str],
) -> str:
    """Находит каталог gettext с учётом раскладки исходников и сборки."""
    if env_locale:
        return env_locale

    if frozen:
        frozen_dirs = []
        if meipass:
            frozen_dirs.append(Path(meipass) / "locale")
        if executable:
            frozen_dirs.append(Path(executable).resolve().parent / "locale")
        for candidate in frozen_dirs:
            if candidate.is_dir():
                return str(candidate)

    source_dir = package_dir.parent / "locale"
    if source_dir.is_dir():
        return str(source_dir)

    return "/usr/share/locale"


def _language_code(value: Optional[str]) -> Optional[str]:
    """Оставляет только поддерживаемые коды языка."""
    if not value:
        return None
    code = value.split(":", 1)[0].split(".", 1)[0].split("@", 1)[0]
    code = code.replace("-", "_").split("_", 1)[0].lower()
    if code == "ru":
        return "ru"
    if code == "en":
        return "en"
    return None


def _environment_language(environ: Mapping[str, str]) -> Optional[str]:
    """Определяет язык по стандартному приоритету переменных gettext."""
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = environ.get(name)
        if value:
            if name == "LANGUAGE":
                for preference in value.split(":"):
                    language_code = _language_code(preference)
                    if language_code:
                        return language_code
                return "en"
            return _language_code(value) or "en"
    return None


def _windows_ui_language() -> Optional[str]:
    """Возвращает язык интерфейса Windows без импорта Win32 на других ОС."""
    try:
        language_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
    except (AttributeError, OSError):
        return None
    windows_locale = locale.windows_locale.get(language_id)
    if windows_locale:
        return _language_code(windows_locale) or "en"
    return None


def _system_language() -> Optional[str]:
    """Определяет язык ОС, когда переменные gettext не заданы."""
    if sys.platform == "win32":
        language_code = _windows_ui_language()
        if language_code:
            return language_code
    # Для будущей поддержки macOS можно добавить системное определение языка.
    try:
        default_locale = locale.getdefaultlocale()
    except (ValueError, TypeError):
        default_locale = None
    if default_locale:
        return _language_code(default_locale[0])
    return None


def _requested_language(environ: Mapping[str, str]) -> str:
    """Выбирает поддерживаемый язык или английский по умолчанию."""
    environment_language = _environment_language(environ)
    if environment_language:
        return environment_language
    return _system_language() or "en"


LOCALEDIR = _find_locale_dir(
    Path(__file__).resolve().parent,
    bool(getattr(sys, "frozen", False)),
    getattr(sys, "_MEIPASS", None),
    sys.executable,
    os.environ.get("PLUGBACKUP_LOCALEDIR"),
)
_requested = _requested_language(os.environ)
_translation = gettext.translation(
    GETTEXT_DOMAIN,
    LOCALEDIR,
    languages=[_requested],
    fallback=True,
)


def _(message: str) -> str:
    return _translation.gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    return _translation.ngettext(singular, plural, n)


def language() -> str:
    """Возвращает язык загруженного перевода или исходных английских строк."""
    if not isinstance(_translation, gettext.GNUTranslations):
        return "en"
    info = _translation.info()
    return _language_code(info.get("language")) or _requested
