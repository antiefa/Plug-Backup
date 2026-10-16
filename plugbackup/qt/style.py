"""Загрузка QSS и ресурсов интерфейса относительно пакета."""
from __future__ import annotations

from pathlib import Path


QT_DIR = Path(__file__).resolve().parent


def load_stylesheet() -> str:
    icon_dir = (QT_DIR / "icons").as_posix()
    return (QT_DIR / "style.qss").read_text(encoding="utf-8").replace("@ICON_DIR@", icon_dir)
