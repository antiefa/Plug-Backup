"""Проверки тестовых двойников appcore."""

import unittest

from plugbackup.appcore.events import Notice, PasswordRequest, Question
from tests.appcore.fakes import FakeClock, FakeDialogs, FakeScheduler, FakeWorker


class FakeSchedulerTests(unittest.TestCase):
    def test_advance_runs_due_calls_in_time_then_insertion_order(self):
        scheduler = FakeScheduler()
        seen = []
        scheduler.call_later(2, seen.append, "later")
        scheduler.call_later(1, seen.append, "first")
        scheduler.call_later(1, seen.append, "second")
        scheduler.advance(1)
        self.assertEqual(seen, ["first", "second"])
        scheduler.advance(1)
        self.assertEqual(seen, ["first", "second", "later"])
        self.assertEqual(scheduler.now, 2)

    def test_cancel_and_call_soon(self):
        scheduler = FakeScheduler()
        seen = []
        cancelled = scheduler.call_soon(seen.append, "cancelled")
        scheduler.cancel(cancelled)
        scheduler.call_soon(seen.append, "soon")
        scheduler.advance(0)
        self.assertEqual(seen, ["soon"])

    def test_negative_time_is_rejected(self):
        with self.assertRaises(ValueError):
            FakeScheduler().advance(-1)


class FakeDialogsTests(unittest.TestCase):
    def test_scenarios_are_recorded_and_consumed_in_order(self):
        dialogs = FakeDialogs([True], ["replace"], ["secret"])
        question = Question("replace", "Title", "Text")
        self.assertTrue(dialogs.confirm(question))
        result = []
        dialogs.ask(question, result.append)
        dialogs.password(PasswordRequest("Password", "Enter it"), result.append)
        dialogs.notify(Notice("info", "Done", "Ready"))
        self.assertEqual(result, ["replace", "secret"])
        self.assertEqual(dialogs.questions, [question, question])
        self.assertEqual(dialogs.password_requests[0].title, "Password")
        self.assertEqual(len(dialogs.notices), 1)

    def test_default_answers_cancel(self):
        dialogs = FakeDialogs()
        self.assertFalse(dialogs.confirm(Question("q", "t", "x")))
        answers = []
        dialogs.ask(Question("q", "t", "x"), answers.append)
        dialogs.password(PasswordRequest("p", "h"), answers.append)
        self.assertEqual(answers, ["cancel", None])


class FakeWorkerAndClockTests(unittest.TestCase):
    def test_worker_runs_success_and_error_synchronously(self):
        worker = FakeWorker()
        results = []
        errors = []
        worker.submit("ok", lambda: 42, results.append, errors.append)

        def fail():
            raise RuntimeError("failed")

        worker.submit("bad", fail, results.append, errors.append)
        self.assertEqual(worker.submissions, ["ok", "bad"])
        self.assertEqual(results, [42])
        self.assertEqual(str(errors[0]), "failed")

    def test_clock_can_be_advanced(self):
        clock = FakeClock(10)
        clock.advance(2.5)
        self.assertEqual(clock.now(), 12.5)


if __name__ == "__main__":
    unittest.main()
