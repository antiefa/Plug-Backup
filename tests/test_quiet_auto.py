import unittest
from types import SimpleNamespace

try:
    import gi
    gi.require_version("Gtk", "3.0")
    from plugbackup.ui.auto_controller import outcome_is_clean, planned_changes
    GI_AVAILABLE = True
except (ImportError, ValueError):
    outcome_is_clean = planned_changes = None
    GI_AVAILABLE = False

from plugbackup.core import journal
from plugbackup.core.runner import JobPlan


def record(status=journal.OK, reason="", jobs=(journal.OK,)):
    return journal.RunRecord("r", "p", "P", "auto", 0.0, status=status, reason=reason,
                             jobs=[journal.JobResult(str(i), "s", "d", status=s) for i, s in enumerate(jobs)])


@unittest.skipUnless(GI_AVAILABLE, "нужен GTK 3")
class QuietAutoTest(unittest.TestCase):
    def test_counts_new_changed_and_deleted(self):
        plans = {"a": JobPlan(new_files=["1", "2"], changed_files=["3"], transfer_bytes=10),
                 "b": JobPlan(deleted_files=["4"], transfer_bytes=0)}
        self.assertEqual(planned_changes(SimpleNamespace(plans=plans)), (4, 10))
        self.assertEqual(planned_changes(SimpleNamespace(plans={"a": JobPlan()})), (0, 0))

    def test_clean_only_without_errors_skips_or_reasons(self):
        self.assertTrue(outcome_is_clean(record()))
        self.assertTrue(outcome_is_clean(record(status=journal.WARNINGS, jobs=(journal.WARNINGS,))))
        self.assertFalse(outcome_is_clean(record(status=journal.SKIPPED, jobs=(journal.SKIPPED,))))
        self.assertFalse(outcome_is_clean(record(jobs=(journal.OK, journal.ERROR))))
        self.assertFalse(outcome_is_clean(record(reason="already-running")))


if __name__ == "__main__":
    unittest.main()
