"""Тексты итогов и причин запуска для GTK-интерфейса."""

from plugbackup.core import journal
from plugbackup.appcore.presentation import (binding_error_text, nothing_changed, reason_text,
                                              record_status_text, status_text, trigger_text)

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
