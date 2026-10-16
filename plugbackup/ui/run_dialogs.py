"""Диалоги запуска: подтверждения, предпросмотр, итог, журнал."""

import os
import time

from gi.repository import Gio, GLib, Gtk, Pango

from plugbackup.core import journal
from plugbackup.core.journal import RunRecord
from plugbackup.core.profiles import Profile
from plugbackup.i18n import _
from plugbackup.ui.common import note
from plugbackup.ui.run_texts import (record_status_text, reason_text, status_image, status_pixbuf, status_text,
                                     trigger_text)

LIST_LIMIT = 300


def _fmt_time(ts: float) -> str:
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(ts)) if ts else "—"


def _path_list(paths: list[str], total: int | None = None) -> Gtk.Widget:
    view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.NONE)
    shown = paths[:LIST_LIMIT]
    text = "\n".join(shown)
    rest = (total if total is not None else len(paths)) - len(shown)
    if rest > 0:
        text += "\n" + _("… and %d more") % rest
    view.get_buffer().set_text(text)
    scrolled = Gtk.ScrolledWindow(min_content_height=60, max_content_height=220, propagate_natural_height=True)
    scrolled.add(view)
    return scrolled


def _expander(title: str, paths: list[str], total: int | None = None) -> Gtk.Widget:
    expander = Gtk.Expander(label=title)
    expander.add(_path_list(paths, total))
    return expander


# ---------- подтверждения из потока запуска ----------

def ask(parent: Gtk.Window, profile: Profile, kind: str, title: str, text: str, details: list[str]) -> bool:
    if kind == "mass-delete":
        heading = _("Delete many files from the copy?")
        body = _("Job “%s → %s” would delete or move to the version history %d or more files — more than the "
                 "safety threshold of the profile. Check that the source folder is the right one.") % (
            title, text, len(details))
        ok_label, destructive = _("Delete and continue"), True
    elif kind == "accept-root":
        heading = _("Accept existing data as the copy?")
        body = _("The folder “%s” on the drive has data but no Plug & Backup marker. Mirror jobs may delete files "
                 "there that do not exist on this computer.") % text
        ok_label, destructive = _("Accept and continue"), True
    elif kind == "accept-job":
        heading = _("Accept the job folder’s existing data?")
        body = _("The folder “%s” on the drive already has data that is not marked as the copy of job “%s”. "
                 "Mirror mode may delete files there.") % (text, title)
        ok_label, destructive = _("Accept and continue"), True
    elif kind == "free-space":
        heading = _("Not enough free space")
        body = _("The copy needs about %s, but only %s is free. Delete the oldest version history folders "
                 "to make room?") % (GLib.format_size(int(title)), GLib.format_size(int(text)))
        ok_label, destructive = _("Delete old versions"), True
    else:
        heading, body, ok_label, destructive = title, text, _("Continue"), False

    dialog = Gtk.Dialog(title=heading, transient_for=parent, modal=True)
    dialog.set_default_size(560, -1)
    dialog.add_button(_("Skip this job"), Gtk.ResponseType.CANCEL)
    ok = dialog.add_button(ok_label, Gtk.ResponseType.OK)
    ok.get_style_context().add_class("destructive-action" if destructive else "suggested-action")
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    area = dialog.get_content_area()
    area.set_border_width(14)
    area.set_spacing(10)
    label = Gtk.Label(xalign=0)
    label.set_markup(f"<b>{GLib.markup_escape_text(heading)}</b>")
    area.pack_start(label, False, False, 0)
    area.pack_start(note(body, "warning"), False, False, 0)
    if details:
        area.pack_start(_expander(_("List (%d)") % len(details), details), False, False, 0)
    dialog.show_all()
    response = dialog.run()
    dialog.destroy()
    return response == Gtk.ResponseType.OK


# ---------- предпросмотр ----------

