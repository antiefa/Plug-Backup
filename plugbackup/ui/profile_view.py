"""Правая часть окна: страница профиля и страница нового носителя."""

import os
import threading
import time

from gi.repository import Gio, GLib, Gtk, Pango

from plugbackup.core import binding, devices, gocrypt, marker, secrets
from plugbackup.core import profiles as P
from plugbackup.core.binding import BindingError
from plugbackup.i18n import _
from plugbackup.ui.common import MODE_TITLES, binding_error_text, note
from plugbackup.ui.profile_actions import ProfileActions, drive_text, find_profile_copies, find_vaults
from plugbackup.ui.volumes_view import VolumeDetails


def _title(text: str) -> Gtk.Label:
    label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
    label.set_markup(f"<span size='x-large' weight='bold'>{GLib.markup_escape_text(text)}</span>")
    return label


def _icon_button(icon: str, tooltip: str, callback, css_class: str | None = None) -> Gtk.Button:
    button = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
    button.set_tooltip_text(tooltip)
    button.set_relief(Gtk.ReliefStyle.NONE)
    button.connect("clicked", lambda _b: callback())
    if css_class:
        button.get_style_context().add_class(css_class)
    return button


def _note_with_button(text: str, kind: str, button_label: str, callback) -> Gtk.Widget:
    box = Gtk.Box(spacing=12)
    box.pack_start(note(text, kind), True, True, 0)
    button = Gtk.Button(label=button_label, valign=Gtk.Align.CENTER)
    button.connect("clicked", lambda _b: callback())
    box.pack_start(button, False, False, 0)
    return box


def _clear(container: Gtk.Container) -> None:
    for child in container.get_children():
        container.remove(child)


def job_subtitle(job: P.Job) -> str:
    parts = [MODE_TITLES[job.mode]()]
    if not job.in_auto:
        parts.append(_("manual only"))
    if not job.enabled:
        parts.append(_("disabled"))
    return " · ".join(parts)


def auto_badge(profile_auto_enabled: bool) -> Gtk.Widget:
    """Метка «АВТО»: задание участвует в автоматическом резервировании (бледная, если в профиле оно выключено)."""
    icon = Gtk.Image.new_from_icon_name("emblem-synchronizing-symbolic", Gtk.IconSize.MENU)
    label = Gtk.Label(label=_("AUTO"))
    badge = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER, halign=Gtk.Align.START)
    badge.pack_start(icon, False, False, 0)
    badge.pack_start(label, False, False, 0)
    badge.get_style_context().add_class("pb-auto-badge")
    if profile_auto_enabled:
        badge.set_tooltip_text(_("The job runs in automatic backup when the drive is inserted"))
    else:
        badge.get_style_context().add_class("off")
        badge.set_tooltip_text(_("The job is marked for automatic backup, but automatic backup is turned off "
                                 "in the profile settings"))
    return badge


def auto_status_text(profile: P.Profile) -> str:
    auto = profile.auto
    if not auto.enabled:
        return _("Automatic backup: off")
    parts = [_("Automatic backup: on")]
    parts.append(_("countdown %d s") % auto.countdown_sec if auto.countdown_sec else _("starts immediately"))
    if auto.min_interval_hours:
        parts.append(_("not more often than every %d h") % auto.min_interval_hours)
    if auto.eject_after_success:
        parts.append(_("ejects after success"))
    return " · ".join(parts)


