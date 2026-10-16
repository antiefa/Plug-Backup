"""Зашифрованная папка профиля (gocryptfs): создание, мастер-ключ, смена режима и перенос копии.

Порядок смены режима «без шифрования → зашифрованная папка»:
  1) создать папку <корень>.enc (новый пароль, показ мастер-ключа) или открыть уже существующую;
  2) если на носителе есть обычная копия — предложить перенести её (место проверяется заранее),
     после сверки — удалить старую (по согласию);
  3) сохранить профиль, записать метку внутрь зашифрованной папки, закрыть её.
Обратно — то же в другую сторону. Отмена на любом шаге оставляет профиль в прежнем режиме.
"""

import copy
import os
import threading

from gi.repository import Gdk, GLib, Gtk, Pango

from plugbackup import plat
from plugbackup.core import binding, marker, transfer
from plugbackup.core.binding import BindingError
from plugbackup.i18n import _
from plugbackup.ui.common import binding_error_text, message, note

gocrypt = plat.vault
secrets = plat.secrets


def visibility_text() -> str:
    return _("File contents and names are encrypted, but the number of files, their approximate sizes, the folder "
             "structure and modification times can be seen without the password.")


def availability_text(reason: str) -> str:
    return {
        "no-gocryptfs": _("The gocryptfs package is not installed. Install it with: sudo apt install gocryptfs"),
        "old-gocryptfs": _("The installed gocryptfs is too old; version %s or newer is needed.")
        % ".".join(map(str, gocrypt.MIN_VERSION)),
        "no-fuse": _("FUSE is not installed. Install it with: sudo apt install fuse3"),
    }.get(reason, "")


# ---------- окна ----------

class NewPasswordDialog(Gtk.Dialog):
    def __init__(self, parent, profile_name: str, remember: bool, keyring_ok: bool):
        super().__init__(title=_("Create encrypted folder"), transient_for=parent, modal=True)
        self.set_default_size(500, -1)
        self.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        self.ok = self.add_button(_("Create"), Gtk.ResponseType.OK)
        self.ok.get_style_context().add_class("suggested-action")
        self.set_default_response(Gtk.ResponseType.OK)

        heading = Gtk.Label(xalign=0, wrap=True, max_width_chars=60)
        heading.set_markup("<b>%s</b>" % GLib.markup_escape_text(
            _("Password for the encrypted folder of profile “%s”") % profile_name))
        self.password = Gtk.Entry(visibility=False, input_purpose=Gtk.InputPurpose.PASSWORD,
                                  placeholder_text=_("Password"))
        self.repeat = Gtk.Entry(visibility=False, input_purpose=Gtk.InputPurpose.PASSWORD, activates_default=True,
                                placeholder_text=_("Repeat the password"))
        for entry in (self.password, self.repeat):
            entry.connect("changed", lambda _e: self._update())
        self.hint = Gtk.Label(xalign=0, wrap=True, max_width_chars=60)
        self.hint.get_style_context().add_class("dim-label")
        self.remember = Gtk.CheckButton(label=_("Remember the password in the keyring"),
                                        active=remember and keyring_ok, sensitive=keyring_ok)

        area = self.get_content_area()
        area.set_border_width(14)
        area.set_spacing(8)
        area.pack_start(heading, False, False, 0)
        area.pack_start(self.password, False, False, 0)
        area.pack_start(self.repeat, False, False, 0)
        area.pack_start(self.hint, False, False, 0)
        area.pack_start(self.remember, False, False, 0)
        area.pack_start(note(visibility_text(), "info"), False, False, 4)
        area.pack_start(note(_("Without the password and the master key (shown on the next step) the copy cannot "
                               "be recovered by anyone."), "warning"), False, False, 0)
        self.show_all()
        self._update()

    def _update(self) -> None:
        first, second = self.password.get_text(), self.repeat.get_text()
        if not first:
            text, ok = "", False
        elif second and first != second:
            text, ok = _("The passwords do not match."), False
        elif len(first) < 8:
            text, ok = _("A password of at least 8 characters is recommended."), first == second
        else:
            text, ok = "", first == second
        self.hint.set_text(text)
        self.ok.set_sensitive(ok)

    def run_and_get(self) -> tuple[str, bool] | None:
        response = self.run()
        result = (self.password.get_text(), self.remember.get_active()) if response == Gtk.ResponseType.OK else None
        self.destroy()
        return result


