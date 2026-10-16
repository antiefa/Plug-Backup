"""Носители через UDisks2: какие разделы показывать, их сведения, слежение за изменениями, монтирование.

Носитель (Volume) — раздел с файловой системой или LUKS-контейнер. Для открытого
LUKS сведения о файловой системе берутся из расшифрованного устройства, а привязка
профиля идёт к UUID контейнера. Разблокировка — unlock_async.
"""

import os
from dataclasses import dataclass

import gi

gi.require_version("UDisks", "2.0")
from gi.repository import GLib, GObject, UDisks  # noqa: E402

# Разделы, смонтированные сюда, — системные; такие не показываются никогда.
SYSTEM_MOUNT_POINTS = frozenset({"/", "/boot", "/boot/efi", "/efi", "/home", "/usr", "/var", "/opt", "/srv", "/tmp"})

LUKS_TYPE = "crypto_LUKS"


@dataclass(frozen=True)
class Volume:
    object_path: str
    device: str
    uuid: str                       # UUID ФС, а для LUKS — UUID контейнера (идентификатор привязки)
    inner_uuid: str                 # UUID ФС внутри открытого LUKS, иначе ""
    fs_type: str                    # vfat / exfat / ntfs / ext4 …; "" у закрытого LUKS
    fs_version: str                 # например FAT32 / FAT16 для vfat
    label: str
    size: int
    mount_points: tuple[str, ...]
    encrypted: bool
    locked: bool
    filesystem_path: str            # объект UDisks2 с интерфейсом Filesystem; "" если монтировать нечего
    drive_path: str
    drive_vendor: str
    drive_model: str
    drive_serial: str
    drive_id: str
    connection_bus: str
    removable: bool
    sibling_id: str
    is_loop: bool
    loop_backing_file: str

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


def _str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.rstrip(b"\0").decode("utf-8", "surrogateescape")
    return str(value)


def _mount_points(filesystem) -> tuple[str, ...]:
    if filesystem is None:
        return ()
    return tuple(_str(p) for p in (filesystem.props.mount_points or ()))


def _is_system(block, mount_points: tuple[str, ...]) -> bool:
    if block.props.id_usage == "other" and block.props.id_type == "swap":
        return True
    return any(mp in SYSTEM_MOUNT_POINTS for mp in mount_points)


def _volume_from_object(client: UDisks.Client, obj, uid: int, show_all: bool) -> Volume | None:
    block = obj.get_block()
    if block is None or block.props.hint_ignore:
        return None
    if block.props.crypto_backing_device not in ("", "/"):
        return None  # расшифрованное устройство учитывается в своём контейнере
    usage, fs_type, fs_version = block.props.id_usage, block.props.id_type, block.props.id_version
    encrypted = usage == "crypto" and fs_type == LUKS_TYPE
    if usage != "filesystem" and not encrypted:
        return None

    filesystem_obj = obj
    inner_uuid = ""
    locked = False
    if encrypted:
        cleartext = client.get_cleartext_block(block)
        locked = cleartext is None
        filesystem_obj = None
        fs_type = fs_version = ""
        if cleartext is not None:
            cleartext_obj = client.get_object(cleartext.get_object_path())
            if cleartext_obj is not None and cleartext_obj.get_filesystem() is not None:
                filesystem_obj = cleartext_obj
                fs_type = cleartext.props.id_type
                fs_version = cleartext.props.id_version
                inner_uuid = cleartext.props.id_uuid

    filesystem = filesystem_obj.get_filesystem() if filesystem_obj is not None else None
    mount_points = _mount_points(filesystem)
    if _is_system(block, mount_points):
        return None

    drive = client.get_drive_for_block(block)
    loop = obj.get_loop()
    own_loop = loop is not None and loop.props.setup_by_uid == uid
    if drive is not None:
        removable = bool(drive.props.removable or drive.props.media_removable
                         or drive.props.connection_bus == "usb")
    else:
        removable = own_loop  # образ, подключённый самим пользователем, ведёт себя как сменный носитель
    if not removable and not show_all:
        return None
    if block.props.hint_system and not removable and not show_all:
        return None

    return Volume(
        object_path=obj.get_object_path(),
        device=_str(block.props.preferred_device) or _str(block.props.device),
        uuid=block.props.id_uuid or "",
        inner_uuid=inner_uuid or "",
        fs_type=fs_type or "",
        fs_version=fs_version or "",
        label=block.props.id_label or "",
        size=int(block.props.size),
        mount_points=mount_points,
        encrypted=encrypted,
        locked=locked,
        filesystem_path=filesystem_obj.get_object_path() if filesystem is not None else "",
        drive_path=drive.get_object_path() if drive is not None else "",
        drive_vendor=(drive.props.vendor or "").strip() if drive is not None else "",
        drive_model=(drive.props.model or "").strip() if drive is not None else "",
        drive_serial=(drive.props.serial or "") if drive is not None else "",
        drive_id=(drive.props.id or "") if drive is not None else "",
        connection_bus=(drive.props.connection_bus or "") if drive is not None else "",
        removable=removable,
        sibling_id=(drive.props.sibling_id or "") if drive is not None else "",
        is_loop=loop is not None,
        loop_backing_file=_str(loop.props.backing_file) if loop is not None else "",
    )


