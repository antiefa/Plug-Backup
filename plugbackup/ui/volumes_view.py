"""Список подключённых носителей и сведения о выбранном; монтирование, разблокировка LUKS, извлечение."""

import threading

from gi.repository import Gio, GLib, Gtk, Pango

from plugbackup import plat
from plugbackup.core import fsinfo
from plugbackup.plat.linux.devices import DeviceMonitor, Volume
from plugbackup.i18n import _
from plugbackup.ui.common import NOTE_CSS

devices = plat.devices
gocrypt = plat.vault
mounts = plat.mounts


def _mount_reason_text(reason: str) -> str:
    texts = {
        "": _("Ready to write"),
        "not-mounted": _("Not mounted"),
        "uuid-not-found": _("The mounted file system does not match this drive"),
        "uuid-mismatch": _("The mounted file system does not match this drive"),
        "invalid-uuid": _("The drive has no file system identifier"),
        "not-filesystem-root": _("Only a subfolder of the drive is mounted"),
        "read-only": _("Mounted read-only"),
        "not-writable": _("No write permission"),
    }
    return texts.get(reason, texts.get(reason.split(":")[0], _("Cannot check the mount point")))


def _icon_name(volume: Volume) -> str:
    if volume.encrypted:
        return "drive-harddisk-system-symbolic" if not volume.locked else "channel-secure-symbolic"
    if volume.is_loop:
        return "media-optical-symbolic"
    if volume.connection_bus == "usb":
        return "drive-removable-media-symbolic"
    return "drive-harddisk-symbolic"


def _fs_text(volume: Volume) -> str:
    if volume.encrypted and volume.locked:
        return _("Encrypted (LUKS), locked")
    name = fsinfo.display_name(volume.fs_type, volume.fs_version)
    return _("%s inside LUKS") % name if volume.encrypted else name


