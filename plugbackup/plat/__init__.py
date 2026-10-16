"""Ленивая точка доступа к реализации платформенных функций."""
from __future__ import annotations

import importlib
import sys
from types import ModuleType

_MODULES = ("devices", "mounts", "secrets", "autostart", "system", "paths", "deps", "vault")


def __getattr__(name: str) -> ModuleType:
    if name not in _MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if name == "vault" and sys.platform == "win32":
        raise NotImplementedError("Зашифрованная папка пока не поддерживается в Windows")
    if sys.platform.startswith("linux"):
        package = "plugbackup.plat.linux"
    elif sys.platform == "win32":
        package = "plugbackup.plat.windows"
    elif sys.platform == "darwin":
        raise NotImplementedError(f"Платформа {sys.platform!r} пока не поддерживается")
    else:
        raise NotImplementedError(f"Платформа {sys.platform!r} не поддерживается")
    return importlib.import_module(f"{package}.{name}")


__all__ = list(_MODULES)
