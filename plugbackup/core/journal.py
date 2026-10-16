"""Журнал запусков: ~/.local/state/plugbackup/log/runs.jsonl и полный вывод rsync по каждому запуску."""

import json
import os
import time
from dataclasses import asdict, dataclass, field

KEEP_RUNS = 100
KEEP_DAYS = 90

# Итоги задания и запуска
OK = "ok"                    # выполнено полностью
WARNINGS = "warnings"        # выполнено, но часть файлов пропущена (ограничения ФС, исчезнувшие файлы)
ERROR = "error"              # ошибка; копия задания может быть неполной
CANCELLED = "cancelled"      # остановлено пользователем или носитель отключён
SKIPPED = "skipped"          # не запускалось (защита, нужно подтверждение)


def state_dir() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "plugbackup")


def log_dir() -> str:
    return os.path.join(state_dir(), "log")


@dataclass
class JobResult:
    job_id: str
    source: str
    dest: str
    status: str = OK
    reason: str = ""                 # код причины для SKIPPED/ERROR/CANCELLED
    message: str = ""                # пояснение для человека
    copied_files: int = 0
    copied_bytes: int = 0
    deleted_files: int = 0           # удалено (зеркало) или перенесено в корзину
    skipped_files: list[str] = field(default_factory=list)   # образец пропущенных (до 50)
    skipped_count: int = 0
    errors: list[str] = field(default_factory=list)          # образец ошибок rsync (до 50)
    rsync_code: int | None = None
    new_files: int = 0               # восстановление: появилось на ПК
    replaced_files: int = 0          # восстановление: заменено на ПК (прежние версии отложены)


@dataclass
class RunRecord:
    run_id: str
    profile_id: str
    profile_name: str
    trigger: str                     # manual / cli / auto
    started: float
    finished: float = 0.0
    status: str = OK
    reason: str = ""
    message: str = ""
    versions_removed: list[str] = field(default_factory=list)
    jobs: list[JobResult] = field(default_factory=list)

    @property
    def log_path(self) -> str:
        return os.path.join(log_dir(), f"{self.run_id}.log")


def overall_status(results: list[JobResult]) -> str:
    statuses = {r.status for r in results}
    for status in (CANCELLED, ERROR, SKIPPED, WARNINGS):
        if status in statuses:
            return status
    return OK


def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime()) + f"-{os.getpid()}-{int(time.time() * 1000) % 1000:03d}"


def append(record: RunRecord) -> None:
    os.makedirs(log_dir(), exist_ok=True)
    with open(os.path.join(log_dir(), "runs.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    _prune()


def _record_from_dict(data: dict) -> RunRecord:
    jobs = [JobResult(**j) for j in data.pop("jobs", [])]
    return RunRecord(jobs=jobs, **data)


def read_runs(profile_id: str | None = None, limit: int = KEEP_RUNS) -> list[RunRecord]:
    """Последние запуски, новые первыми."""
    path = os.path.join(log_dir(), "runs.jsonl")
    records: list[RunRecord] = []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return records
    for line in reversed(lines):
        try:
            record = _record_from_dict(json.loads(line))
        except (ValueError, TypeError):
            continue
        if profile_id is None or record.profile_id == profile_id:
            records.append(record)
            if len(records) >= limit:
                break
    return records


RESTORE_TRIGGER = "restore"


def last_run(profile_id: str) -> RunRecord | None:
    """Последний запуск РЕЗЕРВИРОВАНИЯ профиля (восстановления не учитываются)."""
    for record in read_runs(profile_id):
        if record.trigger != RESTORE_TRIGGER:
            return record
    return None


def _prune() -> None:
    path = os.path.join(log_dir(), "runs.jsonl")
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return
    cutoff = time.time() - KEEP_DAYS * 86400
    kept, dropped = [], []
    for line in lines:
        try:
            started = json.loads(line).get("started", 0)
        except ValueError:
            continue
        (kept if started >= cutoff else dropped).append(line)
    if len(kept) > KEEP_RUNS:
        dropped += kept[:-KEEP_RUNS]
        kept = kept[-KEEP_RUNS:]
    if not dropped:
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(kept)
    os.replace(tmp, path)
    for line in dropped:
        try:
            run_id = json.loads(line).get("run_id")
        except ValueError:
            continue
        if run_id:
            try:
                os.unlink(os.path.join(log_dir(), f"{run_id}.log"))
            except FileNotFoundError:
                pass