class MasterKeyDialog(Gtk.Dialog):
    """Мастер-ключ: показать, дать скопировать и не отпускать, пока пользователь не подтвердит, что сохранил."""

    def __init__(self, parent, key: str):
        super().__init__(title=_("Master key"), transient_for=parent, modal=True, deletable=False)
        self.set_default_size(620, -1)
        self.ok = self.add_button(_("Continue"), Gtk.ResponseType.OK)
        self.ok.get_style_context().add_class("suggested-action")
        area = self.get_content_area()
        area.set_border_width(14)
        area.set_spacing(10)
        heading = Gtk.Label(xalign=0, wrap=True, max_width_chars=70)
        heading.set_markup("<b>%s</b>" % GLib.markup_escape_text(_("Save the master key of the encrypted folder")))
        area.pack_start(heading, False, False, 0)
        if key:
            area.pack_start(Gtk.Label(xalign=0, wrap=True, max_width_chars=70, label=_(
                "If the password is forgotten, the copy can be opened only with this key. Write it down or print "
                "it and keep it separate from the drive. Plug & Backup does not store it anywhere and will not show "
                "it again.")), False, False, 0)
            groups = key.split("-")
            shown = "-".join(groups[:4]) + "-\n" + "-".join(groups[4:]) if len(groups) == 8 else key
            key_label = Gtk.Label(selectable=True, justify=Gtk.Justification.CENTER)
            key_label.set_markup("<span font_family='monospace' size='large' weight='bold'>%s</span>"
                                 % GLib.markup_escape_text(shown))
            copy_button = Gtk.Button(label=_("Copy"), halign=Gtk.Align.CENTER)
            copy_button.connect("clicked", lambda _b: Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(key, -1))
            area.pack_start(key_label, False, False, 8)
            area.pack_start(copy_button, False, False, 0)
            self.check = Gtk.CheckButton(label=_("I have saved the master key in a safe place"))
            self.check.connect("toggled", lambda b: self.ok.set_sensitive(b.get_active()))
            area.pack_start(self.check, False, False, 6)
            self.ok.set_sensitive(False)
        else:
            area.pack_start(note(_("The master key could not be read. You can see it later with the command "
                                   "gocryptfs-xray -dumpmasterkey <folder>/gocryptfs.conf (it asks for the password)."),
                                 "warning"), False, False, 0)
        self.show_all()

    def run_until_confirmed(self) -> None:
        while self.run() != Gtk.ResponseType.OK:
            pass
        self.destroy()


def ask_transfer(parent, title: str, text: str, size: transfer.TreeSize, need: int, free: int | None,
                 delete_label: str, skip_label: str) -> tuple[str, bool]:
    """Вопрос о переносе копии: ("transfer" | "skip" | "cancel", удалить старую после переноса)."""
    dialog = Gtk.Dialog(title=title, transient_for=parent, modal=True)
    dialog.set_default_size(560, -1)
    dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
    dialog.add_button(skip_label, 1)
    move = dialog.add_button(_("Transfer"), Gtk.ResponseType.OK)
    move.get_style_context().add_class("suggested-action")
    area = dialog.get_content_area()
    area.set_border_width(14)
    area.set_spacing(10)
    area.pack_start(Gtk.Label(xalign=0, wrap=True, max_width_chars=70, label=text), False, False, 0)
    details = _("Files in the existing copy: %d (%s). Needed on the drive: %s.") % (
        size.files, GLib.format_size(size.bytes), GLib.format_size(need))
    if free is not None:
        details += " " + _("Free: %s.") % GLib.format_size(free)
    area.pack_start(Gtk.Label(xalign=0, wrap=True, max_width_chars=70, label=details), False, False, 0)
    enough = free is None or free >= need
    if not enough:
        area.pack_start(note(_("There is not enough free space on the drive to transfer the copy."), "error"),
                        False, False, 0)
        move.set_sensitive(False)
    delete = Gtk.CheckButton(label=delete_label, active=True)
    area.pack_start(delete, False, False, 0)
    dialog.show_all()
    response = dialog.run()
    remove = delete.get_active()
    dialog.destroy()
    if response == Gtk.ResponseType.OK:
        return "transfer", remove
    if response == 1:
        return "skip", False
    return "cancel", False


