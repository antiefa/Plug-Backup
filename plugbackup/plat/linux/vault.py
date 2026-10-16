"""Зашифрованная папка на носителе через gocryptfs.

На носителе — <корень>.enc/ (например PlugBackup.enc/), открытый вид монтируется (FUSE) в
$XDG_RUNTIME_DIR/plugbackup/<id-профиля>/ и служит корнем копии: метка, корзина версий и копии
заданий лежат внутри зашифрованной части.

Пароль передаётся только через stdin; пароль и мастер-ключ никогда не попадают в аргументы
команд, журнал, настройки и тексты ошибок. Все функции блокирующие — из интерфейса их вызывают
в отдельном потоке.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading

from plugbackup.core.config import runtime_dir
from plugbackup.plat import mounts

FS_TYPE = "fuse.gocryptfs"
CIPHER_SUFFIX = ".enc"
CONF_FILE = "gocryptfs.conf"
MIN_VERSION = (2, 4, 0)          # версия из Ubuntu 24.04
TIMEOUT = 120                    # scrypt на медленной флешке может думать несколько секунд

# Коды возврата gocryptfs (man gocryptfs, EXIT CODES)
EXIT_CIPHERDIR_NOT_EMPTY = 6
EXIT_MOUNTPOINT_NOT_EMPTY = 10
EXIT_WRONG_PASSWORD = 12
EXIT_EMPTY_PASSWORD = 22

# Коды ошибок для интерфейса
WRONG_PASSWORD = "wrong-password"
NOT_EMPTY = "not-empty"
UNAVAILABLE = "unavailable"
FAILED = "failed"


class GocryptError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail      # текст gocryptfs/fusermount (паролей и ключей там нет)


# ---------- наличие ----------

def _version(text: str) -> tuple[int, ...] | None:
    match = re.search(r"gocryptfs\s+v?(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not match:
        return None
    return tuple(int(x or 0) for x in match.groups())


def fusermount() -> str | None:
    return shutil.which("fusermount3") or shutil.which("fusermount")


def availability() -> tuple[bool, str]:
    """(можно ли пользоваться, причина-код): ok / no-gocryptfs / old-gocryptfs / no-fuse."""
    path = shutil.which("gocryptfs")
    if not path:
        return False, "no-gocryptfs"
    if not fusermount():
        return False, "no-fuse"
    try:
        done = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False, "no-gocryptfs"
    if done.returncode != 0:
        return False, "no-gocryptfs"        # программа есть, но не работает
    version = _version(done.stdout + done.stderr)
    if version is None or version < MIN_VERSION:
        return False, "old-gocryptfs"
    return True, "ok"


_availability_cache: tuple[float, tuple[bool, str]] | None = None


_availability_lock = threading.Lock()


def _refresh_availability() -> tuple[bool, str]:
    import time
    global _availability_cache
    with _availability_lock:
        result = availability()
        _availability_cache = (time.monotonic(), result)
        return result


def availability_cached(max_age: float = 60.0) -> tuple[bool, str]:
    """availability() не чаще раза в max_age секунд (окно вызывает её при каждой перерисовке).
    Устаревший результат возвращается сразу, а обновляется в фоне; ждать приходится только самый первый раз
    (при запуске приложения проверка делается заранее, в фоне)."""
    import time
    cached = _availability_cache
    if cached is None:
        return _refresh_availability()
    if time.monotonic() - cached[0] > max_age and not _availability_lock.locked():
        threading.Thread(target=_refresh_availability, daemon=True).start()
    return cached[1]


# ---------- пути ----------

def cipher_dir(mount_point: str, root: str) -> str:
    return os.path.join(mount_point, root + CIPHER_SUFFIX)


def plain_dir(profile_id: str) -> str:
    """Точка монтирования открытого вида; папку создаёт mount()."""
    return os.path.join(runtime_dir(), profile_id)


def is_initialized(cipher: str) -> bool:
    return not os.path.islink(cipher) and os.path.isfile(os.path.join(cipher, CONF_FILE))


def mounted_source(plain: str) -> str | None:
    """Зашифрованная папка, смонтированная в plain, или None, если там не gocryptfs."""
    target = os.path.normpath(plain)
    for entry in mounts.read_mountinfo():
        if entry.mount_point == target and entry.fs_type == FS_TYPE:
            return entry.source
    return None


def is_open(cipher: str, plain: str) -> bool:
    source = mounted_source(plain)
    return source is not None and os.path.realpath(source) == os.path.realpath(cipher) \
        and os.path.ismount(plain)


# ---------- действия ----------

def _run(args: list[str], password: str | None = None, background: bool = False) -> subprocess.CompletedProcess:
    """background — команда оставляет фоновый процесс (монтирование gocryptfs): вывод идёт во временный
    файл, а не в канал, который фоновый процесс мог бы держать открытым. Остальное — через каналы,
    чтобы вывод с мастер-ключом не попадал на диск."""
    if not background:
        try:
            return subprocess.run(args, input=(password + "\n") if password is not None else None,
                                  capture_output=True, text=True, timeout=TIMEOUT)
        except FileNotFoundError as exc:
            raise GocryptError(UNAVAILABLE, str(exc)) from None
        except subprocess.TimeoutExpired:
            raise GocryptError(FAILED, "timeout") from None
    import tempfile
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as out:
        try:
            proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=out, stderr=subprocess.STDOUT, text=True)
        except FileNotFoundError as exc:
            raise GocryptError(UNAVAILABLE, str(exc)) from None
        try:
            if password is not None:
                proc.stdin.write(password + "\n")
            proc.stdin.close()
            rc = proc.wait(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise GocryptError(FAILED, "timeout") from None
        except BrokenPipeError:
            rc = proc.wait(timeout=TIMEOUT)
        out.seek(0)
        text = out.read()
    return subprocess.CompletedProcess(args, rc, stdout=text, stderr="")


_KEY_RE = re.compile(r"[0-9a-fA-F]{8}(?:-?[0-9a-fA-F]{8}){7}")


def _detail(done: subprocess.CompletedProcess, password: str | None = None) -> str:
    """Конец вывода для сообщения об ошибке — без пароля и без всего, похожего на мастер-ключ."""
    text = (done.stderr or done.stdout or "").strip()
    if password:
        text = text.replace(password, "***")
    return _KEY_RE.sub("***", text)[-500:]


def _check_password(password: str) -> None:
    if not password or "\n" in password or "\r" in password:
        raise GocryptError(FAILED, "invalid password")


def format_master_key(hex_key: str) -> str:
    """64 шестнадцатеричных символа → 8 групп по 8 через дефис (как принимает gocryptfs -masterkey)."""
    key = re.sub(r"[^0-9a-fA-F]", "", hex_key).lower()
    return "-".join(key[i:i + 8] for i in range(0, len(key), 8))


def init(cipher: str, password: str) -> str:
    """Создать зашифрованную папку; вернуть мастер-ключ для показа пользователю ("" — не удалось прочитать)."""
    _check_password(password)
    if os.path.islink(cipher):
        raise GocryptError(FAILED, "the folder is a symbolic link")
    if os.path.exists(cipher) and os.listdir(cipher):
        raise GocryptError(NOT_EMPTY)
    os.makedirs(cipher, exist_ok=True)
    done = _run(["gocryptfs", "-init", "-q", "--", cipher], password)
    if done.returncode == EXIT_CIPHERDIR_NOT_EMPTY:
        raise GocryptError(NOT_EMPTY, _detail(done, password))
    if done.returncode != 0:
        raise GocryptError(FAILED, _detail(done, password))
    # Без терминала gocryptfs ключ не печатает — читаем его из конфигурации тем же паролем.
    xray = _run(["gocryptfs-xray", "-dumpmasterkey", os.path.join(cipher, CONF_FILE)], password)
    match = re.search(r"\b[0-9a-fA-F]{64}\b", xray.stdout) if xray.returncode == 0 else None
    return format_master_key(match.group(0)) if match else ""


def mount(cipher: str, plain: str, password: str) -> None:
    """Смонтировать открытый вид. Уже смонтированное тем же — ничего не делает."""
    _check_password(password)
    if not is_initialized(cipher):
        raise GocryptError(FAILED, "no gocryptfs.conf")
    if is_open(cipher, plain):
        return
    if mounted_source(plain) is not None:
        raise GocryptError(FAILED, "another folder is mounted there")
    os.makedirs(plain, mode=0o700, exist_ok=True)
    if os.listdir(plain):
        raise GocryptError(NOT_EMPTY)
    done = _run(["gocryptfs", "-q", "--", cipher, plain], password, background=True)
    if done.returncode == EXIT_WRONG_PASSWORD:
        raise GocryptError(WRONG_PASSWORD)
    if done.returncode == EXIT_MOUNTPOINT_NOT_EMPTY:
        raise GocryptError(NOT_EMPTY, _detail(done, password))
    if done.returncode != 0 or not is_open(cipher, plain):
        raise GocryptError(FAILED, _detail(done, password))


def unmount(plain: str, lazy: bool = False) -> None:
    """Размонтировать открытый вид (если он смонтирован) и убрать пустую точку монтирования."""
    if mounted_source(plain) is not None:
        tool = fusermount()
        if tool is None:
            raise GocryptError(UNAVAILABLE, "fusermount")
        done = _run([tool, "-u"] + (["-z"] if lazy else []) + ["--", plain])
        if done.returncode != 0 and mounted_source(plain) is not None:   # не размонтировал кто-то другой одновременно
            raise GocryptError(FAILED, _detail(done))
    try:
        os.rmdir(plain)
    except OSError:
        pass


def stale_mounts() -> list[str]:
    """Наши точки монтирования gocryptfs (в runtime_dir), например оставшиеся после сбоя."""
    base = os.path.normpath(runtime_dir())
    return [e.mount_point for e in mounts.read_mountinfo()
            if e.fs_type == FS_TYPE and os.path.dirname(e.mount_point) == base]
