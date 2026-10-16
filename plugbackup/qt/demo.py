"""Демонстрационные объекты интерфейса на моделях ядра."""
from __future__ import annotations

from plugbackup.core.profiles import AutoSettings, DeviceBinding, Job, Profile
from plugbackup.core.volume import Volume
from plugbackup.i18n import language


def _demo_words() -> tuple[str, str, str, str, str]:
    if language() == "ru":
        return "Домашний ноутбук", "Архивный профиль", "Документы", "Фото", "Проекты"
    return "Home laptop", "Archive profile", "Documents", "Photos", "Projects"


def sample_profile() -> Profile:
    home, _archive, documents, photos, projects = _demo_words()
    return Profile(
        id="demo-home", name=home,
        device=DeviceBinding(uuid="5A3C91F2", fs="exfat", label="SANDISK",
                             serial="A1B2-C3D4", model="SanDisk Ultra"),
        root="PlugBackup", versions_keep_days=30,
        auto=AutoSettings(enabled=True, countdown_sec=10, min_interval_hours=4, eject_after_success=True),
        jobs=[
            Job(id="docs", source=r"C:\Users\user\Documents", dest=documents,
                mode="mirror_versions"),
            Job(id="photos", source=r"C:\Users\user\Pictures", dest=photos,
                mode="accumulate", in_auto=True),
            Job(id="projects", source=r"C:\Users\user\Projects", dest=projects,
                mode="mirror_versions", in_auto=False,
                excludes=["node_modules/", ".venv/"]),
        ],
    )


def sample_volume(locked: bool = False) -> Volume:
    return Volume(
        object_path="demo-volume", device="E:", fs_type="exfat", fs_version="",
        uuid="5A3C91F2", inner_uuid="", label="SANDISK", mount_points=() if locked else ("E:\\",),
        encrypted=locked, locked=locked, size=128_000_000_000, drive_vendor="SanDisk",
        filesystem_path="demo-filesystem", drive_path="demo-drive-path", drive_model="Ultra",
        drive_serial="A1B2-C3D4", drive_id="demo-drive",
        connection_bus="usb", removable=True, sibling_id="", is_loop=False,
        loop_backing_file="",
    )


def sample_unbound_profile() -> Profile:
    _home, archive, documents, _photos, _projects = _demo_words()
    return Profile(id="demo-unbound", name=archive,
                   device=DeviceBinding(uuid="DEADBEEF", fs="ntfs", label="ARCHIVE", model="USB Disk"),
                   jobs=[Job(id="linux-home", source="/home/user/" + documents, dest=documents)])