class TransferDialog(Gtk.Dialog):
    """Перенос с прогрессом; done(ok: bool, details: str) — после закрытия окна."""

    def __init__(self, parent, src: str, dst: str, fs_type: str, done):
        super().__init__(title=_("Transferring the copy"), transient_for=parent, modal=True, deletable=False)
        self.set_default_size(520, -1)
        self.done = done
        self.cancel_button = self.add_button(_("Stop"), Gtk.ResponseType.CANCEL)
        self.bar = Gtk.ProgressBar(show_text=True)
        self.detail = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.detail.get_style_context().add_class("dim-label")
        area = self.get_content_area()
        area.set_border_width(14)
        area.set_spacing(8)
        area.pack_start(Gtk.Label(xalign=0, label=_("Copying, then comparing with the original…")), False, False, 0)
        area.pack_start(self.bar, False, False, 0)
        area.pack_start(self.detail, False, False, 0)
        self.job = transfer.Transfer(src, dst, fs_type, self._progress)
        self.connect("response", self._on_response)
        self.show_all()
        threading.Thread(target=self._work, daemon=True).start()

    def _progress(self, percent: int, speed: str, eta: str) -> None:
        GLib.idle_add(self._show_progress, percent, speed, eta)

    def _show_progress(self, percent, speed, eta) -> bool:
        self.bar.set_fraction(min(1.0, percent / 100))
        self.bar.set_text(f"{percent} %")
        self.detail.set_text(_("%s · %s left") % (speed, eta))
        return GLib.SOURCE_REMOVE

    def _on_response(self, _dialog, _response) -> None:
        self.cancel_button.set_sensitive(False)
        self.job.cancel()

    def _work(self) -> None:
        try:
            ok = self.job.run()
            details = self.job.output_tail
        except OSError as exc:
            ok, details = False, str(exc)
        GLib.idle_add(self._finished, ok, details)

    def _finished(self, ok: bool, details: str) -> bool:
        self.destroy()
        self.done(ok and not self.job.cancelled, "" if self.job.cancelled else details)
        return GLib.SOURCE_REMOVE


# ---------- порядок смены режима ----------

def _in_thread(work, done) -> None:
    """work() в отдельном потоке; done(result, error) — в главном цикле."""
    def run():
        try:
            result, error = work(), None
        except Exception as exc:  # показываем пользователю; пароль в тексты ошибок не попадает (gocrypt)
            result, error = None, exc
        GLib.idle_add(lambda: done(result, error) and False)
    threading.Thread(target=run, daemon=True).start()


def _error_text(exc: Exception) -> str:
    if isinstance(exc, BindingError):
        return binding_error_text(exc.code)
    if isinstance(exc, gocrypt.GocryptError):
        from plugbackup.ui.unlock import vault_error_text
        return vault_error_text(exc)
    return str(exc)


