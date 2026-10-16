"""Настройки приложения: ~/.config/plugbackup/config.json."""

import os
from dataclasses import asdict, dataclass, field

from plugbackup.core.storage import StorageError, load_json, save_json

SCHEMA = 1


def config_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "plugbackup")


def runtime_dir() -> str:
    """$XDG_RUNTIME_DIR/plugbackup: блокировки запусков и точки монтирования зашифрованных папок."""
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/plugbackup-{os.getuid()}"
    path = os.path.join(base, "plugbackup")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def config_path() -> str:
    return os.path.join(config_dir(), "config.json")


# Стартовый общий набор исключений.
DEFAULT_GLOBAL_EXCLUDES = (
    "*.tmp", "*.temp", "*.swp", "*~", "~$*", ".~lock.*#",
    "Thumbs.db", "desktop.ini", ".DS_Store", ".Trash-*/", ".cache/", "__pycache__/",
)


@dataclass
class AppConfig:
    show_all_disks: bool = False
    global_excludes: list[str] = field(default_factory=lambda: list(DEFAULT_GLOBAL_EXCLUDES))
    theme: str = "system"          # system / light / dark — только окна приложения
    # Носители, для которых не предлагать создать профиль: {"uuid", "name", "added"}
    ignored_drives: list[dict] = field(default_factory=list)


class ConfigStore:
    """Загружает настройки; если файл повреждён — работает на значениях по умолчанию и ничего не записывает."""

    def __init__(self, path: str | None = None):
        self.path = path or config_path()
        self.error: StorageError | None = None
        self.config = AppConfig()
        self.reload()

    def reload(self) -> None:
        self.error = None
        try:
            data = load_json(self.path)
        except StorageError as exc:
            self.error = exc
            self.config = AppConfig()
            return
        self.config = self._from_dict(data or {})

    @staticmethod
    def _from_dict(data: dict) -> AppConfig:
        defaults = AppConfig()
        excludes = data.get("global_excludes", defaults.global_excludes)
        if not isinstance(excludes, list) or not all(isinstance(e, str) for e in excludes):
            excludes = defaults.global_excludes
        return AppConfig(
            show_all_disks=bool(data.get("show_all_disks", defaults.show_all_disks)),
            global_excludes=list(excludes),
            theme=data.get("theme") if data.get("theme") in ("system", "light", "dark") else defaults.theme,
            ignored_drives=[d for d in data.get("ignored_drives", [])
                            if isinstance(d, dict) and isinstance(d.get("uuid"), str) and d["uuid"]],
        )

    def save(self) -> bool:
        if self.error is not None:
            return False
        save_json(self.path, {"schema": SCHEMA, **asdict(self.config)})
        return True