class PreviewDialog(Gtk.Dialog):
    def __init__(self, parent: Gtk.Window, profile: Profile, outcome):
        super().__init__(title=_("Preview — %s") % profile.name, transient_for=parent, modal=True)
        self.set_default_size(720, 560)
        self.add_button(_("Close"), Gtk.ResponseType.CLOSE)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=14)
        record = outcome.record
        if record.status in (journal.ERROR, journal.SKIPPED) and not record.jobs:
            box.pack_start(note(reason_text(record.reason, record.message), "error"), False, False, 0)
        box.pack_start(note(_("Nothing has been changed on the drive. This is what a backup would do now."), "info"),
                       False, False, 0)
        for result in record.jobs:
            plan = outcome.plans.get(result.job_id)
            frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
            title.set_markup(f"<b>{GLib.markup_escape_text(result.source)}  →  {GLib.markup_escape_text(result.dest)}</b>")
            frame.pack_start(title, False, False, 0)
            if result.status in (journal.SKIPPED, journal.ERROR, journal.CANCELLED):
                frame.pack_start(note(reason_text(result.reason, result.message), "error"), False, False, 0)
                if result.skipped_files:
                    frame.pack_start(_expander(_("Details"), result.skipped_files, result.skipped_count),
                                     False, False, 0)
            elif plan is not None:
                summary = _("To copy: %d new, %d changed (%s) · to delete or move to version history: %d · skipped: %d") % (
                    len(plan.new_files), len(plan.changed_files), GLib.format_size(plan.transfer_bytes),
                    len(plan.deleted_files), len(plan.skipped))
                frame.pack_start(Gtk.Label(label=summary, xalign=0, wrap=True), False, False, 0)
                if plan.free_bytes is not None:
                    enough = plan.free_bytes >= plan.transfer_bytes
                    frame.pack_start(note(_("Free on the drive: %s") % GLib.format_size(plan.free_bytes),
                                          "info" if enough else "error"), False, False, 0)
                if plan.new_files:
                    frame.pack_start(_expander(_("New files (%d)") % len(plan.new_files), plan.new_files),
                                     False, False, 0)
                if plan.changed_files:
                    frame.pack_start(_expander(_("Changed files (%d)") % len(plan.changed_files),
                                               plan.changed_files), False, False, 0)
                if plan.deleted_files:
                    frame.pack_start(_expander(_("To delete or move to version history (%d)") % len(plan.deleted_files),
                                               plan.deleted_files), False, False, 0)
                if plan.skipped:
                    frame.pack_start(_expander(_("Skipped by drive limitations (%d)") % len(plan.skipped),
                                               plan.skipped), False, False, 0)
            box.pack_start(frame, False, False, 0)
            box.pack_start(Gtk.Separator(), False, False, 0)
        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.add(box)
        self.get_content_area().pack_start(scrolled, True, True, 0)
        self.show_all()


# ---------- итог запуска ----------

def record_widget(record: RunRecord) -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    head = Gtk.Box(spacing=10)
    head.pack_start(status_image(record.status, Gtk.IconSize.DIALOG), False, False, 0)
    title = Gtk.Label(xalign=0, wrap=True)
    title.set_markup("<span size='large' weight='bold'>%s</span>\n%s" % (
        GLib.markup_escape_text(record_status_text(record).capitalize()),
        GLib.markup_escape_text(_("%s · %s · started %s") % (record.profile_name, trigger_text(record.trigger),
                                                              _fmt_time(record.started)))))
    head.pack_start(title, True, True, 0)
    box.pack_start(head, False, False, 0)
    if record.reason:
        kind = "info" if record.reason == "no-changes" else "error"
        box.pack_start(note(reason_text(record.reason, record.message), kind), False, False, 0)
    restoring = record.trigger == "restore"
    for result in record.jobs:
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        line = Gtk.Box(spacing=8)
        line.pack_start(status_image(result.status, Gtk.IconSize.BUTTON), False, False, 0)
        if restoring:
            # source — папка копии на носителе, dest — папка на компьютере
            text = _("“%s”: from the copy → %s") % (os.path.basename(result.source.rstrip("/")), result.dest)
        else:
            text = f"{result.source}  →  {result.dest}"
        label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE, label=text)
        label.set_tooltip_text(_("Copy on the drive: %s") % result.source if restoring else None)
        line.pack_start(label, True, True, 0)
        frame.pack_start(line, False, False, 0)
        if restoring and (result.new_files or result.replaced_files or not result.copied_files):
            stats = _("%s · appeared: %d · replaced: %d") % (
                status_text(result.status), result.new_files, result.replaced_files)
        elif restoring:
            stats = _("%s · restored files: %d") % (status_text(result.status), result.copied_files)
        else:
            stats = _("%s · copied: %d (%s) · deleted or moved to version history: %d · skipped: %d") % (
                status_text(result.status), result.copied_files, GLib.format_size(result.copied_bytes),
                result.deleted_files, result.skipped_count)
        stats_label = Gtk.Label(label=stats, xalign=0, wrap=True, margin_start=28)
        stats_label.get_style_context().add_class("dim-label")
        frame.pack_start(stats_label, False, False, 0)
        if restoring and result.message and os.path.isdir(result.message):
            kept = Gtk.Box(spacing=8, margin_start=28)
            kept.pack_start(note(_("The replaced files were saved to %s") % result.message, "info"), True, True, 0)
            open_kept = Gtk.Button(label=_("Open"), valign=Gtk.Align.CENTER)
            open_kept.connect("clicked", lambda _b, path=result.message: Gio.AppInfo.launch_default_for_uri(
                Gio.File.new_for_path(path).get_uri(), None))
            kept.pack_start(open_kept, False, False, 0)
            frame.pack_start(kept, False, False, 0)
        if result.reason:
            reason = note(reason_text(result.reason, result.message),
                          "warning" if result.status == journal.WARNINGS else "error")
            reason.set_margin_start(28)
            frame.pack_start(reason, False, False, 0)
        if result.skipped_files:
            exp = _expander(_("Skipped (%d)") % result.skipped_count, result.skipped_files, result.skipped_count)
            exp.set_margin_start(28)
            frame.pack_start(exp, False, False, 0)
        if result.errors:
            exp = _expander(_("Errors (%d)") % len(result.errors), result.errors)
            exp.set_margin_start(28)
            frame.pack_start(exp, False, False, 0)
        box.pack_start(frame, False, False, 0)
    if record.versions_removed:
        box.pack_start(note(_("Old version history folders removed: %d") % len(record.versions_removed), "info"),
                       False, False, 0)
    if os.path.exists(record.log_path):
        button = Gtk.Button(label=_("Open the full rsync log"), halign=Gtk.Align.START)
        button.connect("clicked", lambda _b: Gio.AppInfo.launch_default_for_uri(
            Gio.File.new_for_path(record.log_path).get_uri(), None))
        box.pack_start(button, False, False, 0)
    return box


