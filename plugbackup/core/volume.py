"""Нейтральная модель подключённого тома."""
from __future__ import annotations

import os
from dataclasses import dataclass
@dataclass(frozen=True)
class EjectOutcome:
    ok: bool
    powered_off: bool
    message: str = ""
    blocked_by: str = ""


@dataclass(frozen=True)
class Volume:
    object_path: str  # Устойчивый идентификатор тома в платформенном слое.
    device: str  # Путь к устройству или его нейтральное представление.
    uuid: str  # Идентификатор файловой системы или контейнера для привязки профиля.
    inner_uuid: str  # Идентификатор ФС внутри разблокированного контейнера; иначе пустая строка.
    fs_type: str  # Тип файловой системы, например vfat, exfat или ntfs.
    fs_version: str  # Версия/вариант файловой системы, например FAT32; иначе пустая строка.
    label: str  # Метка тома.
    size: int  # Размер тома в байтах.
    mount_points: tuple[str, ...]  # Точки подключения; пустой кортеж, если том не подключён.
    encrypted: bool  # Является ли том зашифрованным контейнером.
    locked: bool  # Заблокирован ли контейнер.
    filesystem_path: str  # Идентификатор объекта ФС платформы; пустой, если отдельный объект неприменим.
    drive_path: str  # Идентификатор физического устройства; пустой, если оно неприменимо или неизвестно.
    drive_vendor: str  # Производитель устройства; пустая строка, если неизвестен.
    drive_model: str  # Модель устройства; пустая строка, если неизвестна.
    drive_serial: str  # Серийный номер устройства; пустая строка, если неизвестен.
    drive_id: str  # Дополнительный идентификатор устройства; пустая строка, если неприменим.
    connection_bus: str  # Тип соединения, например usb; пустая строка, если неизвестен или неприменим.
    removable: bool  # Считает ли платформа том сменным.
    sibling_id: str  # Идентификатор связанных томов/слотов; пустой, если группировка неприменима.
    is_loop: bool  # Является ли том образом файла; False на платформах без такого понятия.
    loop_backing_file: str  # Файл-образ; пустая строка, если том не основан на файле.
    access_paths: tuple[str, ...] = ()  # Пути для отображения; запись выполняется по mount_points.
    uuid_partial: bool = False  # UUID содержит только младшие 32 бита.
    bus_unknown: bool = False  # Шину не удалось определить.

    @property
    def mount_point(self) -> str:
        return self.mount_points[0] if self.mount_points else ""

    @property
    def mounted(self) -> bool:
        return bool(self.mount_points)

    @property
    def fs_uuid(self) -> str:
        """UUID файловой системы, по которому проверяется точка монтирования."""
        return self.inner_uuid if self.encrypted else self.uuid

    @property
    def display_name(self) -> str:
        if self.label:
            return self.label
        model = " ".join(p for p in (self.drive_vendor, self.drive_model) if p).strip()
        if model:
            return model
        if self.is_loop and self.loop_backing_file:
            return os.path.basename(self.loop_backing_file)
        return os.path.basename(self.device)


def uuid_conflicts(volumes: list[Volume]) -> set[str]:
    """Вернуть непустые UUID, встречающиеся у нескольких подключённых томов."""
    seen = {}
    for volume in volumes:
        seen[volume.uuid] = seen.get(volume.uuid, 0) + 1
    return {uuid for uuid, count in seen.items() if uuid and count > 1}


def uuid_matches(volume: Volume, expected_uuid: str) -> bool:
    """Полное совпадение UUID или совпадение младших битов NTFS."""
    actual = volume.uuid
    expected = expected_uuid
    if not actual or not expected:
        return False
    if not getattr(volume, "uuid_partial", False):
        return actual == expected
    if getattr(volume, "fs_type", "").lower() != "ntfs":
        return False
    try:
        return int(actual, 16) & 0xffffffff == int(expected, 16) & 0xffffffff
    except ValueError:
        return False
