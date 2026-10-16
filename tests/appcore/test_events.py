"""Проверки неизменяемых событий и шины."""

import unittest
from dataclasses import FrozenInstanceError

from plugbackup.appcore.events import EventBus, ProfileStateChanged, RunFinished


class EventBusTests(unittest.TestCase):
    def test_subscribe_emit_and_unsubscribe(self):
        bus = EventBus()
        seen = []
        unsubscribe = bus.subscribe(ProfileStateChanged, seen.append)
        event = ProfileStateChanged("profile-1", "ready")
        bus.emit(event)
        unsubscribe()
        unsubscribe()
        bus.emit(ProfileStateChanged("profile-2"))
        self.assertEqual(seen, [event])

    def test_event_is_immutable_and_subscriptions_are_typed(self):
        bus = EventBus()
        seen = []
        bus.subscribe(ProfileStateChanged, seen.append)
        event = RunFinished("profile-1", "run-1", "ok")
        bus.emit(event)
        with self.assertRaises(FrozenInstanceError):
            event.status = "error"
        self.assertEqual(seen, [])


if __name__ == "__main__":
    unittest.main()
