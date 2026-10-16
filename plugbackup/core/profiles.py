"""Профили и задания: модель, хранение, проверки, экспорт/импорт.

Модуль не обращается к носителям — только к файлам настроек и (при проверке) к папкам-источникам.
"""
from __future__ import annotations

import copy
import os
import uuid as uuidlib
from dataclasses import asdict, dataclass, field

from plugbackup.core.config import config_dir
from plugbackup.core.storage import StorageError, load_json, save_json
from plugbackup.i18n import _

SCHEMA = 1
EXPORT_FORMAT = "plugbackup-profile"
EXPORT_VERSION = 1

MODES = ("mirror_versions", "mirror", "accumulate")
COMPARE_MODES = ("size_mtime", "checksum")
SYMLINK_MODES = ("auto", "skip", "copy_target")
ENCRYPTION_MODES = ("none", "luks", "gocryptfs")

DEFAULT_ROOT = "PlugBackup"
# Служебные имена на носителе: запрещены на любой глубине пути назначения и как корень копии.
RESERVED_NAMES = frozenset({".versions", ".plugbackup", ".rsync-partial"})
# Источник не может быть системной папкой целиком.
FORBIDDEN_SOURCES = frozenset({"/", "/proc", "/sys", "/dev", "/run", "/boot", "/tmp", "/var", "/usr", "/etc"})

# Готовый набор исключений «Разработка».
DEV_EXCLUDES = ("node_modules/", ".venv/", "venv/", "build/", "dist/", "__pycache__/", ".cache/", "target/")


def new_id() -> str:
    return str(uuidlib.uuid4())


@dataclass
class Job:
    id: str = field(default_factory=new_id)
    enabled: bool = True
    in_auto: bool = True
    source: str = ""
    dest: str = ""
    mode: str = "mirror_versions"
    compare: str = "size_mtime"
    excludes: list[str] = field(default_factory=list)
    use_global_excludes: bool = True
    max_size_mb: int | None = None
    skip_hidden: bool = False
    symlinks: str = "auto"
    one_file_system: bool = True


@dataclass
class DeviceBinding:
    uuid: str = ""
    fs: str = ""
    label: str = ""
    serial: str = ""
    model: str = ""
    luks_inner_uuid: str | None = None


@dataclass
class AutoSettings:
    enabled: bool = False
    countdown_sec: int = 10
    min_interval_hours: int | None = None
    eject_after_success: bool = False


@dataclass
class Encryption:
    mode: str = "none"
    remember_password: bool = False


@dataclass
class Thresholds:
    percent: int = 30
    files: int = 500
    hard_max: int = 5000


@dataclass
class Profile:
    id: str = field(default_factory=new_id)
    name: str = ""
    device: DeviceBinding = field(default_factory=DeviceBinding)
    root: str = DEFAULT_ROOT
    store_profile_on_device: bool = False
    encryption: Encryption = field(default_factory=Encryption)
    auto: AutoSettings = field(default_factory=AutoSettings)
    versions_keep_days: int = 30
    mass_delete_threshold: Thresholds = field(default_factory=Thresholds)
    jobs: list[Job] = field(default_factory=list)
    # Метку на носителе нужно обновить при следующем явном действии с подключённым носителем.
    device_sync_pending: bool = False

    @property
    def bound(self) -> bool:
        return bool(self.device.uuid)

    def job(self, job_id: str) -> Job | None:
        return next((j for j in self.jobs if j.id == job_id), None)


class ProfileFormatError(ValueError):
    pass


# ---------- преобразование в словарь и обратно ----------

def _pick(data: dict, key: str, kind, default):
    value = data.get(key, default)
    if kind is bool:
        return value if isinstance(value, bool) else default
    if kind is int:
        return value if isinstance(value, int) and not isinstance(value, bool) else default
    if kind is str:
        return value if isinstance(value, str) else default
    return value


