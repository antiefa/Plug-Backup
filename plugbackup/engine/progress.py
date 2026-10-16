"""Формирование прогресса копирования в стиле progress2 rsync."""
from __future__ import annotations

import time
from typing import Callable, Optional


class ProgressTracker:
    """Ограничивать частоту обновлений и форматировать статистику передачи."""
    def __init__(self, callback: Optional[Callable], total_bytes: int,
                 clock: Optional[Callable[[], float]] = None,
                 interval: float = 0.2) -> None:
        self.callback = callback
        self.total_bytes = max(0, int(total_bytes))
        self.clock = clock or time.monotonic
        self.interval = interval
        self.started = self.clock()
        self.last_sent = None
        self.last_bytes = 0
        self.speed_value = 0.0

    def update(self, bytes_done: int, file_name: str, force: bool = False) -> None:
        if self.callback is None:
            return
        now = self.clock()
        if not force and self.last_sent is not None and now - self.last_sent < self.interval:
            return
        elapsed = max(0.001, now - self.started)
        instant = max(0, bytes_done - self.last_bytes) / max(0.001, now - (self.last_sent or self.started))
        self.speed_value = instant if self.last_sent is None else self.speed_value * 0.65 + instant * 0.35
        percent = int(min(100, bytes_done * 100 / self.total_bytes)) if self.total_bytes else 100
        remaining = max(0, self.total_bytes - bytes_done)
        eta_seconds = int(remaining / self.speed_value) if self.speed_value > 0 else 0
        self.callback({"bytes_done": int(bytes_done), "total_bytes": self.total_bytes, "percent": percent,
                       "speed": format_speed(self.speed_value), "eta": format_eta(eta_seconds), "file": file_name})
        self.last_sent = now
        self.last_bytes = bytes_done


def format_speed(value: float) -> str:
    """Отформатировать скорость в десятичных единицах rsync progress2."""
    units = ("B/s", "kB/s", "MB/s", "GB/s", "TB/s")
    amount = float(value)
    unit = 0
    while amount >= 1000 and unit < len(units) - 1:
        amount /= 1000.0
        unit += 1
    return f"{amount:.2f}{units[unit]}"


def format_eta(seconds: int) -> str:
    """Отформатировать остаток времени как часы, минуты и секунды."""
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"
