"""Окно «Проверка системы»: какие компоненты найдены и какие функции от них зависят."""

from gi.repository import GLib, Gtk

from plugbackup.core.deps import Check
from plugbackup.i18n import _


def _row(check: Check) -> Gtk.ListBoxRow:
    if check.ok:
        icon, tip = "emblem-ok-symbolic", _("Available")
    elif check.required:
        icon, tip = "dialog-error-symbolic", _("Required component is missing")
    else:
        icon, tip = "dialog-warning-symbolic", _("Unavailable — the feature is turned off")

    image = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)
    image.set_tooltip_text(tip)
    image.set_valign(Gtk.Align.START)

    title = Gtk.Label(xalign=0)
    title.set_markup(f"<b>{GLib.markup_escape_text(check.feature)}</b>")
    text = check.component + (f" — {check.detail}" if check.detail else "")
    subtitle = Gtk.Label(label=text, xalign=0, wrap=True, selectable=True)
    subtitle.get_style_context().add_class("dim-label")

    labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    labels.pack_start(title, False, False, 0)
    labels.pack_start(subtitle, False, False, 0)

    box = Gtk.Box(spacing=12, margin=8)
    box.pack_start(image, False, False, 0)
    box.pack_start(labels, True, True, 0)

    row = Gtk.ListBoxRow(activatable=False, selectable=False)
    row.add(box)
    return row


def show_deps_dialog(parent: Gtk.Window, checks: list[Check]) -> None:
    dialog = Gtk.Dialog(title=_("System check"), transient_for=parent, modal=True)
    dialog.add_button(_("Close"), Gtk.ResponseType.CLOSE)
    dialog.set_default_size(560, 720)

    listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    for check in checks:
        listbox.add(_row(check))

    scrolled = Gtk.ScrolledWindow(vexpand=True)
    scrolled.add(listbox)

    area = dialog.get_content_area()
    area.set_border_width(12)
    area.pack_start(scrolled, True, True, 0)
    dialog.show_all()
    dialog.run()
    dialog.destroy()
