"""Контракты подмодулей платформы.

Ядро и CLI используют перечисленные API без зависимости от циклов событий или
объектной модели конкретной ОС. GTK-приложение Linux 1.x использует добавочный
раздел GTK_API; его типы и детали не являются общим контрактом будущего Qt UI.
Платформы должны экспортировать все имена из соответствующих кортежей.
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Protocol

from plugbackup.core.volume import Volume


DEVICES_API = ("list_volumes", "DeviceMonitor", "uuid_conflicts", "eject")
GTK_DEVICES_API = (
    "collect_volumes", "free_space", "current_mount_point", "mount_sync", "unmount_sync",
    "mount_async", "unlock_async", "is_wrong_passphrase", "eject_sync",
    "EJECT_POWERED_OFF", "EJECT_UNMOUNTED_ONLY",
)
MOUNTS_API = ("MountCheck", "verify_mount")
GTK_MOUNTS_API = ("parent_is_user_writable",)
LINUX_MOUNTS_API = ("MountEntry", "parse_mountinfo", "read_mountinfo", "find_mount", "device_for_uuid")
SECRETS_API = ("OK", "LOCKED", "UNAVAILABLE", "service_state", "lookup", "store", "clear")
AUTOSTART_API = ("is_enabled", "set_enabled")
GTK_AUTOSTART_API = ("autostart_path",)
SYSTEM_API = ("ProfileLock", "inhibit_sleep", "release_inhibit", "syncfs", "free_bytes")
PATHS_API = (
    "runtime_dir", "config_dir", "state_dir", "restore_backup_root",
)
LINUX_PATHS_API = ("config_home", "state_home", "data_home", "autostart_dir")
GTK_PATHS_API = ("theme_dirs",)
DEPS_API = ("run_checks",)
LINUX_DEPS_API = ("which", "gir_available", "dbus_name_available", "tool_version")
VAULT_API = (
    "CIPHER_SUFFIX", "FAILED", "GocryptError", "MIN_VERSION", "NOT_EMPTY", "UNAVAILABLE",
    "WRONG_PASSWORD", "availability", "availability_cached", "init", "is_initialized",
    "is_open", "cipher_dir", "mount", "mounted_source", "plain_dir", "stale_mounts", "unmount",
)

MODULE_APIS = {
    "devices": DEVICES_API,
    "mounts": MOUNTS_API,
    "secrets": SECRETS_API,
    "autostart": AUTOSTART_API,
    "system": SYSTEM_API,
    "paths": PATHS_API,
    "deps": DEPS_API,
    "vault": VAULT_API,
}
GTK_MODULE_APIS = {
    "devices": GTK_DEVICES_API,
    "mounts": GTK_MOUNTS_API,
    "autostart": GTK_AUTOSTART_API,
    "paths": GTK_PATHS_API,
}
LINUX_EXTRA_APIS = {
    "mounts": LINUX_MOUNTS_API,
    "deps": LINUX_DEPS_API,
    "paths": LINUX_PATHS_API,
}


class VolumeMonitor(Protocol):
    """Монитор томов. Изменения также доступны через обычную callable-подписку."""

    def volumes(self) -> list[Volume]:
        """Вернуть снимок текущих томов."""
        ...

    def get(self, identifier: str) -> Optional[Volume]:
        """Вернуть том по устойчивому идентификатору или None."""
        ...

    def find_by_uuid(self, uuid: str) -> list[Volume]:
        """Найти тома по идентификатору файловой системы/тома."""
        ...

    def refresh(self) -> None:
        """Обновить состояние и сообщить подписчикам об изменениях."""
        ...

    def close(self) -> None:
        """Освободить ресурсы монитора."""
        ...

    def set_show_all(self, enabled: bool) -> None:
        """Включить или выключить показ внутренних томов."""
        ...

    def subscribe(self, callback: Callable[[VolumeMonitor], None]) -> Callable[[], None]:
        """Подписать callable; вернуть callable для снятия подписки."""
        ...


class DeviceModule(Protocol):
    def list_volumes(self, show_all: bool = True) -> list[Volume]:
        """Вернуть доступные тома; системные ошибки обнаружения сообщаются исключением."""
        ...

    def DeviceMonitor(self, show_all: bool = False) -> VolumeMonitor:
        """Создать монитор; детали платформы не входят в аргументы общего API."""
        ...

    def uuid_conflicts(self, volumes: list[Volume]) -> set[str]:
        """Вернуть повторяющиеся непустые идентификаторы томов."""
        ...

    def eject(self, volume: Volume) -> Any: ...


class MountCheck(Protocol):
    """Результат проверки пути: ok и нейтральный код причины при отказе."""

    ok: bool
    reason: str


class MountModule(Protocol):
    def verify_mount(self, mount_point: str, expected_uuid: str, writable: bool = True) -> MountCheck:
        """Проверить принадлежность и доступность пути; ошибки ОС представлены reason."""
        ...

class SecretModule(Protocol):
    OK: str
    LOCKED: str
    UNAVAILABLE: str

    def service_state(self) -> str:
        """Вернуть OK, LOCKED или UNAVAILABLE."""
        ...

    def lookup(self, profile_id: str) -> Optional[str]:
        """Вернуть сохранённый пароль или None."""
        ...

    def store(self, profile_id: str, label: str, password: str) -> bool:
        """Сохранить пароль; вернуть False, если хранилище не приняло запись."""
        ...

    def clear(self, profile_id: str) -> bool:
        """Удалить пароль; вернуть False при недоступности хранилища."""
        ...


class AutostartModule(Protocol):
    def is_enabled(self) -> bool:
        """Сообщить, включён ли запуск при входе пользователя."""
        ...

    def set_enabled(self, enabled: bool) -> None:
        """Установить состояние автозапуска; ошибку записи сообщить исключением."""
        ...


class ProfileLockProtocol(Protocol):
    def acquire(self) -> bool:
        """Неблокирующе занять блокировку профиля; False означает, что она занята."""
        ...

    def release(self) -> None:
        """Освободить ранее занятую блокировку."""
        ...


class SystemModule(Protocol):
    def ProfileLock(self, profile_id: str) -> ProfileLockProtocol:
        """Создать блокировку, общую для процессов приложения."""
        ...

    def inhibit_sleep(self, reason: str) -> Any:
        """Запретить сон; вернуть платформенный токен для снятия запрета или None."""
        ...

    def release_inhibit(self, token: Any) -> None: ...

    def syncfs(self, path: str) -> None:
        """Сбросить данные файловой системы; системные ошибки могут поднять OSError."""
        ...

    def free_bytes(self, path: str) -> int:
        """Вернуть доступные пользователю байты, поднять OSError при ошибке."""
        ...


class PathsModule(Protocol):
    def runtime_dir(self) -> str:
        """Вернуть каталог временных данных и блокировок."""
        ...

    def config_dir(self) -> str: ...
    def state_dir(self) -> str: ...
    def runtime_dir(self) -> str: ...
    def restore_backup_root(self) -> str: ...


class DepsModule(Protocol):
    def run_checks(self) -> list[Any]:
        """Вернуть проверки зависимостей с флагом обязательности и пояснением."""
        ...


class VaultModule(Protocol):
    CIPHER_SUFFIX: str

    def availability(self) -> tuple[bool, str]:
        """Вернуть наличие реализации и короткую причину недоступности."""
        ...

    def cipher_dir(self, mount_point: str, root: str) -> str: ...
    def plain_dir(self, profile_id: str) -> str: ...
    def is_initialized(self, cipher: str) -> bool: ...
    def is_open(self, cipher: str, plain: str) -> bool: ...
    def mount(self, cipher: str, plain: str, password: str) -> None: ...
    def unmount(self, plain: str, lazy: bool = False) -> None: ...


# Обязательные аргументы функций. Дополнительные необязательные параметры
# реализации разрешены, а несовпадение имён обязательных аргументов — нет.
REQUIRED_PARAMETERS = {
    "devices.list_volumes": (),
    "devices.uuid_conflicts": ("volumes",),
    "devices.eject": ("volume",),
    "devices.DeviceMonitor": (),
    "devices.DeviceMonitor.volumes": (),
    "devices.DeviceMonitor.get": ("identifier",),
    "devices.DeviceMonitor.find_by_uuid": ("uuid",),
    "devices.DeviceMonitor.refresh": (),
    "devices.DeviceMonitor.close": (),
    "devices.DeviceMonitor.set_show_all": ("enabled",),
    "devices.DeviceMonitor.subscribe": ("callback",),
    "mounts.verify_mount": ("mount_point", "expected_uuid"),
    "secrets.service_state": (),
    "secrets.lookup": ("profile_id",),
    "secrets.store": ("profile_id", "label", "password"),
    "secrets.clear": ("profile_id",),
    "autostart.is_enabled": (),
    "autostart.set_enabled": ("enabled",),
    "system.ProfileLock": ("profile_id",),
    "system.inhibit_sleep": ("reason",),
    "system.release_inhibit": ("token",),
    "system.syncfs": ("path",),
    "system.free_bytes": ("path",),
    "paths.config_dir": (), "paths.state_dir": (), "paths.runtime_dir": (),
    "paths.restore_backup_root": (),
    "deps.run_checks": (),
    "vault.availability": (),
    "vault.availability_cached": (),
    "vault.init": ("cipher", "password"),
    "vault.cipher_dir": ("mount_point", "root"),
    "vault.mounted_source": ("plain",),
    "vault.stale_mounts": (),
    "vault.plain_dir": ("profile_id",),
    "vault.is_initialized": ("cipher",),
    "vault.is_open": ("cipher", "plain"),
    "vault.mount": ("cipher", "plain", "password"),
    "vault.unmount": ("plain",),
}

# GTK-интерфейс Linux 1.x: эти операции принимают Linux-клиент и callback,
# потому что GTK-монитор и его сигналы остаются платформенной деталью.
GTK_REQUIRED_PARAMETERS = {
    "devices.collect_volumes": ("client",),
    "devices.free_space": ("path",),
    "devices.current_mount_point": ("client", "volume"),
    "devices.mount_sync": ("client", "volume"),
    "devices.unmount_sync": ("client", "volume"),
    "devices.mount_async": ("client", "volume", "callback"),
    "devices.unlock_async": ("client", "volume", "passphrase", "callback"),
    "devices.is_wrong_passphrase": ("exc",),
    "devices.eject_sync": ("client", "volume"),
    "mounts.parent_is_user_writable": ("mount_point",),
    "autostart.autostart_path": (),
    "paths.theme_dirs": (),
}

# Linux-вспомогательные функции, используемые тестами и самой Linux-реализацией.
LINUX_REQUIRED_PARAMETERS = {
    "mounts.parse_mountinfo": ("text",),
    "mounts.read_mountinfo": (),
    "mounts.find_mount": ("mount_point",),
    "mounts.device_for_uuid": ("uuid",),
    "paths.config_home": (), "paths.state_home": (), "paths.data_home": (),
    "paths.autostart_dir": (),
    "deps.which": ("name",),
    "deps.gir_available": ("namespace", "version"),
    "deps.dbus_name_available": ("bus_type", "name"),
    "deps.tool_version": ("argv", "pattern"),
}
