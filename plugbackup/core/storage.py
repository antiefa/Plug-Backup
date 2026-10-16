"""Надёжное хранение JSON-файлов настроек.

Запись атомарная: временный файл в той же папке → fsync → переименование.
Предыдущая версия сохраняется рядом как <имя>.bak. Повреждённый файл не перезаписывается
молча: загрузка сообщает об ошибке, а вызывающий код решает, что делать.
"""

import json
import os
import shutil
import tempfile


class StorageError(Exception):
    def __init__(self, path: str, message: str):
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message

    @property
    def backup_path(self) -> str | None:
        bak = self.path + ".bak"
        return bak if os.path.exists(bak) else None


def load_json(path: str) -> dict | None:
    """Содержимое файла или None, если файла нет. StorageError — если файл повреждён."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StorageError(path, str(exc)) from exc
    if not isinstance(data, dict):
        raise StorageError(path, "top-level value is not an object")
    return data


def save_json(path: str, data: dict, backup: bool = True) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    if backup and os.path.exists(path):
        shutil.copy2(path, path + ".bak")
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    dir_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def restore_backup(path: str) -> bool:
    """Заменить файл его копией .bak. Повреждённый файл сохраняется как <имя>.broken."""
    bak = path + ".bak"
    if not os.path.exists(bak):
        return False
    load_json(bak)  # копия тоже должна читаться
    if os.path.exists(path):
        os.replace(path, path + ".broken")
    shutil.copy2(bak, path)
    return True