class JobRow(Gtk.ListBoxRow):
    def __init__(self, profile: P.Profile, job: P.Job, actions: ProfileActions, first: bool, last: bool,
                 can_run: bool, on_run):
        super().__init__(activatable=True, selectable=False)
        self.profile, self.job, self.actions = profile, job, actions

        enabled = Gtk.CheckButton(active=job.enabled, valign=Gtk.Align.CENTER)
        enabled.set_tooltip_text(_("Job is enabled"))
        enabled.connect("toggled", lambda b: actions.toggle_job(profile, job, b.get_active()))

        title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE,
                          label=f"{job.source}  →  {P.normalize_dest(job.dest)}")
        subtitle = Gtk.Label(xalign=0, label=job_subtitle(job), ellipsize=Pango.EllipsizeMode.END)
        subtitle.get_style_context().add_class("dim-label")
        subtitle_row = Gtk.Box(spacing=8)
        subtitle_row.pack_start(subtitle, False, False, 0)
        if job.in_auto:
            subtitle_row.pack_start(auto_badge(profile.auto.enabled), False, False, 0)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        labels.pack_start(title, False, False, 0)
        labels.pack_start(subtitle_row, False, False, 0)
        if not job.enabled:
            labels.set_sensitive(False)

        box = Gtk.Box(spacing=4, margin=6)
        box.pack_start(enabled, False, False, 0)
        box.pack_start(labels, True, True, 4)
        run = _icon_button("media-playback-start-symbolic", _("Back up only this job"), lambda: on_run(job), "pb-run")
        run.set_sensitive(can_run)
        up = _icon_button("go-up-symbolic", _("Move up"), lambda: actions.move_job(profile, job, -1), "pb-move")
        down = _icon_button("go-down-symbolic", _("Move down"), lambda: actions.move_job(profile, job, 1), "pb-move")
        up.set_sensitive(not first)
        down.set_sensitive(not last)
        edit = _icon_button("document-edit-symbolic", _("Edit job"), lambda: actions.edit_job(profile, job), "pb-edit")
        remove = _icon_button("user-trash-symbolic", _("Remove job"), lambda: actions.remove_job(profile, job),
                              "pb-danger")

        group = Gio.SimpleActionGroup()
        for name, handler, needs_drive in (
                ("toggle-auto", lambda: actions.set_job_auto(profile, job, not job.in_auto), False),
                ("restore", lambda: actions.restore(profile, [job.id]), True),
                ("open-copy", lambda: actions.open_copy(profile, job), True),
                ("remove-excluded", lambda: actions.remove_excluded(profile, job), True),
                ("clear-copy", lambda: actions.clear_job_copy(profile, job), True)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, h=handler: h())
            action.set_enabled(can_run or not needs_drive)
            group.add_action(action)
        self.insert_action_group("job", group)
        menu = Gio.Menu()
        auto_section = Gio.Menu()
        auto_section.append(_("Turn off automatic") if job.in_auto else _("Turn on automatic"), "job.toggle-auto")
        menu.append_section(None, auto_section)
        restore_section = Gio.Menu()
        restore_section.append(_("Restore this job…"), "job.restore")
        restore_section.append(_("Open the job’s copy"), "job.open-copy")
        menu.append_section(None, restore_section)
        copy_section = Gio.Menu()
        copy_section.append(_("Remove excluded files from the copy…"), "job.remove-excluded")
        copy_section.append(_("Clear this job’s copy…"), "job.clear-copy")
        menu.append_section(None, copy_section)
        more = Gtk.MenuButton(menu_model=menu, relief=Gtk.ReliefStyle.NONE)
        more.set_image(Gtk.Image.new_from_icon_name("view-more-symbolic", Gtk.IconSize.BUTTON))
        more.set_tooltip_text(_("More actions"))
        more.get_style_context().add_class("pb-move")
        for widget in (run, up, down, edit, remove, more):
            box.pack_start(widget, False, False, 0)
        self.add(box)


def _card(title: str) -> tuple[Gtk.Box, Gtk.Box]:
    """Рамка-раздел: (рамка, строка заголовка — справа в неё можно добавить кнопки)."""
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    card.get_style_context().add_class("pb-card")
    head = Gtk.Box(spacing=6)
    label = Gtk.Label(xalign=0)
    label.set_markup(f"<b>{GLib.markup_escape_text(title)}</b>")
    head.pack_start(label, True, True, 0)
    card.pack_start(head, False, False, 0)
    return card, head


