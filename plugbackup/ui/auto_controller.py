"""Автоматика: вставка носителя → отсчёт с отменой → резервирование → итог → извлечение.

Закрытый LUKS-носитель сначала разблокируется: паролем из связки ключей или через окно;
отсчёт начинается только после этого.

Решения принимает core.auto; здесь — уведомления, таймеры и запуск через RunController окна.
"""

import threading
import time

import gi
from gi.repository import GLib

from plugbackup import APP_ID, APP_NAME
from plugbackup.core import auto, devices, gocrypt, journal
from plugbackup.core import profiles as P
from plugbackup.core.runner import Runner, RunRequest
from plugbackup.i18n import _
from plugbackup.ui.run_texts import reason_text, status_text

try:
    gi.require_version("Notify", "0.7")
    from gi.repository import Notify
except (ValueError, ImportError):  # без libnotify — уведомлений нет, автоматика работает
    Notify = None


NO_CHANGES = "no-changes"


def planned_changes(outcome) -> tuple[int, int]:
    """Сколько файлов и байт изменил бы запуск по итогам пробного сравнения."""
    files = size = 0
    for plan in outcome.plans.values():
        files += len(plan.new_files) + len(plan.changed_files) + len(plan.deleted_files)
        size += plan.transfer_bytes
    return files, size


def outcome_is_clean(record) -> bool:
    """Без ошибок, пропусков и остановок (замечания по ограничениям носителя допустимы)."""
    return (record.status in (journal.OK, journal.WARNINGS) and not record.reason
            and all(j.status in (journal.OK, journal.WARNINGS) for j in record.jobs))


class Notifier:
    def __init__(self):
        self.ok = False
        self.actions = False
        if Notify is not None:
            try:
                self.ok = Notify.init(APP_NAME)
                self.actions = self.ok and "actions" in (Notify.get_server_caps() or [])
            except GLib.Error:
                self.ok = False
        self._keep: list = []   # уведомления с кнопками должны жить, пока открыты

    def show(self, title: str, body: str, actions=(), urgency: str = "normal", timeout_ms: int | None = None):
        if not self.ok:
            return None
        note = Notify.Notification.new(title, body, APP_ID)
        note.set_urgency({"low": Notify.Urgency.LOW, "critical": Notify.Urgency.CRITICAL}.get(
            urgency, Notify.Urgency.NORMAL))
        if timeout_ms is not None:
            note.set_timeout(timeout_ms)
        if self.actions:
            for action_id, label, callback in actions:
                note.add_action(action_id, label, lambda n, a, cb=callback: cb())
        note.connect("closed", lambda n: n in self._keep and self._keep.remove(n))
        self._keep.append(note)
        try:
            note.show()
        except GLib.Error:
            return None
        return note

    @staticmethod
    def close(note) -> None:
        if note is not None:
            try:
                note.close()
            except GLib.Error:
                pass


