"""Системные функции Linux, требующие POSIX, libc или D-Bus."""
from __future__ import annotations

import ctypes
import fcntl
import os

from plugbackup.plat.linux.paths import runtime_dir


class ProfileLock:
    """Неблокирующая блокировка профиля на всё время выполнения операции."""

    def __init__(self, profile_id: str):
        self.path = os.path.join(runtime_dir(), f"{profile_id}.lock")
        self.fd: int | None = None

    def acquire(self) -> bool:
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            self.fd = None
            return False
        return True

    def release(self) -> None:
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None


def inhibit_sleep(reason: str) -> int | None:
    """Запретить сон через logind; вернуть дескриптор или None."""
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        result, fd_list = bus.call_with_unix_fd_list_sync(
            "org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager", "Inhibit",
            GLib.Variant("(ssss)", ("sleep", "Plug & Backup", reason, "block")),
            GLib.VariantType.new("(h)"), Gio.DBusCallFlags.NONE, 5000, None, None)
        return fd_list.get(result.unpack()[0])
    except Exception:
        return None


def release_inhibit(token: int) -> None:
    os.close(token)


def syncfs(path: str) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        if libc.syncfs(fd) != 0:
            os.sync()
    except OSError:
        os.sync()
    finally:
        os.close(fd)


def free_bytes(path: str) -> int:
    """Свободное место для пользователя на ближайшей существующей папке."""
    while not os.path.exists(path) and os.path.dirname(path) != path:
        path = os.path.dirname(path)
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize
