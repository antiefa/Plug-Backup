"""Действия с профилями: создание, привязка, правка, удаление, экспорт/импорт и запись метки.

Запись на носитель (метка и копия профиля) — только по явному действию пользователя:
создание профиля, подтверждённое принятие существующей копии, перепривязка, кнопка
«Записать метку на носитель». Обычные правки профиля носитель не трогают — если метка
от этого устаревает, у профиля ставится признак device_sync_pending.
"""

import json
import os
import threading

from gi.repository import GLib, Gtk

from plugbackup import plat
from plugbackup.core import binding, marker
from plugbackup.core import profiles as P
from plugbackup.core.binding import BindingError
from plugbackup.core.storage import StorageError
from plugbackup.i18n import _
from plugbackup.ui.common import binding_error_text, confirm, message
from plugbackup.ui.import_dialog import ImportDialog
from plugbackup.ui.job_dialog import JobDialog
from plugbackup.ui.profile_dialog import ProfileDialog
from plugbackup.appcore.presentation import drive_text

devices = plat.devices
secrets = plat.secrets

# Поля, от которых зависит содержимое метки и копии профиля на носителе.
_DEVICE_FIELDS = ("root", "store_profile_on_device")


def forget_password(profile_id: str) -> None:
    """Убрать пароль профиля из связки ключей (в фоне: это запрос по D-Bus)."""
    threading.Thread(target=secrets.clear, args=(profile_id,), daemon=True).start()


