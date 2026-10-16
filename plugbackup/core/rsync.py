"""Сборка команд rsync и разбор их вывода.

Команда собирается из задания, типа файловой системы носителя и общего списка исключений.
Модуль не запускает процессы сам — это делает core.runner.
"""

import os
import re
from dataclasses import dataclass, field

from plugbackup.core.profiles import Job, normalize_dest

POSIX_FS = frozenset({"ext4", "ext3", "ext2", "btrfs", "xfs", "f2fs"})
NO_SYMLINK_FS = frozenset({"vfat", "exfat"})
FAT_LIKE = frozenset({"vfat", "exfat"})
FAT32_MAX_SIZE = "4G-1"  # rsync: 4 ГиБ − 1 байт
PARTIAL_DIR = ".rsync-partial"
VERSIONS_DIR = ".versions"

# Коды возврата rsync
RC_OK = 0
RC_PARTIAL = 23          # часть файлов не передана (ошибки по отдельным файлам)
RC_VANISHED = 24         # файлы исчезли во время копирования — обычно не страшно
RC_MAX_DELETE = 25       # сработал --max-delete
RC_SIGNAL = 20


def relative_backup_dir(dest: str, timestamp: str) -> str:
    """Путь корзины относительно папки задания: ../.versions/<время>/<dest>."""
    dest = normalize_dest(dest)
    depth = len(dest.split("/"))
    return "/".join([".."] * depth + [VERSIONS_DIR, timestamp, dest])


def escape_pattern(path: str) -> str:
    """Экранировать путь для точного правила исключения rsync (символы шаблонов)."""
    return re.sub(r"([*?\[\]\\])", r"\\\1", path)


@dataclass
class CommandOptions:
    fs_type: str
    dry_run: bool = False
    timestamp: str = ""              # для корзины версий
    hard_max_delete: int = 5000
    global_excludes: list[str] = field(default_factory=list)
    extra_excludes: list[str] = field(default_factory=list)   # точные пути, отсеянные предпроверкой
    progress: bool = True


def file_options(job: Job, fs_type: str) -> list[str]:
    """Что копировать и как сравнивать — общее для списка, плана и запуска."""
    args = ["--recursive", "--times"]
    if fs_type in POSIX_FS:
        args.append("--perms")
    symlinks = job.symlinks
    if symlinks == "copy_target":
        args.append("--copy-links")
    elif symlinks == "auto" and fs_type in POSIX_FS:
        args.append("--links")
    # иначе ссылки пропускаются (rsync без --links пишет «skipping non-regular file»)
    if fs_type in FAT_LIKE:
        args.append("--modify-window=1")
    if job.one_file_system:
        args.append("--one-file-system")
    if job.compare == "checksum":
        args.append("--checksum")
    return args


def filter_options(job: Job, options: CommandOptions) -> list[str]:
    args = []
    patterns = []
    if job.use_global_excludes:
        patterns.extend(options.global_excludes)
    patterns.extend(job.excludes)
    if job.skip_hidden:
        patterns.append(".*")
    for pattern in patterns:
        pattern = pattern.strip()
        if pattern:
            args.append(f"--exclude={pattern}")
    for path in options.extra_excludes:
        args.append("--exclude=/" + escape_pattern(path))
    limits = []
    if job.max_size_mb:
        limits.append(job.max_size_mb * 1024 * 1024)
    if options.fs_type == "vfat":
        limits.append(4 * 1024 ** 3 - 1)
    if limits:
        args.append(f"--max-size={min(limits)}")
    return args


def build_command(job: Job, source: str, dest_dir: str, options: CommandOptions) -> list[str]:
    """Команда копирования задания: source/ → dest_dir/ (dest_dir — уже проверенная папка на носителе)."""
    args = ["rsync"] + file_options(job, options.fs_type) + filter_options(job, options)
    args += ["--partial-dir=" + PARTIAL_DIR, "--itemize-changes", "--no-human-readable"]
    if job.mode in ("mirror", "mirror_versions"):
        args += ["--delete", f"--max-delete={max(1, options.hard_max_delete)}"]
    if job.mode == "mirror_versions":
        if not options.timestamp:
            raise ValueError("timestamp is required for version history")
        args += ["--backup", "--backup-dir=" + relative_backup_dir(job.dest, options.timestamp)]
    if options.dry_run:
        args += ["--dry-run", "--stats"]
    elif options.progress:
        args += ["--no-inc-recursive", "--info=progress2", "--stats"]
    return args + [_dir_arg(source), _dir_arg(dest_dir)]


