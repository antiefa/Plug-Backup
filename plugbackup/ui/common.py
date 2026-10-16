"""Общие элементы интерфейса: подтверждения, сообщения, список замечаний, тексты ошибок."""

from gi.repository import GLib, Gtk, Pango

from plugbackup.core.profiles import Issue
from plugbackup.appcore.presentation import binding_error_text
from plugbackup.i18n import _

MODE_TITLES = {
    "mirror_versions": lambda: _("Mirror with version history"),
    "mirror": lambda: _("Mirror"),
    "accumulate": lambda: _("Accumulate"),
}
MODE_HINTS = {
    "mirror_versions": lambda: _("The copy matches the folder; deleted and replaced files are moved to the version "
                                 "history on the drive and kept for the set number of days."),
    "mirror": lambda: _("The copy matches the folder exactly: files deleted on this computer are permanently "
                        "deleted from the copy."),
    "accumulate": lambda: _("New and changed files are copied; nothing is ever deleted from the copy."),
}
COMPARE_TITLES = {
    "size_mtime": lambda: _("By size and modification time (fast)"),
    "checksum": lambda: _("By content (slow, reads every file)"),
}
SYMLINK_TITLES = {
    "auto": lambda: _("Automatic: keep as links on ext4, skip on other file systems"),
    "skip": lambda: _("Skip links"),
    "copy_target": lambda: _("Copy what the link points to"),
}


def confirm(parent: Gtk.Window, title: str, text: str, ok_label: str, destructive: bool = False) -> bool:
    dialog = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=Gtk.MessageType.QUESTION,
                               buttons=Gtk.ButtonsType.NONE, text=title)
    dialog.format_secondary_text(text)
    dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
    ok = dialog.add_button(ok_label, Gtk.ResponseType.OK)
    if destructive:
        ok.get_style_context().add_class("destructive-action")
    else:
        ok.get_style_context().add_class("suggested-action")
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    response = dialog.run()
    dialog.destroy()
    return response == Gtk.ResponseType.OK


def message(parent: Gtk.Window, title: str, text: str, error: bool = False) -> None:
    dialog = Gtk.MessageDialog(transient_for=parent, modal=True,
                               message_type=Gtk.MessageType.ERROR if error else Gtk.MessageType.INFO,
                               buttons=Gtk.ButtonsType.CLOSE, text=title)
    dialog.format_secondary_text(text)
    dialog.run()
    dialog.destroy()


# Цвет значка строки по её виду (классы из CSS приложения); info — синий.
NOTE_CSS = {"error": "pb-st-error", "warning": "pb-st-warnings"}


def note(text: str, kind: str = "info") -> Gtk.Widget:
    """Строка с иконкой: info / warning / error."""
    icon_name = {"error": "dialog-error-symbolic", "warning": "dialog-warning-symbolic"}.get(
        kind, "plugbackup-info-symbolic")
    icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.BUTTON)
    icon.get_style_context().add_class(NOTE_CSS.get(kind, "pb-st-cancelled"))
    icon.set_valign(Gtk.Align.START)
    label = Gtk.Label(label=text, xalign=0, wrap=True, max_width_chars=70,
                      wrap_mode=Pango.WrapMode.WORD_CHAR)
    box = Gtk.Box(spacing=8)
    box.pack_start(icon, False, False, 0)
    box.pack_start(label, True, True, 0)
    return box


class IssuesBox(Gtk.Box):
    """Список ошибок и предупреждений проверки."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)

    def set_issues(self, issues: list[Issue]) -> None:
        for child in self.get_children():
            self.remove(child)
        for issue in sorted(issues, key=lambda i: i.level != "error"):
            self.pack_start(note(issue.message, issue.level), False, False, 0)
        self.show_all()


def combo(choices: dict, active: str) -> Gtk.ComboBoxText:
    widget = Gtk.ComboBoxText()
    for key, title in choices.items():
        widget.append(key, title())
    widget.set_active_id(active)
    return widget


def section_label(text: str) -> Gtk.Label:
    label = Gtk.Label(xalign=0, margin_top=6)
    label.set_markup(f"<b>{GLib.markup_escape_text(text)}</b>")
    return label