class ProfileActions:
    def __init__(self, window):
        self.window = window
        self.app = window.get_application()

    # ---------- служебное ----------

    @property
    def store(self) -> P.ProfilesStore:
        return self.app.profiles_store

    def volume_for(self, profile: P.Profile):
        if not profile.bound:
            return None
        return next((v for v in self.app.monitor.volumes() if v.uuid == profile.device.uuid), None)

    def _store_ok(self) -> bool:
        if self.store.error is not None:
            message(self.window, _("Profiles cannot be changed"),
                    _("The profiles file is damaged; restore it first."), error=True)
            return False
        return True

    def _save(self, profile: P.Profile | None = None) -> bool:
        if profile is not None:
            self.store.upsert(profile)
        try:
            ok = self.store.save()
        except OSError as exc:
            message(self.window, _("Could not save profiles"), str(exc), error=True)
            return False
        self.window.refresh(select_profile=profile.id if profile else None)
        return ok

    def with_vault(self, profile: P.Profile, then, keep: bool = False) -> None:
        """Профиль с зашифрованной папкой: открыть её (пароль) и вызвать then(release); release() — когда
        папка больше не нужна. Без зашифрованной папки then(release) вызывается сразу."""
        if profile.encryption.mode != "gocryptfs":
            then(lambda: None)
            return
        volume = self.volume_for(profile)
        if volume is None or not volume.mounted:
            message(self.window, _("The drive is not connected"), _("Connect and mount the profile’s drive first."))
            return
        released = []

        def release():
            if not released:
                released.append(True)
                self.app.vaults.release(profile.id)

        def run():
            try:
                then(release)
            except Exception:
                release()      # одноразовый: если действие уже отпустило папку, второго освобождения не будет
                raise

        self.app.vaults.acquire(profile, volume, self.window, run, keep=keep)

    def close_vault(self, profile: P.Profile) -> None:
        self.app.vaults.close(profile.id)

    def _mount_point(self, profile: P.Profile) -> str | None:
        volume = self.volume_for(profile)
        return volume.mount_point if volume is not None and volume.mounted else None

    def _write_device(self, profile: P.Profile, volume, explain_action: str) -> bool:
        """Записать метку с учётом состояния корня копии. Возвращает True, если метка теперь актуальна."""
        try:
            state = binding.inspect(profile, volume)
        except BindingError as exc:
            message(self.window, _("The marker was not written to the drive"),
                    binding_error_text(exc.code) + "\n\n" + _("The profile is saved; write the marker later."))
            return False
        accept = False
        if state == marker.ROOT_FOREIGN_DATA:
            accept = confirm(
                self.window, _("The drive already has data in “%s”") % profile.root,
                _("There is no Plug & Backup marker in this folder. Accept its contents as the copy for profile "
                  "“%s”? Mirror jobs may later delete files there that do not exist on this computer.") % profile.name,
                _("Accept as this profile’s copy"))
            if not accept:
                return False
        elif state in (marker.ROOT_OTHER_PROFILE, marker.ROOT_CORRUPT):
            message(self.window, _("The marker was not written to the drive"), binding_error_text(state), error=True)
            return False
        try:
            binding.write_device_files(profile, volume, accept_existing=accept)
        except (BindingError, OSError) as exc:
            text = binding_error_text(exc.code) if isinstance(exc, BindingError) else str(exc)
            message(self.window, _("The marker was not written to the drive"), text, error=True)
            return False
        return True

    # ---------- профили ----------

    def create_from_volume(self, volume) -> None:
        if not self._store_ok():
            return
        profile = P.Profile(name=volume.display_name)
        P.bind_to_volume(profile, volume)
        dialog = ProfileDialog(self.window, profile, True, drive_text(volume), drive_ready=volume.mounted)
        result = dialog.run_and_get()
        if result is None:
            return
        if result.encryption.mode == "gocryptfs":
            # Профиль сохранится, когда будет создана (или открыта) его зашифрованная папка.
            from plugbackup.ui.encryption import change_encryption
            current = next((v for v in self.app.monitor.volumes() if v.uuid == volume.uuid), None)
            if current is not None and current.mounted:
                result.device_sync_pending = True
                change_encryption(self, None, result, current)
            return
        # Создание профиля — явное действие: сразу ставим метку, если носитель доступен.
        result.device_sync_pending = True
        current = next((v for v in self.app.monitor.volumes() if v.uuid == volume.uuid), None)
        if current is not None and current.mounted:
            if self._write_device(result, current, "create"):
                result.device_sync_pending = False
        self._save(result)

    def edit(self, profile: P.Profile) -> None:
        if not self._store_ok():
            return
        volume = self.volume_for(profile)
        ready = volume is not None and volume.mounted and not self.window.runs.is_running(profile.id)
        result = ProfileDialog(self.window, profile, False, drive_text(volume), drive_ready=ready).run_and_get()
        if result is None:
            return
        if any(getattr(result, f) != getattr(profile, f) for f in _DEVICE_FIELDS):
            result.device_sync_pending = result.bound
        new_mode = result.encryption.mode
        mode_changed = new_mode != profile.encryption.mode and "gocryptfs" in (new_mode, profile.encryption.mode)
        root_kept = False
        if mode_changed:
            # Остальные правки сохраняются сразу, режим — после переноса копии (мастер может быть отменён).
            result.encryption.mode = profile.encryption.mode
            if result.root != profile.root and ready:
                # Переименование и смена режима сразу — копия под старым именем осталась бы вне профиля.
                result.root, root_kept = profile.root, True
        if self._save(result) and profile.encryption.remember_password and not result.encryption.remember_password:
            forget_password(profile.id)
        if mode_changed and ready:
            from plugbackup.ui.encryption import change_encryption
            saved = self.store.get(profile.id)
            wanted = P.profile_from_dict(P.profile_to_dict(saved))
            wanted.encryption.mode = new_mode
            if root_kept:
                message(self.window, _("The backup folder was not renamed"),
                        _("The encryption mode and the backup folder name cannot be changed at once. Change the "
                          "name separately after the mode is changed."))
            change_encryption(self, saved, wanted, volume)

    def create_vault(self, profile: P.Profile) -> None:
        """Зашифрованной папки профиля на этом носителе ещё нет (новый носитель, папку удалили) — создать."""
        from plugbackup.ui.encryption import change_encryption
        volume = self.volume_for(profile)
        if volume is None or not volume.mounted:
            message(self.window, _("The drive is not connected"), _("Connect and mount the profile’s drive first."))
            return
        plain = P.profile_from_dict(P.profile_to_dict(profile))
        plain.encryption.mode = "none"
        change_encryption(self, plain, profile, volume)

    def set_auto_enabled(self, profile: P.Profile, enabled: bool) -> None:
        if not self._store_ok():
            return
        profile.auto.enabled = enabled
        self._save(profile)

    def restore(self, profile: P.Profile, job_ids: list[str] | None = None) -> None:
        from plugbackup.ui.restore_dialog import RestoreDialog
        if self.window.runs.is_running(profile.id):
            message(self.window, _("A backup is in progress"), _("Wait until it finishes."))
            return
        volume = self.volume_for(profile)
        if volume is None or not volume.mounted:
            message(self.window, _("The drive is not connected"), _("Connect and mount the profile’s drive first."))
            return

        def show(release):
            dialog = RestoreDialog(self.window, profile, volume, job_ids)
            dialog.connect("destroy", lambda _d: release())
            dialog.show()
        self.with_vault(profile, show)

    def open_copy(self, profile: P.Profile, job: P.Job | None = None) -> None:
        volume = self.volume_for(profile)
        if volume is None or not volume.mounted:
            message(self.window, _("The drive is not connected"), _("Connect and mount the profile’s drive first."))
            return
        # Зашифрованная папка остаётся открытой, пока её не закроют, не извлекут носитель или не выйдут.
        self.with_vault(profile, lambda release: (self._open_copy(profile, volume, job), release()), keep=True)

    def _open_copy(self, profile: P.Profile, volume, job: P.Job | None) -> None:
        from plugbackup.core import maintenance
        from gi.repository import Gio
        try:
            root, copy_dir = maintenance.prepare(profile, volume, job)
        except BindingError as exc:
            text = _("This job has no marked copy on the drive yet.") if exc.code == "job-no-marker" \
                else binding_error_text(exc.code)
            message(self.window, _("The action is not available"), text, error=True)
            return
        path = copy_dir if job is not None else root
        if not os.path.isdir(path):
            message(self.window, _("The copy is empty"), _("There is no copy of this job on the drive yet."))
            return
        try:
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)
        except GLib.Error as exc:
            self.window.show_error(exc.message)

    def clear_versions(self, profile: P.Profile) -> None:
        self.with_vault(profile, lambda release: _then_release(lambda: clear_versions(self, profile), release))

    def remove_excluded(self, profile: P.Profile, job: P.Job) -> None:
        self.with_vault(profile, lambda release: _then_release(lambda: remove_excluded(self, profile, job), release))

    def clear_job_copy(self, profile: P.Profile, job: P.Job) -> None:
        self.with_vault(profile, lambda release: _then_release(lambda: clear_job_copy(self, profile, job), release))

    def delete(self, profile: P.Profile) -> None:
        if not self._store_ok():
            return
        if self.window.runs.is_running(profile.id) or self.app.vaults.holds.get(profile.id):
            message(self.window, _("The profile is in use"),
                    _("Wait until the backup finishes and close the restore window, then try again."))
            return
        if not confirm(self.window, _("Delete profile “%s”?") % profile.name,
                       _("This removes the profile from Plug & Backup only; the backup copy and the marker on "
                         "the drive are left untouched."), _("Delete profile"), destructive=True):
            return
        self.store.remove(profile.id)
        self._save()
        forget_password(profile.id)
        if profile.encryption.mode == "gocryptfs":
            self.app.vaults.close(profile.id)

    def clone(self, profile: P.Profile) -> None:
        if not self._store_ok():
            return
        clone = P.clone_for_other_device(profile, _("%s (copy)") % profile.name)
        self._save(clone)

    def bind(self, profile: P.Profile, volume) -> None:
        if not self._store_ok():
            return
        if not confirm(self.window, _("Bind profile “%s” to this drive?") % profile.name,
                       _("Drive: %s. A marker folder will be created on it inside “%s”.") % (
                           drive_text(volume), profile.root),
                       _("Bind")):
            return
        updated = P.profile_from_dict(P.profile_to_dict(profile))
        P.bind_to_volume(updated, volume)
        updated.device_sync_pending = True
        # Зашифрованная папка на новом носителе создаётся отдельно (кнопка «Создать…» на странице профиля).
        if volume.mounted and updated.encryption.mode != "gocryptfs" and self._write_device(updated, volume, "bind"):
            updated.device_sync_pending = False
        if self._save(updated) and profile.device.uuid != volume.uuid:
            forget_password(profile.id)     # пароль был от прежнего носителя

    def unlock(self, profile: P.Profile | None, volume, then=None) -> None:
        """Разблокировать LUKS-носитель; then(volume) — после успешного монтирования."""
        def done(unlocked, error):
            if error:
                self.window.show_error(error)
            elif unlocked is not None and then is not None:
                then(unlocked)
        self.app.unlocker.unlock(volume, profile, self.window, on_done=done)

    def prepare_volume(self, profile: P.Profile, volume, then) -> None:
        """Довести носитель до готовности (разблокировать и/или смонтировать) и вызвать then(volume)."""
        if volume.locked:
            self.unlock(profile, volume, then)
            return
        if volume.mounted:
            then(volume)
            return

        def mounted(_point, error):
            self.app.monitor.refresh()
            if error is not None:
                self.window.show_error(_("Could not mount “%s”: %s") % (volume.display_name, error.message))
                return
            current = self.app.monitor.get(volume.object_path)
            if current is not None and current.mounted:
                then(current)
        devices.mount_async(self.app.monitor.client, volume, mounted)

    def write_marker(self, profile: P.Profile) -> None:
        volume = self.volume_for(profile)
        if volume is None or not volume.mounted:
            message(self.window, _("The drive is not connected"), _("Connect and mount the profile’s drive first."))
            return

        def write(release):
            try:
                if self._write_device(profile, volume, "sync"):
                    profile.device_sync_pending = False
                    self._save(profile)
            finally:
                release()
        self.with_vault(profile, write)

    # ---------- задания ----------

    def add_job(self, profile: P.Profile) -> None:
        if not self._store_ok():
            return
        job = JobDialog(self.window, profile, None, self._mount_point(profile),
                        self.app.config_store.config.global_excludes).run_and_get()
        if job is None:
            return
        profile.jobs.append(job)
        profile.device_sync_pending = profile.bound
        self._save(profile)

    def edit_job(self, profile: P.Profile, job: P.Job) -> None:
        if not self._store_ok():
            return
        result = JobDialog(self.window, profile, job, self._mount_point(profile),
                           self.app.config_store.config.global_excludes).run_and_get()
        if result is None:
            return
        profile.jobs = [result if j.id == job.id else j for j in profile.jobs]
        if P.normalize_dest(result.dest) != P.normalize_dest(job.dest):
            profile.device_sync_pending = profile.bound
        self._save(profile)

    def remove_job(self, profile: P.Profile, job: P.Job) -> None:
        if not self._store_ok():
            return
        if not confirm(self.window, _("Remove job “%s”?") % (job.dest or job.source),
                       _("Only the job is removed. Its copy on the drive is not touched."),
                       _("Remove job"), destructive=True):
            return
        profile.jobs = [j for j in profile.jobs if j.id != job.id]
        profile.device_sync_pending = profile.bound
        self._save(profile)

    def move_job(self, profile: P.Profile, job: P.Job, delta: int) -> None:
        if not self._store_ok():
            return
        index = next(i for i, j in enumerate(profile.jobs) if j.id == job.id)
        target = index + delta
        if 0 <= target < len(profile.jobs):
            profile.jobs[index], profile.jobs[target] = profile.jobs[target], profile.jobs[index]
            self._save(profile)

    def set_job_auto(self, profile: P.Profile, job: P.Job, in_auto: bool) -> None:
        if not self._store_ok() or job.in_auto == in_auto:
            return
        job.in_auto = in_auto
        self._save(profile)

    def toggle_job(self, profile: P.Profile, job: P.Job, enabled: bool) -> None:
        if not self._store_ok() or job.enabled == enabled:
            return
        job.enabled = enabled
        self._save(profile)

    # ---------- экспорт и импорт ----------

    def export(self, profile: P.Profile) -> None:
        dialog = Gtk.FileChooserNative(title=_("Export profile"), transient_for=self.window,
                                       action=Gtk.FileChooserAction.SAVE, accept_label=_("Export"))
        dialog.set_current_name(f"{profile.name}.plugbackup.json")
        dialog.set_do_overwrite_confirmation(True)
        if dialog.run() != Gtk.ResponseType.ACCEPT:
            return
        path = dialog.get_filename()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(P.export_profile(profile), f, ensure_ascii=False, indent=2)
                f.write("\n")
        except OSError as exc:
            message(self.window, _("Could not export the profile"), str(exc), error=True)

    def import_file(self) -> None:
        if not self._store_ok():
            return
        dialog = Gtk.FileChooserNative(title=_("Import profile"), transient_for=self.window,
                                       action=Gtk.FileChooserAction.OPEN, accept_label=_("Open"))
        json_filter = Gtk.FileFilter()
        json_filter.set_name(_("Profile files (*.json)"))
        json_filter.add_pattern("*.json")
        dialog.add_filter(json_filter)
        if dialog.run() != Gtk.ResponseType.ACCEPT:
            return
        try:
            with open(dialog.get_filename(), encoding="utf-8") as f:
                profile = P.import_profile(json.load(f))
        except (OSError, ValueError) as exc:
            message(self.window, _("Could not import the profile"), str(exc), error=True)
            return
        self._import(profile, volume=None)

    def import_from_device(self, volume, root_name: str, data: dict) -> None:
        if not self._store_ok():
            return
        try:
            profile = P.import_profile(data)
        except P.ProfileFormatError as exc:
            message(self.window, _("Could not import the profile"), str(exc), error=True)
            return
        profile.root = root_name
        P.bind_to_volume(profile, volume)  # носитель мог быть переформатирован — привязываем к текущему
        self._import(profile, volume=volume)

    def import_from_vault(self, volume, root_name: str) -> None:
        """Копия профиля внутри зашифрованной папки <root_name>.enc: открыть паролем, прочитать, закрыть."""
        if not self._store_ok():
            return
        probe = P.Profile(id="import-" + P.new_id(), name=root_name, root=root_name)
        P.bind_to_volume(probe, volume)
        probe.encryption = P.Encryption(mode="gocryptfs")

        def opened(release):
            from plugbackup import plat
            gocrypt = plat.vault
            try:
                data = marker.read_profile_copy(gocrypt.plain_dir(probe.id))
            except (marker.MarkerError, OSError, StorageError):
                data = None
            finally:
                release()
            if data is None:
                message(self.window, _("No profile copy in the encrypted folder"), _(
                    "The encrypted folder does not contain a copy of the profile (the profile option “Keep a copy "
                    "of this profile on the drive” was off). Import an exported profile file instead."))
                return
            try:
                profile = P.import_profile(data)
            except P.ProfileFormatError as exc:
                message(self.window, _("Could not import the profile"), str(exc), error=True)
                return
            profile.root = root_name
            P.bind_to_volume(profile, volume)
            profile.encryption.mode = "gocryptfs"
            self._import(profile, volume=volume)

        self.app.vaults.acquire(probe, volume, self.window, lambda: opened(lambda: self.app.vaults.release(probe.id)))

    def _import(self, profile: P.Profile, volume) -> None:
        exists = self.store.get(profile.id) is not None
        choice = ImportDialog(self.window, profile, exists).run_and_get()
        if choice is None:
            return
        if choice == "copy":
            bound = profile.device
            profile = P.clone_for_other_device(profile, profile.name)
            if volume is not None:
                profile.device = bound
                profile.device_sync_pending = True
        self._save(profile)