def _opt_int(data: dict, key: str) -> int | None:
    value = data.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _choice(data: dict, key: str, choices: tuple, default: str) -> str:
    value = data.get(key, default)
    return value if value in choices else default


def job_from_dict(data: dict) -> Job:
    if not isinstance(data, dict):
        raise ProfileFormatError("job is not an object")
    defaults = Job(id="")
    job_id = _pick(data, "id", str, "")
    if not job_id:
        raise ProfileFormatError("job without id")
    excludes = data.get("excludes", [])
    if not isinstance(excludes, list) or not all(isinstance(e, str) for e in excludes):
        raise ProfileFormatError("job excludes must be a list of strings")
    return Job(
        id=job_id,
        enabled=_pick(data, "enabled", bool, defaults.enabled),
        in_auto=_pick(data, "in_auto", bool, defaults.in_auto),
        source=_pick(data, "source", str, ""),
        dest=_pick(data, "dest", str, ""),
        mode=_choice(data, "mode", MODES, defaults.mode),
        compare=_choice(data, "compare", COMPARE_MODES, defaults.compare),
        excludes=list(excludes),
        use_global_excludes=_pick(data, "use_global_excludes", bool, defaults.use_global_excludes),
        max_size_mb=_opt_int(data, "max_size_mb"),
        skip_hidden=_pick(data, "skip_hidden", bool, defaults.skip_hidden),
        symlinks=_choice(data, "symlinks", SYMLINK_MODES, defaults.symlinks),
        one_file_system=_pick(data, "one_file_system", bool, defaults.one_file_system),
    )


def profile_from_dict(data: dict) -> Profile:
    if not isinstance(data, dict):
        raise ProfileFormatError("profile is not an object")
    profile_id = _pick(data, "id", str, "")
    if not profile_id:
        raise ProfileFormatError("profile without id")
    sections = []
    for key in ("device", "auto", "encryption", "mass_delete_threshold"):
        value = data.get(key)
        if value is None:
            value = {}
        elif not isinstance(value, dict):
            raise ProfileFormatError(f"“{key}” must be an object")
        sections.append(value)
    dev, auto, enc, thr = sections
    jobs_data = data.get("jobs", [])
    if not isinstance(jobs_data, list):
        raise ProfileFormatError("jobs must be a list")
    jobs = [job_from_dict(j) for j in jobs_data]
    if len({j.id for j in jobs}) != len(jobs):
        raise ProfileFormatError("duplicate job ids")
    d_auto, d_thr = AutoSettings(), Thresholds()
    inner = dev.get("luks_inner_uuid")
    return Profile(
        id=profile_id,
        name=_pick(data, "name", str, ""),
        device=DeviceBinding(
            uuid=_pick(dev, "uuid", str, ""),
            fs=_pick(dev, "fs", str, ""),
            label=_pick(dev, "label", str, ""),
            serial=_pick(dev, "serial", str, ""),
            model=_pick(dev, "model", str, ""),
            luks_inner_uuid=inner if isinstance(inner, str) and inner else None,
        ),
        root=_pick(data, "root", str, DEFAULT_ROOT) or DEFAULT_ROOT,
        store_profile_on_device=_pick(data, "store_profile_on_device", bool, False),
        encryption=Encryption(
            mode=_choice(enc, "mode", ENCRYPTION_MODES, "none"),
            remember_password=_pick(enc, "remember_password", bool, False),
        ),
        auto=AutoSettings(
            enabled=_pick(auto, "enabled", bool, d_auto.enabled),
            countdown_sec=max(0, _pick(auto, "countdown_sec", int, d_auto.countdown_sec)),
            min_interval_hours=_opt_int(auto, "min_interval_hours"),
            eject_after_success=_pick(auto, "eject_after_success", bool, d_auto.eject_after_success),
        ),
        versions_keep_days=max(1, _pick(data, "versions_keep_days", int, 30)),
        mass_delete_threshold=Thresholds(
            percent=min(100, max(1, _pick(thr, "percent", int, d_thr.percent))),
            files=max(1, _pick(thr, "files", int, d_thr.files)),
            hard_max=max(1, _pick(thr, "hard_max", int, d_thr.hard_max)),
        ),
        jobs=jobs,
        device_sync_pending=_pick(data, "device_sync_pending", bool, False),
    )