def collect_volumes(client: UDisks.Client, show_all: bool = False) -> list[Volume]:
    uid = os.getuid()
    volumes = []
    for obj in client.get_object_manager().get_objects():
        volume = _volume_from_object(client, obj, uid, show_all)
        if volume is not None:
            volumes.append(volume)
    volumes.sort(key=lambda v: (v.drive_path or "~", v.device))
    return volumes


def uuid_conflicts(volumes: list[Volume]) -> set[str]:
    """UUID, встречающиеся у нескольких подключённых носителей или пустые."""
    seen: dict[str, int] = {}
    for v in volumes:
        seen[v.uuid] = seen.get(v.uuid, 0) + 1
    return {uuid for uuid, count in seen.items() if count > 1 and uuid}


def free_space(path: str) -> tuple[int, int] | None:
    """(свободно для пользователя, всего) в байтах, или None, если путь недоступен."""
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    return st.f_bavail * st.f_frsize, st.f_blocks * st.f_frsize


def _filesystem(client: UDisks.Client, volume: Volume):
    if not volume.filesystem_path:
        raise GLib.Error("volume has no mountable filesystem")
    obj = client.get_object(volume.filesystem_path)
    filesystem = obj.get_filesystem() if obj is not None else None
    if filesystem is None:
        raise GLib.Error("filesystem interface is gone")
    return filesystem


def _no_options() -> GLib.Variant:
    return GLib.Variant("a{sv}", {})


def _already_mounted(exc: GLib.Error) -> bool:
    return exc.matches(UDisks.error_quark(), UDisks.Error.ALREADY_MOUNTED)


def current_mount_point(client: UDisks.Client, volume: Volume) -> str | None:
    """Точка монтирования прямо сейчас: Cinnamon может смонтировать носитель сам в любой момент."""
    from plugbackup.core.mounts import read_mountinfo

    client.settle()
    filesystem = _filesystem(client, volume)
    points = _mount_points(filesystem)
    if points:
        return points[0]
    block = client.get_object(volume.filesystem_path).get_block()
    device = os.path.realpath(_str(block.props.device)) if block is not None else ""
    for entry in read_mountinfo():
        if entry.root == "/" and os.path.realpath(entry.source) == device:
            return entry.mount_point
    return None


def mount_sync(client: UDisks.Client, volume: Volume) -> str:
    """Смонтировать носитель (как это делает файловый менеджер) и вернуть точку монтирования.

    Если носитель уже смонтирован (в том числе одновременно с вызовом), возвращается текущая точка.
    """
    existing = current_mount_point(client, volume)
    if existing:
        return existing
    try:
        return _filesystem(client, volume).call_mount_sync(_no_options(), None)
    except GLib.Error as exc:
        if _already_mounted(exc):
            point = current_mount_point(client, volume)
            if point:
                return point
        raise


def unmount_sync(client: UDisks.Client, volume: Volume) -> None:
    _filesystem(client, volume).call_unmount_sync(_no_options(), None)


def mount_async(client: UDisks.Client, volume: Volume, callback) -> None:
    """callback(mount_point: str | None, error: GLib.Error | None) — в главном цикле GLib."""
    try:
        filesystem = _filesystem(client, volume)
    except GLib.Error as exc:
        GLib.idle_add(lambda: callback(None, exc) and False)
        return

    def done(proxy, result):
        try:
            callback(proxy.call_mount_finish(result), None)
        except GLib.Error as exc:
            point = current_mount_point(client, volume) if _already_mounted(exc) else None
            callback(point, None if point else exc)

    filesystem.call_mount(_no_options(), None, done)


def _quiet_options() -> GLib.Variant:
    # Без окон polkit: открыть свой сменный носитель можно и так, а если нужны права — пусть будет ошибка.
    return GLib.Variant("a{sv}", {"auth.no_user_interaction": GLib.Variant("b", True)})


def unlock_async(client: UDisks.Client, volume: Volume, passphrase: str, callback) -> None:
    """Открыть LUKS-контейнер. callback(cleartext_path: str | None, error: GLib.Error | None)
    вызывается в главном цикле GLib. Монтирование — отдельно, когда появится файловая система."""
    obj = client.get_object(volume.object_path)
    encrypted = obj.get_encrypted() if obj is not None else None
    if encrypted is None:
        exc = GLib.Error("the drive is not an encrypted container")
        GLib.idle_add(lambda: callback(None, exc) and False)
        return

    def done(proxy, result):
        try:
            callback(proxy.call_unlock_finish(result), None)
        except GLib.Error as exc:
            callback(None, exc)

    encrypted.call_unlock(passphrase, _quiet_options(), None, done)


