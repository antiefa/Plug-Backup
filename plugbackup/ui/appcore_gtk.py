"""GTK-адаптеры планировщика и фоновой работы для общего appcore."""

from __future__ import annotations

import threading
from typing import Any, Callable


class GtkScheduler:
    """Планировщик, доставляющий однократные вызовы в главный цикл GLib."""

    def __init__(self) -> None:
        from gi.repository import GLib
        self._glib = GLib

    def call_soon(self, callback: Callable[..., Any], *args: Any) -> int:
        return self._glib.idle_add(self._invoke, callback, args)

    def call_later(self, delay_s: float, callback: Callable[..., Any], *args: Any) -> int:
        milliseconds = max(0, int(delay_s * 1000 + 0.5))
        return self._glib.timeout_add(milliseconds, self._invoke, callback, args)

    @staticmethod
    def _invoke(callback: Callable[..., Any], args: tuple[Any, ...]) -> bool:
        callback(*args)
        return False

    def cancel(self, handle: int) -> None:
        try:
            self._glib.source_remove(handle)
        except (TypeError, ValueError):
            pass


class ThreadWorker:
    """Запускает функцию в отдельном потоке и доставляет исход через scheduler."""

    def __init__(self, scheduler: GtkScheduler) -> None:
        self._scheduler = scheduler

    def submit(self, name: str, fn: Callable[[], Any], on_success: Callable[[Any], None],
               on_error: Callable[[Exception], None]) -> threading.Thread:
        def work() -> None:
            try:
                result = fn()
            except Exception as error:
                self._scheduler.call_soon(on_error, error)
            else:
                self._scheduler.call_soon(on_success, result)

        thread = threading.Thread(target=work, name=name, daemon=True)
        thread.start()
        return thread