def profile_to_dict(profile: Profile) -> dict:
    return asdict(profile)


# ---------- хранение ----------

def profiles_path() -> str:
    return os.path.join(config_dir(), "profiles.json")


class ProfilesStore:
    """Профили в файле; при повреждённом файле — пустой список и запрет записи."""

    def __init__(self, path: str | None = None):
        self.path = path or profiles_path()
        self.error: StorageError | None = None
        self.profiles: list[Profile] = []
        self.reload()

    def reload(self) -> None:
        self.error = None
        try:
            data = load_json(self.path) or {"schema": SCHEMA, "profiles": []}
            items = data.get("profiles", [])
            if not isinstance(items, list):
                raise ProfileFormatError("profiles must be a list")
            profiles = [profile_from_dict(p) for p in items]
            if len({p.id for p in profiles}) != len(profiles):
                raise ProfileFormatError("duplicate profile ids")
        except StorageError as exc:
            self.error = exc
            self.profiles = []
            return
        except ProfileFormatError as exc:
            self.error = StorageError(self.path, str(exc))
            self.profiles = []
            return
        self.profiles = profiles

    def save(self) -> bool:
        if self.error is not None:
            return False
        save_json(self.path, {"schema": SCHEMA, "profiles": [profile_to_dict(p) for p in self.profiles]})
        return True

    def get(self, profile_id: str) -> Profile | None:
        return next((p for p in self.profiles if p.id == profile_id), None)

    def find_by_uuid(self, device_uuid: str) -> list[Profile]:
        return [p for p in self.profiles if device_uuid and p.device.uuid == device_uuid]

    def find_by_volume(self, volume) -> list[Profile]:
        """Найти профили по полному UUID; частичный NTFS UUID требует своей метки."""
        from plugbackup.core import marker
        from plugbackup.core.volume import uuid_matches

        matches = []
        for profile in self.profiles:
            if not uuid_matches(volume, profile.device.uuid):
                continue
            if volume.uuid_partial:
                root = os.path.join(volume.mount_point, profile.root)
                if marker.root_state(root, profile.id) != marker.ROOT_MATCH:
                    continue
            matches.append(profile)
        return matches

    def upsert(self, profile: Profile) -> None:
        for i, existing in enumerate(self.profiles):
            if existing.id == profile.id:
                self.profiles[i] = profile
                return
        self.profiles.append(profile)

    def remove(self, profile_id: str) -> None:
        self.profiles = [p for p in self.profiles if p.id != profile_id]


# ---------- проверки ----------

@dataclass(frozen=True)
class Issue:
    level: str      # "error" — сохранить нельзя; "warning" — можно, но стоит знать
    code: str
    message: str


def _err(code: str, message: str) -> Issue:
    return Issue("error", code, message)


def _warn(code: str, message: str) -> Issue:
    return Issue("warning", code, message)


def normalize_dest(dest: str) -> str:
    """Относительный путь на носителе без лишних «/» и «.»."""
    parts = [p for p in dest.replace("\\", "/").split("/") if p not in ("", ".")]
    return "/".join(parts)


def default_dest(source: str) -> str:
    return os.path.basename(os.path.normpath(source)) if source else ""


def path_within(child: str, parent: str) -> bool:
    """child совпадает с parent или лежит внутри него (после разрешения ссылок)."""
    c, p = os.path.realpath(child), os.path.realpath(parent)
    try:
        return os.path.commonpath([c, p]) == p
    except ValueError:
        return False


def _dest_within(a: str, b: str) -> bool:
    a, b = normalize_dest(a), normalize_dest(b)
    return a == b or a.startswith(b + "/")


