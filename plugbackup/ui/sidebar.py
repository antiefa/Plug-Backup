"""Левая колонка: профили и новые носители (носители без профиля)."""

from gi.repository import GLib, Gtk, Pango

from plugbackup.core import profiles as P
from plugbackup.i18n import _
from plugbackup.ui.volumes_view import VolumeRow


class SectionRow(Gtk.ListBoxRow):
    def __init__(self, text: str):
        super().__init__(selectable=False, activatable=False)
        label = Gtk.Label(xalign=0, margin_start=12, margin_top=10, margin_bottom=4)
        label.set_markup(f"<b>{GLib.markup_escape_text(text)}</b>")
        self.add(label)
        self.key = None


class ProfileRow(Gtk.ListBoxRow):
    def __init__(self, profile: P.Profile, volume, conflict: bool):
        super().__init__()
        self.key = ("profile", profile.id)
        if not profile.bound:
            icon, status = "emblem-unreadable-symbolic", _("not bound")
        elif volume is None:
            icon, status = "drive-removable-media-symbolic", _("drive not connected")
        else:
            icon, status = "emblem-ok-symbolic", _("drive connected")
        image = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)
        title = Gtk.Label(label=profile.name, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        subtitle = Gtk.Label(label=status, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        subtitle.get_style_context().add_class("dim-label")
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        labels.pack_start(title, False, False, 0)
        labels.pack_start(subtitle, False, False, 0)
        box = Gtk.Box(spacing=10, margin=8)
        box.pack_start(image, False, False, 0)
        box.pack_start(labels, True, True, 0)
        if conflict or (profile.device_sync_pending and volume is not None):
            warn = Gtk.Image.new_from_icon_name("dialog-warning-symbolic", Gtk.IconSize.BUTTON)
            warn.set_tooltip_text(_("Needs attention"))
            box.pack_end(warn, False, False, 0)
        if volume is None and profile.bound:
            labels.set_opacity(0.6)
            image.set_opacity(0.6)
        self.add(box)


class Sidebar(Gtk.Box):
    def __init__(self, on_select):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.on_select = on_select
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-selected", self._on_row_selected)
        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scrolled.add(self.listbox)
        self.pack_start(scrolled, True, True, 0)
        self.set_size_request(270, -1)
        self._populating = False

    def populate(self, profiles, volumes, conflicts, select_key) -> tuple | None:
        """Перестроить список. Возвращает ключ выбранной строки."""
        self._populating = True
        for row in self.listbox.get_children():
            self.listbox.remove(row)
        by_uuid = {v.uuid: v for v in volumes if v.uuid}
        rows = []
        if profiles:
            self.listbox.add(SectionRow(_("Profiles")))
            for profile in profiles:
                volume = by_uuid.get(profile.device.uuid) if profile.bound else None
                row = ProfileRow(profile, volume, bool(volume and volume.uuid in conflicts))
                self.listbox.add(row)
                rows.append(row)
        free = [v for v in volumes if P.profile_for_volume(profiles, v) is None]
        if free:
            self.listbox.add(SectionRow(_("New drives")))
            for volume in free:
                row = VolumeRow(volume, volume.uuid in conflicts)
                row.key = ("volume", volume.object_path)
                self.listbox.add(row)
                rows.append(row)
        self.listbox.show_all()
        target = next((r for r in rows if r.key == select_key), rows[0] if rows else None)
        if target is not None:
            self.listbox.select_row(target)
        self._populating = False
        return target.key if target is not None else None

    def _on_row_selected(self, _listbox, row):
        if row is None or row.key is None or self._populating:
            return
        self.on_select(row.key)
