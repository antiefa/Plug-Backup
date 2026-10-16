"""Чистые проверки атрибутов Windows для тестирования на разных системах."""
from __future__ import annotations

FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000


def file_attributes(st: object) -> int:
    """Прочитать битовые атрибуты Windows или вернуть ноль на другой ОС."""
    return int(getattr(st, "st_file_attributes", 0))


def is_hidden(st: object) -> bool:
    """Проверить атрибут Hidden у результата stat."""
    return bool(file_attributes(st) & FILE_ATTRIBUTE_HIDDEN)


def is_reparse_point(st: object) -> bool:
    """Проверить атрибут или тег точки повторной обработки."""
    return bool(file_attributes(st) & FILE_ATTRIBUTE_REPARSE_POINT or getattr(st, "st_reparse_tag", 0))


def is_cloud_only(st: object) -> bool:
    """Определить файл OneDrive, чтение которого может запустить загрузку."""
    return bool(file_attributes(st) & (FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS))
