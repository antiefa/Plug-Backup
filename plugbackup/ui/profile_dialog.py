"""Диалог профиля: имя, папка копии на носителе, автоматика, шифрование, хранение версий, пороги."""

import copy
import threading

from gi.repository import GLib, Gtk

from plugbackup.core import profiles as P
from plugbackup.core import secrets
from plugbackup.i18n import _
from plugbackup.ui.common import IssuesBox, note, section_label


def _spin(value: int, lower: int, upper: int, step: int = 1) -> Gtk.SpinButton:
    spin = Gtk.SpinButton.new_with_range(lower, upper, step)
    spin.set_value(value)
    return spin


class ProfileDialog(Gtk.Dialog):
    def __init__(self, parent: Gtk.Window, profile: P.Profile, is_new: bool, drive_text: str,
                 drive_ready: bool = False):
        """drive_ready — носитель профиля подключён и смонтирован: только тогда можно сменить режим шифрования."""
        super().__init__(title=_("New profile") if is_new else _("Profile settings"), transient_for=parent, modal=True)
        self.profile = copy.deepcopy(profile)
        self.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        self.save_button = self.add_button(_("Create") if is_new else _("Save"), Gtk.ResponseType.OK)
        self.save_button.get_style_context().add_class("suggested-action")
        self.set_default_size(560, -1)

        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        row = 0

        def add(label_text: str, widget: Gtk.Widget, hint: str = ""):
            nonlocal row
            grid.attach(Gtk.Label(label=label_text, xalign=0), 0, row, 1, 1)
            widget.set_hexpand(True)
            grid.attach(widget, 1, row, 1, 1)
            row += 1
            if hint:
                label = Gtk.Label(label=hint, xalign=0, wrap=True, max_width_chars=55)
                label.get_style_context().add_class("dim-label")
                grid.attach(label, 1, row, 1, 1)
                row += 1

        drive = Gtk.Label(label=drive_text or _("not bound"), xalign=0, wrap=True, selectable=True)
        add(_("Drive"), drive)
        self.name = Gtk.Entry(text=self.profile.name)
        self.name.connect("changed", lambda _w: self._update())
        add(_("Name"), self.name)
        self.root = Gtk.Entry(text=self.profile.root)
        self.root.connect("changed", lambda _w: self._update())
        add(_("Backup folder on the drive"), self.root,
            _("All jobs and the version history are stored inside this folder; the rest of the drive stays yours."))

        self.store_copy = Gtk.CheckButton(label=_("Keep a copy of this profile on the drive"),
                                          active=self.profile.store_profile_on_device)
        store_hint = Gtk.Label(xalign=0, wrap=True, max_width_chars=65, label=_(
            "Lets you import the profile on a new computer straight from the drive. The copy includes the paths "
            "of your folders, so anyone who reads an unencrypted drive will see them."))
        store_hint.get_style_context().add_class("dim-label")

        self.auto = Gtk.CheckButton(label=_("Back up automatically when the drive is inserted"),
                                    active=self.profile.auto.enabled)
        self.countdown = _spin(self.profile.auto.countdown_sec, 0, 600)
        self.interval = _spin(self.profile.auto.min_interval_hours or 0, 0, 24 * 30)
        self.eject = Gtk.CheckButton(label=_("Eject the drive after a successful automatic backup"),
                                     active=self.profile.auto.eject_after_success)
        auto_grid = Gtk.Grid(column_spacing=12, row_spacing=6, margin_start=24)
        auto_grid.attach(Gtk.Label(label=_("Countdown before start (s)"), xalign=0), 0, 0, 1, 1)
        auto_grid.attach(self.countdown, 1, 0, 1, 1)
        auto_grid.attach(Gtk.Label(label=_("Not more often than every N hours (0 — no limit)"), xalign=0), 0, 1, 1, 1)
        auto_grid.attach(self.interval, 1, 1, 1, 1)
        auto_grid.attach(self.eject, 0, 2, 2, 1)
        self.auto.connect("toggled", lambda _w: auto_grid.set_sensitive(self.auto.get_active()))
        auto_grid.set_sensitive(self.auto.get_active())

        self.keep_days = _spin(self.profile.versions_keep_days, 1, 3650)
        thr = self.profile.mass_delete_threshold
        self.thr_percent = _spin(thr.percent, 1, 100)
        self.thr_files = _spin(thr.files, 1, 1_000_000)
        self.thr_hard = _spin(thr.hard_max, 1, 10_000_000)
        safety = Gtk.Grid(column_spacing=12, row_spacing=6)
        safety.attach(Gtk.Label(label=_("Keep version history (days)"), xalign=0), 0, 0, 1, 1)
        safety.attach(self.keep_days, 1, 0, 1, 1)
        safety.attach(Gtk.Label(label=_("Ask before deleting more than this percentage of files"), xalign=0), 0, 1, 1, 1)
        safety.attach(self.thr_percent, 1, 1, 1, 1)
        safety.attach(Gtk.Label(label=_("…or more than this many files"), xalign=0), 0, 2, 1, 1)
        safety.attach(self.thr_files, 1, 2, 1, 1)
        safety.attach(Gtk.Label(label=_("Never delete more than this many files per run"), xalign=0), 0, 3, 1, 1)
        safety.attach(self.thr_hard, 1, 3, 1, 1)

        self.issues = IssuesBox()

        # Окно стало высоким — содержимое прокручивается, кнопки внизу видны и на небольшом экране.
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=14)
        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True)
        screen = parent.get_screen() if parent is not None else None
        monitor_height = 0
        if screen is not None and parent.get_window() is not None:
            geometry = screen.get_display().get_monitor_at_window(parent.get_window()).get_geometry()
            monitor_height = geometry.height
        scrolled.set_max_content_height(max(400, (monitor_height or 900) - 160))
        scrolled.add(box)
        area = self.get_content_area()
        area.pack_start(scrolled, True, True, 0)
        box.pack_start(grid, False, False, 0)
        box.pack_start(self.store_copy, False, False, 0)
        box.pack_start(store_hint, False, False, 0)
        box.pack_start(section_label(_("Automatic backup")), False, False, 0)
        box.pack_start(self.auto, False, False, 0)
        box.pack_start(auto_grid, False, False, 0)
        box.pack_start(section_label(_("Encryption")), False, False, 0)
        if P.uses_luks(self.profile):
            box.pack_start(self._encryption_box(), False, False, 0)
        else:
            box.pack_start(self._folder_mode_box(drive_ready), False, False, 0)
        box.pack_start(section_label(_("Safety")), False, False, 0)
        box.pack_start(safety, False, False, 0)
        box.pack_start(self.issues, False, False, 0)
        self.show_all()
        self._update()

    def _encryption_box(self, remember_label: str | None = None, hint_text: str | None = None) -> Gtk.Widget:
        self.remember = Gtk.CheckButton(label=remember_label or _("Remember the drive password in the keyring"),
                                        active=self.profile.encryption.remember_password)
        hint = Gtk.Label(xalign=0, wrap=True, max_width_chars=65, label=hint_text or _(
            "The drive is encrypted (LUKS). With a saved password it is unlocked without questions, including for "
            "automatic backup; otherwise you are asked for it in a window. The password is saved the next time "
            "you unlock the drive."))
        hint.get_style_context().add_class("dim-label")
        self.forget_button = Gtk.Button(label=_("Forget password"), halign=Gtk.Align.START)
        self.forget_button.connect("clicked", self._on_forget)
        self.forget_status = Gtk.Label(xalign=0)
        self.forget_status.get_style_context().add_class("dim-label")
        row = Gtk.Box(spacing=10)
        row.pack_start(self.forget_button, False, False, 0)
        row.pack_start(self.forget_status, False, False, 0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.pack_start(self.remember, False, False, 0)
        box.pack_start(hint, False, False, 0)
        box.pack_start(row, False, False, 0)
        return box

    def _folder_mode_box(self, drive_ready: bool) -> Gtk.Widget:
        """Обычный носитель: без шифрования или зашифрованная папка gocryptfs."""
        from plugbackup.core import gocrypt
        from plugbackup.ui.encryption import availability_text, visibility_text

        current = self.profile.encryption.mode
        self.mode_none = Gtk.RadioButton.new_with_label(None, _("No encryption"))
        self.mode_vault = Gtk.RadioButton.new_with_label_from_widget(
            self.mode_none, _("Encrypted folder on the drive (gocryptfs)"))
        self.mode_vault.set_active(current == "gocryptfs")
        hint = Gtk.Label(xalign=0, wrap=True, max_width_chars=65, margin_start=24, label=_(
            "The copy is kept in the encrypted folder “%s.enc”; the rest of the drive stays as it is. ")
            % self.profile.root + visibility_text())
        hint.get_style_context().add_class("dim-label")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.pack_start(self.mode_none, False, False, 0)
        box.pack_start(self.mode_vault, False, False, 0)
        box.pack_start(hint, False, False, 0)

        ok, reason = gocrypt.availability_cached()
        if not ok and current != "gocryptfs":
            self.mode_vault.set_sensitive(False)
            box.pack_start(note(availability_text(reason), "warning"), False, False, 0)
        if not drive_ready:
            self.mode_none.set_sensitive(False)
            self.mode_vault.set_sensitive(False)
            box.pack_start(note(_("The mode can be changed only while the profile’s drive is connected: the copy "
                                  "on it is transferred to the new place."), "info"), False, False, 0)
        elif current == "gocryptfs" or not ok:
            pass
        else:
            box.pack_start(note(_("When you save, Plug & Backup asks for a new password, shows the master key and "
                                  "offers to transfer the existing copy."), "info"), False, False, 0)

        password_box = self._encryption_box(_("Remember the folder password in the keyring"),
                                            _("With a saved password the folder is opened without questions, "
                                              "including for automatic backup; otherwise you are asked for it "
                                              "in a window."))
        password_box.set_margin_start(24)
        box.pack_start(password_box, False, False, 0)
        sync = lambda *_a: password_box.set_sensitive(self.mode_vault.get_active())  # noqa: E731
        self.mode_vault.connect("toggled", sync)
        sync()
        return box

    def _on_forget(self, _button) -> None:
        self.forget_button.set_sensitive(False)
        profile_id = self.profile.id

        def work():
            removed = secrets.clear(profile_id)
            GLib.idle_add(done, removed)

        def done(removed):
            self.forget_status.set_text(_("The password is removed from the keyring.") if removed
                                        else _("No saved password."))
            self.forget_button.set_sensitive(True)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, daemon=True).start()

    def collect(self) -> P.Profile:
        profile = copy.deepcopy(self.profile)
        profile.name = self.name.get_text().strip()
        profile.root = self.root.get_text().strip()
        profile.store_profile_on_device = self.store_copy.get_active()
        profile.auto.enabled = self.auto.get_active()
        profile.auto.countdown_sec = int(self.countdown.get_value())
        interval = int(self.interval.get_value())
        profile.auto.min_interval_hours = interval if interval > 0 else None
        profile.auto.eject_after_success = self.eject.get_active()
        if hasattr(self, "remember"):
            profile.encryption.remember_password = self.remember.get_active()
        if hasattr(self, "mode_vault"):
            profile.encryption.mode = "gocryptfs" if self.mode_vault.get_active() else "none"
        profile.versions_keep_days = int(self.keep_days.get_value())
        profile.mass_delete_threshold = P.Thresholds(
            percent=int(self.thr_percent.get_value()),
            files=int(self.thr_files.get_value()),
            hard_max=int(self.thr_hard.get_value()),
        )
        return profile

    def _update(self):
        issues = P.validate_profile(self.collect())
        self.issues.set_issues(issues)
        self.save_button.set_sensitive(not P.has_errors(issues))

    def run_and_get(self) -> P.Profile | None:
        response = self.run()
        profile = self.collect() if response == Gtk.ResponseType.OK else None
        self.destroy()
        return profile