def _then_release(action, release) -> None:
    try:
        action()
    finally:
        release()


def find_vaults(volume) -> list[str]:
    """Зашифрованные папки Plug & Backup (<имя>.enc с gocryptfs.conf) в корне смонтированного носителя."""
    from plugbackup import plat
    gocrypt = plat.vault
    if not volume.mounted:
        return []
    try:
        entries = list(os.scandir(volume.mount_point))
    except OSError:
        return []
    return sorted(e.name[:-len(gocrypt.CIPHER_SUFFIX)] for e in entries
                  if e.name.endswith(gocrypt.CIPHER_SUFFIX) and len(e.name) > len(gocrypt.CIPHER_SUFFIX)
                  and e.is_dir(follow_symlinks=False) and gocrypt.is_initialized(e.path))


def find_profile_copies(volume) -> list[tuple[str, dict]]:
    """Копии профилей в папках верхнего уровня смонтированного носителя (только чтение)."""
    found: list[tuple[str, dict]] = []
    if not volume.mounted:
        return found
    try:
        entries = list(os.scandir(volume.mount_point))
    except OSError:
        return found
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            continue
        try:
            data = marker.read_profile_copy(entry.path)
        except (marker.MarkerError, OSError, StorageError):
            continue
        if data is not None:
            found.append((entry.name, data))
    return found


