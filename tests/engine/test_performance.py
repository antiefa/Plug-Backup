"""Проверки времени построения и выполнения большого плана."""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from plugbackup.engine import CopySpec, execute, plan


@unittest.skipUnless(os.environ.get("PLUGBACKUP_SKIP_SLOW") != "1", "slow test disabled by environment")
class EnginePerformanceTests(unittest.TestCase):
    """Измерить движок на дереве из десятков тысяч маленьких файлов."""

    def test_plan_and_execute_large_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            destination.mkdir()
            eligible = []
            base_time = 1700000000

            # Создание исходного и тестового назначения не входит в замер.
            for directory_index in range(3000):
                if directory_index < 500:
                    relative = Path("node_modules") / f"group{directory_index:04d}"
                else:
                    relative = Path(f"group{directory_index:04d}")
                source_dir = source / relative
                destination_dir = destination / relative
                source_dir.mkdir(parents=True)
                if directory_index >= 500:
                    destination_dir.mkdir(parents=True)
                for file_index in range(10):
                    name = f"file{file_index:02d}.dat"
                    source_file = source_dir / name
                    source_file.write_bytes(f"{directory_index * 10 + file_index:08d}".encode("ascii"))
                    os.utime(source_file, (base_time, base_time))
                    if directory_index >= 500:
                        destination_file = destination_dir / name
                        destination_file.write_bytes(source_file.read_bytes())
                        os.utime(destination_file, (base_time, base_time))
                        eligible.append(source_file)

            for index, source_file in enumerate(eligible[:1000]):
                source_file.write_bytes(f"X{index:07d}".encode("ascii"))
                os.utime(source_file, (base_time + 10, base_time + 10))
            for index, source_file in enumerate(eligible[:1000]):
                extra = destination / source_file.parent.relative_to(source) / f"extra{index:04d}.dat"
                extra.write_bytes(b"stale")
                os.utime(extra, (base_time, base_time))

            spec = CopySpec(str(source), str(destination), "mirror", "size_mtime", "skip", "ext4",
                            excludes=["node_modules/"], one_file_system=False)
            start = time.perf_counter()
            planned = plan(spec)
            plan_seconds = time.perf_counter() - start
            self.assertEqual(planned.rc, 0)
            self.assertEqual(len(planned.summary.changed_files), 1000)
            self.assertEqual(len(planned.summary.deleted_files), 1000)
            self.assertLess(plan_seconds, 15.0, "plan exceeded 15 seconds")

            start = time.perf_counter()
            result = execute(spec)
            execute_seconds = time.perf_counter() - start
            self.assertEqual(result.rc, 0)
            self.assertEqual(len(result.summary.changed_files), 1000)
            self.assertEqual(len(result.summary.deleted_files), 1000)
            self.assertLess(execute_seconds, 60.0, "execute exceeded 60 seconds")


if __name__ == "__main__":
    unittest.main()
