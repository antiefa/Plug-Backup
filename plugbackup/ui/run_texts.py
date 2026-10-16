"""Тексты итогов и причин запуска для интерфейса."""

from plugbackup.core import journal
from plugbackup.i18n import _
from plugbackup.ui.common import binding_error_text

STATUS_ICONS = {
    journal.OK: "emblem-ok-symbolic",
    journal.WARNINGS: "dialog-warning-symbolic",
    journal.ERROR: "dialog-error-symbolic",
    journal.CANCELLED: "process-stop-symbolic",
    journal.SKIPPED: "action-unavailable-symbolic",
}


STATUS_COLORS = {
    journal.OK: "#16a34a",
    journal.WARNINGS: "#d97706",
    journal.ERROR: "#ef4444",
    journal.CANCELLED: "#3b82f6",
    journal.SKIPPED: "#d97706",
}


def status_image(status: str, size) -> "Gtk.Image":
    """Цветная иконка итога (символьная иконка темы + цвет через CSS-класс pb-st-<итог>)."""
    from gi.repository import Gtk
    image = Gtk.Image.new_from_icon_name(STATUS_ICONS.get(status, "dialog-question-symbolic"), size)
    image.get_style_context().add_class(f"pb-st-{status}")
    return image


def status_pixbuf(status: str, size: int = 16):
    """Цветная иконка итога для списков (Gtk.TreeView): перекрашенная символьная иконка."""
    from gi.repository import Gdk, Gtk
    info = Gtk.IconTheme.get_default().lookup_icon(STATUS_ICONS.get(status, "dialog-question-symbolic"), size, 0)
    if info is None:
        return None
    color = Gdk.RGBA()
    color.parse(STATUS_COLORS.get(status, "#8a8a8a"))
    try:
        pixbuf, _was_symbolic = info.load_symbolic(color, None, None, None)
    except Exception:
        return None
    return pixbuf


def status_text(status: str) -> str:
    return {
        journal.OK: _("completed"),
        journal.WARNINGS: _("completed with notes"),
        journal.ERROR: _("error"),
        journal.CANCELLED: _("stopped"),
        journal.SKIPPED: _("skipped"),
    }.get(status, status)


def nothing_changed(record) -> bool:
    """Запуск прошёл успешно, но ничего не скопировал и не удалил (в т. ч. тихая проверка «no-changes»)."""
    if record.reason == "no-changes":
        return True
    return (record.status == journal.OK and not record.reason and bool(record.jobs)
            and all(j.status == journal.OK and not j.copied_files and not j.deleted_files for j in record.jobs))


def record_status_text(record) -> str:
    """Итог запуска для журнала: «изменений нет» отдельно от «выполнено»."""
    return _("no changes") if nothing_changed(record) else status_text(record.status)


def trigger_text(trigger: str) -> str:
    return {"manual": _("manual"), "cli": _("command line"), "auto": _("automatic"),
            "restore": _("restore")}.get(trigger, trigger)


def reason_text(reason: str, message: str = "") -> str:
    texts = {
        "": "",
        "already-running": _("This profile is already being backed up."),
        "no-copy": _("There is no copy of this job on the drive."),
        "no-changes": _("No changes — the copy is up to date (checked automatically, nothing was written)."),
        "no-marker": _("The backup folder contains data without a profile marker, and it was not accepted."),
        "marker-write-failed": _("Could not write the profile marker to the drive."),
        "empty-source": _("The source folder is empty while the copy has files — the copy was left untouched. "
                          "If the folder is empty on purpose, clear the job’s copy manually."),
        "name-collision": _("Some file names differ only in letter case; on this drive they would overwrite "
                            "each other. Rename or exclude them."),
        "job-no-marker": _("The job folder on the drive already has data that is not marked as this job’s copy, "
                           "and it was not accepted."),
        "mass-delete": _("Too many files would be deleted from the copy; the job was skipped."),
        "no-space": _("Not enough free space on the drive."),
        "source-list-failed": _("Could not read the source folder."),
        "plan-failed": _("Could not prepare the copy plan."),
        "max-delete": _("The hard limit on deletions per run was reached; deletions stopped."),
        "partial-transfer": _("Some files could not be copied (see the log)."),
        "files-vanished": _("Some files disappeared while copying."),
        "drive-lost": _("The drive was disconnected or changed during the backup."),
        "user": _("Stopped by the user."),
        "signal": _("The copy process was interrupted."),
        "os-error": _("System error: %s") % message,
        "dest-is-link": _("A folder on the drive is a link to somewhere else; it will not be written to."),
        "dest-not-folder": _("Something that is not a folder blocks the job’s path on the drive."),
        "vault-missing": _("The profile’s encrypted folder has not been created on this drive yet."),
        "vault-closed": _("The encrypted folder is closed (no password)."),
    }
    if reason in texts:
        return texts[reason]
    if reason.startswith("rsync-"):
        return _("rsync finished with error code %s.") % reason[6:]
    if reason.startswith(("source-", "dest-", "max-size", "exclude-")):
        return message or reason
    return binding_error_text(reason)