class ProfilePage(Gtk.Box):
    def __init__(self, actions: ProfileActions, monitor):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=18)
        self.actions = actions
        self.monitor = monitor
        self.runs = actions.window.runs
        self.profile: P.Profile | None = None
        self.volume = None
        self.conflicts: set[str] = set()
        self.ready_reason = ""
        self.keyring_state: str | None = None     # состояние связки ключей (для LUKS-профилей с паролем)
        self._keyring_checking = False
        self._keyring_checked_at = 0.0

        # ----- шапка: имя и меню профиля -----
        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.menu_actions = Gio.SimpleActionGroup()
        for name, handler in (
                ("bind", self._on_bind),
                ("write-marker", lambda: actions.write_marker(self.profile)),
                ("open-copy", lambda: actions.open_copy(self.profile)),
                ("clear-versions", lambda: actions.clear_versions(self.profile)),
                ("clone", lambda: actions.clone(self.profile)),
                ("export", lambda: actions.export(self.profile)),
                ("delete", lambda: actions.delete(self.profile))):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, h=handler: self.profile and h())
            self.menu_actions.add_action(action)
        self.insert_action_group("profile", self.menu_actions)
        menu = Gio.Menu()
        drive_section = Gio.Menu()
        drive_section.append(_("Open the copy on the drive"), "profile.open-copy")
        drive_section.append(_("Bind to another drive…"), "profile.bind")
        drive_section.append(_("Write the marker to the drive"), "profile.write-marker")
        drive_section.append(_("Clear the version history…"), "profile.clear-versions")
        menu.append_section(None, drive_section)
        profile_section = Gio.Menu()
        profile_section.append(_("Create the same profile for another drive"), "profile.clone")
        profile_section.append(_("Export…"), "profile.export")
        profile_section.append(_("Delete profile…"), "profile.delete")
        menu.append_section(None, profile_section)
        more = Gtk.MenuButton(menu_model=menu)
        more.set_image(Gtk.Image.new_from_icon_name("view-more-symbolic", Gtk.IconSize.BUTTON))
        more.set_tooltip_text(_("More actions"))
        header = Gtk.Box(spacing=6)
        header.pack_start(self.title, True, True, 0)
        header.pack_end(more, False, False, 0)

        # ----- строка носителя и всплывающие сведения -----
        self.drive_icon = Gtk.Image.new_from_icon_name("drive-removable-media-symbolic", Gtk.IconSize.BUTTON)
        self.drive_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.drive_label.get_style_context().add_class("dim-label")
        self.volume_details = VolumeDetails(monitor, actions.window.show_error, compact=True,
                                            is_busy=lambda v: self.profile is not None
                                            and self.runs.is_running(self.profile.id))
        popover = Gtk.Popover()
        popover.add(self.volume_details)
        self.info_button = Gtk.MenuButton(popover=popover, relief=Gtk.ReliefStyle.NONE, valign=Gtk.Align.CENTER)
        self.info_button.set_image(Gtk.Image.new_from_icon_name("plugbackup-info-symbolic", Gtk.IconSize.BUTTON))
        self.info_button.set_tooltip_text(_("Drive details"))
        self.info_button.get_style_context().add_class("pb-info")
        self.info_button.connect("toggled", self._on_info_toggled)
        drive_row = Gtk.Box(spacing=6)
        drive_row.pack_start(self.drive_icon, False, False, 0)
        drive_row.pack_start(self.drive_label, False, False, 0)
        drive_row.pack_start(self.info_button, False, False, 0)

        self.banners = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        # ----- раздел «Резервирование» -----
        backup_card, _head = _card(_("Backup"))
        self.backup_button = Gtk.Button()
        content = Gtk.Box(spacing=6)
        content.pack_start(Gtk.Image.new_from_icon_name("media-playback-start-symbolic", Gtk.IconSize.BUTTON),
                           False, False, 0)
        content.pack_start(Gtk.Label(label=_("Back up")), False, False, 0)
        self.backup_button.add(content)
        self.backup_button.get_style_context().add_class("suggested-action")
        self.backup_button.connect("clicked", lambda _b: self._start())
        self.preview_button = Gtk.Button(label=_("Preview"))
        self.preview_button.connect("clicked", lambda _b: self._start(preview=True))
        journal_button = Gtk.Button(label=_("Log…"))
        journal_button.connect("clicked", lambda _b: self.profile and self._show_journal())
        self.settings_button = Gtk.Button(label=_("Profile settings…"))
        self.settings_button.connect("clicked", lambda _b: self.profile and actions.edit(self.profile))
        buttons = Gtk.Box(spacing=6)
        buttons.pack_start(self.backup_button, False, False, 0)
        buttons.pack_start(self.preview_button, False, False, 0)
        self.restore_button = Gtk.Button(label=_("Restore…"))
        self.restore_button.connect("clicked", lambda _b: self.profile and actions.restore(self.profile))
        buttons.pack_start(self.restore_button, False, False, 0)
        buttons.pack_end(self.settings_button, False, False, 0)
        buttons.pack_end(journal_button, False, False, 0)
        backup_card.pack_start(buttons, False, False, 0)

        self.status_label = Gtk.Label(xalign=0, wrap=True)
        self.status_label.get_style_context().add_class("dim-label")
        self.auto_on_button = Gtk.Button(label=_("Turn on automatic backup"), relief=Gtk.ReliefStyle.NONE,
                                         no_show_all=True)
        self.auto_on_button.connect("clicked", lambda _b: self.profile
                                    and actions.set_auto_enabled(self.profile, True))
        status_row = Gtk.Box(spacing=8)
        status_row.pack_start(self.status_label, False, False, 0)
        status_row.pack_start(self.auto_on_button, False, False, 0)
        backup_card.pack_start(status_row, False, False, 0)

        self.run_title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        self.run_bar = Gtk.ProgressBar(show_text=True)
        self.run_detail = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        self.run_detail.get_style_context().add_class("dim-label")
        stop = Gtk.Button(label=_("Stop"), valign=Gtk.Align.CENTER)
        stop.get_style_context().add_class("destructive-action")
        stop.connect("clicked", lambda _b: self.profile and self.runs.stop(self.profile.id))
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        texts.pack_start(self.run_title, False, False, 0)
        texts.pack_start(self.run_bar, False, False, 0)
        texts.pack_start(self.run_detail, False, False, 0)
        self.run_panel = Gtk.Box(spacing=12, no_show_all=True)
        self.run_panel.pack_start(texts, True, True, 0)
        self.run_panel.pack_start(stop, False, False, 0)
        for child in (texts, self.run_title, self.run_bar, self.run_detail, stop):
            child.show()
        backup_card.pack_start(self.run_panel, False, False, 0)

        # ----- раздел «Задания» -----
        jobs_card, jobs_head = _card("")
        self.jobs_title = jobs_head.get_children()[0]
        self.add_button = Gtk.Button(label=_("+ Add job"))
        self.add_button.connect("clicked", lambda _b: self.profile and actions.add_job(self.profile))
        jobs_head.pack_end(self.add_button, False, False, 0)
        self.jobs = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.jobs.connect("row-activated", lambda _l, row: actions.edit_job(row.profile, row.job))
        placeholder = Gtk.Label(label=_("No jobs yet — add the first folder to back up."), margin=18)
        placeholder.get_style_context().add_class("dim-label")
        placeholder.show()
        self.jobs.set_placeholder(placeholder)
        jobs_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, min_content_height=150)
        jobs_scroll.add(self.jobs)
        jobs_frame = Gtk.Frame()
        jobs_frame.add(jobs_scroll)
        jobs_card.pack_start(jobs_frame, True, True, 0)

        self.pack_start(header, False, False, 0)
        self.pack_start(drive_row, False, False, 0)
        self.pack_start(self.banners, False, False, 0)
        self.pack_start(backup_card, False, False, 0)
        self.pack_start(jobs_card, True, True, 0)

    # ----- действия -----

    def _on_bind(self):
        choose_volume_and_bind(self.actions, self.profile, self.monitor)

    def _on_info_toggled(self, button):
        if button.get_active() and self.volume is not None:
            self.volume_details.show_volume(self.volume, False)

    def _start(self, preview: bool = False, job_ids=None):
        if self.profile is None or self.volume is None:
            return
        self.runs.start(self.profile, self.volume, job_ids=job_ids, preview=preview)

    def _show_journal(self):
        from plugbackup.ui.run_dialogs import JournalDialog
        dialog = JournalDialog(self.actions.window, self.profile)
        dialog.run()
        dialog.destroy()

    # ----- состояние -----

    def update_run_state(self) -> None:
        running = self.profile is not None and self.runs.is_running(self.profile.id)
        ready = self.volume is not None and not self.ready_reason
        self.backup_button.set_sensitive(ready and not running)
        self.preview_button.set_sensitive(ready and not running)
        tip = self.ready_reason or ("" if self.volume is not None else _("Insert the profile’s drive first."))
        self.backup_button.set_tooltip_text(tip or None)
        self.preview_button.set_tooltip_text(tip or None)
        self.settings_button.set_sensitive(not running)
        self.add_button.set_sensitive(not running)
        self.jobs.set_sensitive(not running)
        self.menu_actions.lookup_action("clear-versions").set_enabled(ready and not running)
        self.menu_actions.lookup_action("open-copy").set_enabled(ready)
        self.restore_button.set_sensitive(ready and not running)
        self.restore_button.set_tooltip_text(tip or None)
        self.run_panel.set_visible(running)
        if running:
            self.update_progress(self.runs.progress.get(self.profile.id, {}))
        self._update_status()

    def _update_status(self) -> None:
        from plugbackup.core import journal
        from plugbackup.ui.run_texts import status_text
        record = journal.last_run(self.profile.id) if self.profile else None
        if record is None:
            last = _("No backups yet")
        else:
            when = GLib.DateTime.new_from_unix_local(int(record.started)).format("%d.%m.%Y %H:%M")
            last = _("Last backup: %s — %s") % (when, status_text(record.status))
        auto = auto_status_text(self.profile) if self.profile else ""
        self.status_label.set_text(f"{last}   ·   ⟳ {auto}")
        self.auto_on_button.set_visible(self.profile is not None and not self.profile.auto.enabled)

    def update_progress(self, info: dict) -> None:
        job = info.get("job")
        count = max(1, info.get("job_count", 1))
        index = info.get("job_index", 0)
        phase = info.get("phase", "prepare")
        preview = info.get("preview", False)
        if job is not None:
            self.run_title.set_text(_("Job %d of %d: %s → %s") % (index + 1, count, job.source,
                                                                   P.normalize_dest(job.dest)))
        else:
            self.run_title.set_text(_("Preparing…"))
        if phase == "copy":
            percent = info.get("percent", 0)
            self.run_bar.set_fraction(min(1.0, (index + percent / 100) / count))
            self.run_bar.set_text(f"{percent} %")
            self.run_detail.set_text(_("%s · %s left · %s") % (info.get("speed", ""), info.get("eta", ""),
                                                              info.get("file", "")))
        else:
            self.run_bar.set_fraction(index / count)
            phase_text = {"scan": _("Reading the source folder…"), "plan": _("Comparing with the copy…")}.get(
                phase, _("Checking the drive…"))
            self.run_bar.set_text(_("Preview") if preview else "")
            self.run_detail.set_text(phase_text)

    def _check_keyring(self) -> None:
        """Состояние связки ключей узнаётся в фоне; если оно изменилось — страница перерисовывается."""
        if self._keyring_checking or time.monotonic() - self._keyring_checked_at < 10:
            return
        self._keyring_checking = True
        self._keyring_checked_at = time.monotonic()

        def work():
            try:
                state = secrets.service_state()
            except Exception:
                state = secrets.UNAVAILABLE
            GLib.idle_add(done, state)

        def done(state):
            self._keyring_checking = False
            changed = state != self.keyring_state
            self.keyring_state = state
            if changed and self.profile is not None:
                self.show_profile(self.profile, self.volume, self.conflicts)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, daemon=True).start()

    def show_profile(self, profile: P.Profile, volume, conflicts: set[str]) -> None:
        self.profile = profile
        self.volume = volume
        self.conflicts = conflicts
        if (P.uses_luks(profile) or profile.encryption.mode == "gocryptfs") and profile.encryption.remember_password:
            self._check_keyring()
        _clear(self.banners)
        _clear(self.jobs)

        self.title.set_markup(f"<span size='x-large' weight='bold'>{GLib.markup_escape_text(profile.name)}</span>")
        if not profile.bound:
            status = _("Not bound to a drive")
        elif volume is None:
            status = _("Drive not connected · %s") % (profile.device.label or profile.device.model or profile.device.uuid)
        else:
            space = devices.free_space(volume.mount_point) if volume.mounted else None
            status = drive_text(volume)
            if space is not None:
                status += " · " + _("%s free") % GLib.format_size(space[0])
        self.drive_label.set_text(status)

        self.ready_reason = ""
        for widget in self._banners(profile, volume, conflicts):
            self.banners.pack_start(widget, False, False, 0)

        self.jobs_title.set_markup("<b>%s</b>" % GLib.markup_escape_text(_("Jobs (%d)") % len(profile.jobs)))
        can_run = volume is not None and not self.ready_reason
        for i, job in enumerate(profile.jobs):
            self.jobs.add(JobRow(profile, job, self.actions, i == 0, i == len(profile.jobs) - 1, can_run,
                                 lambda j: self._start(job_ids=[j.id])))

        self.menu_actions.lookup_action("write-marker").set_enabled(volume is not None and volume.mounted)
        self.show_all()
        self.info_button.set_visible(volume is not None)
        if volume is not None and self.info_button.get_active():
            self.volume_details.show_volume(volume, volume.uuid in conflicts)
        self.update_run_state()

    def _vault_banners(self, profile: P.Profile, volume) -> tuple[list[Gtk.Widget], bool]:
        """Баннеры зашифрованной папки и можно ли проверять метку (только у открытой папки)."""
        return _vault_banners_impl(self, profile, volume)

    def _banners(self, profile: P.Profile, volume, conflicts: set[str]) -> list[Gtk.Widget]:
        items: list[Gtk.Widget] = []
        if not profile.bound:
            items.append(_note_with_button(_("This profile is not bound to a drive yet."), "info",
                                           _("Bind to a drive…"), self._on_bind))
            return items
        if volume is None:
            items.append(note(_("Insert the profile’s drive to back up or restore. Settings and jobs can be "
                                "edited now."), "info"))
            if profile.device_sync_pending:
                items.append(note(_("The marker on the drive will need updating after the drive is connected."),
                                  "warning"))
            return items
        if volume.uuid in conflicts:
            self.ready_reason = _("Another connected drive has the same identifier (UUID). Disconnect one of them.")
            items.append(note(self.ready_reason, "error"))
            return items
        if (P.uses_luks(profile) or profile.encryption.mode == "gocryptfs") and profile.encryption.remember_password \
                and self.keyring_state in (secrets.LOCKED, secrets.UNAVAILABLE):
            text = {secrets.LOCKED: _("The keyring is locked: you will be asked for the password in a window, "
                                      "including for automatic backup."),
                    secrets.UNAVAILABLE: _("The keyring service is unavailable: the password cannot be saved, "
                                           "so you will be asked for it in a window.")}[self.keyring_state]
            items.append(note(text, "warning"))
        if volume.locked:
            self.ready_reason = _("The drive is encrypted and locked.")
            items.append(_note_with_button(_("The drive is encrypted (LUKS) and locked. Unlock it to back up or "
                                             "restore."), "info", _("Unlock…"),
                                           lambda: self.actions.unlock(profile, volume)))
            return items
        if not volume.mounted:
            self.ready_reason = _("The drive is connected but not mounted.")
            if volume.filesystem_path:
                items.append(_note_with_button(self.ready_reason, "info", _("Mount"),
                                               lambda: self.actions.prepare_volume(profile, volume, lambda v: None)))
            else:
                items.append(note(self.ready_reason, "info"))
            return items
        if profile.encryption.mode == "gocryptfs":
            vault_items, show_state = self._vault_banners(profile, volume)
            items += vault_items
            if not show_state:
                return items
        try:
            state = binding.inspect(profile, volume)
        except BindingError as exc:
            self.ready_reason = binding_error_text(exc.code)
            items.append(note(self.ready_reason, "error"))
            return items
        except OSError as exc:
            self.ready_reason = str(exc)
            items.append(note(self.ready_reason, "error"))
            return items
        write = lambda: self.actions.write_marker(profile)  # noqa: E731
        if state == marker.ROOT_FOREIGN_DATA:
            items.append(_note_with_button(binding_error_text(state), "warning",
                                           _("Accept as this profile’s copy…"), write))
        elif state in (marker.ROOT_OTHER_PROFILE, marker.ROOT_CORRUPT):
            self.ready_reason = binding_error_text(state)
            items.append(note(binding_error_text(state) + " " +
                              _("Change the backup folder name in the profile settings or import that profile."),
                              "error"))
        elif state in (marker.ROOT_MISSING, marker.ROOT_EMPTY):
            items.append(_note_with_button(_("The profile marker has not been written to this drive yet."),
                                           "warning", _("Write marker"), write))
        elif profile.device_sync_pending:
            items.append(_note_with_button(_("The marker on the drive is out of date after profile changes."),
                                           "warning", _("Update marker"), write))
        return items


