"""Точка входа: Gtk.Application с единственным экземпляром и ключами командной строки."""

import sys
import threading
import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from plugbackup import APP_ID, APP_NAME, __version__  # noqa: E402
from plugbackup.core import deps  # noqa: E402
from plugbackup.core.config import ConfigStore  # noqa: E402
from plugbackup.core.profiles import ProfilesStore  # noqa: E402
from plugbackup.core.storage import StorageError, restore_backup  # noqa: E402
from plugbackup.i18n import _  # noqa: E402


APP_CSS = b"""
.pb-auto-badge {
    background-color: #059669;
    color: #ffffff;
    border-radius: 9px;
    padding: 1px 8px;
    font-size: smaller;
    font-weight: bold;
}
.pb-auto-badge.off {
    background-color: alpha(currentColor, 0.18);
    color: alpha(currentColor, 0.65);
}
.pb-card {
    border: 1px solid alpha(currentColor, 0.18);
    border-radius: 8px;
    padding: 12px;
    background-color: alpha(currentColor, 0.03);
}
.pb-run image { color: #16a34a; }
.pb-edit image { color: #3b82f6; }
.pb-danger image { color: #ef4444; }
.pb-move image { color: alpha(currentColor, 0.75); }
.pb-info image { color: #3b82f6; }
button:disabled image { opacity: 0.45; }
.pb-st-ok { color: #16a34a; }
.pb-st-warnings { color: #d97706; }
.pb-st-error { color: #ef4444; }
.pb-st-skipped { color: #d97706; }
.pb-st-cancelled { color: #3b82f6; }
"""