def validate_name_component(name: str) -> list[Issue]:
    if not name.strip():
        return [_err("root-empty", _("The backup folder name on the drive is empty."))]
    if "/" in name or "\\" in name or name in (".", ".."):
        return [_err("root-invalid", _("The backup folder on the drive must be a single folder name."))]
    if name in RESERVED_NAMES:
        return [_err("root-reserved", _("“%s” is a reserved service name.") % name)]
    return []


def validate_dest(dest: str) -> list[Issue]:
    norm = normalize_dest(dest)
    if not norm:
        return [_err("dest-empty", _("The destination folder on the drive is empty."))]
    if dest.replace("\\", "/").startswith("/"):
        return [_err("dest-absolute", _("The destination must be a folder inside the backup folder, not an absolute path."))]
    parts = norm.split("/")
    if ".." in parts:
        return [_err("dest-parent", _("The destination must not contain “..”."))]
    reserved = [p for p in parts if p in RESERVED_NAMES]
    if reserved:
        return [_err("dest-reserved", _("“%s” is a reserved service name.") % reserved[0])]
    return []


def validate_job(job: Job, profile: Profile, mount_point: str | None = None) -> list[Issue]:
    """Проверки задания. mount_point — где сейчас смонтирован носитель профиля, если известно."""
    issues: list[Issue] = []
    source = job.source
    if not source:
        issues.append(_err("source-empty", _("Choose a folder on this computer to back up.")))
    elif not os.path.isabs(source):
        issues.append(_err("source-relative", _("The source folder must be an absolute path.")))
    else:
        real = os.path.realpath(source)
        if real in FORBIDDEN_SOURCES:
            issues.append(_err("source-system", _("A system folder cannot be backed up as a whole.")))
        elif not os.path.exists(source):
            issues.append(_err("source-missing", _("The source folder does not exist.")))
        elif not os.path.isdir(source):
            issues.append(_err("source-not-dir", _("The source is not a folder.")))
        elif not os.access(source, os.R_OK | os.X_OK):
            issues.append(_err("source-unreadable", _("The source folder cannot be read.")))
        if mount_point:
            if path_within(source, mount_point):
                issues.append(_err("source-on-drive", _("The source folder is on the backup drive itself.")))
            elif path_within(mount_point, source):
                issues.append(_err("source-contains-drive",
                                   _("The source folder contains the backup drive; the copy would include itself.")))

    issues.extend(validate_dest(job.dest))
    for other in profile.jobs:
        if other.id == job.id:
            continue
        if normalize_dest(job.dest) and normalize_dest(other.dest) and (
                _dest_within(job.dest, other.dest) or _dest_within(other.dest, job.dest)):
            issues.append(_err("dest-overlap",
                               _("The destination overlaps with another job (“%s”).") % normalize_dest(other.dest)))
        if source and other.source and os.path.isabs(source) and os.path.isabs(other.source) and (
                path_within(source, other.source) or path_within(other.source, source)):
            issues.append(_warn("source-overlap",
                                _("The source overlaps with another job; some files will be copied twice.")))

    if job.mode == "mirror":
        issues.append(_warn("mirror-irreversible",
                            _("Mirror mode: files deleted on this computer are deleted from the copy too, and "
                              "changed files replace their previous versions — they cannot be got back.")))
    if job.max_size_mb is not None and job.max_size_mb <= 0:
        issues.append(_err("max-size-invalid", _("The file size limit must be a positive number.")))
    for pattern in job.excludes:
        if "\n" in pattern or "\r" in pattern:
            issues.append(_err("exclude-newline", _("An exclusion pattern must be a single line.")))
            break
    return issues


