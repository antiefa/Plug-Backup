"""Метка профиля и копия профиля в корне копии на носителе.

<корень>/.plugbackup/id            — метка: ID профиля и заданий, без путей на ПК (всегда)
<корень>/.plugbackup/profile.json  — копия профиля (только при включённом флажке)

Функции работают с уже проверенной папкой корня копии (root_dir) и ничего не знают о носителях:
проверку, что root_dir лежит на нужном смонтированном носителе, делает core.binding.
"""

import os
from dataclasses import dataclass

from plugbackup.core.profiles import Profile, device_dest_paths, export_profile
from plugbackup.core.storage import StorageError, load_json, save_json

SERVICE_DIR = ".plugbackup"
MARKER_FILE = "id"
PROFILE_COPY_FILE = "profile.json"
MARKER_FORMAT = "plugbackup-marker"
MARKER_VERSION = 1

# Состояния корня копии
ROOT_MISSING = "missing"                  # папки корня нет
ROOT_EMPTY = "empty"                      # папка есть, пустая (или только наша служебная папка без метки)
ROOT_MATCH = "match"                      # метка этого профиля
ROOT_FOREIGN_DATA = "data-without-marker"  # есть данные, метки нет
ROOT_OTHER_PROFILE = "other-profile"      # метка другого профиля
ROOT_CORRUPT = "corrupt"                  # метка не читается


class MarkerError(Exception):
    pass


@dataclass(frozen=True)
class Marker:
    profile_id: str
    jobs: dict[str, str]  # ID задания → папка назначения


def service_dir(root_dir: str) -> str:
    return os.path.join(root_dir, SERVICE_DIR)


def marker_path(root_dir: str) -> str:
    return os.path.join(service_dir(root_dir), MARKER_FILE)


def profile_copy_path(root_dir: str) -> str:
    return os.path.join(service_dir(root_dir), PROFILE_COPY_FILE)


def read_marker(root_dir: str) -> Marker | None:
    try:
        data = load_json(marker_path(root_dir))
    except StorageError as exc:
        raise MarkerError(str(exc)) from exc
    if data is None:
        return None
    if data.get("format") != MARKER_FORMAT or not isinstance(data.get("version"), int):
        raise MarkerError("not a Plug & Backup marker")
    profile_id = data.get("profile_id")
    jobs = data.get("jobs", {})
    if not isinstance(profile_id, str) or not profile_id or not isinstance(jobs, dict) \
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in jobs.items()):
        raise MarkerError("marker has an invalid structure")
    return Marker(profile_id, dict(jobs))


def write_marker(root_dir: str, profile: Profile) -> None:
    data = {
        "format": MARKER_FORMAT,
        "version": MARKER_VERSION,
        "profile_id": profile.id,
        "jobs": device_dest_paths(profile),
    }
    save_json(marker_path(root_dir), data, backup=False)


def write_profile_copy(root_dir: str, profile: Profile) -> None:
    save_json(profile_copy_path(root_dir), export_profile(profile), backup=False)


def remove_profile_copy(root_dir: str) -> bool:
    try:
        os.unlink(profile_copy_path(root_dir))
    except FileNotFoundError:
        return False
    return True


def read_profile_copy(root_dir: str) -> dict | None:
    """Сырые данные копии профиля (импорт — через profiles.import_profile)."""
    try:
        return load_json(profile_copy_path(root_dir))
    except StorageError as exc:
        raise MarkerError(str(exc)) from exc


def _has_user_data(root_dir: str) -> bool:
    """В корне есть что-то кроме нашей служебной папки."""
    with os.scandir(root_dir) as entries:
        return any(entry.name != SERVICE_DIR for entry in entries)


def root_state(root_dir: str, profile_id: str) -> str:
    if not os.path.isdir(root_dir):
        return ROOT_MISSING
    try:
        marker = read_marker(root_dir)
    except MarkerError:
        return ROOT_CORRUPT
    if marker is not None:
        return ROOT_MATCH if marker.profile_id == profile_id else ROOT_OTHER_PROFILE
    return ROOT_FOREIGN_DATA if _has_user_data(root_dir) else ROOT_EMPTY


def sync_device_files(root_dir: str, profile: Profile) -> None:
    """Записать метку и копию профиля (или убрать копию, если флажок выключен).

    Вызывающий отвечает за то, что root_dir — проверенный корень копии на нужном носителе
    и что состояние корня позволяет запись (missing / empty / match / подтверждённое принятие).
    """
    os.makedirs(service_dir(root_dir), exist_ok=True)
    write_marker(root_dir, profile)
    if profile.store_profile_on_device:
        write_profile_copy(root_dir, profile)
    else:
        remove_profile_copy(root_dir)