# ---------- обслуживание копии: только вручную и с подтверждением ----------

def _maintenance_target(actions: "ProfileActions", profile: P.Profile, job: P.Job | None):
    """Проверенные пути на носителе или None (с сообщением пользователю)."""
    from plugbackup.core import maintenance

    if actions.window.runs.is_running(profile.id):
        message(actions.window, _("A backup is in progress"), _("Wait until it finishes."))
        return None
    volume = actions.volume_for(profile)
    if volume is None or not volume.mounted:
        message(actions.window, _("The drive is not connected"), _("Connect and mount the profile’s drive first."))
        return None
    try:
        return maintenance.prepare(profile, volume, job)
    except BindingError as exc:
        text = _("This job has no marked copy on the drive yet.") if exc.code == "job-no-marker" \
            else binding_error_text(exc.code)
        message(actions.window, _("The action is not available"), text, error=True)
        return None


def _list_view(lines: list[str], limit: int = 500) -> Gtk.Widget:
    view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True)
    text = "\n".join(lines[:limit])
    if len(lines) > limit:
        text += "\n" + _("… and %d more") % (len(lines) - limit)
    view.get_buffer().set_text(text)
    scrolled = Gtk.ScrolledWindow(min_content_height=120, max_content_height=300, propagate_natural_height=True)
    scrolled.add(view)
    return scrolled