class VolumeRow(Gtk.ListBoxRow):
    def __init__(self, volume: Volume, conflict: bool):
        super().__init__()
        self.volume = volume

        icon = Gtk.Image.new_from_icon_name(_icon_name(volume), Gtk.IconSize.LARGE_TOOLBAR)
        title = Gtk.Label(label=volume.display_name, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        subtitle = Gtk.Label(
            label=f"{_fs_text(volume)} · {GLib.format_size(volume.size)}",
            xalign=0, ellipsize=Pango.EllipsizeMode.END,
        )
        subtitle.get_style_context().add_class("dim-label")

        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        labels.pack_start(title, False, False, 0)
        labels.pack_start(subtitle, False, False, 0)

        box = Gtk.Box(spacing=10, margin=8)
        box.pack_start(icon, False, False, 0)
        box.pack_start(labels, True, True, 0)
        if conflict:
            warn = Gtk.Image.new_from_icon_name("dialog-warning-symbolic", Gtk.IconSize.BUTTON)
            warn.set_tooltip_text(_("Another connected drive has the same identifier"))
            box.pack_end(warn, False, False, 0)
        self.add(box)


class VolumeDetails(Gtk.Box):
    """Правая часть: сведения о носителе, предупреждения и действия."""

    def __init__(self, monitor: DeviceMonitor, on_error, compact: bool = False, is_busy=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10 if compact else 12,
                         margin=10 if compact else 18)
        self.monitor = monitor
        self.on_error = on_error
        self.compact = compact
        self.is_busy = is_busy or (lambda volume: False)
        self.volume: Volume | None = None

        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.grid = Gtk.Grid(column_spacing=18, row_spacing=6)
        self.warnings = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        self.unlock_button = Gtk.Button(label=_("Unlock…"), no_show_all=True)
        self.unlock_button.get_style_context().add_class("suggested-action")
        self.unlock_button.connect("clicked", self._on_unlock)
        self.mount_button = Gtk.Button(label=_("Mount"), no_show_all=True)
        self.mount_button.connect("clicked", self._on_mount)
        self.open_button = Gtk.Button(label=_("Open in file manager"), no_show_all=True)
        self.open_button.connect("clicked", self._on_open)
        self.eject_button = Gtk.Button(label=_("Eject"), no_show_all=True)
        self.eject_button.connect("clicked", self._on_eject)
        buttons = Gtk.Box(spacing=6)
        buttons.pack_start(self.unlock_button, False, False, 0)
        buttons.pack_start(self.mount_button, False, False, 0)
        buttons.pack_start(self.open_button, False, False, 0)
        buttons.pack_start(self.eject_button, False, False, 0)

        self.pack_start(self.title, False, False, 0)
        self.pack_start(self.grid, False, False, 0)
        self.pack_start(self.warnings, False, False, 0)
        self.pack_start(buttons, False, False, 0)

    def show_volume(self, volume: Volume, conflict: bool) -> None:
        self.volume = volume
        name = GLib.markup_escape_text(volume.display_name)
        if self.compact:
            self.title.set_markup(f"<b>{GLib.markup_escape_text(_('Drive details'))}: {name}</b>")
        else:
            self.title.set_markup(f"<span size='x-large' weight='bold'>{name}</span>")

        for child in self.grid.get_children():
            self.grid.remove(child)
        for child in self.warnings.get_children():
            self.warnings.remove(child)

        rows: list[tuple[str, str]] = []
        if volume.is_loop:
            rows.append((_("Device"), _("Disk image: %s") % volume.loop_backing_file))
        else:
            model = " ".join(p for p in (volume.drive_vendor, volume.drive_model) if p)
            rows.append((_("Device"), model or volume.device))
        rows.append((_("Volume label"), volume.label or "—"))
        rows.append((_("File system"), _fs_text(volume)))
        rows.append((_("Size"), GLib.format_size(volume.size)))

        check = None
        if volume.mounted:
            space = devices.free_space(volume.mount_point)
            if space is not None:
                rows.append((_("Free"), GLib.format_size(space[0])))
            rows.append((_("Mount point"), volume.mount_point))
            check = mounts.verify_mount(volume.mount_point, volume.fs_uuid)
            rows.append((_("Check"), _mount_reason_text(check.reason)))
        elif not volume.locked:
            rows.append((_("Mount point"), _("Not mounted")))
        rows.append(("UUID", volume.uuid or "—"))
        if volume.encrypted and volume.inner_uuid:
            rows.append((_("File system UUID"), volume.inner_uuid))
        if volume.drive_serial:
            rows.append((_("Serial number"), volume.drive_serial))
        rows.append((_("Connection"), _("disk image") if volume.is_loop else (volume.connection_bus.upper() or "—")))

        for i, (name, value) in enumerate(rows):
            key = Gtk.Label(label=name, xalign=0, valign=Gtk.Align.START)
            key.get_style_context().add_class("dim-label")
            val = Gtk.Label(label=value, xalign=0, selectable=True, wrap=True,
                            wrap_mode=Pango.WrapMode.WORD_CHAR, width_chars=24, max_width_chars=60)
            self.grid.attach(key, 0, i, 1, 1)
            self.grid.attach(val, 1, i, 1, 1)

        for text, kind in self._warnings(volume, conflict, check):
            self.warnings.pack_start(self._warning(text, kind), False, False, 0)

        self.mount_button.set_sensitive(True)
        self.show_all()
        self.unlock_button.set_visible(volume.locked)
        self.mount_button.set_visible(not volume.mounted and not volume.locked and bool(volume.filesystem_path))
        self.open_button.set_visible(volume.mounted)
        self.eject_button.set_visible(volume.mounted or bool(volume.drive_path) or (volume.encrypted and not volume.locked))
        busy = self.is_busy(volume)
        self.eject_button.set_sensitive(not busy)
        self.eject_button.set_tooltip_text(_("A backup is in progress on this drive.") if busy else None)

    def _warnings(self, volume: Volume, conflict: bool, check) -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = []
        if conflict:
            items.append((_("Another connected drive has the same identifier (UUID). Automatic backup "
                            "will not run for either of them until one is disconnected."), "error"))
        if not volume.uuid:
            items.append((_("The drive has no file system identifier (UUID); it cannot be bound to a profile."), "error"))
        if volume.locked:
            items.append((_("The drive is encrypted (LUKS) and locked. Unlock it with its password to back up "
                            "or to create a profile."), "info"))
        if check is not None and check.reason == "not-writable":
            items.append((_("The root folder of the drive belongs to another user, so backups cannot be written. "
                            "This is typical for a freshly formatted ext4 drive: change the owner of the root "
                            "folder in the Disks application or with chown."), "error"))
        if volume.mounted and mounts.parent_is_user_writable(volume.mount_point):
            items.append((_("The drive is mounted in a folder you can write to. If the drive disappears, "
                            "files could be written there instead — mount it under /media for safety."), "warning"))
        if volume.fs_type and volume.fs_type not in fsinfo.SUPPORTED:
            items.append((_("This file system has not been tested with Plug & Backup."), "warning"))
        else:
            for text in fsinfo.limitations(volume.fs_type, volume.fs_version):
                items.append((text, "info"))
        return items

    @staticmethod
    def _warning(text: str, kind: str) -> Gtk.Widget:
        icon_name = {"error": "dialog-error-symbolic", "warning": "dialog-warning-symbolic"}.get(
            kind, "plugbackup-info-symbolic")
        icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.BUTTON)
        icon.get_style_context().add_class(NOTE_CSS.get(kind, "pb-st-cancelled"))
        icon.set_valign(Gtk.Align.START)
        label = Gtk.Label(label=text, xalign=0, wrap=True, max_width_chars=70)
        box = Gtk.Box(spacing=8)
        box.pack_start(icon, False, False, 0)
        box.pack_start(label, True, True, 0)
        return box

    def _on_mount(self, _button):
        volume = self.volume
        if volume is None:
            return
        self.mount_button.set_sensitive(False)

        def done(mount_point, error):
            self.mount_button.set_sensitive(True)
            if error is not None:
                self.on_error(_("Could not mount “%s”: %s") % (volume.display_name, error.message))
            self.monitor.refresh()

        devices.mount_async(self.monitor.client, volume, done)

    def _on_unlock(self, _button):
        volume = self.volume
        app = Gio.Application.get_default()
        if volume is None or app is None or not hasattr(app, "unlocker"):
            return
        from plugbackup.core import profiles as P
        profile = P.profile_for_volume(app.profiles_store.profiles, volume)
        window = app.window

        def done(_unlocked, error):
            if error:
                self.on_error(error)

        app.unlocker.unlock(volume, profile, window, on_done=done)

    def _on_eject(self, _button):
        volume = self.volume
        if volume is None or self.is_busy(volume):
            return
        self.eject_button.set_sensitive(False)
        client = self.monitor.client

        vaults = getattr(Gio.Application.get_default(), "vaults", None)

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
            self.eject_button.set_sensitive(True)
            if error is not None:
                self.on_error(_("Could not eject “%s”: %s") % (volume.display_name, error.message))
            elif result == devices.EJECT_UNMOUNTED_ONLY:
                self.on_error(_("“%s” is unmounted, but the device was not powered off: another partition on it "
                                "(or in the same card reader) is still in use, or the device does not support it.")
                              % volume.display_name)
            self.monitor.refresh()
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, daemon=True).start()

    def _on_open(self, _button):
        if self.volume is None or not self.volume.mounted:
            return
        uri = Gio.File.new_for_path(self.volume.mount_point).get_uri()
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error as exc:
            self.on_error(exc.message)
