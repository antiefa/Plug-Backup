"""Автоматическое резервирование при вставке носителя: решение и отслеживание вставок.

Модуль без GTK: окно передаёт сюда текущий список носителей и время, а получает, что делать.
"""

from dataclasses import dataclass, field

from plugbackup.core import journal
from plugbackup.core.profiles import Profile

MOUNT_WAIT_SECONDS = 5.0    # сколько ждать автомонтирования Cinnamon, прежде чем монтировать самим

# Причины, по которым автоматический запуск не выполняется
PAUSED = "paused"
AUTO_OFF = "auto-off"
CONFLICT = "conflict"
LOCKED = "locked"
NO_AUTO_JOBS = "no-auto-jobs"
TOO_SOON = "too-soon"
RUN = "run"


@dataclass(frozen=True)
class Decision:
    run: bool
    reason: str
    job_ids: tuple[str, ...] = ()


def last_success_time(profile_id: str) -> float | None:
    """Время начала последнего успешного запуска профиля (любого: ручного, CLI, автоматического)."""
    for record in journal.read_runs(profile_id):
        if record.trigger != journal.RESTORE_TRIGGER and record.status in (journal.OK, journal.WARNINGS):
            return record.started
    return None


def decide(profile: Profile, volume, conflicts: set[str], now: float,
           last_success: float | None, paused_until: float | None) -> Decision:
    if paused_until is not None and now < paused_until:
        return Decision(False, PAUSED)
    if not profile.auto.enabled:
        return Decision(False, AUTO_OFF)
    if volume.uuid in conflicts:
        return Decision(False, CONFLICT)
    jobs = tuple(j.id for j in profile.jobs if j.enabled and j.in_auto)
    if not jobs:
        return Decision(False, NO_AUTO_JOBS)
    hours = profile.auto.min_interval_hours
    if hours and last_success is not None and now - last_success < hours * 3600:
        return Decision(False, TOO_SOON)
    if volume.locked:
        # Запускать было бы можно, но сначала носитель нужно разблокировать (пароль из связки или окно).
        return Decision(False, LOCKED, jobs)
    return Decision(True, RUN, jobs)


# ---------- отслеживание вставок ----------

READY = "ready"            # носитель вставлен и смонтирован — можно решать
NEEDS_MOUNT = "needs-mount"  # вставлен, но за отведённое время не смонтирован — смонтировать самим


@dataclass
class _Pending:
    uuid: str
    since: float
    asked_mount: bool = False


@dataclass
class InsertionTracker:
    """Каждая вставка носителя обрабатывается ровно один раз (до его извлечения).

    Носители, уже подключённые при первом вызове (запуск приложения), считаются вставленными сейчас.
    Закрытый LUKS обрабатывается сразу (чтобы предложить разблокировку), а после разблокировки —
    ещё раз, уже как обычный носитель. Обратный переход (закрыли после извлечения) событий не даёт.
    """

    pending: dict[str, _Pending] = field(default_factory=dict)
    handled: dict[str, str] = field(default_factory=dict)   # object_path → uuid
    handled_locked: set[str] = field(default_factory=set)   # object_path, обработанные в закрытом виде

    def update(self, volumes, now: float) -> list[tuple[str, object]]:
        events: list[tuple[str, object]] = []
        present = {v.object_path: v for v in volumes}
        for path in list(self.handled):
            v = present.get(path)
            if v is None or v.uuid != self.handled[path]:
                del self.handled[path]          # извлечён (или под тем же путём другой носитель)
                self.handled_locked.discard(path)
            elif path in self.handled_locked and not v.locked:
                del self.handled[path]          # разблокирован — обработать заново, как только смонтируется
                self.handled_locked.discard(path)
            elif v.locked:
                self.handled_locked.add(path)
        for path in list(self.pending):
            v = present.get(path)
            if v is None or v.uuid != self.pending[path].uuid:
                del self.pending[path]
        for path, v in present.items():
            if path in self.handled:
                continue
            if v.mounted or v.locked:
                self.pending.pop(path, None)
                self.handled[path] = v.uuid
                if v.locked:
                    self.handled_locked.add(path)
                events.append((READY, v))
                continue
            state = self.pending.setdefault(path, _Pending(v.uuid, now))
            if not state.asked_mount and now - state.since >= MOUNT_WAIT_SECONDS:
                state.asked_mount = True
                events.append((NEEDS_MOUNT, v))
        return events

    def forget(self, object_path: str) -> None:
        self.pending.pop(object_path, None)
        self.handled.pop(object_path, None)
        self.handled_locked.discard(object_path)