def _confirm_with_list(parent, title: str, text: str, lines: list[str], ok_label: str) -> bool:
    from plugbackup.ui.common import note

    dialog = Gtk.Dialog(title=title, transient_for=parent, modal=True)
    dialog.set_default_size(620, -1)
    dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
    ok = dialog.add_button(ok_label, Gtk.ResponseType.OK)
    ok.get_style_context().add_class("destructive-action")
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    area = dialog.get_content_area()
    area.set_border_width(14)
    area.set_spacing(10)
    area.pack_start(note(text, "warning"), False, False, 0)
    if lines:
        area.pack_start(_list_view(lines), True, True, 0)
    dialog.show_all()
    response = dialog.run()
    dialog.destroy()
    return response == Gtk.ResponseType.OK


def _job_move_target(root: str, job: P.Job) -> str | None:
    from plugbackup.core import maintenance

    return maintenance.versions_target(root, job.dest) if job.mode == "mirror_versions" else None


def _where_text(job: P.Job) -> str:
    if job.mode == "mirror_versions":
        return _("They will be moved to the version history on the drive and kept for the profile’s set number of days.")
    return _("They will be deleted from the drive permanently.")


def clear_versions(actions: "ProfileActions", profile: P.Profile) -> None:
    from plugbackup.core import maintenance

    target = _maintenance_target(actions, profile, None)
    if target is None:
        return
    root, _dest = target
    folders = maintenance.list_versions(root)
    if not folders:
        message(actions.window, _("The version history is empty"), _("There is nothing to delete."))
        return
    dialog = Gtk.Dialog(title=_("Clear the version history"), transient_for=actions.window, modal=True)
    dialog.set_default_size(560, -1)
    dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
    ok = dialog.add_button(_("Delete selected"), Gtk.ResponseType.OK)
    ok.get_style_context().add_class("destructive-action")
    area = dialog.get_content_area()
    area.set_border_width(14)
    area.set_spacing(8)
    area.pack_start(Gtk.Label(xalign=0, wrap=True, label=_(
        "Old versions of deleted and replaced files. Deleted folders cannot be restored.")), False, False, 0)
    checks = []
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    for folder in folders:
        when = GLib.DateTime.new_from_unix_local(int(folder.stamp)).format("%d.%m.%Y %H:%M")
        check = Gtk.CheckButton(active=True, label=_("%s — files: %d, %s") % (
            when, folder.files, GLib.format_size(folder.size)))
        checks.append((check, folder.name))
        box.pack_start(check, False, False, 0)
    scrolled = Gtk.ScrolledWindow(min_content_height=120, max_content_height=320, propagate_natural_height=True)
    scrolled.add(box)
    area.pack_start(scrolled, True, True, 0)
    dialog.show_all()
    response = dialog.run()
    names = [name for check, name in checks if check.get_active()]
    dialog.destroy()
    if response != Gtk.ResponseType.OK or not names:
        return
    target = _maintenance_target(actions, profile, None)  # повторная проверка непосредственно перед удалением
    if target is None:
        return
    try:
        removed = maintenance.delete_versions(target[0], names)
    except OSError as exc:
        message(actions.window, _("Could not delete"), str(exc), error=True)
        return
    message(actions.window, _("Done"), _("Version history folders deleted: %d") % len(removed))
    actions.window.refresh()


