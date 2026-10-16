"""Блокировки и системные функции Windows."""
from __future__ import annotations

import os
import shutil
import threading

from plugbackup.plat.windows import paths, win32


class ProfileLock:
    def __init__(self, profile_id: str):
        self.profile_id = profile_id
        self.fd = None

    def acquire(self) -> bool:
        self.fd = win32.lock_profile(self.profile_id, paths.runtime_dir())
        return self.fd is not None

    def release(self) -> None:
        if self.fd is not None:
            fd, self.fd = self.fd, None
            win32.unlock_profile(fd)


class _InhibitToken:
    def __init__(self):
        self.release = threading.Event()
        self.thread = threading.Thread(target=self._run, name="PlugBackupSleepInhibitor", daemon=True)
        self.error = None

    def _run(self):
        try:
            win32.inhibit_sleep(True)
            self.release.wait()
        except Exception as exc:
            self.error = exc
        finally:
            try:
                win32.inhibit_sleep(False)
            except Exception as exc:
                self.error = self.error or exc


def inhibit_sleep(reason: str):
    token = _InhibitToken()
    token.thread.start()
    return token


def release_inhibit(token) -> None:
    if token is None:
        return
    token.release.set()
    if threading.current_thread() is not token.thread:
        token.thread.join()


def syncfs(path: str) -> None:
    # Сброс буферов выполняет движок для каждого файла; полный сброс тома требует прав администратора.
    return None


def free_bytes(path: str) -> int:
    while not os.path.exists(path) and os.path.dirname(path) != path:
        path = os.path.dirname(path)
    return shutil.disk_usage(path).free
