"""Заглушка слежения за носителями, когда UDisks2 недоступен: список всегда пуст."""

from gi.repository import GObject


class NullMonitor(GObject.Object):
    __gsignals__ = {
        "volume-added": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "volume-removed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "volume-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "volumes-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    client = None
    show_all = False

    def set_show_all(self, value: bool) -> None:
        self.show_all = value

    def volumes(self) -> list:
        return []

    def get(self, _object_path: str):
        return None

    def refresh(self) -> None:
        pass
