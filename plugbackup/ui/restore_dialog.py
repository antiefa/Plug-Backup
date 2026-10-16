"""Диалог восстановления: выбор → проверка → восстановление → итог.

На компьютере ничего не удаляется; перезаписываемые файлы откладываются в restore-backup.
"""

import os
import threading

from gi.repository import Gio, GLib, Gtk, Pango

from plugbackup.core import maintenance, restore
from plugbackup.core import profiles as P
from plugbackup.core.binding import BindingError
from plugbackup.i18n import _
from plugbackup.ui.common import binding_error_text, note, section_label
from plugbackup.ui.run_dialogs import record_widget

LIST_LIMIT = 300


def _paths_view(paths: list[str]) -> Gtk.Widget:
    view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True)
    text = "\n".join(paths[:LIST_LIMIT])
    if len(paths) > LIST_LIMIT:
        text += "\n" + _("… and %d more") % (len(paths) - LIST_LIMIT)
    view.get_buffer().set_text(text)
    scrolled = Gtk.ScrolledWindow(min_content_height=60, max_content_height=200, propagate_natural_height=True)
    scrolled.add(view)
    return scrolled


class RestoreDialog(Gtk.Dialog):
    def __init__(self, window, profile: P.Profile, volume, preselect: list[str] | None = None):
        super().__init__(title=_("Restore — %s") % profile.name, transient_for=window, modal=True)
        self.main_window, self.profile, self.volume = window, profile, volume
        self.set_default_size(720, 560)
        self.restorer = None
        self.items: list[restore.RestoreItem] = []

        self.back_button = self.add_button(_("Back"), 1)
        self.cancel_button = self.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        self.next_button = self.add_button(_("Check"), 2)
        self.next_button.get_style_context().add_class("suggested-action")
        self.connect("response", self._on_response)
        self.connect("delete-event", lambda *_a: self.restorer is not None)  # во время восстановления не закрывать

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.add_named(self._choose_page(preselect), "choose")
        self.preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=14)
        scrolled = Gtk.ScrolledWindow()
        scrolled.add(self.preview_box)
        self.stack.add_named(scrolled, "preview")
        self.stack.add_named(self._progress_page(), "progress")
        self.result_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=14)
        result_scroll = Gtk.ScrolledWindow()
        result_scroll.add(self.result_box)
        self.stack.add_named(result_scroll, "result")
        self.get_content_area().pack_start(self.stack, True, True, 0)
        self.show_all()
        self._set_page("choose")

    # ---------- страницы ----------

    def _choose_page(self, preselect) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=14)
        box.pack_start(section_label(_("What to restore")), False, False, 0)
        self.job_checks: list[tuple[Gtk.CheckButton, P.Job, str | None]] = []
        for job in self.profile.jobs:
            copy_dir, problem = None, ""
            try:
                _root, copy_dir = maintenance.prepare(self.profile, self.volume, job)
                if not os.path.isdir(copy_dir) or not os.listdir(copy_dir):
                    copy_dir, problem = None, _("no copy on the drive yet")
            except BindingError as exc:
                problem = _("no copy on the drive yet") if exc.code == "job-no-marker" else binding_error_text(exc.code)
            label = f"{P.normalize_dest(job.dest)}  →  {job.source}"
            if problem:
                label += f"   ({problem})"
            check = Gtk.CheckButton(label=label)
            check.get_child().set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            check.set_active(copy_dir is not None and (preselect is None or job.id in preselect))
            check.set_sensitive(copy_dir is not None)
            self.job_checks.append((check, job, copy_dir))
            box.pack_start(check, False, False, 0)

        box.pack_start(section_label(_("Where to")), False, False, 0)
        self.to_original = Gtk.RadioButton.new_with_label(None, _("Back to the original folders on this computer"))
        self.to_other = Gtk.RadioButton.new_with_label_from_widget(self.to_original, _("Into another folder:"))
        self.other_chooser = Gtk.FileChooserButton(title=_("Folder for restored files"),
                                                   action=Gtk.FileChooserAction.SELECT_FOLDER)
        default_base = restore.default_other_base()
        self.other_chooser.set_filename(default_base if os.path.isdir(default_base) else os.path.expanduser("~"))
        self.other_base = default_base
        self.other_chooser.connect("file-set", lambda c: setattr(self, "other_base", c.get_filename()))
        other_row = Gtk.Box(spacing=8)
        other_row.pack_start(self.to_other, False, False, 0)
        other_row.pack_start(self.other_chooser, True, True, 0)
        hint = Gtk.Label(xalign=0, wrap=True, label=_(
            "Each job goes into its own subfolder, for example “%s”.") % os.path.join(
            "…", os.path.basename(restore.default_other_target(self.profile.jobs[0])) if self.profile.jobs else ""))
        hint.get_style_context().add_class("dim-label")
        hint.set_margin_start(24)
        box.pack_start(self.to_original, False, False, 0)
        box.pack_start(other_row, False, False, 0)
        box.pack_start(hint, False, False, 0)

        self.skip_newer = Gtk.CheckButton(label=_("Do not replace files that are newer on this computer than in the copy"),
                                          active=True)
        box.pack_start(self.skip_newer, False, False, 0)
        box.pack_start(note(_("Nothing is deleted on this computer. Files that get replaced are first saved to %s.")
                            % restore.restore_backup_root(), "info"), False, False, 0)
        box.pack_start(note(_("The current state of the copy is restored, not a state at a given date. In "
                              "“Accumulate” mode the copy also contains files deleted long ago; excluded files "
                              "are not in the copy; after an interrupted backup the copy may be incomplete. Old "
                              "versions are in the version history (open the copy on the drive)."), "info"),
                       False, False, 0)
        return box

    def _progress_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=24, valign=Gtk.Align.CENTER)
        self.progress_title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        self.progress_bar = Gtk.ProgressBar(show_text=True)
        self.progress_detail = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        self.progress_detail.get_style_context().add_class("dim-label")
        for widget in (self.progress_title, self.progress_bar, self.progress_detail):
            box.pack_start(widget, False, False, 0)
        return box

    def _set_page(self, name: str) -> None:
        self.stack.set_visible_child_name(name)
        self.back_button.set_visible(name == "preview")
        self.next_button.set_visible(name in ("choose", "preview"))
        self.next_button.set_label(_("Check") if name == "choose" else _("Restore"))
        self.cancel_button.set_label(_("Stop") if name == "progress" else
                                     _("Close") if name == "result" else _("Cancel"))

    # ---------- шаги ----------

    def _collect(self) -> list[restore.RestoreItem]:
        items = []
        for check, job, copy_dir in self.job_checks:
            if not check.get_active() or copy_dir is None:
                continue
            if self.to_original.get_active():
                target = job.source
            else:
                target = os.path.join(self.other_base or os.path.expanduser("~"),
                                      os.path.basename(restore.default_other_target(job)))
            items.append(restore.RestoreItem(job, copy_dir, target))
        return items

    def _show_preview(self) -> None:
        self.items = self._collect()
        for child in self.preview_box.get_children():
            self.preview_box.remove(child)
        if not self.items:
            self.preview_box.pack_start(note(_("Choose at least one job with a copy."), "warning"), False, False, 0)
            self.next_button.set_sensitive(False)
        else:
            self.next_button.set_sensitive(True)
        total_over = 0
        for item in self.items:
            title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
            title.set_markup("<b>%s</b>" % GLib.markup_escape_text(f"{item.copy_dir}  →  {item.target_dir}"))
            self.preview_box.pack_start(title, False, False, 0)
            try:
                plan = restore.plan(item, self.volume.fs_type, self.skip_newer.get_active())
            except OSError as exc:
                self.preview_box.pack_start(note(str(exc), "error"), False, False, 0)
                continue
            total_over += len(plan.overwritten)
            self.preview_box.pack_start(Gtk.Label(xalign=0, wrap=True, label=_(
                "Will appear: %d · will be replaced: %d · size: %s") % (
                len(plan.new_files), len(plan.overwritten), GLib.format_size(plan.transfer_bytes))), False, False, 0)
            if plan.overwritten:
                exp = Gtk.Expander(label=_("Will be replaced (old versions are saved) — %d") % len(plan.overwritten))
                exp.add(_paths_view(plan.overwritten))
                self.preview_box.pack_start(exp, False, False, 0)
            if plan.new_files:
                exp = Gtk.Expander(label=_("Will appear — %d") % len(plan.new_files))
                exp.add(_paths_view(plan.new_files))
                self.preview_box.pack_start(exp, False, False, 0)
        if total_over:
            self.preview_box.pack_start(note(_("Files to be replaced will first be saved to %s.")
                                             % restore.restore_backup_root(), "warning"), False, False, 0)
        self.preview_box.show_all()
        self._set_page("preview")

    def _start(self) -> None:
        if not self.items:
            return
        self._set_page("progress")
        self.progress_title.set_text(_("Restoring…"))

        def progress(info):
            GLib.idle_add(self._on_progress, info)

        self.restorer = restore.Restorer(self.profile.id, self.profile.name, self.items, self.volume.fs_type,
                                         keep_overwritten=True, skip_newer=self.skip_newer.get_active(),
                                         progress=progress)
        restorer = self.restorer

        def work():
            try:
                record, error = restorer.run(), None
            except Exception as exc:
                record, error = None, exc
            GLib.idle_add(self._finished, record, error)

        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, info) -> bool:
        if self.restorer is None:
            return GLib.SOURCE_REMOVE
        count, index = max(1, info["count"]), info["index"]
        self.progress_title.set_text(_("Job %d of %d: %s") % (index + 1, count, P.normalize_dest(info["job"].dest)))
        self.progress_bar.set_fraction(min(1.0, (index + info["percent"] / 100) / count))
        self.progress_bar.set_text(f"{info['percent']} %")
        self.progress_detail.set_text(_("%s · %s left · %s") % (info["speed"], info["eta"], info["file"]))
        return GLib.SOURCE_REMOVE

    def _finished(self, record, error) -> bool:
        backup_root = self.restorer.backup_root if self.restorer else None
        self.restorer = None
        for child in self.result_box.get_children():
            self.result_box.remove(child)
        if error is not None:
            self.result_box.pack_start(note(str(error), "error"), False, False, 0)
        if record is not None:
            self.result_box.pack_start(record_widget(record), False, False, 0)
        if backup_root and os.path.isdir(backup_root):
            button = Gtk.Button(label=_("Open replaced files"), halign=Gtk.Align.START)
            button.connect("clicked", lambda _b: Gio.AppInfo.launch_default_for_uri(
                Gio.File.new_for_path(backup_root).get_uri(), None))
            self.result_box.pack_start(button, False, False, 0)
        targets = sorted({item.target_dir for item in self.items})
        if len(targets) == 1:
            open_target = Gtk.Button(label=_("Open the restored folder"), halign=Gtk.Align.START)
            open_target.connect("clicked", lambda _b: Gio.AppInfo.launch_default_for_uri(
                Gio.File.new_for_path(targets[0]).get_uri(), None))
            self.result_box.pack_start(open_target, False, False, 0)
        self.result_box.show_all()
        self._set_page("result")
        return GLib.SOURCE_REMOVE

    def _on_response(self, _dialog, response) -> None:
        page = self.stack.get_visible_child_name()
        if response == 2:
            self.stop_emission_by_name("response")
            if page == "choose":
                self._show_preview()
            elif page == "preview":
                self._start()
        elif response == 1:
            self.stop_emission_by_name("response")
            self._set_page("choose")
        elif page == "progress":
            self.stop_emission_by_name("response")
            if self.restorer is not None:
                self.restorer.cancel()
        else:
            self.destroy()
