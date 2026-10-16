"""Пароли LUKS-носителей в связке ключей: libsecret, атрибуты app=plugbackup, profile=<id>.

Все функции синхронные (D-Bus) — из интерфейса их вызывают в отдельном потоке.
Пароль никогда не пишется в журнал, настройки и сообщения об ошибках.
"""

from __future__ import annotations

import gi
from gi.repository import GLib

try:
    gi.require_version("Secret", "1")
    from gi.repository import Secret
except (ValueError, ImportError):  # без libsecret пароль просто не запоминается
    Secret = None

APP = "plugbackup"

# Состояние службы связки ключей
OK = "ok"
LOCKED = "locked"            # связка ключей заблокирована: чтение пароля вызвало бы окно разблокировки связки
UNAVAILABLE = "unavailable"  # нет libsecret, службы Secret Service или связки по умолчанию

_schema = None


def _get_schema():
    global _schema
    if _schema is None:
        _schema = Secret.Schema.new("io.github.plugbackup.DrivePassword", Secret.SchemaFlags.NONE,
                                    {"app": Secret.SchemaAttributeType.STRING,
                                     "profile": Secret.SchemaAttributeType.STRING})
    return _schema


def _attributes(profile_id: str) -> dict[str, str]:
    return {"app": APP, "profile": profile_id}


def service_state() -> str:
    """Можно ли прямо сейчас прочитать или сохранить пароль без лишних окон."""
    if Secret is None:
        return UNAVAILABLE
    try:
        service = Secret.Service.get_sync(Secret.ServiceFlags.OPEN_SESSION, None)
        collection = Secret.Collection.for_alias_sync(service, Secret.COLLECTION_DEFAULT,
                                                      Secret.CollectionFlags.NONE, None)
        if collection is None:
            return UNAVAILABLE
        return LOCKED if collection.get_locked() else OK
    except GLib.Error:
        return UNAVAILABLE


def lookup(profile_id: str) -> str | None:
    """Сохранённый пароль или None. При заблокированной связке служба может показать окно её разблокировки."""
    if Secret is None or not profile_id:
        return None
    try:
        return Secret.password_lookup_sync(_get_schema(), _attributes(profile_id), None)
    except GLib.Error:
        return None


def store(profile_id: str, label: str, password: str) -> bool:
    if Secret is None or not profile_id:
        return False
    try:
        return bool(Secret.password_store_sync(_get_schema(), _attributes(profile_id), Secret.COLLECTION_DEFAULT,
                                               label, password, None))
    except GLib.Error:
        return False


def clear(profile_id: str) -> bool:
    """Забыть пароль профиля. True — если что-то было удалено."""
    if Secret is None or not profile_id:
        return False
    try:
        return bool(Secret.password_clear_sync(_get_schema(), _attributes(profile_id), None))
    except GLib.Error:
        return False
