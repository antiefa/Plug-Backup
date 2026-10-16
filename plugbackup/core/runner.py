"""Выполнение резервирования профиля: проверки, план, защиты, rsync, журнал.

Runner.run() — блокирующий; окно вызывает его в отдельном потоке, командная строка — напрямую.
Вопросы пользователю задаются через Callbacks.confirm; без интерактивности (автозапуск, CLI)
ответ всегда «нет», и задание пропускается — ничего опасного не делается молча.
"""

import calendar
import ctypes
import dataclasses
import fcntl
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field

from plugbackup.core import binding, journal, marker
from plugbackup.core import rsync as R
from plugbackup.core.binding import BindingError
from plugbackup.core.config import runtime_dir  # noqa: F401  (здесь же блокировки запусков)
from plugbackup.core.fsinfo import check_names
from plugbackup.core.journal import CANCELLED, ERROR, OK, SKIPPED, WARNINGS, JobResult, RunRecord
from plugbackup.core.mounts import verify_mount
from plugbackup.core.profiles import Job, Profile, normalize_dest, validate_job

VERSIONS_TS_FORMAT = "%Y-%m-%dT%H-%M-%SZ"
SAMPLE = 50                      # сколько путей хранить в журнале для образца
SPACE_MARGIN_RATIO = 0.10        # запас к оценке объёма
SPACE_MARGIN_MIN = 100 * 1024 ** 2
ENCRYPTION_OVERHEAD = 0.02       # gocryptfs: заголовки и 32 байта на блок 4 КиБ (~0,8 %) плюс длинные имена


class Callbacks:
    """Переопределяется окном. По умолчанию — неинтерактивно."""

    interactive = False

    def confirm(self, kind: str, title: str, text: str, details: list[str]) -> bool:
        return False

    def progress(self, info: dict) -> None:
        pass


@dataclass
class JobPlan:
    new_files: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)      # пропуски по ограничениям ФС и ссылкам
    transfer_bytes: int = 0
    free_bytes: int | None = None
    dest_files_before: int = 0


@dataclass
class RunRequest:
    profile: Profile
    volume: object
    trigger: str = "manual"                 # manual / cli / auto
    job_ids: list[str] | None = None        # None — все включённые задания
    preview: bool = False
    global_excludes: list[str] = field(default_factory=list)


@dataclass
class RunOutcome:
    record: RunRecord
    plans: dict[str, JobPlan] = field(default_factory=dict)
    marker_written: bool = False


# ---------- системные мелочи ----------

class ProfileLock:
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
    """Запрет сна через logind; возвращает дескриптор (закрыть — снять запрет) или None."""
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


def count_files(path: str) -> int:
    total = 0
    for _root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d != R.PARTIAL_DIR]
        total += len(files)
    return total


def has_any_file(path: str) -> bool:
    for _root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d != R.PARTIAL_DIR]
        if files:
            return True
    return False


def unique_versions_name(root: str, moment: time.struct_time) -> str:
    """Имя папки корзины этого запуска: время UTC, при совпадении — с суффиксом -2, -3 … (существующие не трогаем)."""
    base = time.strftime(VERSIONS_TS_FORMAT, moment)
    name, n = base, 1
    while os.path.lexists(os.path.join(root, R.VERSIONS_DIR, name)):
        n += 1
        name = f"{base}-{n}"
    return name


def versions_dirs(root: str) -> list[tuple[float, str]]:
    """Папки корзины (время в UTC, путь), старые первыми."""
    base = os.path.join(root, R.VERSIONS_DIR)
    if os.path.islink(base) or not os.path.isdir(base):
        return []
    found = []
    for name in os.listdir(base):
        path = os.path.join(base, name)
        if os.path.islink(path) or not os.path.isdir(path):
            continue
        try:
            stamp = calendar.timegm(time.strptime(name.split("Z", 1)[0] + "Z", VERSIONS_TS_FORMAT))
        except ValueError:
            continue
        found.append((stamp, path))
    return sorted(found)


