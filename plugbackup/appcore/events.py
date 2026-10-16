"""Неизменяемые сообщения общего прикладного слоя."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Type, TypeVar


@dataclass(frozen=True)
class Question:
    """Запрос решения с семантическими кодами вариантов."""

    kind: str
    title: str
    text: str
    confirm_label: str = ""
    details: tuple[str, ...] = ()
    destructive: bool = False
    choices: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Notice:
    """Уведомление с кодом вида и текстовыми данными."""

    kind: str
    title: str
    text: str
    profile_id: str = ""
    actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class PasswordRequest:
    """Данные для запроса пароля без ссылки на окно или виджет."""

    title: str
    heading: str
    purpose: str = ""
    profile_id: str = ""
    remember: bool | None = None


@dataclass(frozen=True)
class AppSnapshot:
    """Снимок доменных идентификаторов и состояний приложения."""

    profile_ids: tuple[str, ...] = ()
    volume_ids: tuple[str, ...] = ()
    active_run_ids: tuple[str, ...] = ()
    timestamp: float = 0.0


@dataclass(frozen=True)
class ProfileStateChanged:
    """Изменение состояния или данных профиля."""

    profile_id: str
    state: str = "updated"
    reason: str = ""


@dataclass(frozen=True)
class VolumeStateChanged:
    """Подключение, отключение или изменение состояния тома."""

    volume_id: str
    state: str = "updated"
    reason: str = ""


@dataclass(frozen=True)
class RunStarted:
    """Запуск резервирования."""

    profile_id: str
    run_id: str
    trigger: str = "manual"


@dataclass(frozen=True)
class RunProgress:
    """Изменение прогресса резервирования."""

    profile_id: str
    run_id: str
    completed: int = 0
    total: int = 0
    message: str = ""


@dataclass(frozen=True)
class RunFinished:
    """Завершение резервирования с кодом итога и причины."""

    profile_id: str
    run_id: str
    status: str
    reason: str = ""


@dataclass(frozen=True)
class AutoCountdownStarted:
    """Начало отсчёта автоматического запуска."""

    profile_id: str
    seconds: int
    changes: int = 0


@dataclass(frozen=True)
class AutoCountdownUpdated:
    """Остаток времени автоматического запуска."""

    profile_id: str
    remaining: int


@dataclass(frozen=True)
class AutoCountdownCancelled:
    """Отмена отсчёта автоматического запуска."""

    profile_id: str
    reason: str = "cancelled"


@dataclass(frozen=True)
class QuestionRequested:
    """Событие запроса решения."""

    question: Question


@dataclass(frozen=True)
class NoticeRequested:
    """Событие запроса уведомления."""

    notice: Notice


@dataclass(frozen=True)
class ErrorRaised:
    """Ошибка приложения, представленная кодом и простыми данными."""

    code: str
    message: str = ""
    profile_id: str = ""
    details: tuple[str, ...] = ()


Event = TypeVar("Event")
Callback = Callable[[Any], None]


class EventBus:
    """Синхронная шина событий с подписками по точному типу события."""

    def __init__(self) -> None:
        self._subscribers: Dict[Type[Any], List[Callback]] = {}

    def subscribe(self, event_type: Type[Event], callback: Callable[[Event], None]) -> Callable[[], None]:
        """Подписать callback на тип события и вернуть идемпотентную отписку."""
        callbacks = self._subscribers.setdefault(event_type, [])
        callbacks.append(callback)
        active = True

        def unsubscribe() -> None:
            nonlocal active
            if active:
                active = False
                callbacks.remove(callback)
                if not callbacks:
                    self._subscribers.pop(event_type, None)

        return unsubscribe

    def emit(self, event: Any) -> None:
        """Передать событие текущим подписчикам в порядке подписки."""
        for callback in tuple(self._subscribers.get(type(event), ())):
            callback(event)