def build_list_command(job: Job, source: str, options: CommandOptions) -> list[str]:
    """Полный список файлов источника с учётом исключений (для предпроверки имён и размеров)."""
    args = ["rsync", "--list-only", "--no-human-readable"]
    args += [a for a in file_options(job, options.fs_type) if a not in ("--times", "--perms", "--checksum")]
    args += [a for a in filter_options(job, options) if not a.startswith("--max-size")]
    return args + [_dir_arg(source)]


def _dir_arg(path: str) -> str:
    return path.rstrip("/") + "/"


# ---------- разбор вывода ----------

@dataclass
class ListEntry:
    kind: str      # "f" файл, "d" папка, "l" ссылка, "o" прочее
    size: int
    path: str


_LIST_RE = re.compile(r"^([-dlcbps])\S{9}\s+([\d,]+)\s+\d{4}/\d\d/\d\d\s+\d\d:\d\d:\d\d\s(.*)$")


def unescape_rsync(name: str) -> str:
    """rsync выводит непечатаемые байты имени как \\#ooo (восьмеричный код байта) — собрать байты обратно."""
    if "\\#" not in name:
        return name
    raw = name.encode("utf-8", "surrogateescape")
    raw = re.sub(rb"\\#([0-7]{3})", lambda m: bytes([int(m.group(1), 8)]), raw)
    return raw.decode("utf-8", "surrogateescape")


def parse_list(text: str) -> list[ListEntry]:
    entries = []
    for line in text.splitlines():
        m = _LIST_RE.match(line)
        if not m:
            continue
        kind_char, size, name = m.groups()
        kind = {"-": "f", "d": "d", "l": "l"}.get(kind_char, "o")
        if kind == "l" and " -> " in name:
            name = name.split(" -> ", 1)[0]
        name = unescape_rsync(name)
        if name in (".", "./"):
            continue
        entries.append(ListEntry(kind, int(size.replace(",", "")), name))
    return entries


@dataclass
class ItemizeSummary:
    new_files: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    deleted_dirs: list[str] = field(default_factory=list)
    skipped_nonregular: list[str] = field(default_factory=list)
    other_messages: list[str] = field(default_factory=list)


_ITEM_RE = re.compile(r"^([<>ch.*][fdLDS][^ ]*) (.*)$")


def parse_itemize_line(line: str, summary: ItemizeSummary) -> str | None:
    """Учесть строку вывода; вернуть путь файла, если строка о передаче файла."""
    if line.startswith("*deleting "):
        path = unescape_rsync(line[len("*deleting "):].strip())
        (summary.deleted_dirs if path.endswith("/") else summary.deleted_files).append(path)
        return None
    if line.startswith("skipping non-regular file "):
        summary.skipped_nonregular.append(unescape_rsync(line.split(" ", 3)[3].strip('"')))
        return None
    m = _ITEM_RE.match(line)
    if not m:
        if line.strip():
            summary.other_messages.append(line.strip())
        return None
    code, path = m.groups()
    path = unescape_rsync(path.split(" -> ", 1)[0])
    if code[1] != "f":
        return None
    if "+++++++++" in code:
        summary.new_files.append(path)
    elif code[0] in "<>":
        summary.changed_files.append(path)
    return path


@dataclass
class Stats:
    transferred_size: int = 0
    total_size: int = 0
    files_transferred: int = 0
    deleted: int = 0


_STATS = {
    "Total transferred file size": "transferred_size",
    "Total file size": "total_size",
    "Number of regular files transferred": "files_transferred",
    "Number of deleted files": "deleted",
}


def parse_stats(text: str) -> Stats:
    stats = Stats()
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() in _STATS:
            number = re.match(r"\s*([\d,]+)", value)
            if number:
                setattr(stats, _STATS[key.strip()], int(number.group(1).replace(",", "")))
    return stats


@dataclass
class Progress:
    bytes_done: int
    percent: int
    speed: str
    eta: str


_PROGRESS_RE = re.compile(r"^\s*([\d,]+)\s+(\d+)%\s+(\S+)\s+(\d+:\d\d:\d\d)")


def parse_progress(line: str) -> Progress | None:
    m = _PROGRESS_RE.match(line)
    if not m:
        return None
    return Progress(int(m.group(1).replace(",", "")), int(m.group(2)), m.group(3), m.group(4))


def is_stats_line(line: str) -> bool:
    return any(line.startswith(k + ":") for k in _STATS) or line.startswith(("Number of ", "Total ", "Literal data",
                                                                               "Matched data", "File list", "sent ",
                                                                               "total size is"))


def rsync_env() -> dict:
    env = dict(os.environ)
    # Английские сообщения для разбора; UTF-8 — чтобы имена файлов выводились как есть, а не \\#ooo.
    env["LC_ALL"] = "C.UTF-8"
    return env
