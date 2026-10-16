"""Запуск при входе в систему: ~/.config/autostart/<ID>.desktop с ключом --hidden."""

from __future__ import annotations

import os

from plugbackup import APP_ID, APP_NAME
from plugbackup.plat.linux.paths import autostart_dir

DESKTOP = f"""[Desktop Entry]
Type=Application
Name={APP_NAME}
Comment=Back up folders to removable drives
Comment[ru]=Резервное копирование папок на сменные носители
Exec=plugbackup --hidden
Icon={APP_ID}
Terminal=false
X-GNOME-Autostart-enabled=true
"""


def autostart_path() -> str:
    return os.path.join(autostart_dir(), f"{APP_ID}.desktop")


def is_enabled() -> bool:
    path = autostart_path()
    if not os.path.isfile(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key, value = key.strip().lower(), value.strip().lower()
        if (key == "x-gnome-autostart-enabled" and value == "false") or (key == "hidden" and value == "true"):
            return False
    return True


def set_enabled(enabled: bool) -> None:
    path = autostart_path()
    if not enabled:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(DESKTOP)
    os.replace(tmp, path)