class Application(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        GLib.set_application_name(APP_NAME)
        self.add_main_option("version", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             _("Show the version and exit"), None)
        self.add_main_option("check", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             _("Check system components and exit"), None)
        self.add_main_option("list", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             _("List profiles and their drives, then exit"), None)
        self.add_main_option("backup", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             _("Back up the profile with this name or ID without a window"), _("PROFILE"))
        self.add_main_option("hidden", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             _("Start minimized to the tray"), None)
        self.connect("handle-local-options", self._on_local_options)
        self.config_store: ConfigStore | None = None
        self.profiles_store: ProfilesStore | None = None
        self.monitor = None
        self.window = None
        self.tray = None
        self.auto = None
        self._start_hidden = False
        self._activated = False

    def _on_local_options(self, _app, options: GLib.VariantDict) -> int:
        if options.contains("version"):
            print(f"{APP_NAME} {__version__}")
            return 0
        if options.contains("list"):
            from plugbackup import cli
            return cli.cmd_list()
        if options.contains("backup"):
            from plugbackup import cli
            return cli.cmd_backup(options.lookup_value("backup", GLib.VariantType("s")).get_string())
        if options.contains("check"):
            checks = deps.run_checks()
            print(deps.format_report(checks))
            return 1 if deps.missing_required(checks) else 0
        self._start_hidden = options.contains("hidden")
        return -1

    def do_startup(self):
        Gtk.Application.do_startup(self)
        Gtk.Window.set_default_icon_name(APP_ID)
        self._install_css()
        for name, handler in (("about", self._on_about), ("check", self._on_check), ("quit", self._on_quit),
                              ("help", self._on_help)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)
        self.set_accels_for_action("app.quit", ["<Primary>q"])
        self.set_accels_for_action("app.help", ["F1"])
        self.help_window = None

        self.config_store = ConfigStore()
        self.profiles_store = ProfilesStore()
        show_all = self.config_store.config.show_all_disks
        show_all_action = Gio.SimpleAction.new_stateful("show-all-disks", None, GLib.Variant.new_boolean(show_all))
        show_all_action.connect("change-state", self._on_show_all_disks)
        self.add_action(show_all_action)
        self.monitor = self._create_monitor(show_all)

        settings = Gtk.Settings.get_default()
        self._system_theme = settings.props.gtk_theme_name
        self._system_prefer_dark = settings.props.gtk_application_prefer_dark_theme
        theme_mode = self.config_store.config.theme
        theme_action = Gio.SimpleAction.new_stateful("theme", GLib.VariantType.new("s"),
                                                     GLib.Variant.new_string(theme_mode))
        theme_action.connect("change-state", self._on_theme)
        self.add_action(theme_action)
        if theme_mode != "system":  # «как в системе» — ничего не трогаем, тогда GTK сам следит за сменой темы
            self.apply_theme(theme_mode)

        from plugbackup import plat
        autostart = plat.autostart
        autostart_action = Gio.SimpleAction.new_stateful("autostart", None,
                                                         GLib.Variant.new_boolean(autostart.is_enabled()))
        autostart_action.connect("change-state", self._on_autostart)
        self.add_action(autostart_action)

        from plugbackup.ui.auto_controller import AutoController
        from plugbackup.ui.main_window import MainWindow
        from plugbackup.ui.tray import Tray
        from plugbackup.ui.unlock import Unlocker
        from plugbackup.ui.vaults import Vaults
        self.unlocker = Unlocker(self)
        self.vaults = Vaults(self)
        self.vaults.cleanup_stale()        # зашифрованные папки, оставшиеся открытыми после сбоя
        from plugbackup import plat
        gocrypt = plat.vault
        threading.Thread(target=gocrypt.availability_cached, daemon=True).start()   # проверка gocryptfs заранее
        self.window = MainWindow(self)
        self.tray = Tray(self)
        if self.tray.available:
            self.hold()  # приложение живёт в трее и без открытого окна
        self.auto = AutoController(self)

    # ---------- окно, трей, автоматика ----------

    def show_window(self) -> None:
        self.window.show_all()
        self.window.refresh()
        self.window.present()

    def toggle_window(self) -> None:
        if self.window.get_visible() and self.window.is_active():
            self.window.hide()
        else:
            self.show_window()

    def on_window_close(self) -> bool:
        """Закрытие окна: при трее — просто скрыть (резервирование продолжается)."""
        if self.tray is not None and self.tray.available:
            self.window.hide()
            return True
        return not self.window.confirm_close()

    def quit_from_tray(self) -> None:
        if self.window.runs.any_running():
            self.show_window()
        if self.window.confirm_close():
            self.quit()

    def runs_active(self, profile_id: str) -> bool:
        return self.window is not None and self.window.runs.is_running(profile_id)

    def start_run(self, profile, volume, job_ids=None, trigger="manual", on_done=None) -> None:
        self.window.runs.start(profile, volume, job_ids=job_ids, trigger=trigger, on_done=on_done)

    def _volume_for(self, profile):
        return next((v for v in self.monitor.volumes() if profile.bound and v.uuid == profile.device.uuid), None)

    def backup_from_tray(self, profile) -> None:
        volume = self._volume_for(profile)
        if volume is None:
            return
        if volume.locked or not volume.mounted:
            # Сначала разблокировать (LUKS) или смонтировать, затем запустить.
            self.show_window()
            self.window.actions.prepare_volume(profile, volume, lambda v: self.start_run(profile, v))
            return
        self.show_window()
        self.start_run(profile, volume)

    def stop_from_tray(self, profile) -> None:
        self.window.runs.stop(profile.id)

    def eject_from_tray(self, profile) -> None:
        volume = self._volume_for(profile)
        if volume is None or self.runs_active(profile.id):
            return
        self.auto._eject_after(profile)

    @property
    def auto_paused_until(self):
        return self.auto.paused_until if self.auto is not None else None

    def pause_auto(self, seconds) -> None:
        self.auto.pause(seconds)

    def create_profile_for(self, volume) -> None:
        self.show_window()
        current = next((v for v in self.monitor.volumes() if v.uuid == volume.uuid), None)
        if current is not None:
            self.window.actions.create_from_volume(current)

    def show_run(self, record) -> None:
        from plugbackup.ui.run_dialogs import SummaryDialog
        self.show_window()
        dialog = SummaryDialog(self.window, record)
        dialog.run()
        dialog.destroy()

    def tray_profiles(self):
        from plugbackup.core import journal
        from plugbackup.ui.run_texts import status_text
        items = []
        for profile in self.profiles_store.profiles:
            if not profile.bound:
                continue
            volume = self._volume_for(profile)
            if self.runs_active(profile.id):
                status = _("backing up")
            elif profile.id in (self.auto.countdowns if self.auto else {}):
                status = _("starting soon")
            elif volume is None:
                status = _("drive not connected")
            elif volume.locked:
                status = _("drive connected, locked")
            else:
                last = journal.last_run(profile.id)
                status = _("drive connected")
                if last is not None:
                    when = time.strftime("%H:%M", time.localtime(last.finished or last.started))
                    if last.reason == "no-changes":
                        status += ", " + _("copy is up to date (checked at %s)") % when
                    else:
                        status += ", " + _("last run at %s: %s") % (when, status_text(last.status))
            items.append((profile, volume, status))
        return items

    def tray_state(self):
        from plugbackup.core import journal
        from plugbackup.ui import tray
        profiles = self.tray_profiles()
        lines = [f"{p.name}: {status}" for p, _v, status in profiles]
        if self.window is not None and self.window.runs.any_running():
            return tray.RUNNING, "\n".join(lines)
        if self.auto is not None and self.auto.countdowns:
            return tray.RUNNING, "\n".join(lines)
        for profile, _volume, _status in profiles:
            last = journal.last_run(profile.id)
            if last is not None and last.status in (journal.ERROR, journal.CANCELLED):
                return tray.ERROR, "\n".join(lines)
        if any(volume is not None for _p, volume, _s in profiles):
            return tray.CONNECTED, "\n".join(lines)
        paused = self.auto_paused_until
        if paused is not None:
            lines.append(_("Automatic backup is paused"))
        return tray.IDLE, "\n".join(lines) or _("No connected drives")

    def on_auto_state_changed(self) -> None:
        if self.tray is not None:
            self.tray.update()

    def on_run_progress(self, profile_id: str, info: dict) -> None:
        if self.tray is not None and info.get("phase") == "copy":
            profile = self.profiles_store.get(profile_id)
            name = profile.name if profile else ""
            self.tray.set_progress(f"{name}: {info.get('percent', 0)} %")

    def _on_autostart(self, action, value: GLib.Variant):
        from plugbackup import plat
        autostart = plat.autostart
        try:
            autostart.set_enabled(value.get_boolean())
        except OSError as exc:
            self.window.show_error(_("Could not change the “Start at login” setting: %s") % exc)
            return
        action.set_state(GLib.Variant.new_boolean(autostart.is_enabled()))

    def apply_theme(self, mode: str) -> None:
        """Тема только окон приложения: как в системе, светлая или тёмная (тема системы не меняется)."""
        from plugbackup.core import theme

        settings = Gtk.Settings.get_default()
        if mode == theme.SYSTEM:
            settings.props.gtk_theme_name = self._system_theme
            settings.props.gtk_application_prefer_dark_theme = self._system_prefer_dark
            return
        name, prefer_dark = theme.resolve(mode, self._system_theme, theme.installed_gtk3_themes())
        settings.props.gtk_theme_name = name
        settings.props.gtk_application_prefer_dark_theme = prefer_dark

    def _on_theme(self, action, value: GLib.Variant):
        action.set_state(value)
        mode = value.get_string()
        self.apply_theme(mode)
        self.config_store.config.theme = mode
        self.config_store.save()

    @staticmethod
    def _install_css():
        from gi.repository import Gdk

        css = Gtk.CssProvider()
        css.load_from_data(APP_CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    @staticmethod
    def _create_monitor(show_all: bool):
        from plugbackup.ui.null_monitor import NullMonitor

        try:
            from plugbackup.plat.linux.devices import DeviceMonitor

            return DeviceMonitor(show_all=show_all)
        except Exception as exc:  # нет UDisks2 — окно покажет это в проверке системы
            print(f"plugbackup: drive monitoring is unavailable: {exc}", file=sys.stderr)
            return NullMonitor()

    def _on_show_all_disks(self, action, value: GLib.Variant):
        action.set_state(value)
        self.monitor.set_show_all(value.get_boolean())
        self.config_store.config.show_all_disks = value.get_boolean()
        self.config_store.save()

    def restore_config_backup(self) -> None:
        window = self.window
        try:
            restore_backup(self.config_store.path)
        except (OSError, StorageError) as exc:
            if window is not None:
                window.show_error(_("Could not restore settings: %s") % exc)
            return
        self.config_store.reload()
        show_all = self.config_store.config.show_all_disks
        self.lookup_action("show-all-disks").set_state(GLib.Variant.new_boolean(show_all))
        self.monitor.set_show_all(show_all)
        if window is not None:
            window.update_damaged_bars()

    def restore_profiles_backup(self) -> None:
        window = self.window
        try:
            restore_backup(self.profiles_store.path)
        except (OSError, StorageError) as exc:
            if window is not None:
                window.show_error(_("Could not restore profiles: %s") % exc)
            return
        self.profiles_store.reload()
        if window is not None:
            window.update_damaged_bars()
            window.refresh()

    def do_activate(self):
        first = not self._activated
        self._activated = True
        if first and self._start_hidden and self.tray is not None and self.tray.available:
            return  # автозагрузка: сразу в трей
        self.show_window()

    def _on_about(self, *_args):
        dialog = Gtk.AboutDialog(
            transient_for=self.window,
            modal=True,
            program_name=APP_NAME,
            version=__version__,
            logo_icon_name=APP_ID,
            comments=_("Back up selected folders to removable drives with one click "
                       "or automatically when the drive is inserted."),
        )
        dialog.run()
        dialog.destroy()

    def _on_help(self, *_args):
        from plugbackup.ui.help_window import HelpWindow
        if self.help_window is None:
            self.help_window = HelpWindow(self)
            self.help_window.connect("destroy", lambda _w: setattr(self, "help_window", None))
        self.help_window.present()

    def _on_check(self, *_args):
        self.show_window()
        self.window.show_checks()

    def _on_quit(self, *_args):
        self.quit_from_tray()

    def do_shutdown(self):
        vaults = getattr(self, "vaults", None)
        if vaults is not None:
            vaults.close_all_sync()
        Gtk.Application.do_shutdown(self)


def main() -> int:
    GLib.set_prgname(APP_ID)
    return Application().run(sys.argv)