def _vault_banners_impl(page, profile: P.Profile, volume) -> tuple[list[Gtk.Widget], bool]:
    from plugbackup.ui.encryption import availability_text

    ok, reason = gocrypt.availability_cached()
    if not ok:
        page.ready_reason = availability_text(reason)
        return [note(_("The profile keeps its copy in an encrypted folder, but it cannot be opened: ")
                     + page.ready_reason, "error")], False
    try:
        cipher = binding.cipher_dir_for(profile, volume)
    except BindingError as exc:
        page.ready_reason = binding_error_text(exc.code)
        return [note(page.ready_reason, "error")], False
    if not gocrypt.is_initialized(cipher):
        page.ready_reason = _("The encrypted folder has not been created on this drive yet.")
        return [_note_with_button(page.ready_reason, "warning", _("Create…"),
                                  lambda: page.actions.create_vault(profile))], False
    vaults = page.actions.app.vaults
    if not vaults.is_open(profile, volume):
        return [note(_("The copy is in the encrypted folder “%s”. It is opened with the password when you back up, "
                       "restore or open the copy, and closed afterwards.") % os.path.basename(cipher), "info")], False
    running = page.runs.is_running(profile.id)
    if running:
        return [], True
    return [_note_with_button(_("The encrypted folder is open."), "info", _("Close"),
                              lambda: page.actions.close_vault(profile))], True


