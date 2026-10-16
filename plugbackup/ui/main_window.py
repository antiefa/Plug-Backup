"""Главное окно: профили и носители слева, страница выбранного справа (версия 0.3)."""

from gi.repository import Gio, GLib, Gtk

from plugbackup import APP_ID, APP_NAME
from plugbackup import plat
from plugbackup.core.deps import Check, missing_required, run_checks
from plugbackup.i18n import _
from plugbackup.ui.deps_dialog import show_deps_dialog
from plugbackup.ui.profile_actions import ProfileActions
from plugbackup.ui.profile_view import ProfilePage, VolumePage
from plugbackup.ui.run_controller import RunController
from plugbackup.ui.sidebar import Sidebar

devices = plat.devices


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(1040, 660)
        self.checks: list[Check] = []
        self.selected_key: tuple | None = None
        self.runs = RunController(self)
        self.actions = ProfileActions(self)

        header = Gtk.HeaderBar(show_close_button=True, title=APP_NAME)
        menu = Gio.Menu()
        profile_section = Gio.Menu()
        profile_section.append(_("Import profile…"), "win.import-profile")
        menu.append_section(None, profile_section)
        view_section = Gio.Menu()
        view_section.append(_("Common exclusions…"), "win.global-excludes")
        view_section.append(_("Ignored drives…"), "win.ignored-drives")
        view_section.append(_("Start at login"), "app.autostart")
        view_section.append(_("Show all disks"), "app.show-all-disks")
        theme_menu = Gio.Menu()
        theme_menu.append(_("As in the system"), "app.theme::system")
        theme_menu.append(_("Light"), "app.theme::light")
        theme_menu.append(_("Dark"), "app.theme::dark")
        view_section.append_submenu(_("Theme"), theme_menu)
        menu.append_section(None, view_section)
        main_section = Gio.Menu()
        main_section.append(_("Help"), "app.help")
        main_section.append(_("System check"), "app.check")
        main_section.append(_("About"), "app.about")
        main_section.append(_("Quit"), "app.quit")
        menu.append_section(None, main_section)
        menu_button = Gtk.MenuButton(menu_model=menu)
        menu_button.set_image(Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON))
        menu_button.set_tooltip_text(_("Menu"))
        header.pack_end(menu_button)
        self.set_titlebar(header)

        excludes_action = Gio.SimpleAction.new("global-excludes", None)
        excludes_action.connect("activate", lambda *_a: self._edit_global_excludes())
        self.add_action(excludes_action)
        self.connect("delete-event", lambda *_a: app.on_window_close())
        ignored_action = Gio.SimpleAction.new("ignored-drives", None)
        ignored_action.connect("activate", lambda *_a: self._edit_ignored_drives())
        self.add_action(ignored_action)

        import_action = Gio.SimpleAction.new("import-profile", None)
        import_action.connect("activate", lambda *_a: self.actions.import_file())
        self.add_action(import_action)

        # Недостающие компоненты системы
        self.deps_bar = Gtk.InfoBar(show_close_button=False, revealed=False)
        self.deps_label = Gtk.Label(xalign=0, wrap=True)
        self.deps_bar.get_content_area().add(self.deps_label)
        self.deps_bar.add_button(_("Details"), Gtk.ResponseType.ACCEPT)
        self.deps_bar.connect("response", lambda *_a: self.show_checks())

        # Повреждённые файлы настроек и профилей
        self.config_bar, self.config_label = self._damaged_bar(self._restore_config)
        self.profiles_bar, self.profiles_label = self._damaged_bar(self._restore_profiles)

        # Ошибки действий
        self.error_bar = Gtk.InfoBar(show_close_button=True, revealed=False, message_type=Gtk.MessageType.ERROR)
        self.error_label = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.error_bar.get_content_area().add(self.error_label)
        self.error_bar.connect("response", lambda bar, _r: bar.set_revealed(False))

        self.sidebar = Sidebar(self._on_select)
        self.profile_page = ProfilePage(self.actions, app.monitor)
        self.volume_page = VolumePage(self.actions, app.monitor)
        self.stack = Gtk.Stack()
        for name, widget in (("empty", self._empty_state()), ("volume", self.volume_page)):
            scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
            scrolled.add(widget)
            scrolled.show_all()
            self.stack.add_named(scrolled, name)
        # Страница профиля прокручивает только список заданий — кнопки запуска всегда видны.
        self.profile_page.show_all()
        self.stack.add_named(self.profile_page, "profile")

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.pack1(self.sidebar, False, False)
        paned.pack2(self.stack, True, False)

        # Короткие сообщения (например «копия актуальна»), скрываются сами
        self.info_bar = Gtk.InfoBar(show_close_button=True, revealed=False, message_type=Gtk.MessageType.INFO)
        self.info_label = Gtk.Label(xalign=0, wrap=True)
        self.info_bar.get_content_area().add(self.info_label)
        self.info_bar.connect("response", lambda bar, _r: bar.set_revealed(False))
        self._info_timer = 0

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for bar in (self.deps_bar, self.config_bar, self.profiles_bar, self.error_bar, self.info_bar):
            root.pack_start(bar, False, False, 0)
        root.pack_start(paned, True, True, 0)
        self.add(root)

        app.monitor.connect("volumes-changed", lambda _m: self.refresh())
        self.update_damaged_bars()
        self.refresh()
        GLib.idle_add(self._run_checks)

    # ---------- построение ----------

    def _damaged_bar(self, restore):
        bar = Gtk.InfoBar(show_close_button=False, revealed=False, message_type=Gtk.MessageType.ERROR)
        label = Gtk.Label(xalign=0, wrap=True)
        bar.get_content_area().add(label)
        bar.add_button(_("Restore from backup copy"), Gtk.ResponseType.APPLY)
        bar.connect("response", lambda _b, r: r == Gtk.ResponseType.APPLY and restore())
        return bar, label

    def _empty_state(self) -> Gtk.Widget:
        icon = Gtk.Image.new_from_icon_name(APP_ID, Gtk.IconSize.DIALOG)
        icon.set_pixel_size(128)
        title_text = GLib.markup_escape_text(_("No profiles yet"))
        title = Gtk.Label()
        title.set_markup(f"<span size='x-large' weight='bold'>{title_text}</span>")
        hint = Gtk.Label(
            label=_("Insert a USB flash drive, SD card or external disk and create a backup profile for it."),
            wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=50,
        )
        hint.get_style_context().add_class("dim-label")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, margin=24)
        box.pack_start(icon, False, False, 0)
        box.pack_start(title, False, False, 0)
        box.pack_start(hint, False, False, 0)
        return box

    # ---------- обновление ----------

    def refresh(self, select_profile: str | None = None) -> None:
        app = self.get_application()
        profiles = app.profiles_store.profiles
        volumes = app.monitor.volumes()
        conflicts = devices.uuid_conflicts(volumes)
        wanted = ("profile", select_profile) if select_profile else self.selected_key
        self.selected_key = self.sidebar.populate(profiles, volumes, conflicts, wanted)
        self._show_selected(profiles, volumes, conflicts)

    def _on_select(self, key: tuple) -> None:
        self.selected_key = key
        app = self.get_application()
        volumes = app.monitor.volumes()
        self._show_selected(app.profiles_store.profiles, volumes, devices.uuid_conflicts(volumes))

    def _show_selected(self, profiles, volumes, conflicts) -> None:
        key = self.selected_key
        if key is None:
            self.stack.set_visible_child_name("empty")
            return
        kind, ident = key
        if kind == "profile":
            profile = next((p for p in profiles if p.id == ident), None)
            if profile is None:
                self.stack.set_visible_child_name("empty")
                return
            volume = next((v for v in volumes if profile.bound and v.uuid == profile.device.uuid), None)
            self.profile_page.show_profile(profile, volume, conflicts)
            self.stack.set_visible_child_name("profile")
        else:
            volume = next((v for v in volumes if v.object_path == ident), None)
            if volume is None:
                self.stack.set_visible_child_name("empty")
                return
            self.volume_page.show_volume(volume, profiles, conflicts)
            self.stack.set_visible_child_name("volume")

    def confirm_close(self) -> bool:
        """Можно ли закрыть окно/выйти: если идёт резервирование — спросить и остановить."""
        if not self.runs.any_running():
            return True
        from plugbackup.ui.common import confirm
        if not confirm(self, _("Stop the backup and quit?"),
                       _("A backup is in progress. Files copied so far stay on the drive; the copy will be "
                         "incomplete until the next backup."), _("Stop and quit"), destructive=True):
            return False
        self.runs.stop_all()

        def wait_and_quit():
            if self.runs.any_running():
                return GLib.SOURCE_CONTINUE
            self.get_application().quit()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(200, wait_and_quit)
        return False

    def _edit_global_excludes(self) -> None:
        from plugbackup.core.config import DEFAULT_GLOBAL_EXCLUDES
        app = self.get_application()
        dialog = Gtk.Dialog(title=_("Common exclusions"), transient_for=self, modal=True)
        dialog.set_default_size(480, 420)
        dialog.add_button(_("Reset to defaults"), 1)
        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        save = dialog.add_button(_("Save"), Gtk.ResponseType.OK)
        save.get_style_context().add_class("suggested-action")
        hint = Gtk.Label(xalign=0, wrap=True, label=_(
            "Applied to every job that has “Also apply the common exclusion list” turned on. One pattern per "
            "line, rsync syntax. Excluded files are not deleted from existing copies."))
        view = Gtk.TextView(monospace=True)
        view.get_buffer().set_text("\n".join(app.config_store.config.global_excludes))
        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.add(view)
        area = dialog.get_content_area()
        area.set_border_width(14)
        area.set_spacing(8)
        area.pack_start(hint, False, False, 0)
        area.pack_start(scrolled, True, True, 0)
        dialog.show_all()
        while True:
            response = dialog.run()
            if response == 1:
                view.get_buffer().set_text("\n".join(DEFAULT_GLOBAL_EXCLUDES))
                continue
            break
        if response == Gtk.ResponseType.OK:
            buffer = view.get_buffer()
            text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
            app.config_store.config.global_excludes = [l.strip() for l in text.splitlines() if l.strip()]
            if not app.config_store.save():
                self.show_error(_("The settings file is damaged; the list was not saved."))
        dialog.destroy()

    def on_run_state_changed(self, profile_id: str) -> None:
        self.refresh()
        self.get_application().on_auto_state_changed()

    def on_run_progress(self, profile_id: str, info: dict) -> None:
        if self.selected_key == ("profile", profile_id):
            self.profile_page.update_progress(info)
        self.get_application().on_run_progress(profile_id, info)

    def _edit_ignored_drives(self) -> None:
        app = self.get_application()
        dialog = Gtk.Dialog(title=_("Ignored drives"), transient_for=self, modal=True)
        dialog.set_default_size(460, 300)
        dialog.add_button(_("Close"), Gtk.ResponseType.CLOSE)
        area = dialog.get_content_area()
        area.set_border_width(14)
        area.set_spacing(8)
        area.pack_start(Gtk.Label(xalign=0, wrap=True, label=_(
            "Plug & Backup does not offer to create a profile for these drives.")), False, False, 0)
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        placeholder = Gtk.Label(label=_("The list is empty."), margin=12)
        placeholder.show()
        listbox.set_placeholder(placeholder)

        def fill():
            for row in listbox.get_children():
                listbox.remove(row)
            for drive in list(app.config_store.config.ignored_drives):
                box = Gtk.Box(spacing=8, margin=6)
                box.pack_start(Gtk.Label(xalign=0, label=f"{drive.get('name') or drive['uuid']}  ·  "
                                                         f"{drive['uuid']}  ·  {drive.get('added', '')}"), True, True, 0)
                remove = Gtk.Button(label=_("Remove from list"))
                remove.connect("clicked", lambda _b, d=drive: (app.config_store.config.ignored_drives.remove(d),
                                                               app.config_store.save(), fill()))
                box.pack_start(remove, False, False, 0)
                listbox.add(box)
            listbox.show_all()

        fill()
        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.add(listbox)
        area.pack_start(scrolled, True, True, 0)
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def show_info(self, text: str, seconds: int = 8) -> None:
        self.info_label.set_text(text)
        self.info_bar.set_revealed(True)
        if self._info_timer:
            GLib.source_remove(self._info_timer)

        def hide():
            self._info_timer = 0
            self.info_bar.set_revealed(False)
            return GLib.SOURCE_REMOVE

        self._info_timer = GLib.timeout_add_seconds(seconds, hide)

    def show_error(self, text: str) -> None:
        self.error_label.set_text(text)
        self.error_bar.set_revealed(True)

    def update_damaged_bars(self) -> None:
        app = self.get_application()
        for store, bar, label, text in (
                (app.config_store, self.config_bar, self.config_label,
                 _("The settings file is damaged and was not changed: %s. "
                   "Default settings are used until it is fixed.")),
                (app.profiles_store, self.profiles_bar, self.profiles_label,
                 _("The profiles file is damaged and was not changed: %s. "
                   "Profiles cannot be shown or edited until it is fixed."))):
            error = store.error
            if error is None:
                bar.set_revealed(False)
                continue
            label.set_text(text % error.path)
            bar.set_response_sensitive(Gtk.ResponseType.APPLY, error.backup_path is not None)
            bar.set_revealed(True)

    def _restore_config(self):
        self.get_application().restore_config_backup()

    def _restore_profiles(self):
        self.get_application().restore_profiles_backup()

    # ---------- проверка системы ----------

    def _run_checks(self) -> bool:
        self.checks = run_checks()
        missing = missing_required(self.checks)
        unavailable = [c for c in self.checks if not c.ok and not c.required]
        if missing:
            self.deps_bar.set_message_type(Gtk.MessageType.ERROR)
            self.deps_label.set_text(
                _("Required components are missing: %s. The application cannot work until they are installed.")
                % ", ".join(c.component for c in missing)
            )
            self.deps_bar.set_revealed(True)
        elif unavailable:
            features = sorted({c.feature for c in unavailable})
            self.deps_bar.set_message_type(Gtk.MessageType.WARNING)
            self.deps_label.set_text(_("Some features are unavailable: %s.") % ", ".join(features))
            self.deps_bar.set_revealed(True)
        return GLib.SOURCE_REMOVE

    def show_checks(self) -> None:
        if not self.checks:
            self.checks = run_checks()
        show_deps_dialog(self, self.checks)
