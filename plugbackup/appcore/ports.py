"""Платформенно-независимые интерфейсы взаимодействия с приложением."""

from __future__ import annotations

from typing import Any, Callable, Protocol

from plugbackup.appcore.events import Notice, PasswordRequest, Question

Handle = Any


class Scheduler(Protocol):
    """Планировщик однократных вызовов; callbacks всегда выполняются в главном UI-потоке."""

    def call_soon(self, callback: Callable[..., Any], *args: Any) -> Handle:
        """Запланировать callback на ближайшую итерацию главного цикла интерфейса."""
        ...

    def call_later(self, delay_s: float, callback: Callable[..., Any], *args: Any) -> Handle:
        """Запланировать callback через delay_s секунд и вернуть отменяемый handle."""
        ...

    def cancel(self, handle: Handle) -> None:
        """Отменить вызов, если он ещё не был выполнен."""
        ...


class Dialogs(Protocol):
    """Порт вопросов и уведомлений; аргументы являются данными, а не UI-объектами."""

    def confirm(self, question: Question) -> bool:
        """Синхронно спросить подтверждение из рабочего потока."""
        ...

    def ask(self, question: Question, callback: Callable[[str], None]) -> None:
        """Показать вопрос с вариантами и передать semantic ID выбранного ответа."""
        ...

    def notify(self, notice: Notice) -> None:
        """Показать уведомление."""
        ...

    def password(self, request: PasswordRequest, callback: Callable[[str | None], None]) -> None:
        """Запросить пароль; None означает отмену."""
        ...


class Worker(Protocol):
    """Выполняет работу вне UI-потока и доставляет результат через UI-планировщик."""

    def submit(self, name: str, fn: Callable[[], Any], on_success: Callable[[Any], None],
               on_error: Callable[[Exception], None]) -> Any:
        """Запустить fn и вызвать ровно один callback с результатом или исключением."""
        ...


class Clock(Protocol):
    """Источник текущего времени в секундах Unix."""

    def now(self) -> float:
        """Вернуть текущее время в секундах Unix."""
        ...