def free_bytes(path: str) -> int:
    """Свободно для пользователя на ФС пути (если пути ещё нет — по ближайшей существующей папке)."""
    while not os.path.exists(path) and os.path.dirname(path) != path:
        path = os.path.dirname(path)
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


# ---------- выполнение ----------

class Runner:
    def __init__(self, request: RunRequest, callbacks: Callbacks | None = None):
        self.request = request
        self.cb = callbacks or Callbacks()
        self._cancel = threading.Event()
        self._cancel_reason = ""
        self._proc: subprocess.Popen | None = None
        self._proc_lock = threading.Lock()

    # Остановка из другого потока (кнопка «Остановить», отключение носителя)
    def cancel(self, reason: str = "user") -> None:
        if not self._cancel.is_set():
            self._cancel_reason = reason
            self._cancel.set()
        with self._proc_lock:
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()

    def _ask(self, kind: str, title: str, text: str, details: list[str]) -> bool:
        """Вопрос пользователю — только при ручном запуске; автоматика и CLI всегда получают «нет»."""
        if self.request.trigger != "manual":
            return False
        return self.cb.confirm(kind, title, text, details)

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> RunOutcome:
        req = self.request
        profile = req.profile
        record = RunRecord(run_id=journal.new_run_id(), profile_id=profile.id, profile_name=profile.name,
                           trigger=req.trigger, started=time.time())
        outcome = RunOutcome(record)
        lock = ProfileLock(profile.id)
        if not lock.acquire():
            return self._finish(outcome, SKIPPED, "already-running")
        inhibit_fd = None if req.preview else inhibit_sleep("Backup in progress")
        log = None
        try:
            if not req.preview:
                os.makedirs(journal.log_dir(), exist_ok=True)
                log = open(record.log_path, "w", encoding="utf-8", errors="surrogateescape")
            self._run(outcome, log)
        finally:
            if log is not None:
                log.close()
            if inhibit_fd is not None:
                os.close(inhibit_fd)
            lock.release()
        return outcome

    def _finish(self, outcome: RunOutcome, status: str, reason: str = "", message: str = "") -> RunOutcome:
        record = outcome.record
        record.status, record.reason, record.message = status, reason, message
        record.finished = time.time()
        if not self.request.preview:
            journal.append(record)
        return outcome

    def _selected_jobs(self) -> list[Job]:
        profile = self.request.profile
        if self.request.job_ids is not None:
            return [j for j in profile.jobs if j.id in self.request.job_ids]
        if self.request.trigger == "auto":
            return [j for j in profile.jobs if j.enabled and j.in_auto]
        return [j for j in profile.jobs if j.enabled]

    def _run(self, outcome: RunOutcome, log) -> None:
        req, record = self.request, outcome.record
        profile, volume = req.profile, req.volume
        try:
            root = binding.root_dir_for(profile, volume)
            state = marker.root_state(root, profile.id)
        except BindingError as exc:
            self._finish(outcome, ERROR, exc.code)
            return
        if state in (marker.ROOT_OTHER_PROFILE, marker.ROOT_CORRUPT):
            self._finish(outcome, ERROR, state)
            return
        if state == marker.ROOT_FOREIGN_DATA and not req.preview:
            if not self._ask("accept-root", "", profile.root, []):
                self._finish(outcome, SKIPPED, "no-marker")
                return
        existing_marker = marker.read_marker(root) if state == marker.ROOT_MATCH else None

        if not req.preview and state != marker.ROOT_MATCH:
            # Первый запуск в эту папку (или принятая копия) — явное действие пользователя: ставим метку.
            try:
                binding.write_device_files(profile, volume, accept_existing=(state == marker.ROOT_FOREIGN_DATA))
                outcome.marker_written = True
            except (BindingError, OSError) as exc:
                self._finish(outcome, ERROR, "marker-write-failed", str(exc))
                return

        timestamp = unique_versions_name(root, time.gmtime())

        jobs = self._selected_jobs()
        for index, job in enumerate(jobs):
            result = JobResult(job_id=job.id, source=job.source, dest=normalize_dest(job.dest))
            record.jobs.append(result)
            if self.cancelled:
                result.status, result.reason = CANCELLED, self._cancel_reason
                continue
            self.cb.progress({"phase": "prepare", "job_index": index, "job_count": len(jobs), "job": job})
            plan = JobPlan()
            outcome.plans[job.id] = plan
            try:
                self._run_job(job, root, timestamp, existing_marker, result, plan, log, index, len(jobs))
            except BindingError as exc:
                result.status, result.reason = ERROR, exc.code
                self.cancel("drive-lost")
            except OSError as exc:
                result.status, result.reason, result.message = ERROR, "os-error", str(exc)

        status = journal.overall_status(record.jobs) if record.jobs else OK
        if req.preview:
            self._finish(outcome, status)
            return

        try:
            binding.root_dir_for(profile, volume)  # носитель всё ещё тот же
            syncfs(root)
        except BindingError as exc:
            self._finish(outcome, CANCELLED, exc.code)
            return
        if status in (OK, WARNINGS):
            try:
                binding.write_device_files(profile, volume)   # метка с актуальным списком заданий
                outcome.marker_written = True
                record.versions_removed = self._cleanup_versions(root, profile.versions_keep_days, timestamp)
                self._remove_if_empty(os.path.join(root, R.VERSIONS_DIR, timestamp))
                syncfs(root)
            except (BindingError, OSError) as exc:
                record.message = str(exc)
        self._finish(outcome, status, self._cancel_reason if status == CANCELLED else "")

    def _run_job(self, job: Job, root: str, timestamp: str, existing_marker, result: JobResult,
                 plan: JobPlan, log, index: int, count: int) -> None:
        req = self.request
        profile, volume = req.profile, req.volume
        binding.root_dir_for(profile, volume)  # носитель всё ещё тот же и смонтирован как надо
        mount_point = volume.mount_point

        errors = [i for i in validate_job(job, profile, mount_point) if i.level == "error"]
        if errors:
            result.status, result.reason, result.message = SKIPPED, errors[0].code, errors[0].message
            return

        dest = normalize_dest(job.dest)
        dest_dir = os.path.join(root, dest)
        self._check_dest_path(root, dest)
        options = R.CommandOptions(fs_type=volume.fs_type, timestamp=timestamp,
                                   hard_max_delete=profile.mass_delete_threshold.hard_max,
                                   global_excludes=req.global_excludes)

        # Полный список источника: защита от пустого источника и предпроверка имён.
        self.cb.progress({"phase": "scan", "job_index": index, "job_count": count, "job": job})
        listing = self._capture(R.build_list_command(job, job.source, options))
        if listing is None:
            result.status, result.reason = CANCELLED, self._cancel_reason
            return
        rc, text = listing
        if rc not in (R.RC_OK, R.RC_VANISHED):
            result.status, result.reason, result.rsync_code = ERROR, "source-list-failed", rc
            result.errors = _errors(text)
            return
        entries = R.parse_list(text)
        dest_has_files = os.path.isdir(dest_dir) and has_any_file(dest_dir)
        if job.mode in ("mirror", "mirror_versions") and dest_has_files \
                and not any(e.kind != "d" for e in entries):
            result.status, result.reason = SKIPPED, "empty-source"
            return
        encrypted = profile.encryption.mode == "gocryptfs"
        names = check_names(entries, volume.fs_type, job.symlinks == "copy_target", encrypted)
        if names.collisions:
            result.status, result.reason = SKIPPED, "name-collision"
            result.skipped_files = [f"{a} ⇄ {b}" for a, b in names.collisions[:SAMPLE]]
            result.skipped_count = len(names.collisions)
            return
        options.extra_excludes = names.invalid
        plan.skipped = ([f"{p}" for p in names.invalid] + [p for p, _s in names.too_big] + names.symlinks)

        # Задание без своей записи в метке, а в его папке уже есть данные.
        if existing_marker is not None and job.id not in existing_marker.jobs and dest_has_files \
                and job.mode in ("mirror", "mirror_versions") and not req.preview:
            if not self._ask("accept-job", job.source, dest, []):
                result.status, result.reason = SKIPPED, "job-no-marker"
                return

        # План — пробный запуск
        self.cb.progress({"phase": "plan", "job_index": index, "job_count": count, "job": job})
        dry = R.build_command(job, job.source, dest_dir, dataclasses.replace(options, dry_run=True))
        captured = self._capture(dry)
        if captured is None:
            result.status, result.reason = CANCELLED, self._cancel_reason
            return
        rc, text = captured
        if rc not in (R.RC_OK, R.RC_VANISHED, R.RC_PARTIAL):
            result.status, result.reason, result.rsync_code = ERROR, "plan-failed", rc
            result.errors = _errors(text)
            return
        summary = R.ItemizeSummary()
        for line in text.splitlines():
            R.parse_itemize_line(line, summary)
        stats = R.parse_stats(text)
        plan.new_files, plan.changed_files = summary.new_files, summary.changed_files
        plan.deleted_files = summary.deleted_files
        plan.skipped += [p for p in summary.skipped_nonregular if p not in plan.skipped]
        plan.transfer_bytes = stats.transferred_size
        plan.free_bytes = free_bytes(dest_dir)

        # Массовое удаление
        deletions = len(plan.deleted_files)
        if deletions and job.mode in ("mirror", "mirror_versions"):
            plan.dest_files_before = count_files(dest_dir) if os.path.isdir(dest_dir) else 0
            thr = profile.mass_delete_threshold
            too_many = deletions > thr.files or (
                plan.dest_files_before > 0 and deletions * 100 > thr.percent * plan.dest_files_before)
            if too_many and not req.preview:
                if not self._ask("mass-delete", job.source, dest, plan.deleted_files[:SAMPLE]):
                    result.status, result.reason = SKIPPED, "mass-delete"
                    result.deleted_files = deletions
                    return

        # Свободное место
        need = plan.transfer_bytes + max(int(plan.transfer_bytes * SPACE_MARGIN_RATIO), SPACE_MARGIN_MIN) \
            if plan.transfer_bytes else 0
        if encrypted:
            need += int(plan.transfer_bytes * ENCRYPTION_OVERHEAD)
        if need > plan.free_bytes and not req.preview:
            if not self._make_space(root, need, timestamp, dest_dir):
                result.status, result.reason = SKIPPED, "no-space"
                result.message = f"{need}/{plan.free_bytes}"
                return

        result.copied_files = len(plan.new_files) + len(plan.changed_files)
        result.copied_bytes = plan.transfer_bytes
        result.deleted_files = deletions
        result.skipped_files = plan.skipped[:SAMPLE]
        result.skipped_count = len(plan.skipped)
        if req.preview:
            result.status = WARNINGS if plan.skipped else OK
            return

        # Копирование
        os.makedirs(dest_dir, exist_ok=True)
        self._check_dest_path(root, dest)
        command = R.build_command(job, job.source, dest_dir, options)
        if log is not None:
            log.write(f"\n=== {job.source} → {dest}\n$ {' '.join(command)}\n")
        rc, summary = self._execute(command, log, job, index, count)
        result.rsync_code = rc
        result.errors = _errors_from_summary(summary)
        if self.cancelled or rc == R.RC_SIGNAL or rc < 0:
            result.status, result.reason = CANCELLED, self._cancel_reason or "signal"
        elif rc == R.RC_OK:
            result.status = WARNINGS if plan.skipped else OK
        elif rc == R.RC_VANISHED:
            result.status, result.reason = WARNINGS, "files-vanished"
        elif rc == R.RC_MAX_DELETE:
            result.status, result.reason = ERROR, "max-delete"
        elif rc == R.RC_PARTIAL:
            result.status, result.reason = ERROR, "partial-transfer"
        else:
            result.status, result.reason = ERROR, f"rsync-{rc}"
        result.copied_files = len(summary.new_files) + len(summary.changed_files)
        result.deleted_files = len(summary.deleted_files)

    # ---------- вспомогательное ----------

    @staticmethod
    def _check_dest_path(root: str, dest: str) -> None:
        """Ни одна часть пути назначения не должна быть ссылкой (запись строго внутри корня копии)."""
        path = root
        for part in dest.split("/"):
            path = os.path.join(path, part)
            if os.path.islink(path):
                raise BindingError("dest-is-link")
            if os.path.lexists(path) and not os.path.isdir(path):
                raise BindingError("dest-not-folder")

    def _capture(self, command: list[str]) -> tuple[int, str] | None:
        """Запустить команду и собрать вывод; None — если отменено."""
        if self.cancelled:
            return None
        with self._proc_lock:
            self._proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                          env=R.rsync_env(), text=True, encoding="utf-8",
                                          errors="surrogateescape")
        text, _ = self._proc.communicate()
        rc = self._proc.returncode
        with self._proc_lock:
            self._proc = None
        return None if self.cancelled else (rc, text)

    def _execute(self, command: list[str], log, job: Job, index: int, count: int):
        summary = R.ItemizeSummary()
        watchdog_stop = threading.Event()
        volume, profile = self.request.volume, self.request.profile

        def watchdog():
            while not watchdog_stop.wait(2.0):
                if not verify_mount(volume.mount_point, volume.fs_uuid).ok:
                    self.cancel("drive-lost")
                    return

        with self._proc_lock:
            if self.cancelled:
                return -1, summary
            self._proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                          env=R.rsync_env(), bufsize=0)
        thread = threading.Thread(target=watchdog, daemon=True)
        thread.start()
        current = ""
        buffer = b""
        last_emit = 0.0
        stream = self._proc.stdout
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            buffer += chunk
            while True:
                cut = min((i for i in (buffer.find(b"\n"), buffer.find(b"\r")) if i >= 0), default=-1)
                if cut < 0:
                    break
                raw, buffer = buffer[:cut], buffer[cut + 1:]
                line = raw.decode("utf-8", "surrogateescape")
                if not line.strip():
                    continue
                progress = R.parse_progress(line)
                if progress is not None:
                    now = time.monotonic()
                    if now - last_emit > 0.2:
                        last_emit = now
                        self.cb.progress({"phase": "copy", "job_index": index, "job_count": count, "job": job,
                                          "percent": progress.percent, "speed": progress.speed,
                                          "eta": progress.eta, "file": current})
                    continue
                if log is not None:
                    log.write(line + "\n")
                path = R.parse_itemize_line(line, summary)
                if path:
                    current = path
        stream.close()
        rc = self._proc.wait()
        watchdog_stop.set()
        with self._proc_lock:
            self._proc = None
        return rc, summary

    def _cleanup_versions(self, root: str, keep_days: int, current: str) -> list[str]:
        """Удалить папки корзины старше срока — только после успешного запуска."""
        cutoff = time.time() - keep_days * 86400
        removed = []
        for stamp, path in versions_dirs(root):
            if os.path.basename(path) == current or stamp >= cutoff:
                continue
            shutil.rmtree(path)
            removed.append(os.path.basename(path))
        return removed

    def _make_space(self, root: str, need: int, current: str, dest_dir: str) -> bool:
        """Нехватка места: при ручном запуске — предложить удалить старые папки корзины."""
        candidates = [(s, p) for s, p in versions_dirs(root) if os.path.basename(p) != current]
        if not candidates:
            return False
        names = [os.path.basename(p) for _s, p in candidates]
        if not self._ask("free-space", str(need), str(free_bytes(root)), names):
            return False
        target = dest_dir if os.path.isdir(dest_dir) else root
        for _stamp, path in candidates:
            if free_bytes(target) >= need:
                break
            shutil.rmtree(path)
        return free_bytes(target) >= need

    @staticmethod
    def _remove_if_empty(path: str) -> None:
        """Пустая папка корзины этого запуска (ничего не заменялось) не нужна."""
        if not os.path.isdir(path) or os.path.islink(path):
            return
        for current, dirs, files in os.walk(path, topdown=False):
            if files:
                return
            for d in dirs:
                os.rmdir(os.path.join(current, d))
        os.rmdir(path)


def _errors(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith(("rsync:", "rsync error:"))][:SAMPLE]


def _errors_from_summary(summary: R.ItemizeSummary) -> list[str]:
    return [m for m in summary.other_messages if m.startswith(("rsync:", "rsync error:"))][:SAMPLE]
