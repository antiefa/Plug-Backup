"""Значок в трее: состояние, подсказка с ходом резервирования, меню.

XApp.StatusIcon — родной для Cinnamon. Если его нет, пробуется AyatanaAppIndicator3;
если нет ни того ни другого — трея нет, и окно при закрытии сворачивается обычным образом.
"""

import time

import gi
from gi.repository import Gtk

from plugbackup import APP_NAME
from plugbackup.i18n import _

IDLE, CONNECTED, RUNNING, ERROR = "idle", "connected", "running", "error"
ICONS = {state: f"plugbackup-tray-{state}-symbolic" for state in (IDLE, CONNECTED, RUNNING, ERROR)}


def _load_backend():
    try:
        gi.require_version("XApp", "1.0")
        from gi.repository import XApp
        return "xapp", XApp
    except (ValueError, ImportError):
        pass
    try:
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3
        return "ayatana", AyatanaAppIndicator3
    except (ValueError, ImportError):
        return None, None


class Tray:
    def __init__(self, app):
        self.app = app
        self.kind, module = _load_backend()
        self.icon = None
        self.menu = Gtk.Menu()
        if self.kind == "xapp":
            self.icon = module.StatusIcon()
            self.icon.set_name("plugbackup")
            self.icon.connect("activate", self._on_activate)
            self.icon.set_secondary_menu(self.menu)
        elif self.kind == "ayatana":
            self.icon = module.Indicator.new("plugbackup", ICONS[IDLE],
                                             module.IndicatorCategory.APPLICATION_STATUS)
            self.icon.set_status(module.IndicatorStatus.ACTIVE)
            self.icon.set_menu(self.menu)
        self.state = None
        self.update()

    @property
    def available(self) -> bool:
        return self.icon is not None

    def _on_activate(self, _icon, button, _time):
        if button == 1:
            self.app.toggle_window()

    # ---------- состояние ----------

    def update(self, tooltip_extra: str = "") -> None:
        if self.icon is None:
            return
        state, tooltip = self.app.tray_state()
        if tooltip_extra:
            tooltip = f"{tooltip}\n{tooltip_extra}"
        if state != self.state:
            self.state = state
            if self.kind == "xapp":
                self.icon.set_icon_name(ICONS[state])
            else:
                self.icon.set_icon_full(ICONS[state], tooltip)
        self._tooltip = tooltip
        if self.kind == "xapp":
            self.icon.set_tooltip_text(f"{APP_NAME}\n{tooltip}".strip())
        self._rebuild_menu()

    def set_progress(self, text: str) -> None:
        """Ход резервирования — только в подсказке (меню не перестраивается)."""
        if self.kind == "xapp":
            self.icon.set_tooltip_text(f"{APP_NAME}\n{text}\n{getattr(self, '_tooltip', '')}".strip())

    def _item(self, label: str, callback, sensitive: bool = True) -> Gtk.MenuItem:
        item = Gtk.MenuItem(label=label)
        item.set_sensitive(sensitive)
        item.connect("activate", lambda _i: callback())
        return item

    def _rebuild_menu(self) -> None:
        for child in self.menu.get_children():
            self.menu.remove(child)
        app = self.app
        for profile, volume, status in app.tray_profiles():
            header = Gtk.MenuItem(label=f"{profile.name} — {status}")
            sub = Gtk.Menu()
            running = app.runs_active(profile.id)
            ready = volume is not None and not running and (volume.mounted or volume.locked
                                                            or bool(volume.filesystem_path))
            sub.append(self._item(_("Back up"), lambda p=profile: app.backup_from_tray(p), ready))
            if running:
                sub.append(self._item(_("Stop"), lambda p=profile: app.stop_from_tray(p)))
            sub.append(self._item(_("Eject"), lambda p=profile: app.eject_from_tray(p),
                                  volume is not None and not running))
            header.set_submenu(sub)
            self.menu.append(header)
        if self.menu.get_children():
            self.menu.append(Gtk.SeparatorMenuItem())

        paused_until = app.auto_paused_until
        if paused_until is not None and paused_until > time.time():
            self.menu.append(self._item(_("Resume automatic backup"), lambda: app.pause_auto(None)))
        else:
            pause = Gtk.MenuItem(label=_("Pause automatic backup"))
            sub = Gtk.Menu()
            sub.append(self._item(_("For 1 hour"), lambda: app.pause_auto(3600)))
            sub.append(self._item(_("For 4 hours"), lambda: app.pause_auto(4 * 3600)))
            sub.append(self._item(_("Until quit"), lambda: app.pause_auto(float("inf"))))
            pause.set_submenu(sub)
            self.menu.append(pause)
        self.menu.append(Gtk.SeparatorMenuItem())
        self.menu.append(self._item(_("Open Plug & Backup"), app.show_window))
        self.menu.append(self._item(_("Quit"), app.quit_from_tray))
        self.menu.show_all()
