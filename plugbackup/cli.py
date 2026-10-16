"""Командная строка без окна: --list и --backup. Ничего не спрашивает у пользователя."""
from __future__ import annotations

import sys

from plugbackup.core import journal
from plugbackup.core.config import ConfigStore
from plugbackup.core.profiles import ProfilesStore
from plugbackup.core.runner import Callbacks, Runner, RunRequest

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NO_DRIVE = 3
EXIT_NEEDS_CONFIRMATION = 4
EXIT_CANCELLED = 5
EXIT_ALREADY_RUNNING = 6


def cmd_version() -> int:
    from plugbackup import APP_NAME, __version__

    print(f"{APP_NAME} {__version__}")
    return EXIT_OK


def cmd_check() -> int:
    from plugbackup.core import deps

    checks = deps.run_checks()
    print(deps.format_report(checks))
    return EXIT_ERROR if deps.missing_required(checks) else EXIT_OK


def _volumes():
    from plugbackup import plat

    return plat.devices.list_volumes(show_all=True)


def _store_or_fail() -> ProfilesStore | None:
    store = ProfilesStore()
    if store.error is not None:
        print(f"plugbackup: profiles file is damaged: {store.error}", file=sys.stderr)
        return None
    return store


def cmd_list() -> int:
    store = _store_or_fail()
    if store is None:
        return EXIT_ERROR
    volumes = _volumes()
    if not store.profiles:
        print("No profiles.")
    for profile in store.profiles:
        matches = [v for v in volumes if profile.bound and v.uuid == profile.device.uuid]
        if not profile.bound:
            drive = "not bound"
        elif not matches:
            drive = "drive not connected"
        elif len(matches) > 1:
            drive = "CONFLICT: several drives with the same UUID"
        elif matches[0].locked:
            drive = "drive locked (encrypted)"
        elif not matches[0].mounted:
            drive = "drive connected, not mounted"
        else:
            drive = f"drive mounted at {matches[0].mount_point}"
        last = journal.last_run(profile.id)
        last_text = f"last: {last.status}" if last else "never backed up"
        jobs = sum(1 for j in profile.jobs if j.enabled)
        print(f"{profile.name}\t{profile.id}\t{drive}\tjobs: {jobs}\t{last_text}")
    return EXIT_OK


class _PrintCallbacks(Callbacks):
    def progress(self, info):
        if info.get("phase") == "prepare" and info.get("job") is not None:
            job = info["job"]
            print(f"[{info['job_index'] + 1}/{info['job_count']}] {job.source} -> {job.dest}", flush=True)


def exit_code(record) -> int:
    if record.reason == "already-running":
        return EXIT_ALREADY_RUNNING
    statuses = {record.status} | {j.status for j in record.jobs}
    if journal.ERROR in statuses:
        return EXIT_ERROR
    if journal.CANCELLED in statuses:
        return EXIT_CANCELLED
    if journal.SKIPPED in statuses:
        return EXIT_NEEDS_CONFIRMATION
    return EXIT_OK


def _open_vault(profile, volume) -> tuple[bool, str]:
    """Зашифрованная папка: открыть паролем из связки ключей, ничего не спрашивая.
    (открыли ли мы её сами, текст ошибки)."""
    if profile.encryption.mode != "gocryptfs":
        return False, ""
    from plugbackup import plat
    try:
        gocrypt = plat.vault
    except NotImplementedError:
        return False, "encrypted folder unavailable on this system"
    secrets = plat.secrets
    from plugbackup.core import binding
    ok, reason = gocrypt.availability()
    if not ok:
        return False, f"encrypted folder unavailable ({reason})"
    try:
        cipher = binding.cipher_dir_for(profile, volume)
    except binding.BindingError as exc:
        return False, exc.code
    plain = gocrypt.plain_dir(profile.id)
    if not gocrypt.is_initialized(cipher):
        return False, "the encrypted folder has not been created on the drive"
    if gocrypt.is_open(cipher, plain):
        return False, ""                 # уже открыта (например, окном приложения) — её и закроет окно
    password = None
    if profile.encryption.remember_password and secrets.service_state() == secrets.OK:
        password = secrets.lookup(profile.id)
    if not password:
        return False, "the encrypted folder password is not saved in the keyring (open the profile in the window)"
    try:
        gocrypt.mount(cipher, plain, password)
    except gocrypt.GocryptError as exc:
        return False, f"could not open the encrypted folder ({exc.code})"
    return True, ""


def _close_vault(profile) -> None:
    from plugbackup import plat
    try:
        gocrypt = plat.vault
    except NotImplementedError:
        return
    try:
        gocrypt.unmount(gocrypt.plain_dir(profile.id))
    except gocrypt.GocryptError as exc:
        print(f"plugbackup: {profile.name}: could not close the encrypted folder ({exc.detail or exc.code})",
              file=sys.stderr)


def cmd_backup(name: str) -> int:
    store = _store_or_fail()
    if store is None:
        return EXIT_ERROR
    wanted = name.strip().casefold()
    profiles = [p for p in store.profiles if p.id == name or p.name.casefold() == wanted]
    if len(profiles) != 1:
        print(f"plugbackup: profile “{name}” not found" if not profiles
              else f"plugbackup: several profiles are called “{name}”; use the profile id", file=sys.stderr)
        return EXIT_NO_DRIVE if not profiles else EXIT_USAGE
    profile = profiles[0]
    matches = [v for v in _volumes() if profile.bound and v.uuid == profile.device.uuid]
    if len(matches) != 1 or matches[0].locked or not matches[0].mounted:
        reason = ("drive not connected" if not matches else
                  "several drives with the same UUID" if len(matches) > 1 else
                  "drive is locked (encrypted)" if matches[0].locked else "drive is not mounted")
        print(f"plugbackup: {profile.name}: {reason}", file=sys.stderr)
        return EXIT_NO_DRIVE
    opened, error = _open_vault(profile, matches[0])
    if error:
        print(f"plugbackup: {profile.name}: {error}", file=sys.stderr)
        return EXIT_NO_DRIVE
    request = RunRequest(profile=profile, volume=matches[0], trigger="cli",
                         global_excludes=list(ConfigStore().config.global_excludes))
    try:
        outcome = Runner(request, _PrintCallbacks()).run()
    finally:
        if opened:
            _close_vault(profile)
    record = outcome.record
    if outcome.marker_written and profile.device_sync_pending:
        profile.device_sync_pending = False
        store.save()
    print(f"{profile.name}: {record.status}" + (f" ({record.reason})" if record.reason else ""))
    for job in record.jobs:
        line = (f"  {job.status:9} {job.source} -> {job.dest}: copied {job.copied_files} "
                f"({job.copied_bytes} bytes), deleted/versioned {job.deleted_files}, skipped {job.skipped_count}")
        if job.reason:
            line += f" [{job.reason}]"
        print(line)
    print(f"log: {record.log_path}")
    return exit_code(record)
