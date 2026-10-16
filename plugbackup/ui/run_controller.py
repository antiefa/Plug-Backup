"""Запуск резервирования из окна: отдельный поток, ход выполнения, вопросы, итог."""

import threading

from gi.repository import GLib

from plugbackup.core.runner import Callbacks, Runner, RunRequest
from plugbackup.i18n import _
from plugbackup.ui.run_dialogs import PreviewDialog, SummaryDialog, ask


class GuiCallbacks(Callbacks):
    """Вызывается из потока запуска; всё, что трогает GTK, уходит в главный цикл."""

    interactive = True

    def __init__(self, controller, profile):
        self.controller = controller
        self.profile = profile

    def confirm(self, kind, title, text, details):
        done = threading.Event()
        answer = {"value": False}

        def show():
            answer["value"] = ask(self.controller.window, self.profile, kind, title, text, details)
            done.set()
            return GLib.SOURCE_REMOVE

        GLib.idle_add(show)
        done.wait()
        return answer["value"]

    def progress(self, info):
        GLib.idle_add(self.controller.on_progress, self.profile.id, info)


class RunController:
    def __init__(self, window):
        self.window = window
        self.active: dict[str, Runner] = {}        # profile_id → идущий запуск
        self.progress: dict[str, dict] = {}        # profile_id → последнее состояние
        self.opening: set[str] = set()             # profile_id, для которых открывается зашифрованная папка
        self.vault_held: set[str] = set()          # profile_id запусков, держащих зашифрованную папку

    def is_running(self, profile_id: str) -> bool:
        return profile_id in self.active

    def any_running(self) -> bool:
        return bool(self.active)

    def start(self, profile, volume, job_ids=None, preview=False, trigger="manual", on_done=None) -> None:
        if profile.id in self.active or profile.id in self.opening:
            return
        app = self.window.get_application()
        auto = getattr(app, "auto", None)
        if trigger != "auto" and not preview and auto is not None:
            auto.cancel_countdown(profile.id)   # иначе после ручного запуска сработал бы ещё и автоматический
        vaults = getattr(app, "vaults", None)
        if vaults is not None and profile.encryption.mode == "gocryptfs":
            # Зашифрованная папка открывается на время запуска (пароль из связки ключей или окно).
            self.opening.add(profile.id)

            def opened():
                self.opening.discard(profile.id)
                self.vault_held.add(profile.id)
                self._start_now(profile, volume, job_ids, preview, trigger, on_done)

            def failed(error):
                self.opening.discard(profile.id)
                if error:
                    self.window.show_error(error)
                if on_done is not None:
                    on_done(None)

            vaults.acquire(profile, volume, self.window, opened, on_fail=failed)
            return
        self._start_now(profile, volume, job_ids, preview, trigger, on_done)

    def _start_now(self, profile, volume, job_ids, preview, trigger, on_done) -> None:
        app = self.window.get_application()
        request = RunRequest(profile=profile, volume=volume, trigger=trigger, job_ids=job_ids, preview=preview,
                             global_excludes=list(app.config_store.config.global_excludes))
        runner = Runner(request, GuiCallbacks(self, profile))
        self.active[profile.id] = runner
        self.progress[profile.id] = {"phase": "prepare", "preview": preview}
        self.window.on_run_state_changed(profile.id)

        def work():
            try:
                outcome = runner.run()
                error = None
            except Exception as exc:  # непредвиденное — не оставить окно в состоянии «идёт»
                outcome, error = None, exc
            GLib.idle_add(self._finished, profile, preview, outcome, error, on_done)

        threading.Thread(target=work, name=f"backup-{profile.id}", daemon=True).start()

    def _clear_pending(self, profile_id: str) -> None:
        store = self.window.get_application().profiles_store
        stored = store.get(profile_id)
        if stored is not None and stored.device_sync_pending:
            stored.device_sync_pending = False
            store.save()

    def stop(self, profile_id: str) -> None:
        runner = self.active.get(profile_id)
        if runner is not None:
            runner.cancel("user")

    def stop_all(self) -> None:
        for runner in self.active.values():
            runner.cancel("user")

    def on_progress(self, profile_id: str, info: dict) -> bool:
        if profile_id in self.active:
            info = {**info, "preview": self.progress.get(profile_id, {}).get("preview", False)}
            self.progress[profile_id] = info
            self.window.on_run_progress(profile_id, info)
        return GLib.SOURCE_REMOVE

    def _finished(self, profile, preview, outcome, error, on_done=None) -> bool:
        try:
            return self._show_result(profile, preview, outcome, error, on_done)
        finally:
            # Папка закрывается после окна итога: из предпросмотра можно сразу запустить резервирование.
            if profile.id in self.vault_held:
                self.vault_held.discard(profile.id)
                self.window.get_application().vaults.release(profile.id)

    def _show_result(self, profile, preview, outcome, error, on_done=None) -> bool:
        self.active.pop(profile.id, None)
        self.progress.pop(profile.id, None)
        if error is not None:
            self.window.show_error(str(error))
        if on_done is not None:
            # Автоматический запуск: итог — уведомлением, без окна поверх рабочего стола.
            if outcome is not None and outcome.marker_written:
                self._clear_pending(profile.id)
            self.window.on_run_state_changed(profile.id)
            on_done(outcome)
            return GLib.SOURCE_REMOVE
        if error is None and outcome is not None:
            if outcome.marker_written:
                self._clear_pending(profile.id)
            record = outcome.record
            nothing_done = (not preview and record.status in ("ok", "warnings") and not record.reason
                            and all(j.status in ("ok", "warnings") for j in record.jobs)
                            and not any(j.copied_files or j.deleted_files for j in record.jobs))
            if nothing_done:
                self.window.on_run_state_changed(profile.id)
                self.window.show_info(_("“%s”: the copy is up to date — nothing to change.") % profile.name)
                return GLib.SOURCE_REMOVE
            if preview:
                dialog = PreviewDialog(self.window, profile, outcome)
            else:
                dialog = SummaryDialog(self.window, outcome.record)
            self.window.on_run_state_changed(profile.id)
            dialog.run()
            dialog.destroy()
        self.window.on_run_state_changed(profile.id)
        return GLib.SOURCE_REMOVE

