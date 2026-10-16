"""Диалог задания: папка на ПК → папка на носителе, режим и исключения."""

import copy

from gi.repository import Gtk

from plugbackup.core import profiles as P
from plugbackup.i18n import _
from plugbackup.ui.common import (COMPARE_TITLES, MODE_HINTS, MODE_TITLES, SYMLINK_TITLES, IssuesBox, combo,
                                  section_label)


class JobDialog(Gtk.Dialog):
    def __init__(self, parent: Gtk.Window, profile: P.Profile, job: P.Job | None, mount_point: str | None,
                 global_excludes: list[str]):
        is_new = job is None
        super().__init__(title=_("New job") if is_new else _("Edit job"), transient_for=parent, modal=True)
        self.profile = profile
        self.mount_point = mount_point
        self.job = copy.deepcopy(job) if job is not None else P.Job()
        self._dest_edited = not is_new

        self.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        self.save_button = self.add_button(_("Save"), Gtk.ResponseType.OK)
        self.save_button.get_style_context().add_class("suggested-action")
        self.set_default_size(640, -1)

        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        row = 0

        def add(label_text: str, widget: Gtk.Widget):
            nonlocal row
            label = Gtk.Label(label=label_text, xalign=0)
            grid.attach(label, 0, row, 1, 1)
            grid.attach(widget, 1, row, 1, 1)
            row += 1

        self.source = Gtk.FileChooserButton(title=_("Folder to back up"), action=Gtk.FileChooserAction.SELECT_FOLDER)
        self.source.set_hexpand(True)
        if self.job.source:
            self.source.set_filename(self.job.source)
        self.source.connect("file-set", self._on_source_set)
        add(_("Folder on this computer"), self.source)

        self.dest = Gtk.Entry(text=self.job.dest, hexpand=True)
        self.dest.set_placeholder_text(_("Folder name inside the backup folder"))
        self.dest.connect("changed", self._on_dest_changed)
        add(_("Folder on the drive"), self.dest)
        dest_hint = Gtk.Label(xalign=0, wrap=True)
        dest_hint.set_text(_("Inside “%s” on the drive.") % profile.root)
        dest_hint.get_style_context().add_class("dim-label")
        grid.attach(dest_hint, 1, row, 1, 1)
        row += 1

        self.mode = combo(MODE_TITLES, self.job.mode)
        self.mode.connect("changed", lambda _w: self._update())
        add(_("Mode"), self.mode)
        self.mode_hint = Gtk.Label(xalign=0, wrap=True, max_width_chars=60)
        self.mode_hint.get_style_context().add_class("dim-label")
        grid.attach(self.mode_hint, 1, row, 1, 1)
        row += 1

        self.compare = combo(COMPARE_TITLES, self.job.compare)
        add(_("Detect changes"), self.compare)
        self.symlinks = combo(SYMLINK_TITLES, self.job.symlinks)
        add(_("Symbolic links"), self.symlinks)

        self.max_size = Gtk.SpinButton.new_with_range(0, 10_000_000, 100)
        self.max_size.set_value(self.job.max_size_mb or 0)
        self.max_size.set_tooltip_text(_("0 — no limit"))
        add(_("Skip files larger than (MB)"), self.max_size)

        checks = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.enabled = Gtk.CheckButton(label=_("Job is enabled"), active=self.job.enabled)
        self.in_auto = Gtk.CheckButton(label=_("Include in automatic backup"), active=self.job.in_auto)
        self.skip_hidden = Gtk.CheckButton(label=_("Skip hidden files and folders"), active=self.job.skip_hidden)
        self.one_fs = Gtk.CheckButton(label=_("Do not enter other disks mounted inside the folder"),
                                      active=self.job.one_file_system)
        for widget in (self.enabled, self.in_auto, self.skip_hidden, self.one_fs):
            checks.pack_start(widget, False, False, 0)

        self.use_global = Gtk.CheckButton(label=_("Also apply the common exclusion list"),
                                          active=self.job.use_global_excludes)
        self.use_global.set_tooltip_text("\n".join(global_excludes))
        self.excludes = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.NONE)
        self.excludes.get_buffer().set_text("\n".join(self.job.excludes))
        self.excludes.get_buffer().connect("changed", lambda _b: self._update())
        excludes_scroll = Gtk.ScrolledWindow(min_content_height=110)
        excludes_scroll.add(self.excludes)
        dev_button = Gtk.Button(label=_("Add the Development set"))
        dev_button.set_tooltip_text(" ".join(P.DEV_EXCLUDES))
        dev_button.connect("clicked", self._on_add_dev)
        excludes_hint = Gtk.Label(xalign=0, wrap=True, max_width_chars=70,
                                  label=_("One pattern per line, rsync syntax: *.tmp — by name at any depth; "
                                          "node_modules/ — a folder at any depth; /Archive/ — only at the top of the job."))
        excludes_hint.get_style_context().add_class("dim-label")

        self.issues = IssuesBox()

        box = self.get_content_area()
        box.set_spacing(10)
        box.set_border_width(14)
        box.pack_start(grid, False, False, 0)
        box.pack_start(checks, False, False, 0)
        box.pack_start(section_label(_("Exclusions")), False, False, 0)
        box.pack_start(self.use_global, False, False, 0)
        box.pack_start(excludes_scroll, True, True, 0)
        box.pack_start(excludes_hint, False, False, 0)
        dev_row = Gtk.Box()
        dev_row.pack_start(dev_button, False, False, 0)
        box.pack_start(dev_row, False, False, 0)
        box.pack_start(self.issues, False, False, 0)
        self.show_all()
        self._update()

    def _on_source_set(self, _button):
        if not self._dest_edited:
            self.dest.handler_block_by_func(self._on_dest_changed)
            self.dest.set_text(P.default_dest(self.source.get_filename() or ""))
            self.dest.handler_unblock_by_func(self._on_dest_changed)
        self._update()

    def _on_dest_changed(self, _entry):
        self._dest_edited = True
        self._update()

    def _on_add_dev(self, _button):
        buffer = self.excludes.get_buffer()
        current = [line for line in buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False).splitlines()
                   if line.strip()]
        for pattern in P.DEV_EXCLUDES:
            if pattern not in current:
                current.append(pattern)
        buffer.set_text("\n".join(current))

    def collect(self) -> P.Job:
        job = copy.deepcopy(self.job)
        job.source = self.source.get_filename() or ""
        job.dest = P.normalize_dest(self.dest.get_text())
        job.mode = self.mode.get_active_id()
        job.compare = self.compare.get_active_id()
        job.symlinks = self.symlinks.get_active_id()
        size = int(self.max_size.get_value())
        job.max_size_mb = size if size > 0 else None
        job.enabled = self.enabled.get_active()
        job.in_auto = self.in_auto.get_active()
        job.skip_hidden = self.skip_hidden.get_active()
        job.one_file_system = self.one_fs.get_active()
        job.use_global_excludes = self.use_global.get_active()
        buffer = self.excludes.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        job.excludes = [line.strip() for line in text.splitlines() if line.strip()]
        return job

    def _update(self):
        self.mode_hint.set_text(MODE_HINTS[self.mode.get_active_id()]())
        issues = P.validate_job(self.collect(), self.profile, self.mount_point)
        self.issues.set_issues(issues)
        self.save_button.set_sensitive(not P.has_errors(issues))

    def run_and_get(self) -> P.Job | None:
        response = self.run()
        job = self.collect() if response == Gtk.ResponseType.OK else None
        self.destroy()
        return job