class SummaryDialog(Gtk.Dialog):
    def __init__(self, parent: Gtk.Window, record: RunRecord):
        super().__init__(title=_("Backup result"), transient_for=parent, modal=True)
        self.set_default_size(680, -1)
        self.add_button(_("Close"), Gtk.ResponseType.CLOSE)
        scrolled = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=560)
        content = record_widget(record)
        content.set_border_width(14)
        scrolled.add(content)
        self.get_content_area().pack_start(scrolled, True, True, 0)
        self.show_all()


class JournalDialog(Gtk.Dialog):
    def __init__(self, parent: Gtk.Window, profile: Profile):
        super().__init__(title=_("Log — %s") % profile.name, transient_for=parent, modal=True)
        self.set_default_size(1080, 680)
        self.add_button(_("Close"), Gtk.ResponseType.CLOSE)
        runs = journal.read_runs(profile.id)
        from gi.repository import GdkPixbuf
        store = Gtk.ListStore(str, str, str, GdkPixbuf.Pixbuf, int)
        for i, run in enumerate(runs):
            store.append([_fmt_time(run.started), trigger_text(run.trigger), record_status_text(run),
                          status_pixbuf(run.status), i])
        view = Gtk.TreeView(model=store, headers_visible=True)
        icon = Gtk.TreeViewColumn("", Gtk.CellRendererPixbuf(), pixbuf=3)
        view.append_column(icon)
        for col, title in ((0, _("Started")), (1, _("How")), (2, _("Result"))):
            view.append_column(Gtk.TreeViewColumn(title, Gtk.CellRendererText(), text=col))
        left = Gtk.ScrolledWindow(min_content_width=300)
        left.add(view)
        self.details = Gtk.ScrolledWindow(hexpand=True)
        placeholder = Gtk.Label(label=_("No runs yet.") if not runs else _("Choose a run."))
        placeholder.get_style_context().add_class("dim-label")
        self.details.add(placeholder)
        # Разделитель подвижный: обе части можно сужать и расширять мышью.
        paned = Gtk.Paned(position=470, wide_handle=True)
        paned.pack1(left, True, False)
        paned.pack2(self.details, True, False)
        self.get_content_area().pack_start(paned, True, True, 0)

        def on_changed(selection):
            model, it = selection.get_selected()
            if it is None:
                return
            for child in self.details.get_children():
                self.details.remove(child)
            widget = record_widget(runs[model[it][4]])
            widget.set_border_width(12)
            self.details.add(widget)
            self.details.show_all()

        view.get_selection().connect("changed", on_changed)
        self.show_all()
        if runs:
            view.get_selection().select_path(Gtk.TreePath.new_first())
