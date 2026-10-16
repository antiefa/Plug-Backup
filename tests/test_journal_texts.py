import unittest

import gi

gi.require_version("Gtk", "3.0")

from plugbackup.core import journal  # noqa: E402
from plugbackup.ui.run_texts import nothing_changed  # noqa: E402


def rec(status=journal.OK, reason="", trigger="manual", jobs=((journal.OK, 0, 0),)):
    return journal.RunRecord("r", "p", "P", trigger, 0.0, status=status, reason=reason,
                             jobs=[journal.JobResult(str(i), "s", "d", status=s, copied_files=c, deleted_files=d)
                                   for i, (s, c, d) in enumerate(jobs)])


class NothingChangedTest(unittest.TestCase):
    def test_quiet_check_and_empty_successful_runs(self):
        self.assertTrue(nothing_changed(rec(reason="no-changes", trigger="auto")))
        self.assertTrue(nothing_changed(rec()))

    def test_real_work_or_problems_are_not_no_changes(self):
        self.assertFalse(nothing_changed(rec(jobs=((journal.OK, 3, 0),))))
        self.assertFalse(nothing_changed(rec(jobs=((journal.OK, 0, 1),))))
        self.assertFalse(nothing_changed(rec(status=journal.WARNINGS, jobs=((journal.WARNINGS, 0, 0),))))
        self.assertFalse(nothing_changed(rec(status=journal.SKIPPED, jobs=((journal.SKIPPED, 0, 0),))))
        self.assertFalse(nothing_changed(rec(jobs=())))


if __name__ == "__main__":
    unittest.main()
