"""Связь профиля с подключённым носителем: проверенный путь к корню копии и запись метки.

Любая запись на носитель идёт только через write_device_files и только после проверки,
что путь — точка монтирования носителя этого профиля и корень копии — обычная папка
на нём, а не ссылка куда-то ещё. У профиля с зашифрованной папкой (gocryptfs) корень
копии — открытый вид <корень>.enc/, подключённый во временном каталоге профиля; открывает
его интерфейс (нужен пароль), здесь — только проверка, что открыта именно папка с этого носителя.
"""
from __future__ import annotations

import os

from plugbackup import plat
from plugbackup.core import marker
from plugbackup.core.volume import uuid_matches
from plugbackup.core.profiles import Profile, validate_name_component


class BindingError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _verified_mount_point(profile: Profile, volume) -> str:
    if not profile.device.uuid or not uuid_matches(volume, profile.device.uuid):
        raise BindingError("not-this-drive")
    if volume.locked:
        raise BindingError("locked")
    if not volume.mounted:
        raise BindingError("not-mounted")
    if getattr(volume, "uuid_partial", False):
        root = os.path.join(volume.mount_point, profile.root)
        if marker.root_state(root, profile.id) != marker.ROOT_MATCH:
            raise BindingError("uuid-conflict")
    check = plat.mounts.verify_mount(volume.mount_point, volume.fs_uuid)
    if not check.ok:
        raise BindingError(check.reason.split(":")[0])
    return volume.mount_point


def cipher_dir_for(profile: Profile, volume) -> str:
    """Проверенный путь к зашифрованной папке профиля на носителе (может ещё не существовать)."""
    try:
        vault = plat.vault
    except NotImplementedError as exc:
        raise BindingError("encryption-unavailable") from exc
    mount_point = _verified_mount_point(profile, volume)
    if validate_name_component(profile.root) or validate_name_component(profile.root + vault.CIPHER_SUFFIX):
        raise BindingError("root-invalid")
    cipher = vault.cipher_dir(mount_point, profile.root)
    if os.path.islink(cipher):
        raise BindingError("root-is-link")
    if os.path.lexists(cipher) and not os.path.isdir(cipher):
        raise BindingError("root-not-folder")
    if os.path.exists(cipher) and os.path.realpath(cipher) != os.path.join(
            os.path.realpath(mount_point), profile.root + vault.CIPHER_SUFFIX):
        raise BindingError("root-is-link")
    return cipher


def plain_root_dir_for(profile: Profile, volume) -> str:
    """Корень обычной (незашифрованной) копии <точка монтирования>/<корень> — без учёта режима профиля."""
    mount_point = _verified_mount_point(profile, volume)
    if validate_name_component(profile.root):
        raise BindingError("root-invalid")
    root = os.path.join(mount_point, profile.root)
    for path in (root, marker.service_dir(root)):
        if os.path.islink(path):
            raise BindingError("root-is-link")
        if os.path.lexists(path) and not os.path.isdir(path):
            raise BindingError("root-not-folder")
    if os.path.exists(root) and os.path.realpath(root) != os.path.join(os.path.realpath(mount_point), profile.root):
        raise BindingError("root-is-link")
    return root


def root_dir_for(profile: Profile, volume) -> str:
    if profile.encryption.mode == "gocryptfs":
        try:
            vault = plat.vault
        except NotImplementedError as exc:
            raise BindingError("encryption-unavailable") from exc
        cipher = cipher_dir_for(profile, volume)
        if not vault.is_initialized(cipher):
            raise BindingError("vault-missing")
        plain = vault.plain_dir(profile.id)
        if not vault.is_open(cipher, plain):
            raise BindingError("vault-closed")
        service = marker.service_dir(plain)
        if os.path.islink(service) or (os.path.lexists(service) and not os.path.isdir(service)):
            raise BindingError("root-not-folder")
        return plain
    mount_point = _verified_mount_point(profile, volume)
    if validate_name_component(profile.root):
        raise BindingError("root-invalid")
    root = os.path.join(mount_point, profile.root)
    for path in (root, marker.service_dir(root)):
        if os.path.islink(path):
            raise BindingError("root-is-link")
        if os.path.lexists(path) and not os.path.isdir(path):
            raise BindingError("root-not-folder")
    if os.path.exists(root) and os.path.realpath(root) != os.path.join(os.path.realpath(mount_point), profile.root):
        raise BindingError("root-is-link")
    return root


def inspect(profile: Profile, volume) -> str:
    """Состояние корня копии на носителе (marker.ROOT_*)."""
    return marker.root_state(root_dir_for(profile, volume), profile.id)


def write_device_files(profile: Profile, volume, accept_existing: bool = False) -> str:
    """Записать метку (и копию профиля по флажку). Возвращает состояние корня до записи.

    accept_existing — пользователь подтвердил «Принять существующие данные как копию этого профиля».
    """
    root = root_dir_for(profile, volume)
    state = marker.root_state(root, profile.id)
    if state in (marker.ROOT_OTHER_PROFILE, marker.ROOT_CORRUPT):
        raise BindingError(state)
    if state == marker.ROOT_FOREIGN_DATA and not accept_existing:
        raise BindingError(state)
    _verified_mount_point(profile, volume)  # повторно — непосредственно перед записью
    marker.sync_device_files(root, profile)
    return state
