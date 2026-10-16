"""Детерминированные тестовые реализации портов appcore."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from plugbackup.appcore.events import Notice, PasswordRequest, Question


@dataclass
class _ScheduledCall:
    when: float
    order: int
    callback: Callable[..., Any]
    args: tuple[Any, ...]
    cancelled: bool = False


class FakeScheduler:
    """Планировщик с виртуальным временем и стабильным порядком выполнения."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = float(start)
        self._order = 0
        self._calls: list[_ScheduledCall] = []

    def call_soon(self, callback: Callable[..., Any], *args: Any) -> _ScheduledCall:
        return self.call_later(0, callback, *args)

    def call_later(self, delay_s: float, callback: Callable[..., Any], *args: Any) -> _ScheduledCall:
        if delay_s < 0:
            raise ValueError("delay_s must not be negative")
        self._order += 1
        call = _ScheduledCall(self.now + delay_s, self._order, callback, args)
        self._calls.append(call)
        return call

    def cancel(self, handle: _ScheduledCall) -> None:
        handle.cancelled = True

    def advance(self, seconds: float) -> None:
        """Переместить часы вперёд и выполнить все созревшие callbacks."""
        if seconds < 0:
            raise ValueError("seconds must not be negative")
        target = self.now + seconds
        while True:
            ready = [call for call in self._calls if not call.cancelled and call.when <= target]
            if not ready:
                break
            call = min(ready, key=lambda item: (item.when, item.order))
            self._calls.remove(call)
            self.now = call.when
            call.callback(*call.args)
        self.now = target


class FakeDialogs:
    """Сценарные ответы диалогов и журнал показанных запросов."""

    def __init__(self, confirms: list[bool] | None = None, answers: list[str] | None = None,
                 passwords: list[str | None] | None = None) -> None:
        self.confirm_answers = list(confirms or [])
        self.answers = list(answers or [])
        self.password_answers = list(passwords or [])
        self.questions: list[Question] = []
        self.notices: list[Notice] = []
        self.password_requests: list[PasswordRequest] = []

    def confirm(self, question: Question) -> bool:
        self.questions.append(question)
        return self.confirm_answers.pop(0) if self.confirm_answers else False

    def ask(self, question: Question, callback: Callable[[str], None]) -> None:
        self.questions.append(question)
        callback(self.answers.pop(0) if self.answers else "cancel")

    def notify(self, notice: Notice) -> None:
        self.notices.append(notice)

    def password(self, request: PasswordRequest, callback: Callable[[str | None], None]) -> None:
        self.password_requests.append(request)
        callback(self.password_answers.pop(0) if self.password_answers else None)


class FakeWorker:
    """Синхронный worker с журналом запусков и передачей результата callback-ам."""

    def __init__(self) -> None:
        self.submissions: list[str] = []

    def submit(self, name: str, fn: Callable[[], Any], on_success: Callable[[Any], None],
               on_error: Callable[[Exception], None]) -> None:
        self.submissions.append(name)
        try:
            result = fn()
        except Exception as error:
            on_error(error)
        else:
            on_success(result)


class FakeClock:
    """Часы, значение которых управляется тестом."""

    def __init__(self, timestamp: float = 0.0) -> None:
        self.timestamp = float(timestamp)

    def now(self) -> float:
        return self.timestamp

    def advance(self, seconds: float) -> None:
        self.timestamp += seconds
