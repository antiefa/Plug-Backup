"""Открытые зашифрованные папки (gocryptfs): кто их держит и когда закрывать.

Папка открывается на время действия (резервирование, предпросмотр, восстановление, обслуживание,
проверка перед автозапуском) и закрывается, когда её больше никто не держит. «Открыть копию» держит
папку до «Закрыть», извлечения носителя или выхода из приложения.
"""

import threading

from gi.repository import GLib

from plugbackup import plat
from plugbackup.core import binding
from plugbackup.i18n import _

gocrypt = plat.vault


def uses_vault(profile) -> bool:
    return profile is not None and profile.encryption.mode == "gocryptfs"


class Vaults:
    def __init__(self, app):
        self.app = app
        self.holds: dict[str, int] = {}     # profile_id → сколько действий держат папку открытой
        self.kept: set[str] = set()         # открыта по «Открыть копию» — до явного закрытия

    # ---------- открыть / отпустить ----------

    def is_open(self, profile, volume) -> bool:
        if not uses_vault(profile) or volume is None or not volume.mounted:
            return False
        try:
            return gocrypt.is_open(binding.cipher_dir_for(profile, volume), gocrypt.plain_dir(profile.id))
        except (binding.BindingError, OSError):
            return False

    def acquire(self, profile, volume, parent, then, on_fail=None, purpose: str = "",
                wait_for_screen: bool = False, keep: bool = False) -> None:
        """Открыть папку (пароль из связки или окно) и вызвать then(). Каждому acquire — свой release().

        Профиль без зашифрованной папки — then() сразу. on_fail(error_text | None) — отмена или ошибка
        (по умолчанию ошибка показывается в окне)."""
        if not uses_vault(profile):
            then()
            return

        def opened(plain, error):
            if plain is None:
                if on_fail is not None:
                    on_fail(error)
                elif error:
                    self._show_error(error)
                return
            self.holds[profile.id] = self.holds.get(profile.id, 0) + 1
            if keep:
                self.kept.add(profile.id)
            window = getattr(self.app, "window", None)
            if window is not None:
                GLib.idle_add(lambda: window.refresh() and False)
            then()

        if self.is_open(profile, volume):
            opened(gocrypt.plain_dir(profile.id), None)
            return
        self.app.unlocker.open_vault(volume, profile, parent, on_done=opened, purpose=purpose,
                                     wait_for_screen=wait_for_screen)

    def release(self, profile_id: str) -> None:
        count = self.holds.get(profile_id, 0) - 1
        if count > 0:
            self.holds[profile_id] = count
            return
        self.holds.pop(profile_id, None)
        if profile_id not in self.kept:
            self.close(profile_id)

    def drop_hold(self, profile_id: str) -> None:
        """Отпустить своё удержание, не закрывая папку (её сейчас закроет сам вызывающий)."""
        count = self.holds.get(profile_id, 0) - 1
        if count > 0:
            self.holds[profile_id] = count
        else:
            self.holds.pop(profile_id, None)

    def close(self, profile_id: str, then=None) -> None:
        """Закрыть папку в фоне (если её никто не держит). then(error_text | None)."""
        if self.holds.get(profile_id):
            if then is not None:
                then(_("The encrypted folder is in use."))
            return
        self.kept.discard(profile_id)
        plain = gocrypt.plain_dir(profile_id)

        def work():
            try:
                gocrypt.unmount(plain)
                error = None
            except gocrypt.GocryptError as exc:
                error = _("Could not close the encrypted folder: %s") % (exc.detail or exc.code)
            GLib.idle_add(done, error)

        def done(error):
            if then is not None:
                then(error)
            elif error:
                self._show_error(error + " " + _("Close any files open in that folder and try again."))
            window = getattr(self.app, "window", None)
            if window is not None:
                window.refresh()
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, daemon=True).start()

    # ---------- извлечение и выход ----------

    def close_for_volume_sync(self, volume) -> None:
        """Перед извлечением (вызывается в рабочем потоке): закрыть папки профилей этого носителя.
        Не получилось — GocryptError, извлечение не выполняется."""
        for profile in self.app.profiles_store.profiles:
            if not (uses_vault(profile) and profile.bound and profile.device.uuid == volume.uuid):
                continue
            plain = gocrypt.plain_dir(profile.id)
            if gocrypt.mounted_source(plain) is None:
                continue
            if self.holds.get(profile.id):
                # Папкой пользуется действие (восстановление, перенос, обслуживание) — сначала завершить его.
                raise gocrypt.GocryptError(gocrypt.FAILED, _("Profile “%s”: an action using the copy has not finished")
                                           % profile.name)
            gocrypt.unmount(plain)
            GLib.idle_add(self._forget, profile.id)

    def _forget(self, profile_id: str) -> bool:
        self.holds.pop(profile_id, None)
        self.kept.discard(profile_id)
        return GLib.SOURCE_REMOVE

    def close_all_sync(self) -> None:
        """При выходе: закрыть все свои папки; занятые — «лениво» (закроются, как только освободятся).
        Папку профиля, который сейчас выполняется из командной строки, не трогаем."""
        from plugbackup import plat

        for plain in gocrypt.stale_mounts():
            lock = plat.system.ProfileLock(plain.rstrip("/").rsplit("/", 1)[-1])
            if not lock.acquire():
                continue
            try:
                gocrypt.unmount(plain)
            except gocrypt.GocryptError:
                try:
                    gocrypt.unmount(plain, lazy=True)
                except gocrypt.GocryptError:
                    pass
            finally:
                lock.release()
        self.holds.clear()
        self.kept.clear()

    def cleanup_stale(self) -> None:
        """При запуске приложения: закрыть свои папки, оставшиеся открытыми после сбоя.
        Папку профиля, который сейчас выполняется (например, из командной строки), не трогаем."""
        from plugbackup import plat

        def work():
            failed = []
            for plain in gocrypt.stale_mounts():
                lock = plat.system.ProfileLock(plain.rstrip("/").rsplit("/", 1)[-1])
                if not lock.acquire():
                    continue
                try:
                    gocrypt.unmount(plain)
                except gocrypt.GocryptError:
                    failed.append(plain)
                finally:
                    lock.release()
            if failed:
                GLib.idle_add(self._show_error, _("Encrypted folders left open after a failure could not be "
                                                  "closed: %s") % ", ".join(failed))

        threading.Thread(target=work, daemon=True).start()

    def _show_error(self, text: str) -> bool:
        window = getattr(self.app, "window", None)
        if window is not None:
            window.show_error(text)
        return GLib.SOURCE_REMOVE
