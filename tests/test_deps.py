import os
import unittest
from unittest import mock

try:
    import gi  # noqa: F401
    GI_AVAILABLE = True
except ImportError:
    GI_AVAILABLE = False

from plugbackup.core import deps
from plugbackup.core.deps import Check
from plugbackup import plat


def make(key, ok, required):
    return Check(key, "Feature " + key, "component-" + key, ok, required)


class FormatAndFilterTest(unittest.TestCase):
    def test_missing_required_only_counts_required_failures(self):
        checks = [make("a", True, True), make("b", False, True), make("c", False, False)]
        self.assertEqual([c.key for c in deps.missing_required(checks)], ["b"])

    def test_report_marks_each_state(self):
        checks = [make("a", True, True), make("b", False, True), make("c", False, False)]
        report = deps.format_report(checks).splitlines()
        self.assertTrue(report[0].startswith("[OK ]"))
        self.assertTrue(report[1].startswith("[ERR]"))
        self.assertTrue(report[2].startswith("[-- ]"))


@unittest.skipUnless(GI_AVAILABLE, "нужен GI")
class WhichTest(unittest.TestCase):
    def test_finds_tools_in_sbin_without_it_in_path(self):
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent"}):
            found = plat.deps.which("sh")
        self.assertIsNotNone(found)

    def test_unknown_tool(self):
        self.assertIsNone(plat.deps.which("plugbackup-no-such-tool"))


@unittest.skipUnless(GI_AVAILABLE, "нужен GI")
class GirTest(unittest.TestCase):
    def test_missing_namespace_reports_reason(self):
        ok, detail = plat.deps.gir_available("PlugbackupNoSuchNamespace", "1.0")
        self.assertFalse(ok)
        self.assertTrue(detail)


@unittest.skipUnless(GI_AVAILABLE, "нужен GI")
class RunChecksTest(unittest.TestCase):
    """Проверка на реальной системе: структура результата и обязательные ключи."""

    def test_all_keys_present(self):
        keys = {c.key for c in deps.run_checks()}
        self.assertTrue({"gtk", "udisks-gir", "udisks-service", "rsync", "notify", "tray",
                         "secret-gir", "secret-service", "cryptsetup", "gocryptfs", "fuse"} <= keys)

    def test_required_components_are_flagged(self):
        required = {c.key for c in deps.run_checks() if c.required}
        self.assertEqual(required, {"gtk", "udisks-gir", "udisks-service", "rsync"})


if __name__ == "__main__":
    unittest.main()