class EncryptionChanger:
    """Смена режима профиля none ⇄ gocryptfs (или создание недостающей зашифрованной папки).

    old — профиль как есть (или None для нового профиля), new — с новыми настройками.
    on_saved() — после сохранения профиля."""

    def __init__(self, actions, old, new, volume, on_saved=None):
        self.actions = actions
        self.app = actions.app
        self.window = actions.window
        self.old = old
        self.new = new
        self.volume = volume
        self.on_saved = on_saved
        self.held = False

    # ----- общее -----

    def start(self) -> None:
        ok, reason = gocrypt.availability_cached()
        to_vault = self.new.encryption.mode == "gocryptfs"
        if not ok and (to_vault or (self.old is not None and self.old.encryption.mode == "gocryptfs")):
            message(self.window, _("The encrypted folder is unavailable"), availability_text(reason), error=True)
            return
        if to_vault:
            self._to_vault()
        else:
            self._to_plain()

    def _fail(self, text: str) -> None:
        self._release()
        message(self.window, _("The encryption mode was not changed"), text, error=True)

    def _release(self) -> None:
        if self.held:
            self.held = False
            self.app.vaults.release(self.new.id)

    def _hold(self, then) -> None:
        """Зарегистрировать уже открытую (или открыть) папку в учёте Vaults."""
        def opened():
            self.held = True
            then()
        self.app.vaults.acquire(self.new, self.volume, self.window, opened,
                                on_fail=lambda error: self._fail(error) if error else self._release())

    def _save(self) -> bool:
        """Сохранить профиль с новым режимом. False — не сохранён (сообщение уже показано)."""
        if not self.actions._save(self.new):
            return False
        if self.on_saved is not None:
            self.on_saved()
        return True

    # ----- в зашифрованную папку -----

    def _to_vault(self) -> None:
        try:
            cipher = binding.cipher_dir_for(self.new, self.volume)
        except BindingError as exc:
            self._fail(binding_error_text(exc.code))
            return
        if gocrypt.is_initialized(cipher):
            # Папка уже есть (например, профиль создаётся заново) — открыть её паролем и использовать.
            self.app.vaults.acquire(
                self.new, self.volume, self.window, lambda: self._after_open(True),
                on_fail=lambda error: self._fail(error) if error else None,
                purpose=_("The drive already has the encrypted folder “%s”. Enter its password to use it.")
                % os.path.basename(cipher))
            return
        if os.path.lexists(cipher) and not transfer.is_empty_dir(cipher):
            self._fail(_("The folder “%s” already exists on the drive and is not an encrypted folder.")
                       % os.path.basename(cipher))
            return
        keyring_ok = secrets.service_state() != secrets.UNAVAILABLE
        answer = NewPasswordDialog(self.window, self.new.name, self.new.encryption.remember_password,
                                   keyring_ok).run_and_get()
        if answer is None:
            return
        password, remember = answer
        self.new.encryption.remember_password = remember
        plain = gocrypt.plain_dir(self.new.id)
        label = _("Plug & Backup: encrypted folder of profile “%s”") % self.new.name

        def work():
            key = gocrypt.init(cipher, password)
            gocrypt.mount(cipher, plain, password)
            stored = secrets.store(self.new.id, label, password) if remember else True
            return key, stored

        def done(result, error):
            if error is not None:
                self._fail(_error_text(error))
                return
            key, stored = result
            if not stored:
                self.new.encryption.remember_password = False
                self.window.show_error(_("The password could not be saved in the keyring."))
            MasterKeyDialog(self.window, key).run_until_confirmed()
            self._hold(lambda: self._after_open(False))

        _in_thread(work, done)

    def _after_open(self, existed: bool) -> None:
        if not self.held:
            self.held = True     # acquire() уже засчитал это открытие
        plain = gocrypt.plain_dir(self.new.id)
        try:
            old_root = binding.plain_root_dir_for(self.new, self.volume)
        except BindingError as exc:
            self._fail(binding_error_text(exc.code))
            return
        if transfer.is_empty_dir(old_root):
            self._finish_vault()
            return
        if not transfer.is_empty_dir(plain):
            if _confirm_keep(self.window, _("The encrypted folder already contains data, so the existing "
                                            "unencrypted copy in “%s” cannot be transferred into it. The "
                                            "unencrypted copy stays on the drive.") % self.new.root):
                self._finish_vault()
            else:
                self._release()
            return
        self._offer_transfer(old_root, plain, to_vault=True)

    def _finish_vault(self, after_saved=None) -> None:
        """Записать метку, сохранить профиль; after_saved(done) — после успешного сохранения (папка ещё открыта
        и удерживается: извлечение носителя до done() будет отклонено)."""
        plain = gocrypt.plain_dir(self.new.id)
        try:
            state = marker.root_state(plain, self.new.id)
            if state in (marker.ROOT_MISSING, marker.ROOT_EMPTY, marker.ROOT_MATCH):
                binding.write_device_files(self.new, self.volume)
                self.new.device_sync_pending = False
            else:   # чужие данные или метка другого профиля — решит пользователь на странице профиля
                self.new.device_sync_pending = True
        except (BindingError, OSError) as exc:
            self.new.device_sync_pending = True
            self.window.show_error(_("The marker was not written to the drive") + ": " + _error_text(exc))
        if self._save() and after_saved is not None:
            after_saved(self._release)      # удаление старой копии — только после сохранения нового режима
        else:
            self._release()

    # ----- обратно в обычную папку -----

    def _to_plain(self) -> None:
        source = self.old if self.old is not None else self.new
        try:
            cipher = binding.cipher_dir_for(source, self.volume)
        except BindingError as exc:
            self._fail(binding_error_text(exc.code))
            return
        if not gocrypt.is_initialized(cipher):
            self._save()
            return
        self.app.vaults.acquire(source, self.volume, self.window, self._after_open_plain,
                                on_fail=lambda error: self._fail(error) if error else None,
                                purpose=_("Enter the password to move the copy out of the encrypted folder."))

    def _after_open_plain(self) -> None:
        self.held = True
        plain = gocrypt.plain_dir(self.new.id)
        if transfer.is_empty_dir(plain):
            self._save()
            self._release()
            return
        try:
            dst = binding.plain_root_dir_for(self.new, self.volume)
        except BindingError as exc:
            self._fail(binding_error_text(exc.code))
            return
        if not transfer.is_empty_dir(dst):
            if _confirm_keep(self.window, _("The folder “%s” on the drive already contains data, so the encrypted "
                                            "copy cannot be moved there. The encrypted folder stays on the drive.")
                             % self.new.root):
                self._save()
            self._release()
            return
        self._offer_transfer(plain, dst, to_vault=False)

    # ----- перенос -----

    def _offer_transfer(self, src: str, dst: str, to_vault: bool) -> None:
        def work():
            size = transfer.tree_size(src)
            return size, transfer.space_needed(size, to_vault), _free(dst)

        def done(result, error):
            if error is not None:
                self._fail(_error_text(error))
                return
            size, need, free = result
            if to_vault:
                choice, remove = ask_transfer(
                    self.window, _("Transfer the copy into the encrypted folder?"),
                    _("The drive already has an unencrypted copy of this profile in “%s”. It can be transferred "
                      "into the encrypted folder, so the version history and the copy are kept.") % self.new.root,
                    size, need, free, _("Delete the unencrypted copy after a successful transfer"),
                    _("Start a new copy"))
            else:
                choice, remove = ask_transfer(
                    self.window, _("Move the copy out of the encrypted folder?"),
                    _("The copy will no longer be encrypted: anyone who reads the drive will see it."),
                    size, need, free, _("Delete the encrypted folder after a successful transfer"),
                    _("Leave it encrypted on the drive"))
            if choice == "cancel":
                self._release()
                return
            if choice == "skip":
                if to_vault:
                    self._finish_vault()
                else:
                    self._save()
                    self._release()
                return
            TransferDialog(self.window, src, dst, self.volume.fs_type,
                           lambda ok, details: self._transferred(ok, details, src, dst, to_vault, remove))

        _in_thread(work, done)

    def _path_safe(self, path: str, kind: str) -> bool:
        """Непосредственно перед удалением (в рабочем потоке): носитель всё ещё смонтирован там же и путь ведёт
        на него, а не в папку под бывшей точкой монтирования. kind: "vault" — открытый вид папки,
        "plain" — обычный корень копии, "cipher" — <корень>.enc. Проверка по ядру (mountinfo), без UDisks."""
        try:
            if kind == "vault":
                return path == gocrypt.plain_dir(self.new.id) and gocrypt.is_open(
                    binding.cipher_dir_for(self.new, self.volume), path)
            if kind == "cipher":
                return binding.cipher_dir_for(self.new, self.volume) == path
            return binding.plain_root_dir_for(self.new, self.volume) == path
        except (BindingError, OSError):
            return False

    def _remove(self, path: str, kind: str, clear_only: bool = False, after=None) -> None:
        """Удалить (или очистить) в фоне, если путь безопасен; after() — когда закончено (в любом случае)."""
        def work():
            if not self._path_safe(path, kind):
                return False
            transfer.clear_dir(path) if clear_only else transfer.remove_tree(path)
            return True

        def done(removed, error):
            if error is not None:
                self.window.show_error(_error_text(error))
            elif not removed:
                self.window.show_error(_("The drive is no longer available; nothing was deleted from it."))
            if after is not None:
                after()

        _in_thread(work, done)

    def _transferred(self, ok: bool, details: str, src: str, dst: str, to_vault: bool, remove: bool) -> None:
        if not ok:
            # Новая копия неполная — убрать её (папка ещё открыта), затем закрыть; старая не тронута.
            text = _("The copy was not transferred; nothing has changed.") + ("\n\n" + details if details else "")
            self._remove(dst, "vault" if to_vault else "plain", clear_only=True, after=lambda: self._fail(text))
            return
        if to_vault:
            if remove:
                self._finish_vault(after_saved=lambda done: self._remove(src, "plain", after=done))
            else:
                self._finish_vault()
            return
        try:
            cipher = binding.cipher_dir_for(self.new, self.volume)
        except BindingError:
            cipher, remove = "", False
        if not self._save():
            self._release()          # режим не сохранён — обе копии остаются на носителе
            return
        if remove:
            self.held = False

            def closed(error):
                if error:
                    self.window.show_error(error + " " + _("The encrypted folder was not deleted."))
                    return
                self._remove(cipher, "cipher")

            self.app.vaults.drop_hold(self.new.id)
            self.app.vaults.close(self.new.id, then=closed)
        else:
            self._release()


def _free(path: str) -> int | None:
    from plugbackup import plat
    try:
        return plat.system.free_bytes(path)
    except OSError:
        return None


def _confirm_keep(parent, text: str) -> bool:
    from plugbackup.ui.common import confirm
    return confirm(parent, _("Change the mode without transferring the copy?"), text, _("Change the mode"))


def change_encryption(actions, old, new, volume, on_saved=None) -> None:
    EncryptionChanger(actions, old, copy.deepcopy(new), volume, on_saved).start()