def remove_excluded(actions: "ProfileActions", profile: P.Profile, job: P.Job) -> None:
    from plugbackup.core import maintenance

    target = _maintenance_target(actions, profile, job)
    if target is None:
        return
    root, dest_dir = target
    volume = actions.volume_for(profile)
    excludes = list(actions.app.config_store.config.global_excludes)
    try:
        paths = maintenance.excluded_in_copy(job, dest_dir, volume.fs_type, excludes)
    except OSError as exc:
        message(actions.window, _("Could not check the copy"), str(exc), error=True)
        return
    files = [p for p in paths if not p.endswith("/")]
    if not files:
        message(actions.window, _("Nothing to remove"), _("The copy has no files that match the exclusions."))
        return
    if not _confirm_with_list(actions.window, _("Remove excluded files from the copy?"),
                              _("Files in the copy that match the job’s exclusions: %d. ") % len(files) + _where_text(job),
                              files, _("Remove")):
        return
    target = _maintenance_target(actions, profile, job)
    if target is None:
        return
    try:
        current = set(maintenance.excluded_in_copy(job, dest_dir, volume.fs_type, excludes))
        confirmed = [p for p in paths if p in current]  # только то, что показали и что по-прежнему исключено
        removed, _dirs = maintenance.remove_paths(dest_dir, confirmed, _job_move_target(root, job))
    except OSError as exc:
        message(actions.window, _("Could not remove"), str(exc), error=True)
        return
    message(actions.window, _("Done"), _("Files removed from the copy: %d") % removed)
    actions.window.refresh()