class VolumePage(Gtk.Box):
    """Носитель без профиля: сведения, создание профиля, перепривязка, импорт копии профиля."""

    def __init__(self, actions: ProfileActions, monitor):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.actions = actions
        self.monitor = monitor
        self.volume = None
        self.offers = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=18, margin_end=18,
                              margin_top=18)
        self.details = VolumeDetails(monitor, actions.window.show_error)
        self.pack_start(self.offers, False, False, 0)
        self.pack_start(self.details, False, False, 0)

    def show_volume(self, volume, profiles: list[P.Profile], conflicts: set[str]) -> None:
        self.volume = volume
        _clear(self.offers)
        conflict = volume.uuid in conflicts
        if volume.uuid and not conflict and not volume.locked:
            create = Gtk.Button(label=_("Create a profile for this drive…"), halign=Gtk.Align.START)
            create.get_style_context().add_class("suggested-action")
            create.connect("clicked", lambda _b: self.actions.create_from_volume(volume))
            self.offers.pack_start(create, False, False, 0)
            connected = {v.uuid for v in self.monitor.volumes()}
            for profile in P.rebind_candidates(profiles, volume, connected):
                self.offers.pack_start(_note_with_button(
                    _("This looks like the drive of profile “%s”, but it has been reformatted.") % profile.name,
                    "warning", _("Rebind…"), lambda p=profile: self.actions.bind(p, volume)), False, False, 0)
            for root_name, data in find_profile_copies(volume):
                name = (data.get("profile") or {}).get("name") or root_name
                self.offers.pack_start(_note_with_button(
                    _("The drive holds a copy of profile “%s”.") % name, "info", _("Import…"),
                    lambda r=root_name, d=data: self.actions.import_from_device(volume, r, d)), False, False, 0)
            for root_name in find_vaults(volume):
                self.offers.pack_start(_note_with_button(
                    _("The drive holds an encrypted Plug & Backup folder “%s.enc”.") % root_name, "info",
                    _("Import…"), lambda r=root_name: self.actions.import_from_vault(volume, r)), False, False, 0)
        self.details.show_volume(volume, conflict)
        self.show_all()


