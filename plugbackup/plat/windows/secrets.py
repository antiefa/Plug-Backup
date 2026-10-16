"""Учётные данные Windows Credential Manager."""
from __future__ import annotations

from plugbackup.plat.windows import win32

OK = "ok"
LOCKED = "locked"
UNAVAILABLE = "unavailable"


def service_state() -> str:
    try:
        win32._cred_api()
        return OK
    except (OSError, AttributeError):
        return UNAVAILABLE


def lookup(profile_id: str):
    try:
        return win32.credential_read(profile_id) if profile_id else None
    except OSError:
        return None


def store(profile_id: str, label: str, password: str) -> bool:
    try:
        win32.credential_write(profile_id, password)
        return True
    except OSError:
        return False


def clear(profile_id: str) -> bool:
    try:
        win32.credential_delete(profile_id)
        return True
    except OSError:
        return False
