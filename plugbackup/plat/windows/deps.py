"""Проверка требований запуска Windows."""
from __future__ import annotations

import importlib.util
import sys

from plugbackup.core.deps import Check
from plugbackup.i18n import _
from plugbackup.plat.windows import win32


def _windows_supported(version) -> bool:
    if version is None:
        return True
    return (version.major, version.minor) > (6, 1) \
        or (version.major, version.minor) == (6, 1) and version.service_pack_major >= 1


def run_checks() -> list[Check]:
    checks = []
    version = sys.getwindowsversion() if sys.platform == "win32" and hasattr(sys, "getwindowsversion") else None
    windows_ok = _windows_supported(version)
    checks.append(Check("windows", _("Operating system"), _("Windows 7 SP1 / 10 / 11"), windows_ok, True,
                        "" if windows_ok else _("Windows 7 SP1 or later is required")))
    py_ok = sys.version_info >= (3, 8)
    checks.append(Check("python", _("Python"), _("Python 3.8+"), py_ok, True, sys.version.split()[0]))
    qt = importlib.util.find_spec("PyQt5") is not None
    checks.append(Check("pyqt5", _("Interface"), "PyQt5", qt, False,
                        "" if qt else _("PyQt5 is unavailable; the command line remains available")))
    for name in ("kernel32", "setupapi", "cfgmgr32", "shell32", "advapi32"):
        try:
            win32._dll(name)
            ok, detail = True, ""
        except (OSError, win32.Win32Unavailable) as exc:
            ok, detail = False, str(exc)
        checks.append(Check("win32-" + name, _("Windows services"), name, ok, True, detail))
    return checks
