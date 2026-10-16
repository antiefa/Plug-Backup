"""Пароли шифрования: пароль из связки ключей или окно ввода.

LUKS: Unlock → монтирование носителя (Unlocker.unlock). Зашифрованная папка gocryptfs: монтирование
открытого вида (Unlocker.open_vault). Один и тот же путь для кнопок окна, меню трея и автоматики.
Пока носитель разблокируется, автоматика его не трогает (is_busy) и получает его заново после завершения.
"""

import threading
import time
import traceback

from gi.repository import Gio, GLib, Gtk, Pango

from plugbackup import plat
from plugbackup.i18n import _

devices = plat.devices
gocrypt = plat.vault
secrets = plat.secrets

MOUNTABLE_WAIT_SECONDS = 15   # сколько ждать появления файловой системы внутри открытого контейнера
SCREEN_POLL_SECONDS = 3

_SCREENSAVERS = (
    ("org.cinnamon.ScreenSaver", "/org/cinnamon/ScreenSaver", "org.cinnamon.ScreenSaver"),
    ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver", "org.freedesktop.ScreenSaver"),
)


def screen_locked() -> bool:
    """Экран заблокирован — окно пароля сейчас никто не увидит."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        return False
    for name, path, iface in _SCREENSAVERS:
        try:
            reply = bus.call_sync(name, path, iface, "GetActive", None, GLib.VariantType("(b)"),
                                  Gio.DBusCallFlags.NO_AUTO_START, 1000, None)
            return bool(reply.unpack()[0])
        except GLib.Error:
            continue
    return False


def keyring_state_text(state: str) -> str:
    return {
        secrets.LOCKED: _("The keyring is locked, so the saved password could not be read."),
        secrets.UNAVAILABLE: _("The keyring service is unavailable, so the password cannot be saved."),
    }.get(state, "")


class PasswordDialog(Gtk.Dialog):
    """Окно пароля. Остаётся открытым, пока идёт попытка: неверный пароль — сообщение здесь же."""

    def __init__(self, parent, title: str, heading: str, ok_label: str, purpose: str, remember: bool | None,
                 keyring_state: str, message: str = ""):
        super().__init__(title=title, transient_for=parent, modal=parent is not None)
        self.set_default_size(440, -1)
        self.set_keep_above(parent is None)
        self.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        self.ok_button = self.add_button(ok_label, Gtk.ResponseType.OK)
        self.ok_button.get_style_context().add_class("suggested-action")
        self.set_default_response(Gtk.ResponseType.OK)

        icon = Gtk.Image.new_from_icon_name("channel-secure-symbolic", Gtk.IconSize.DIALOG)
        icon.set_valign(Gtk.Align.START)
        heading_label = Gtk.Label(xalign=0, wrap=True, max_width_chars=50)
        heading_label.set_markup("<b>%s</b>" % GLib.markup_escape_text(heading))
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        texts.pack_start(heading_label, False, False, 0)
        if purpose:
            hint = Gtk.Label(label=purpose, xalign=0, wrap=True, max_width_chars=50)
            hint.get_style_context().add_class("dim-label")
            texts.pack_start(hint, False, False, 0)

        self.entry = Gtk.Entry(visibility=False, activates_default=True, input_purpose=Gtk.InputPurpose.PASSWORD)
        self.entry.connect("changed", lambda _e: self._update())
        self.remember = None
        if remember is not None:
            self.remember = Gtk.CheckButton(label=_("Remember the password in the keyring"), active=remember)
            if keyring_state == secrets.UNAVAILABLE:
                self.remember.set_active(False)
                self.remember.set_sensitive(False)
        self.keyring_note = Gtk.Label(label=keyring_state_text(keyring_state) if remember is not None else "",
                                      xalign=0, wrap=True, max_width_chars=50, no_show_all=True)
        self.keyring_note.get_style_context().add_class("dim-label")
        self.error = Gtk.Label(xalign=0, wrap=True, max_width_chars=50, no_show_all=True,
                               ellipsize=Pango.EllipsizeMode.NONE)
        self.error.get_style_context().add_class("pb-st-error")
        self.spinner = Gtk.Spinner(no_show_all=True)

        texts.pack_start(self.entry, False, False, 6)
        if self.remember is not None:
            texts.pack_start(self.remember, False, False, 0)
        texts.pack_start(self.keyring_note, False, False, 0)
        error_row = Gtk.Box(spacing=8)
        error_row.pack_start(self.spinner, False, False, 0)
        error_row.pack_start(self.error, True, True, 0)
        texts.pack_start(error_row, False, False, 0)
        box = Gtk.Box(spacing=14)
        box.pack_start(icon, False, False, 0)
        box.pack_start(texts, True, True, 0)
        area = self.get_content_area()
        area.set_border_width(14)
        area.pack_start(box, True, True, 0)
        self.show_all()
        self.keyring_note.set_visible(bool(self.keyring_note.get_text()))
        if message:
            self.show_error(message)
        self._update()

    def _update(self) -> None:
        self.ok_button.set_sensitive(bool(self.entry.get_text()) and not self.spinner.get_visible())

    @property
    def busy(self) -> bool:
        return self.spinner.get_visible()

    def set_busy(self, busy: bool) -> None:
        self.set_response_sensitive(Gtk.ResponseType.CANCEL, not busy)
        self.spinner.set_visible(busy)
        self.spinner.set_property("active", busy)
        self.entry.set_sensitive(not busy)
        if busy:
            self.error.hide()
        self._update()

    def show_error(self, text: str) -> None:
        self.error.set_text(text)
        self.error.show()
        self.entry.grab_focus()
        self.entry.select_region(0, -1)

    @property
    def remember_chosen(self) -> bool | None:
        return None if self.remember is None else self.remember.get_active()


class Unlocker:
    def __init__(self, app):
        self.app = app
        self.flows: dict[str, "_Flow"] = {}    # object_path → идущая разблокировка

    @property
    def busy(self) -> set[str]:
        return set(self.flows)

    def is_busy(self, object_path: str) -> bool:
        return object_path in self.flows

    def unlock(self, volume, profile=None, parent=None, on_done=None, purpose: str = "",
               wait_for_screen: bool = False) -> None:
        """Разблокировать и смонтировать. on_done(volume | None, error_text | None); отмена — (None, None).

        profile — профиль носителя (для пароля в связке ключей), parent — окно-владелец или None (автоматика);
        wait_for_screen — при заблокированном экране не показывать окно, а дождаться разблокировки экрана.
        Если этот носитель уже разблокируется, второй вызов присоединяется к идущему: окно одно,
        on_done получат все.
        """
        flow = self.flows.get(volume.object_path)
        if flow is not None:
            flow.join(on_done, parent)
            return
        flow = _LuksFlow(self, volume, profile, parent, on_done, purpose, wait_for_screen)
        self.flows[volume.object_path] = flow
        flow.start()

    def open_vault(self, volume, profile, parent=None, on_done=None, purpose: str = "",
                   wait_for_screen: bool = False) -> None:
        """Открыть зашифрованную папку профиля (gocryptfs). on_done(plain_dir | None, error_text | None)."""
        key = "vault:" + profile.id
        flow = self.flows.get(key)
        if flow is not None:
            flow.join(on_done, parent)
            return
        flow = _VaultFlow(self, volume, profile, parent, on_done, purpose, wait_for_screen)
        self.flows[key] = flow
        flow.start()

    def _finished(self, flow: "_Flow") -> None:
        if self.flows.get(flow.key) is flow:
            del self.flows[flow.key]
        auto = getattr(self.app, "auto", None)
        if auto is not None:
            GLib.idle_add(auto.check)


class _Flow:
    """Общий порядок: пароль из связки → иначе окно (при заблокированном экране — после его разблокировки)
    → попытка → при неверном пароле окно остаётся → успех → _after_success. Наследники задают попытку."""

    def __init__(self, owner: Unlocker, volume, profile, parent, on_done, purpose, wait_for_screen):
        self.owner = owner
        self.app = owner.app
        self.volume = volume
        self.profile = profile
        self.parent = parent
        self.callbacks = [on_done] if on_done is not None else []
        self.purpose = purpose
        self.wait_for_screen = wait_for_screen
        self.dialog: PasswordDialog | None = None
        self.keyring_state = secrets.OK
        self.screen_note = None
        self.finished = False
        self._removed_handler = 0

    # ---------- что различается у LUKS и зашифрованной папки ----------

    @property
    def key(self) -> str:
        return self.volume.object_path

    def _needs_password(self, current) -> bool:
        return current.locked

    def _texts(self) -> tuple[str, str, str]:
        """(заголовок окна, текст над полем, надпись кнопки)."""
        return (_("Unlock drive"), _("Enter the password for the encrypted drive “%s”") % self.volume.display_name,
                _("Unlock"))

    def _keyring_label(self) -> str:
        return _("Plug & Backup: drive of profile “%s”") % self.profile.name

    def _attempt(self, current, password: str, done) -> None:
        """done(error_text | None, wrong_password: bool) — в главном цикле."""
        raise NotImplementedError

    def _after_success(self) -> None:
        raise NotImplementedError

    def _waiting_title(self) -> str:
        return _("The drive “%s” is waiting for its password") % self.volume.display_name

    # ---------- шаги ----------

    def start(self) -> None:
        self._removed_handler = self.app.monitor.connect("volumes-changed", self._on_volumes_changed)
        remember = self.profile is not None and self.profile.encryption.remember_password
        profile_id = self.profile.id if self.profile is not None else ""

        def work():
            state, password = secrets.UNAVAILABLE, None
            try:
                state = secrets.service_state()
                password = secrets.lookup(profile_id) if remember and state == secrets.OK else None
            except Exception:  # связка ключей не ответила как надо — просто спросим пароль
                pass
            GLib.idle_add(self._after_lookup, state, password)

        threading.Thread(target=work, daemon=True).start()

    def join(self, on_done, parent) -> None:
        """Ещё один запрос на тот же носитель: дождаться общего итога; окно пароля — показать поверх."""
        if on_done is not None:
            self.callbacks.append(on_done)
        if parent is not None:
            self.wait_for_screen = False        # пользователь сам у компьютера — спросить сразу
            self.parent = parent
        if self.dialog is not None:
            self.dialog.present()
        elif self.screen_note is not None:
            self._show_dialog("")

    def _after_lookup(self, state: str, password: str | None) -> bool:
        if self.finished:
            return GLib.SOURCE_REMOVE
        self.keyring_state = state
        if password:
            self._try(password, saved=True)
        else:
            self._ask("")
        return GLib.SOURCE_REMOVE

    def _current(self):
        """Тот же носитель (тот же путь и UUID) или None, если его вынули или на его месте другой."""
        current = self.app.monitor.get(self.volume.object_path)
        return current if current is not None and current.uuid == self.volume.uuid else None

    def _ask(self, message: str) -> None:
        if self.finished:
            return
        current = self._current()
        if current is None:            # носитель вынули, пока ждали
            self._finish(None, None)
            return
        if not self._needs_password(current):   # открыли в другом месте (например, в файловом менеджере)
            self._after_success()
            return
        if not self.wait_for_screen:
            self._show_dialog(message)
            return

        def work():
            try:
                locked = screen_locked()
            except Exception:
                locked = False
            GLib.idle_add(after, locked)

        def after(locked):
            if self.finished:
                return GLib.SOURCE_REMOVE
            if locked and self.wait_for_screen:
                self._wait_screen()
            else:
                self._show_dialog(message)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, daemon=True).start()

    def _show_dialog(self, message: str) -> None:
        if self.finished or self.dialog is not None:
            return
        self._close_screen_note()
        remember = None if self.profile is None else self.profile.encryption.remember_password
        title, heading, ok_label = self._texts()
        self.dialog = PasswordDialog(self.parent, title, heading, ok_label, self.purpose, remember,
                                     self.keyring_state, message)
        self.dialog.connect("response", self._on_response)
        self.dialog.present()

    def _wait_screen(self) -> None:
        notifier = getattr(getattr(self.app, "auto", None), "notifier", None)
        if self.screen_note is None and notifier is not None:
            self.screen_note = notifier.show(
                self._waiting_title(),
                _("Unlock the screen to enter the password; automatic backup will start after that."))
        GLib.timeout_add_seconds(SCREEN_POLL_SECONDS, lambda: self._ask("") and False)

    def _close_screen_note(self) -> None:
        notifier = getattr(getattr(self.app, "auto", None), "notifier", None)
        if self.screen_note is not None and notifier is not None:
            notifier.close(self.screen_note)
        self.screen_note = None

    def _on_response(self, dialog: PasswordDialog, response: int) -> None:
        if dialog.busy:          # идёт попытка — закрыть окно можно будет после ответа
            return
        if response != Gtk.ResponseType.OK:
            self._close_dialog()
            self._finish(None, None)
            return
        password = dialog.entry.get_text()
        if not password:
            return
        dialog.set_busy(True)
        self._try(password, saved=False)

    def _try(self, password: str, saved: bool) -> None:
        if self.finished:
            return
        current = self._current()
        if current is None:
            self._close_dialog()
            self._finish(None, None)
            return
        if not self._needs_password(current):
            self._unlocked(None, saved)
            return

        def done(error_text, wrong):
            if self.finished:
                return
            if error_text is None and not wrong:
                self._unlocked(password, saved)
                return
            if saved and wrong:
                self._ask(_("The saved password is incorrect. Enter the password."))
            elif self.dialog is not None and wrong:
                self.dialog.set_busy(False)
                self.dialog.show_error(_("Wrong password."))
            else:
                self._close_dialog()
                self._finish(None, error_text or _("Wrong password."))

        self._attempt(current, password, done)

    def _unlocked(self, password: str | None, saved: bool) -> None:
        remember = self.dialog.remember_chosen if self.dialog is not None else None
        self._close_dialog()
        if self.profile is not None and remember is not None and not saved:
            self._apply_remember(remember, password)
        self._after_success()

    def _apply_remember(self, remember: bool, password: str | None) -> None:
        profile = self.profile
        store = self.app.profiles_store
        current = store.get(profile.id)
        if current is None:
            return      # профиль ещё не сохранён (создание, импорт) — пароль запомнится при следующем открытии
        if current.encryption.remember_password != remember:
            current.encryption.remember_password = remember
            try:
                store.save()
            except Exception as exc:  # не удалось сохранить настройку — сам носитель уже открыт
                self._error(str(exc))
        label = self._keyring_label()

        def work():
            ok = True
            try:
                if remember and password:
                    ok = secrets.store(profile.id, label, password)
                elif not remember:
                    secrets.clear(profile.id)
            except Exception:
                ok = False
            if not ok:
                GLib.idle_add(self._error, _("The password could not be saved in the keyring."))

        threading.Thread(target=work, daemon=True).start()

    # ---------- служебное ----------

    def _on_volumes_changed(self, _monitor) -> None:
        # Носитель вынули или на его месте теперь другой (иной UUID) — эта разблокировка больше не нужна.
        if not self.finished and self._current() is None:
            self._close_dialog()
            self._finish(None, None)

    def _close_dialog(self) -> None:
        if self.dialog is not None:
            self.dialog.destroy()
            self.dialog = None

    def _error(self, text: str) -> bool:
        window = getattr(self.app, "window", None)
        if window is not None:
            window.show_error(text)
        return GLib.SOURCE_REMOVE

    def _finish(self, volume, error: str | None) -> None:
        if self.finished:
            return
        self.finished = True
        self._close_screen_note()
        if self._removed_handler:
            self.app.monitor.disconnect(self._removed_handler)
            self._removed_handler = 0
        self.owner._finished(self)
        if not self.callbacks and error:
            self._error(error)
        for callback in self.callbacks:
            try:
                callback(volume, error)
            except Exception:  # сбой одного получателя не должен лишить итога остальных
                traceback.print_exc()


class _LuksFlow(_Flow):
    """LUKS-носитель: Encrypted.Unlock, затем монтирование файловой системы внутри."""

    def _attempt(self, current, password: str, done) -> None:
        def unlocked(_cleartext, error):
            if error is None:
                done(None, False)
            elif devices.is_wrong_passphrase(error):
                done(None, True)
            else:
                done(_("Could not unlock “%s”: %s") % (self.volume.display_name, error.message), False)

        devices.unlock_async(self.app.monitor.client, current, password, unlocked)

    def _after_success(self) -> None:
        self._wait_mountable(time.monotonic())

    def _wait_mountable(self, since: float) -> bool:
        if self.finished:
            return GLib.SOURCE_REMOVE
        monitor = self.app.monitor
        monitor.refresh()
        volume = self._current()
        if volume is None:
            self._finish(None, _("The drive was disconnected."))
            return GLib.SOURCE_REMOVE
        if volume.mounted:
            self._finish(volume, None)
            return GLib.SOURCE_REMOVE
        if not volume.locked and volume.filesystem_path:
            devices.mount_async(monitor.client, volume, self._mounted)
            return GLib.SOURCE_REMOVE
        if time.monotonic() - since > MOUNTABLE_WAIT_SECONDS:
            self._finish(None, _("The drive is unlocked, but no supported file system was found inside it."))
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(300, lambda: self._wait_mountable(since))
        return GLib.SOURCE_REMOVE

    def _mounted(self, _point, error) -> None:
        self.app.monitor.refresh()
        if error is not None:
            self._finish(None, _("Could not mount “%s”: %s") % (self.volume.display_name, error.message))
            return
        self._finish(self._current(), None)


class _VaultFlow(_Flow):
    """Зашифрованная папка gocryptfs профиля: монтирование открытого вида в $XDG_RUNTIME_DIR/plugbackup/<id>."""

    @property
    def key(self) -> str:
        return "vault:" + self.profile.id

    def _paths(self, current) -> tuple[str, str]:
        from plugbackup.core import binding
        return binding.cipher_dir_for(self.profile, current), gocrypt.plain_dir(self.profile.id)

    def _current(self):
        current = super()._current()
        return current if current is not None and current.mounted else None

    def _needs_password(self, current) -> bool:
        try:
            cipher, plain = self._paths(current)
        except Exception:
            return True
        return not gocrypt.is_open(cipher, plain)

    def _texts(self) -> tuple[str, str, str]:
        return (_("Open encrypted folder"),
                _("Enter the password for the encrypted folder of profile “%s”") % self.profile.name, _("Open"))

    def _keyring_label(self) -> str:
        return _("Plug & Backup: encrypted folder of profile “%s”") % self.profile.name

    def _waiting_title(self) -> str:
        return _("Profile “%s” is waiting for the password of its encrypted folder") % self.profile.name

    def _attempt(self, current, password: str, done) -> None:
        from plugbackup.core.binding import BindingError

        def work():
            try:
                cipher, plain = self._paths(current)
                gocrypt.mount(cipher, plain, password)
                result = (None, False)
            except gocrypt.GocryptError as exc:
                result = (None, True) if exc.code == gocrypt.WRONG_PASSWORD else (vault_error_text(exc), False)
            except BindingError as exc:
                from plugbackup.ui.common import binding_error_text
                result = (binding_error_text(exc.code), False)
            except Exception as exc:
                result = (str(exc), False)
            GLib.idle_add(lambda: done(*result) and False)

        threading.Thread(target=work, daemon=True).start()

    def _after_success(self) -> None:
        current = self._current()
        if current is None:
            self._finish(None, _("The drive was disconnected."))
            return
        try:
            cipher, plain = self._paths(current)
        except Exception as exc:
            self._finish(None, str(exc))
            return
        if gocrypt.is_open(cipher, plain):
            self._finish(plain, None)
        else:
            self._finish(None, _("The encrypted folder could not be opened."))


def vault_error_text(exc: "gocrypt.GocryptError") -> str:
    texts = {
        gocrypt.WRONG_PASSWORD: _("Wrong password."),
        gocrypt.NOT_EMPTY: _("The folder is not empty."),
        gocrypt.UNAVAILABLE: _("gocryptfs or FUSE is not installed."),
    }
    text = texts.get(exc.code, _("gocryptfs reported an error."))
    return f"{text} {exc.detail}".strip() if exc.code == gocrypt.FAILED and exc.detail else text
