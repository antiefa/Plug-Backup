"""Общие модели внутреннего плана копирования."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from plugbackup.core import rsync

RC_NO_SPACE = 11


@dataclass
class CopySpec:
    """Параметры одного задания копирования."""
    source: str
    dest_dir: str
    mode: str
    compare: str
    symlinks: str
    fs_type: str
    excludes: List[str] = field(default_factory=list)
    extra_excludes: List[str] = field(default_factory=list)
    max_size: Optional[int] = None
    one_file_system: bool = True
    backup_dir: Optional[str] = None
    hard_max_delete: int = 5000
    skip_hidden_attr: bool = False
    update_only: bool = False
    delete_excluded: bool = False


@dataclass
class EngineResult:
    """Код результата, сводка изменений и счётчики передачи."""
    rc: int = rsync.RC_OK
    summary: rsync.ItemizeSummary = field(default_factory=rsync.ItemizeSummary)
    stats: rsync.Stats = field(default_factory=rsync.Stats)

