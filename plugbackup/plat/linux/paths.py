"""Пути данных Linux в соответствии с XDG."""
from __future__ import annotations

import os


def config_home() -> str:
    return os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")


def state_home() -> str:
    return os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")


def data_home() -> str:
    return os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")


def runtime_dir() -> str:
    path = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/plugbackup-{os.getuid()}", "plugbackup")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def config_dir() -> str:
    return os.path.join(config_home(), "plugbackup")


def state_dir() -> str:
    return os.path.join(state_home(), "plugbackup")


def restore_backup_root() -> str:
    return os.path.join(data_home(), "plugbackup", "restore-backup")


def autostart_dir() -> str:
    return os.path.join(config_home(), "autostart")


def theme_dirs() -> list[str]:
    home = os.path.expanduser("~")
    data_home_path = data_home()
    dirs = [os.path.join(home, ".themes"), os.path.join(data_home_path, "themes")]
    for base in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":"):
        if base:
            dirs.append(os.path.join(base, "themes"))
    return dirs
