"""Проверка компонентов системы, от которых зависят функции приложения.

Отсутствие обязательного компонента делает приложение неработоспособным;
отсутствие необязательного отключает только зависящую от него функцию.
"""

from __future__ import annotations

import importlib
import os
import re
import shutil
import subprocess

try:
    import gi
except ImportError:  # Python без PyGObject, например отдельный интерпретатор
    gi = None

from plugbackup.core.deps import Check
from plugbackup.i18n import _

# Утилиты из sbin (cryptsetup) могут отсутствовать в PATH обычного пользователя.
_EXTRA_PATH = ("/usr/sbin", "/sbin", "/usr/bin", "/bin")


def which(name: str) -> str | None:
    path = os.environ.get("PATH", "")
    extra = os.pathsep.join(p for p in _EXTRA_PATH if p not in path.split(os.pathsep))
    return shutil.which(name, path=os.pathsep.join(filter(None, (path, extra))))


def gir_available(namespace: str, version: str) -> tuple[bool, str]:
    if gi is None:
        return False, "PyGObject (gi) is unavailable"
    try:
        gi.require_version(namespace, version)
        importlib.import_module(f"gi.repository.{namespace}")
    except (ValueError, ImportError) as exc:
        return False, str(exc)
    return True, ""


def dbus_name_available(bus_type, name: str) -> bool:
    """Имя на шине D-Bus занято или может быть запущено по запросу."""
    if gi is None:
        return False
    from gi.repository import Gio, GLib

    try:
        bus = Gio.bus_get_sync(bus_type, None)
        for method in ("NameHasOwner", "ListActivatableNames"):
            params = GLib.Variant("(s)", (name,)) if method == "NameHasOwner" else None
            reply = bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                method, params, None, Gio.DBusCallFlags.NONE, 2000, None,
            ).unpack()[0]
            if reply is True or (isinstance(reply, list) and name in reply):
                return True
    except Exception:
        return False
    return False


def tool_version(argv: list[str], pattern: str) -> str:
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    match = re.search(pattern, out.stdout + out.stderr)
    return match.group(1) if match else ""


def run_checks() -> list[Check]:
    if gi is None:
        system_bus = session_bus = None
    else:
        from gi.repository import Gio
        system_bus = Gio.BusType.SYSTEM
        session_bus = Gio.BusType.SESSION

    checks: list[Check] = []

    ok, detail = gir_available("Gtk", "3.0")
    checks.append(Check("gtk", _("Interface"), "GTK 3 (gir1.2-gtk-3.0)", ok, True, detail))

    ok, detail = gir_available("UDisks", "2.0")
    checks.append(Check("udisks-gir", _("Drive detection"), "UDisks2 (gir1.2-udisks-2.0)", ok, True, detail))
    ok = dbus_name_available(system_bus, "org.freedesktop.UDisks2")
    checks.append(Check(
        "udisks-service", _("Drive detection"), _("UDisks2 service (udisks2)"), ok, True,
        "" if ok else _("The org.freedesktop.UDisks2 service is not available on the system bus"),
    ))

    path = which("rsync")
    version = tool_version([path, "--version"], r"version\s+(\S+)") if path else ""
    checks.append(Check("rsync", _("Copying"), "rsync", bool(path), True, version))

    ok, detail = gir_available("Notify", "0.7")
    checks.append(Check("notify", _("Notifications"), "libnotify (gir1.2-notify-0.7)", ok, False, detail))

    xapp, _detail = gir_available("XApp", "1.0")
    ayatana, _detail = gir_available("AyatanaAppIndicator3", "0.1")
    if xapp:
        tray_detail = "XApp.StatusIcon"
    elif ayatana:
        tray_detail = "AyatanaAppIndicator3"
    else:
        tray_detail = _("Neither XApp nor AyatanaAppIndicator3 is installed")
    checks.append(Check(
        "tray", _("Tray icon"), "gir1.2-xapp-1.0 | gir1.2-ayatanaappindicator3-0.1",
        xapp or ayatana, False, tray_detail,
    ))

    ok, detail = gir_available("Secret", "1")
    checks.append(Check("secret-gir", _("Password storage"), "libsecret (gir1.2-secret-1)", ok, False, detail))
    ok = dbus_name_available(session_bus, "org.freedesktop.secrets")
    checks.append(Check(
        "secret-service", _("Password storage"), _("Keyring service (Secret Service)"), ok, False,
        "" if ok else _("No Secret Service on the session bus (for example, gnome-keyring)"),
    ))

    path = which("cryptsetup")
    version = tool_version([path, "--version"], r"cryptsetup\s+(\S+)") if path else ""
    checks.append(Check("cryptsetup", _("Encrypted drives (LUKS)"), "cryptsetup", bool(path), False, version))

    path = which("gocryptfs")
    version = tool_version([path, "-version"], r"gocryptfs\s+v?(\S+?);") if path else ""
    checks.append(Check("gocryptfs", _("Encrypted folder (gocryptfs)"), "gocryptfs", bool(path), False, version))
    fuse = which("fusermount3") or which("fusermount")
    checks.append(Check(
        "fuse", _("Encrypted folder (gocryptfs)"), "FUSE (fusermount)", bool(fuse), False,
        os.path.basename(fuse) if fuse else "",
    ))

    return checks


