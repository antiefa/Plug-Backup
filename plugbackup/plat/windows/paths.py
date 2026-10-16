"""Каталоги пользовательских данных Windows."""
from __future__ import annotations

import os

from plugbackup.plat.windows import win32


def config_dir() -> str:
    return win32.roaming_app_data()


def state_dir() -> str:
    return os.path.join(win32.local_app_data(), "state")


def runtime_dir() -> str:
    return os.path.join(win32.local_app_data(), "run")


def restore_backup_root() -> str:
    return os.path.join(win32.local_app_data(), "restore-backup")
