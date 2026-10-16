"""Перенос копии при смене режима шифрования.

Обычная копия <корень>/ ⇄ открытый вид зашифрованной папки. Сначала проверяется место, затем
rsync копирует всё (вместе с корзиной версий и меткой), затем сверка: повторный пробный rsync не
должен найти различий. Удаление старой копии — отдельным шагом и только по согласию пользователя.
Функции блокирующие — интерфейс вызывает их в отдельном потоке.
"""

import os
import shutil
import subprocess
import threading
from dataclasses import dataclass

from plugbackup.core import rsync as R

SPACE_MARGIN = 100 * 1024 ** 2
ENCRYPTION_OVERHEAD = 0.03       # шифрованная копия чуть больше обычной


@dataclass(frozen=True)
class TreeSize:
    files: int
    bytes: int


def tree_size(path: str) -> TreeSize:
    files = size = 0
    for root, dirs, names in os.walk(path):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for name in names:
            try:
                st = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            files += 1
            size += st.st_size
    return TreeSize(files, size)


def is_empty_dir(path: str) -> bool:
    """Папки нет или в ней ничего нет."""
    return not os.path.lexists(path) or (os.path.isdir(path) and not os.path.islink(path) and not os.listdir(path))


def space_needed(size: TreeSize, to_encrypted: bool) -> int:
    extra = int(size.bytes * ENCRYPTION_OVERHEAD) if to_encrypted else 0
    return size.bytes + extra + SPACE_MARGIN


def _options(fs_type: str) -> list[str]:
    args = ["--recursive", "--times"]
    if fs_type in R.POSIX_FS:
        args += ["--perms", "--links"]
    if fs_type in R.FAT_LIKE:
        args.append("--modify-window=1")
    return args


def copy_command(src: str, dst: str, fs_type: str, dry_run: bool = False) -> list[str]:
    args = ["rsync"] + _options(fs_type) + ["--no-human-readable"]
    if dry_run:
        # Сверка перед удалением старой копии — по содержимому, а не по размеру и времени.
        args += ["--dry-run", "--itemize-changes", "--checksum"]
    else:
        args += ["--info=progress2", "--no-inc-recursive"]
    return args + ["--", src.rstrip("/") + "/", dst.rstrip("/") + "/"]


class Transfer:
    """Копирование src → dst с прогрессом и остановкой. progress(percent: int, speed: str, eta: str)."""

    def __init__(self, src: str, dst: str, fs_type: str, progress=None):
        self.src, self.dst, self.fs_type = src, dst, fs_type
        self.progress = progress or (lambda percent, speed, eta: None)
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self.cancelled = False
        self.output_tail = ""

    def cancel(self) -> None:
        self.cancelled = True
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()

    def run(self) -> bool:
        """True — всё скопировано и сверено."""
        os.makedirs(self.dst, exist_ok=True)
        with self._lock:
            if self.cancelled:
                return False
            self._proc = subprocess.Popen(copy_command(self.src, self.dst, self.fs_type), stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT, env=R.rsync_env(), bufsize=0)
        tail: list[str] = []
        buffer = b""
        while True:
            chunk = self._proc.stdout.read(4096)
            if not chunk:
                break
            buffer += chunk
            parts = buffer.replace(b"\r", b"\n").split(b"\n")
            buffer = parts.pop()
            for raw in parts:
                line = raw.decode("utf-8", "surrogateescape")
                progress = R.parse_progress(line)
                if progress is not None:
                    self.progress(progress.percent, progress.speed, progress.eta)
                elif line.strip():
                    tail = (tail + [line])[-20:]
        self._proc.stdout.close()
        rc = self._proc.wait()
        with self._lock:
            self._proc = None
        self.output_tail = "\n".join(tail)
        if self.cancelled or rc != R.RC_OK:
            return False
        return self.verify()

    def verify(self) -> bool:
        """Пробный rsync не находит, что ещё копировать, — копия совпадает с исходной."""
        done = subprocess.run(copy_command(self.src, self.dst, self.fs_type, dry_run=True), capture_output=True,
                              text=True, env=R.rsync_env(), errors="surrogateescape")
        changes = [line for line in done.stdout.splitlines() if line and not line.startswith(".d")]
        if done.returncode != R.RC_OK or changes:
            self.output_tail = "\n".join((changes or [done.stderr.strip()])[:20])
            return False
        return True


def clear_dir(path: str) -> None:
    """Удалить содержимое папки (сама папка остаётся) — после неудачного или отменённого переноса."""
    if os.path.islink(path) or not os.path.isdir(path):
        return
    for entry in os.scandir(path):
        if entry.is_dir(follow_symlinks=False):
            shutil.rmtree(entry.path)
        else:
            os.unlink(entry.path)


def remove_tree(path: str) -> None:
    """Удалить старую копию целиком (уже сверенную с новой)."""
    if os.path.islink(path):
        raise OSError(f"{path} is a symbolic link")
    shutil.rmtree(path)