class AutoController:
    def __init__(self, app):
        self.app = app
        self.notifier = Notifier()
        self.tracker = auto.InsertionTracker()
        self.paused_until: float | None = None
        self.countdowns: dict[str, tuple[int, object]] = {}   # profile_id → (таймер, уведомление)
        self.asked_unknown: set[str] = set()                  # UUID, о которых уже спрашивали в эту вставку
        self.auto_runs: set[str] = set()                      # profile_id автоматических запусков
        self.checking: set[str] = set()                       # profile_id, для которых идёт тихая проверка
        self.unlocking: set[str] = set()                      # profile_id, носитель которых разблокируется
        self.vault_held: set[str] = set()                     # profile_id, чью зашифрованную папку держит автоматика
        app.monitor.connect("volumes-changed", lambda _m: self.check())
        GLib.timeout_add_seconds(2, self._tick)               # ожидание автомонтирования
        GLib.idle_add(self.check)

    def _tick(self) -> bool:
        if self.tracker.pending:
            self.check()
        return GLib.SOURCE_CONTINUE

    # ---------- вставки ----------

    def check(self) -> bool:
        volumes = self.app.monitor.volumes()
        present = {v.uuid for v in volumes}
        self.asked_unknown &= present
        for event, volume in self.tracker.update(volumes, time.monotonic()):
            if event == auto.NEEDS_MOUNT:
                self._on_needs_mount(volume)
            else:
                self._on_ready(volume)
        return GLib.SOURCE_REMOVE

    def _profile_for(self, volume):
        return P.profile_for_volume(self.app.profiles_store.profiles, volume)

    def _on_needs_mount(self, volume) -> None:
        profile = self._profile_for(volume)
        if profile is None or not profile.auto.enabled or volume.locked:
            return
        # Носитель профиля с автоматикой так и не смонтировался — монтируем сами.
        devices.mount_async(self.app.monitor.client, volume, lambda point, error: self.app.monitor.refresh())

    def _on_ready(self, volume) -> None:
        unlocker = getattr(self.app, "unlocker", None)
        if unlocker is not None and unlocker.is_busy(volume.object_path):
            # Носитель сейчас разблокируется — решать будем после этого (Unlocker снова вызовет check).
            self.tracker.forget(volume.object_path)
            return
        profile = self._profile_for(volume)
        if profile is None:
            if not volume.locked:          # о закрытом спросим, когда его разблокируют
                self._offer_profile(volume)
            return
        conflicts = devices.uuid_conflicts(self.app.monitor.volumes())
        decision = auto.decide(profile, volume, conflicts, time.time(),
                               auto.last_success_time(profile.id), self.paused_until)
        if decision.reason == auto.CONFLICT:
            self.notifier.show(_("Drive conflict"), _("Two connected drives have the same identifier; automatic "
                                                      "backup of “%s” will not start.") % profile.name, urgency="critical")
        if self.app.runs_active(profile.id) or profile.id in self.countdowns \
                or profile.id in self.checking or profile.id in self.unlocking:
            return
        if decision.reason == auto.LOCKED:
            self._unlock_then_check(profile, volume)
            return
        if not decision.run:
            return
        if profile.encryption.mode == "gocryptfs" and getattr(self.app, "vaults", None) is not None:
            self._open_vault_then_check(profile, volume, list(decision.job_ids))
            return
        self._check_then_countdown(profile, volume, list(decision.job_ids))

    def _open_vault_then_check(self, profile, volume, job_ids) -> None:
        """Зашифрованная папка: пароль из связки — без вопросов, иначе окно; держим её до конца цепочки."""
        self.unlocking.add(profile.id)

        def opened():
            self.unlocking.discard(profile.id)
            self.vault_held.add(profile.id)
            self._check_then_countdown(profile, volume, job_ids)

        def failed(error):
            self.unlocking.discard(profile.id)
            if error:
                self.notifier.show(_("Automatic backup: %s") % profile.name, error, urgency="critical")

        self.app.vaults.acquire(profile, volume, None, opened, on_fail=failed, wait_for_screen=True,
                                purpose=_("Automatic backup of profile “%s” will start after the encrypted folder "
                                          "is opened.") % profile.name)

    def _release_vault(self, profile_id: str) -> None:
        if profile_id in self.vault_held:
            self.vault_held.discard(profile_id)
            self.app.vaults.release(profile_id)

    def _unlock_then_check(self, profile, volume) -> None:
        """Пароль из связки ключей — без вопросов; иначе окно (при заблокированном экране — после его разблокировки)."""
        unlocker = getattr(self.app, "unlocker", None)
        if unlocker is None:
            return
        self.unlocking.add(profile.id)

        def done(unlocked, error):
            self.unlocking.discard(profile.id)
            if error:
                self.notifier.show(_("Automatic backup: %s") % profile.name, error, urgency="critical")
            # Дальше — обычным порядком: Unlocker вызовет check(), и смонтированный носитель придёт как готовый.

        unlocker.unlock(volume, profile, None, on_done=done, wait_for_screen=True,
                        purpose=_("Automatic backup of profile “%s” will start after the drive is unlocked.")
                        % profile.name)

    def _check_then_countdown(self, profile, volume, job_ids) -> None:
        """Сначала молча сравнить с копией: если менять нечего — ни отсчёта, ни уведомлений."""
        self.checking.add(profile.id)
        started = time.time()
        request = RunRequest(profile=profile, volume=volume, trigger="auto", job_ids=job_ids, preview=True,
                             global_excludes=list(self.app.config_store.config.global_excludes))

        def work():
            try:
                outcome, error = Runner(request).run(), None
            except Exception as exc:  # проверка не удалась — пусть настоящий запуск покажет причину
                outcome, error = None, exc
            GLib.idle_add(done, outcome, error)

        def done(outcome, error):
            self.checking.discard(profile.id)
            last = journal.last_run(profile.id)
            if self.app.runs_active(profile.id) or (last is not None and last.started >= started):
                self._release_vault(profile.id)
                return GLib.SOURCE_REMOVE   # пока шла проверка, профиль запустили вручную — второй запуск не нужен
            if error is not None or outcome is None:
                self._start_countdown(profile, job_ids, None)
                return GLib.SOURCE_REMOVE
            changes, size = planned_changes(outcome)
            if changes == 0 and outcome_is_clean(outcome.record):
                record = outcome.record
                record.reason, record.finished = NO_CHANGES, time.time()
                journal.append(record)          # «не чаще раза в N часов» отсчитывается и от проверки
                self._release_vault(profile.id)
                self.app.on_auto_state_changed()
                if profile.auto.eject_after_success:
                    self._eject_after(profile)
                return GLib.SOURCE_REMOVE
            self._start_countdown(profile, job_ids, (changes, size))
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, daemon=True).start()

    def _offer_profile(self, volume) -> None:
        config = self.app.config_store.config
        if not volume.uuid or volume.uuid in self.asked_unknown \
                or any(d.get("uuid") == volume.uuid for d in config.ignored_drives):
            return
        self.asked_unknown.add(volume.uuid)
        name = volume.display_name
        self.notifier.show(
            _("New drive: %s") % name,
            _("Create a backup profile for it?"),
            actions=(("create", _("Create profile"), lambda: self.app.create_profile_for(volume)),
                     ("ignore", _("Don’t ask for this drive"), lambda: self._ignore(volume))))

    def _ignore(self, volume) -> None:
        store = self.app.config_store
        store.config.ignored_drives.append({"uuid": volume.uuid, "name": volume.display_name,
                                            "added": time.strftime("%Y-%m-%d")})
        store.save()

    # ---------- отсчёт и запуск ----------

    def _start_countdown(self, profile, job_ids, changes=None) -> None:
        seconds = profile.auto.countdown_sec
        if seconds <= 0:
            self._run(profile.id, job_ids)
            return
        body = _("Starts in %d s.") % seconds
        if changes is not None:
            body += " " + _("Files to change: %d (%s).") % (changes[0], GLib.format_size(changes[1]))
        note = self.notifier.show(
            _("Automatic backup: %s") % profile.name,
            body,
            actions=(("cancel", _("Cancel"), lambda: self.cancel_countdown(profile.id)),
                     ("now", _("Now"), lambda: self._fire(profile.id, job_ids))),
            timeout_ms=(seconds + 2) * 1000)
        timer = GLib.timeout_add_seconds(seconds, lambda: self._fire(profile.id, job_ids) and False)
        self.countdowns[profile.id] = (timer, note)
        self.app.on_auto_state_changed()

    def cancel_countdown(self, profile_id: str) -> None:
        entry = self.countdowns.pop(profile_id, None)
        if entry is not None:
            timer, note = entry
            GLib.source_remove(timer)
            self.notifier.close(note)
            self._release_vault(profile_id)
            self.app.on_auto_state_changed()

    def _fire(self, profile_id: str, job_ids) -> bool:
        entry = self.countdowns.pop(profile_id, None)
        if entry is not None:
            timer, note = entry
            try:
                GLib.source_remove(timer)
            except Exception:
                pass
            self.notifier.close(note)
        self._run(profile_id, job_ids)
        return False

    def _run(self, profile_id: str, job_ids) -> None:
        profile = self.app.profiles_store.get(profile_id)
        if profile is None:
            self._release_vault(profile_id)
            return
        volume = next((v for v in self.app.monitor.volumes() if v.uuid == profile.device.uuid), None)
        if volume is None or not volume.mounted or self.app.runs_active(profile_id):
            self._release_vault(profile_id)
            return
        self.auto_runs.add(profile_id)
        self.app.start_run(profile, volume, job_ids=job_ids, trigger="auto",
                           on_done=lambda outcome: self._finished(profile, outcome))
        self.app.on_auto_state_changed()

    def _finished(self, profile, outcome) -> None:
        self.auto_runs.discard(profile.id)
        self._release_vault(profile.id)
        if outcome is None:
            return
        record = outcome.record
        ok = record.status in (journal.OK, journal.WARNINGS)
        if ok and outcome_is_clean(record) and not any(j.copied_files or j.deleted_files for j in record.jobs):
            # Ничего не скопировано и не удалено — итоговое уведомление не нужно.
            if profile.auto.eject_after_success:
                self._eject_after(profile)
            return
        skipped = [j for j in record.jobs if j.status == journal.SKIPPED]
        lines = [_("Result: %s") % status_text(record.status)]
        copied = sum(j.copied_files for j in record.jobs)
        lines.append(_("Copied files: %d") % copied)
        if record.reason:
            lines.append(reason_text(record.reason, record.message))
        for job in skipped[:3]:
            lines.append(f"• {job.dest}: {reason_text(job.reason, job.message)}")
        self.notifier.show(_("Automatic backup: %s") % profile.name, "\n".join(lines),
                           actions=(("open", _("Details"), lambda: self.app.show_run(record)),),
                           urgency="normal" if ok else "critical")
        if ok and profile.auto.eject_after_success:
            self._eject_after(profile)

    def _eject_after(self, profile) -> None:
        volume = next((v for v in self.app.monitor.volumes() if v.uuid == profile.device.uuid), None)
        if volume is None or self.app.runs_active(profile.id):
            return
        client = self.app.monitor.client

        vaults = getattr(self.app, "vaults", None)

        def work():
            try:
                if vaults is not None:
                    vaults.close_for_volume_sync(volume)    # сначала закрыть зашифрованные папки на носителе
                result, error = devices.eject_sync(client, volume), None
            except GLib.Error as exc:
                result, error = None, exc
            except gocrypt.GocryptError as exc:
                result, error = None, GLib.Error(_("The encrypted folder on the drive is in use: %s")
                                                 % (exc.detail or exc.code))
            GLib.idle_add(done, result, error)

        def done(result, error):
            if error is not None:
                self.notifier.show(_("Could not eject the drive"), error.message, urgency="critical")
            elif result == devices.EJECT_POWERED_OFF:
                self.notifier.show(_("The drive can be removed"), profile.name)
            else:
                self.notifier.show(_("The drive is unmounted"), _("The device was not powered off: another "
                                                                  "partition on it is still in use."))
            self.app.monitor.refresh()
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, daemon=True).start()

    # ---------- пауза ----------

    def pause(self, seconds: float | None) -> None:
        self.paused_until = None if seconds is None else time.time() + seconds
        if seconds is not None:
            for profile_id in list(self.countdowns):
                self.cancel_countdown(profile_id)
        self.app.on_auto_state_changed()
