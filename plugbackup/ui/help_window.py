"""Окно справки (F1, ☰ → «Справка»): разделы слева, текст справа."""

from gi.repository import GLib, Gtk

from plugbackup import APP_NAME
from plugbackup.i18n import _


def sections() -> list[tuple[str, str, str]]:
    """Разделы справки на том языке, на котором показан интерфейс: есть русский перевод — русская справка,
    иначе (английский и любые языки без перевода) — английская."""
    from plugbackup import i18n
    if i18n.language() == "ru":
        from plugbackup import help_ru as content
    else:
        from plugbackup import help_en as content
    return content.SECTIONS


class HelpWindow(Gtk.Window):
    def __init__(self, app, section: str | None = None):
        super().__init__(application=app, title=_("Help — %s") % APP_NAME)
        self.set_default_size(920, 640)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        for key, title, markup in sections():
            heading = Gtk.Label(xalign=0, margin_bottom=10)
            heading.set_markup(f"<span size='x-large' weight='bold'>{GLib.markup_escape_text(title)}</span>")
            text = Gtk.Label(xalign=0, yalign=0, wrap=True, selectable=True, max_width_chars=95)
            text.set_markup(markup)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin=22)
            box.pack_start(heading, False, False, 0)
            box.pack_start(text, False, False, 0)
            scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
            scrolled.add(box)
            self.stack.add_titled(scrolled, key, title)
        sidebar = Gtk.StackSidebar(stack=self.stack)
        sidebar.set_size_request(240, -1)
        paned = Gtk.Paned()
        paned.pack1(sidebar, False, False)
        paned.pack2(self.stack, True, False)
        self.add(paned)
        self.show_all()
        if section:
            self.show_section(section)

    def show_section(self, key: str) -> None:
        if self.stack.get_child_by_name(key) is not None:
            self.stack.set_visible_child_name(key)
