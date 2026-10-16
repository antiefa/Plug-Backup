from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from plugbackup.core import config, rsync
from plugbackup.core.profiles import DEV_EXCLUDES, Job
from plugbackup.engine import CopySpec, list_source

@unittest.skipUnless(shutil.which("rsync"), "требуется rsync")
class FilterDifferentialTests(unittest.TestCase):
    def test_filter_subset_matches_rsync_on_deep_tree(self):
        """Фильтры должны повторять rsync на дереве глубокой вложенности и спецсимволах."""
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            names = ["plain.txt", "x.tmp", "x.temp", "x.swp", "backup~", "~$doc", ".~lock.a#",
                     "Thumbs.db", "desktop.ini", ".DS_Store", ".Trash-1", ".cache", "__pycache__",
                     "node_modules", ".venv", "venv", "build", "dist", "target", "Архив", "a", "dir",
                     "file*name", "ax", "xx", "one.a", "one.o", "one.c", "lead\\slash", "br[acket]"]
            for name in names:
                path = source / name
                path.mkdir()
                (path / "inside.txt").write_text("v", encoding="utf-8")
            for i in range(25):
                for suffix in ("", "/deeper", "/deeper/next"):
                    branch = source / f"branch{i:02d}"
                    folder = branch / suffix.lstrip("/") if suffix else branch
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / f"file{i:02d}.txt").write_text("v", encoding="utf-8")
            for folder in ("a/b/c", "x/a/y/c", "z/build", "deep/z/build", "dir/nested", "Архив/private"):
                path = source / folder
                path.mkdir(parents=True, exist_ok=True)
                (path / "payload").write_text("v", encoding="utf-8")
            patterns = list(DEV_EXCLUDES) + ["/Архив/", "a/*/c", "**/build/", "*.[oa]", "?x", "dir/", r"file\*name"]
            job = Job(source=str(source), dest="task", excludes=patterns, use_global_excludes=True)
            options = rsync.CommandOptions(fs_type="ext4", global_excludes=list(config.DEFAULT_GLOBAL_EXCLUDES))
            actual = {(e.kind, e.size, e.path) for e in list_source(CopySpec(
                str(source), str(Path(tmp) / "dest"), job.mode, job.compare, "skip", "ext4",
                list(config.DEFAULT_GLOBAL_EXCLUDES) + patterns))}
            command = rsync.build_list_command(job, str(source), options)
            result = subprocess.run(command, capture_output=True, text=True, env=rsync.rsync_env())
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = {(e.kind, e.size, e.path) for e in rsync.parse_list(result.stdout)}
            self.assertEqual(actual, expected)

if __name__ == "__main__":
    unittest.main()