def validate_profile(profile: Profile) -> list[Issue]:
    issues: list[Issue] = []
    if not profile.name.strip():
        issues.append(_err("name-empty", _("Enter a profile name.")))
    issues.extend(validate_name_component(profile.root))
    if profile.auto.countdown_sec < 0 or profile.auto.countdown_sec > 600:
        issues.append(_err("countdown-invalid", _("The countdown must be between 0 and 600 seconds.")))
    if profile.versions_keep_days < 1:
        issues.append(_err("versions-invalid", _("Versions must be kept for at least one day.")))
    return issues


def has_errors(issues: list[Issue]) -> bool:
    return any(i.level == "error" for i in issues)


# ---------- подбор профиля к носителю ----------

def profile_for_volume(profiles: list[Profile], volume) -> Profile | None:
    """Профиль этого носителя; частичный NTFS UUID требует совпадающей метки."""
    from plugbackup.core import marker
    from plugbackup.core.volume import uuid_matches

    for profile in profiles:
        if not uuid_matches(volume, profile.device.uuid):
            continue
        if getattr(volume, "uuid_partial", False):
            root = os.path.join(volume.mount_point, profile.root)
            if marker.root_state(root, profile.id) != marker.ROOT_MATCH:
                continue
        return profile
    return None


def rebind_candidates(profiles: list[Profile], volume, connected_uuids: set[str]) -> list[Profile]:
    """Профили, чей носитель, похоже, переформатирован: серийник совпал, UUID другой и тот UUID сейчас не подключён."""
    serial = getattr(volume, "drive_serial", "")
    if not serial or profile_for_volume(profiles, volume) is not None:
        return []
    return [p for p in profiles
            if p.device.serial == serial and p.device.uuid != volume.uuid
            and p.device.uuid not in connected_uuids]


def bind_to_volume(profile: Profile, volume) -> None:
    profile.device = DeviceBinding(
        uuid=volume.uuid,
        fs=volume.fs_type,
        label=volume.label,
        serial=volume.drive_serial,
        model=" ".join(p for p in (volume.drive_vendor, volume.drive_model) if p),
        luks_inner_uuid=volume.inner_uuid or None,
    )
    if getattr(volume, "encrypted", False):
        profile.encryption.mode = "luks"
    elif profile.encryption.mode == "luks":
        profile.encryption = Encryption()


def uses_luks(profile: Profile) -> bool:
    """Носитель профиля зашифрован целиком (LUKS); в старых профилях режим не записан — признак: UUID внутренней ФС."""
    return profile.encryption.mode == "luks" or bool(profile.device.luks_inner_uuid)


def clone_for_other_device(profile: Profile, name: str) -> Profile:
    """Такой же профиль для другого носителя: новые ID, без привязки и без отложенной синхронизации."""
    clone = copy.deepcopy(profile)
    clone.id = new_id()
    clone.name = name
    clone.device = DeviceBinding()
    clone.encryption = Encryption()     # шифрование определяется новым носителем при привязке
    clone.device_sync_pending = False
    for job in clone.jobs:
        job.id = new_id()
    return clone


# ---------- экспорт и импорт ----------

def export_profile(profile: Profile) -> dict:
    data = profile_to_dict(profile)
    data.pop("device_sync_pending", None)
    return {"format": EXPORT_FORMAT, "version": EXPORT_VERSION, "profile": data}


def import_profile(data: dict) -> Profile:
    """Профиль из экспорта или копии на носителе. Автоматика выключается до подтверждения путей."""
    if not isinstance(data, dict) or data.get("format") != EXPORT_FORMAT:
        raise ProfileFormatError("not a Plug & Backup profile")
    version = data.get("version")
    if not isinstance(version, int) or version > EXPORT_VERSION:
        raise ProfileFormatError("unsupported profile format version")
    profile = profile_from_dict(data.get("profile"))
    profile.auto.enabled = False
    profile.device_sync_pending = False
    return profile


def device_dest_paths(profile: Profile) -> dict[str, str]:
    """ID задания → нормализованная папка назначения (для метки на носителе)."""
    return {job.id: normalize_dest(job.dest) for job in profile.jobs}