def clear_job_copy(actions: "ProfileActions", profile: P.Profile, job: P.Job) -> None:
    from plugbackup.core import maintenance

    target = _maintenance_target(actions, profile, job)
    if target is None:
        return
    root, dest_dir = target
    if not os.path.isdir(dest_dir):
        message(actions.window, _("The copy is empty"), _("There is nothing to clear."))
        return
    files, size = maintenance.copy_contents(dest_dir)
    if not files:
        message(actions.window, _("The copy is empty"), _("There is nothing to clear."))
        return
    text = _("All files in the copy of “%s” on the drive (%d, %s) will be removed. Use this when the source "
             "folder is empty on purpose. ") % (P.normalize_dest(job.dest), len(files), GLib.format_size(size))
    if not _confirm_with_list(actions.window, _("Clear this job’s copy?"), text + _where_text(job), files,
                              _("Clear the copy")):
        return
    target = _maintenance_target(actions, profile, job)
    if target is None:
        return
    try:
        removed, _dirs = maintenance.clear_dir(dest_dir, _job_move_target(root, job))
    except OSError as exc:
        message(actions.window, _("Could not clear"), str(exc), error=True)
        return
    message(actions.window, _("Done"), _("Files removed from the copy: %d") % removed)
    actions.window.refresh()