def is_wrong_passphrase(exc: GLib.Error) -> bool:
    """UDisks не даёт отдельного кода ошибки для неверного пароля — узнаём по тексту libblockdev/cryptsetup."""
    if exc.matches(UDisks.error_quark(), UDisks.Error.NOT_AUTHORIZED) \
            or exc.matches(UDisks.error_quark(), UDisks.Error.NOT_AUTHORIZED_CAN_OBTAIN):
        return False
    text = (exc.message or "").lower()
    return any(s in text for s in ("passphrase", "no key available", "operation not permitted", "bad password"))


class DeviceMonitor(GObject.Object):
    """Следит за носителями и сообщает об изменениях.

    Сигналы: volume-added(Volume), volume-removed(str object_path),
    volume-changed(Volume), volumes-changed() — после любого набора изменений.
    """

    __gsignals__ = {
        "volume-added": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "volume-removed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "volume-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "volumes-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, show_all: bool = False, client: UDisks.Client | None = None):
        super().__init__()
        self.client = client or UDisks.Client.new_sync(None)
        self._show_all = show_all
        self._volumes: dict[str, Volume] = {
            v.object_path: v for v in collect_volumes(self.client, show_all)
        }
        self._handler = self.client.connect("changed", lambda _client: self.refresh())

    @property
    def show_all(self) -> bool:
        return self._show_all

    def set_show_all(self, value: bool) -> None:
        if value != self._show_all:
            self._show_all = value
            self.refresh()

    def volumes(self) -> list[Volume]:
        return sorted(self._volumes.values(), key=lambda v: (v.drive_path or "~", v.device))

    def get(self, object_path: str) -> Volume | None:
        return self._volumes.get(object_path)

    def find_by_uuid(self, uuid: str) -> list[Volume]:
        return [v for v in self._volumes.values() if v.uuid == uuid]

    def refresh(self) -> None:
        current = {v.object_path: v for v in collect_volumes(self.client, self._show_all)}
        old = self._volumes
        self._volumes = current
        changed = False
        for path in old.keys() - current.keys():
            self.emit("volume-removed", path)
            changed = True
        for path in current.keys() - old.keys():
            self.emit("volume-added", current[path])
            changed = True
        for path in current.keys() & old.keys():
            if current[path] != old[path]:
                self.emit("volume-changed", current[path])
                changed = True
        if changed:
            self.emit("volumes-changed")

    def close(self) -> None:
        if self._handler:
            self.client.disconnect(self._handler)
            self._handler = 0


EJECT_POWERED_OFF = "powered-off"
EJECT_UNMOUNTED_ONLY = "unmounted-only"


def _drive_busy(client: UDisks.Client, volume: Volume) -> bool:
    """На том же устройстве или соседнем (тот же кардридер, Drive.SiblingId) есть другие подключённые тома."""
    drive_obj = client.get_object(volume.drive_path)
    drive = drive_obj.get_drive() if drive_obj is not None else None
    sibling = drive.props.sibling_id if drive is not None else ""
    for obj in client.get_object_manager().get_objects():
        block = obj.get_block()
        if block is None:
            continue
        other_drive = client.get_drive_for_block(block)
        if other_drive is None:
            continue
        same = other_drive.get_object_path() == volume.drive_path or (
            sibling and other_drive.props.sibling_id == sibling)
        if not same:
            continue
        filesystem = obj.get_filesystem()
        if filesystem is not None and filesystem.props.mount_points:
            return True
        encrypted = obj.get_encrypted()
        if encrypted is not None and encrypted.props.cleartext_device not in ("", "/"):
            return True
    return False


def eject_sync(client: UDisks.Client, volume: Volume) -> str:
    """Безопасное извлечение: отключить том, закрыть LUKS, выключить питание устройства,
    только если на нём и на соседних устройствах больше ничего не подключено."""
    if volume.mounted and volume.filesystem_path:
        _filesystem(client, volume).call_unmount_sync(_no_options(), None)
    if volume.encrypted and not volume.locked:
        container = client.get_object(volume.object_path)
        if container is not None and container.get_encrypted() is not None:
            container.get_encrypted().call_lock_sync(_no_options(), None)
    client.settle()
    if not volume.drive_path:
        return EJECT_UNMOUNTED_ONLY
    drive_obj = client.get_object(volume.drive_path)
    drive = drive_obj.get_drive() if drive_obj is not None else None
    if drive is None or not drive.props.can_power_off or _drive_busy(client, volume):
        return EJECT_UNMOUNTED_ONLY
    drive.call_power_off_sync(_no_options(), None)
    return EJECT_POWERED_OFF
