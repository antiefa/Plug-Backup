"""Проверки GTK-адаптера на цикле GLib без создания окон."""

import unittest
import threading
import time

try:
    from gi.repository import GLib
except ImportError:
    GLib = None

from plugbackup.ui.appcore_gtk import GtkScheduler, ThreadWorker


@unittest.skipIf(GLib is None, "GLib is not installed")
class GtkSchedulerTests(unittest.TestCase):
    def test_idle_delivery_and_timeout_cancellation(self):
        scheduler = GtkScheduler()
        context = GLib.MainContext.default()
        seen = []
        scheduler.call_soon(seen.append, "idle")
        handle = scheduler.call_later(60, seen.append, "cancelled")
        scheduler.cancel(handle)
        while context.pending():
            context.iteration(False)
        self.assertEqual(seen, ["idle"])

    def test_worker_result_is_delivered_on_main_context(self):
        scheduler = GtkScheduler()
        context = GLib.MainContext.default()
        worker = ThreadWorker(scheduler)
        delivered = threading.Event()
        seen = []

        def success(value):
            seen.append((value, threading.current_thread()))
            delivered.set()

        worker.submit("test-worker", lambda: 17, success, self.fail)
        deadline = time.monotonic() + 2
        while not delivered.is_set() and time.monotonic() < deadline:
            context.iteration(True)
        self.assertTrue(delivered.is_set())
        self.assertEqual(seen[0][0], 17)
        self.assertIs(seen[0][1], threading.current_thread())


if __name__ == "__main__":
    unittest.main()
