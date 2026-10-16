"""Чистые тексты и представления данных для пользовательских интерфейсов."""

from __future__ import annotations

from plugbackup.core import journal, marker
from plugbackup.i18n import _


def binding_error_text(code: str) -> str:
    """Преобразовать код проверки привязки тома в пользовательский текст."""
    texts = {
        "not-this-drive": _("This drive is not bound to the profile."),
        "locked": _("The drive is encrypted and locked."),
        "not-mounted": _("The drive is not mounted."),
        "uuid-mismatch": _("The mounted file system does not match the profile’s drive."),
        "uuid-not-found": _("The mounted file system does not match the profile’s drive."),
        "not-filesystem-root": _("Only a subfolder of the drive is mounted."),
        "read-only": _("The drive is mounted read-only."),
        "not-writable": _("You do not have permission to write to the drive."),
        "root-invalid": _("The backup folder name is invalid."),
        "root-is-link": _("The backup folder on the drive is a link to somewhere else; it will not be used."),
        "root-not-folder": _("Something that is not a folder has the backup folder’s name on the drive."),
        "vault-missing": _("The profile’s encrypted folder has not been created on this drive yet."),
        "vault-closed": _("The profile’s encrypted folder is closed."),
        marker.ROOT_OTHER_PROFILE: _("The backup folder on the drive belongs to another profile."),
        marker.ROOT_CORRUPT: _("The profile marker on the drive is damaged."),
        marker.ROOT_FOREIGN_DATA: _("The backup folder on the drive already contains data without a profile marker."),
    }
    return texts.get(code, _("The drive cannot be used right now (%s).") % code)


def drive_text(volume) -> str:
    """Собрать подпись носителя из полей доменной модели."""
    if volume is None:
        return ""
    model = " ".join(part for part in (volume.drive_vendor, volume.drive_model) if part)
    parts = [volume.display_name]
    if model and model != volume.display_name:
        parts.append(model)
    if volume.encrypted:
        parts.append(_("%s inside LUKS") % volume.fs_type if volume.fs_type else "LUKS")
    else:
        parts.append(volume.fs_type)
    return " · ".join(part for part in parts if part)


def status_text(status: str) -> str:
    """Вернуть переводимый текст кода результата запуска."""
    return {
        journal.OK: _("completed"),
        journal.WARNINGS: _("completed with notes"),
        journal.ERROR: _("error"),
        journal.CANCELLED: _("stopped"),
        journal.SKIPPED: _("skipped"),
    }.get(status, status)


def nothing_changed(record) -> bool:
    """Определить успешный запуск без скопированных и удалённых файлов."""
    if record.reason == "no-changes":
        return True
    return (record.status == journal.OK and not record.reason and bool(record.jobs)
            and all(job.status == journal.OK and not job.copied_files and not job.deleted_files
                    for job in record.jobs))


def record_status_text(record) -> str:
    """Вернуть итог запуска для журнала."""
    return _("no changes") if nothing_changed(record) else status_text(record.status)


def trigger_text(trigger: str) -> str:
    """Вернуть текст источника запуска."""
    return {"manual": _("manual"), "cli": _("command line"), "auto": _("automatic"),
            "restore": _("restore")}.get(trigger, trigger)


def reason_text(reason: str, message: str = "") -> str:
    """Преобразовать код причины запуска в пользовательский текст."""
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
        "timeshift": _("The file times on the drive look shifted by whole hours (usually after changing the "
                       "operating system or time zone); the job was skipped so that unchanged files are not "
                       "copied again. Run the backup manually to fix the times."),
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