def choose_volume_and_bind(actions: ProfileActions, profile: P.Profile, monitor) -> None:
    profiles = actions.store.profiles
    bound = {p.device.uuid for p in profiles if p.bound}
    volumes = [v for v in monitor.volumes() if v.uuid and v.uuid not in bound and not v.locked]
    if not volumes:
        from plugbackup.ui.common import message
        message(actions.window, _("No available drive"),
                _("Insert a drive that is not bound to any profile yet."))
        return
    dialog = Gtk.Dialog(title=_("Bind to a drive"), transient_for=actions.window, modal=True)
    dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
    ok = dialog.add_button(_("Bind"), Gtk.ResponseType.OK)
    ok.get_style_context().add_class("suggested-action")
    chooser = Gtk.ComboBoxText()
    for v in volumes:
        chooser.append(v.object_path, drive_text(v))
    chooser.set_active(0)
    area = dialog.get_content_area()
    area.set_border_width(14)
    area.set_spacing(8)
    area.pack_start(Gtk.Label(label=_("Drive for profile “%s”:") % profile.name, xalign=0), False, False, 0)
    area.pack_start(chooser, False, False, 0)
    dialog.show_all()
    response = dialog.run()
    selected = chooser.get_active_id()
    dialog.destroy()
    if response == Gtk.ResponseType.OK and selected:
        volume = monitor.get(selected)
        if volume is not None:
            actions.bind(profile, volume)
