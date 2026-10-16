"""Предпросмотр импортируемого профиля: задания и наличие их папок на этом компьютере."""

import os

from gi.repository import GLib, Gtk, Pango

from plugbackup.core import profiles as P
from plugbackup.i18n import _
from plugbackup.ui.common import note


class ImportDialog(Gtk.Dialog):
    def __init__(self, parent: Gtk.Window, profile: P.Profile, exists: bool):
        super().__init__(title=_("Import profile"), transient_for=parent, modal=True)
        self.set_default_size(600, -1)
        self.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        if exists:
            self.add_button(_("Import as a separate copy"), 2)
            replace = self.add_button(_("Replace existing profile"), 1)
            replace.get_style_context().add_class("destructive-action")
        else:
            ok = self.add_button(_("Import"), 1)
            ok.get_style_context().add_class("suggested-action")

        title = Gtk.Label(xalign=0)
        title.set_markup(f"<span size='large' weight='bold'>{GLib.markup_escape_text(profile.name)}</span>")
        box = self.get_content_area()
        box.set_spacing(10)
        box.set_border_width(14)
        box.pack_start(title, False, False, 0)

        drive = profile.device.label or profile.device.model or profile.device.uuid or _("not bound")
        info = Gtk.Label(xalign=0, wrap=True, selectable=True,
                         label=_("Drive: %s · backup folder “%s” · jobs: %d") % (drive, profile.root, len(profile.jobs)))
        box.pack_start(info, False, False, 0)

        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        missing = 0
        for job in profile.jobs:
            ok = os.path.isdir(job.source)
            missing += not ok
            icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic" if ok else "dialog-warning-symbolic",
                                                Gtk.IconSize.BUTTON)
            icon.set_tooltip_text(_("The folder exists on this computer") if ok
                                  else _("There is no such folder on this computer"))
            label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE,
                              label=f"{job.source}  →  {job.dest}")
            row_box = Gtk.Box(spacing=8, margin=6)
            row_box.pack_start(icon, False, False, 0)
            row_box.pack_start(label, True, True, 0)
            listbox.add(row_box)
        scrolled = Gtk.ScrolledWindow(min_content_height=120, max_content_height=300, propagate_natural_height=True)
        scrolled.add(listbox)
        box.pack_start(scrolled, True, True, 0)

        box.pack_start(note(_("Automatic backup is turned off for the imported profile. Check the folders and "
                              "turn it on in the profile settings."), "info"), False, False, 0)
        if missing:
            box.pack_start(note(_("Some job folders do not exist on this computer (%d). Edit those jobs before "
                                  "backing up.") % missing, "warning"), False, False, 0)
        if exists:
            box.pack_start(note(_("A profile with the same ID already exists. Replacing it keeps its binding "
                                  "to the marker on the drive; importing it as a separate copy assigns new IDs."),
                                "warning"), False, False, 0)
        self.show_all()

    def run_and_get(self) -> str | None:
        response = self.run()
        self.destroy()
        return {1: "replace", 2: "copy"}.get(response)
