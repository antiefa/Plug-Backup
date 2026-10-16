"""Автозапуск текущей программы через HKCU Run."""
from __future__ import annotations

import sys

from plugbackup.plat.windows import win32


def _command() -> str:
    import subprocess
    if getattr(sys, "frozen", False):
        args = [sys.executable, "--hidden"]
    else:
        args = [sys.executable, "-m", "plugbackup", "--hidden"]
    return subprocess.list2cmdline(args)


def is_enabled() -> bool:
    value = win32.startup_get()
    return bool(value and value == _command())


def set_enabled(enabled: bool) -> None:
    if enabled:
        win32.startup_set(_command())
    else:
        win32.startup_delete()
