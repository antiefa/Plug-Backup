"""Проверки внешнего поведения контроллеров GTK без создания окон."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import GLib
    from plugbackup.ui import auto_controller, run_controller
    GTK_AVAILABLE = True
except (ImportError, ValueError):
    GLib = None
    auto_controller = run_controller = None
    GTK_AVAILABLE = False

from plugbackup.core import auto, journal
from plugbackup.core.profiles import AutoSettings, DeviceBinding, Encryption, Job, Profile
from plugbackup.core.runner import JobPlan, RunOutcome, RunRequest


class FakeNotifier:
    def __init__(self):
        self.events = []

    def show(self, title, body, actions=(), urgency="normal", timeout_ms=None):
        note = SimpleNamespace(title=title, body=body, actions=actions, closed=False)
        self.events.append(("show", note))
        return note

    def close(self, note):
        if note is not None:
            note.closed = True
            self.events.append(("close", note))


class FakeMonitor:
    def __init__(self, volumes=()):
        self.current = list(volumes)
        self.client = object()
        self.callbacks = []

    def connect(self, _signal, callback):
        self.callbacks.append(callback)

    def volumes(self):
        return list(self.current)

    def refresh(self):
        pass


class FakeApp:
    def __init__(self, profile, volume):
        self.monitor = FakeMonitor([volume])
        self.profiles_store = SimpleNamespace(profiles=[profile], get=lambda _id: profile)
        self.config_store = SimpleNamespace(config=SimpleNamespace(global_excludes=[], ignored_drives=[]), save=lambda: None)
        self.state_events = 0
        self.active = set()
        self.started = []
        self.auto = None
        self.vaults = None
        self.unlocker = None

    def runs_active(self, profile_id):
        return profile_id in self.active

    def on_auto_state_changed(self):
        self.state_events += 1

    def start_run(self, profile, volume, **kwargs):
        self.started.append((profile, volume, kwargs))

    def create_profile_for(self, volume):
        pass

    def show_run(self, record):
        pass


def make_profile(countdown=10, eject=False):
    return Profile(id="p", name="Profile", device=DeviceBinding(uuid="disk"),
                   auto=AutoSettings(enabled=True, countdown_sec=countdown,
                                     min_interval_hours=None, eject_after_success=eject),
                   jobs=[Job(id="auto-job", enabled=True, in_auto=True),
                         Job(id="manual-job", enabled=True, in_auto=False)])


def make_volume(uuid="disk", path="/fake/disk", mounted=True, locked=False):
    return SimpleNamespace(uuid=uuid, object_path=path, mounted=mounted, locked=locked,
                           display_name="Test drive", mount_point="/fake")


class VirtualGLib:
    SOURCE_REMOVE = GLib.SOURCE_REMOVE if GTK_AVAILABLE else False
    SOURCE_CONTINUE = GLib.SOURCE_CONTINUE if GTK_AVAILABLE else True

    def __init__(self):
        self.next_id = 1
        self.timers = {}
        self.idles = []
        self.removed = []

    def timeout_add_seconds(self, seconds, callback):
        timer_id = self.next_id
        self.next_id += 1
        self.timers[timer_id] = (seconds, callback)
        return timer_id

    def timeout_add(self, seconds, callback):
        return self.timeout_add_seconds(seconds, callback)

    def idle_add(self, callback, *args):
        self.idles.append((callback, args))
        return self.next_id

    def source_remove(self, timer_id):
        self.removed.append(timer_id)
        self.timers.pop(timer_id, None)
        return True

    def format_size(self, size):
        return str(size)

    def drain(self):
        while self.idles:
            callback, args = self.idles.pop(0)
            callback(*args)


@unittest.skipUnless(GTK_AVAILABLE, "нужен GTK 3")
class AutoControllerCharacterization(unittest.TestCase):
    def setUp(self):
        self.loop = VirtualGLib()
        self.profile = make_profile()
        self.volume = make_volume()
        self.app = FakeApp(self.profile, self.volume)
        self.patches = [
            patch.object(auto_controller, "Notifier", FakeNotifier),
            patch.object(auto_controller.GLib, "timeout_add_seconds", self.loop.timeout_add_seconds),
            patch.object(auto_controller.GLib, "timeout_add", self.loop.timeout_add),
            patch.object(auto_controller.GLib, "idle_add", self.loop.idle_add),
            patch.object(auto_controller.GLib, "source_remove", self.loop.source_remove),
            patch.object(auto_controller, "Runner"),
            patch.object(auto_controller.threading, "Thread", side_effect=lambda **kw: SimpleNamespace(start=kw["target"])),
            patch.object(auto_controller.devices, "uuid_conflicts", return_value=set()),
            patch.object(auto_controller.auto, "last_success_time", return_value=None),
            patch.object(auto_controller.time, "time", return_value=1000.0),
            patch.object(auto_controller.time, "monotonic", return_value=10.0),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.controller = auto_controller.AutoController(self.app)
        self.controller.notifier = FakeNotifier()

    def test_mounted_media_at_start_starts_only_auto_jobs_after_countdown(self):
        self.controller._check_then_countdown = lambda profile, volume, job_ids: self.controller._start_countdown(profile, job_ids, (1, 4))
        self.controller.check()
        self.assertEqual(self.controller.countdowns.keys(), {"p"})
        note = self.controller.countdowns["p"][1]
        self.assertIn("10", note.body)
        self.assertIn("1", note.body)
        self.assertEqual(self.loop.timers[self.controller.countdowns["p"][0]][0], 10)
        self.assertEqual(self.app.started, [])

    def test_countdown_cancel_and_now_have_distinct_effects(self):
        self.controller._start_countdown(self.profile, ["auto-job"])
        timer, note = self.controller.countdowns["p"]
        actions = dict((key, callback) for key, _label, callback in note.actions)
        actions["cancel"]()
        self.assertNotIn("p", self.controller.countdowns)
        self.assertIn(timer, self.loop.removed)
        self.assertTrue(note.closed)
        self.assertEqual(self.app.started, [])

        self.controller._start_countdown(self.profile, ["auto-job"])
        _timer, note = self.controller.countdowns["p"]
        dict((key, callback) for key, _label, callback in note.actions)["now"]()
        self.assertEqual(self.app.started[0][2]["job_ids"], ["auto-job"])
        self.assertEqual(self.app.started[0][2]["trigger"], "auto")
        self.assertNotIn("p", self.controller.countdowns)

    def test_countdown_expiry_starts_the_automatic_run(self):
        self.controller._start_countdown(self.profile, ["auto-job"])
        timer, _note = self.controller.countdowns["p"]
        self.loop.timers[timer][1]()
        self.assertEqual(self.app.started[0][2]["job_ids"], ["auto-job"])
        self.assertNotIn("p", self.controller.countdowns)

    def test_zero_countdown_runs_immediately_and_manual_run_blocks_automation(self):
        self.profile.auto.countdown_sec = 0
        self.controller._start_countdown(self.profile, ["auto-job"])
        self.assertEqual(len(self.app.started), 1)
        self.app.active.add("p")
        self.controller._on_ready(self.volume)
        self.assertEqual(len(self.app.started), 1)

    def test_unknown_uuid_is_offered_once_until_removed(self):
        self.app.profiles_store.profiles = []
        self.controller.check()
        self.controller.check()
        notes = [note for kind, note in self.controller.notifier.events if kind == "show"]
        self.assertEqual(len(notes), 1)
        self.app.monitor.current = []
        self.controller.check()
        self.app.monitor.current = [self.volume]
        self.controller.check()
        notes = [note for kind, note in self.controller.notifier.events if kind == "show"]
        self.assertEqual(len(notes), 2)

    def test_missing_uuid_is_not_offered_and_duplicate_uuid_is_blocked(self):
        self.app.profiles_store.profiles = []
        self.volume.uuid = ""
        self.controller._on_ready(self.volume)
        self.assertEqual(self.controller.notifier.events, [])

        self.volume.uuid = "disk"
        self.app.profiles_store.profiles = [self.profile]
        with patch.object(auto_controller.devices, "uuid_conflicts", return_value={"disk"}):
            self.controller._on_ready(self.volume)
        self.assertEqual(self.app.started, [])
        self.assertEqual(self.controller.countdowns, {})
        self.assertTrue(any("conflict" in note.title.lower() or "конфликт" in note.title.lower()
                            for kind, note in self.controller.notifier.events if kind == "show"))

    def test_saved_label_mismatch_does_not_override_uuid_binding(self):
        self.profile.device.label = "OLD LABEL"
        self.volume.label = "NEW LABEL"
        with patch.object(self.controller, "_check_then_countdown") as check:
            self.controller._on_ready(self.volume)
        check.assert_called_once_with(self.profile, self.volume, ["auto-job"])

    def test_mount_wait_requests_mount_after_five_seconds(self):
        volume = make_volume(mounted=False)
        self.app.monitor.current = [volume]
        with patch.object(auto_controller.devices, "mount_async") as mount:
            self.controller.tracker.update([volume], 0)
            for event, candidate in self.controller.tracker.update([volume], 5):
                self.controller._on_needs_mount(candidate)
        mount.assert_called_once()
        self.assertIs(mount.call_args.args[1], volume)

    def test_locked_volume_is_unlocked_then_automatically_rechecked(self):
        locked = make_volume(locked=True)
        self.app.monitor.current = [locked]
        calls = []
        self.app.unlocker = SimpleNamespace(is_busy=lambda _path: False,
                                             unlock=lambda *args, **kwargs: calls.append((args, kwargs)))
        self.controller._on_ready(locked)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][1]["wait_for_screen"])
        self.assertIn("p", self.controller.unlocking)

    def test_auto_off_no_auto_jobs_and_frequency_limit_do_not_start(self):
        self.profile.auto.enabled = False
        self.controller._on_ready(self.volume)
        self.profile.auto.enabled = True
        self.profile.jobs = [Job(id="manual", enabled=True, in_auto=False)]
        self.controller._on_ready(self.volume)
        self.profile.jobs = [Job(id="auto", enabled=True, in_auto=True)]
        with patch.object(auto_controller.auto, "last_success_time", return_value=999.0):
            self.profile.auto.min_interval_hours = 1
            self.controller._on_ready(self.volume)
        self.assertEqual(self.app.started, [])
        self.assertEqual(self.controller.countdowns, {})

    def test_clean_quiet_check_is_journaled_and_ejects_only_after_vault_release(self):
        self.controller.vault_held.add("p")
        events = []
        self.controller.app.vaults = SimpleNamespace(
            release=lambda profile_id: events.append(("release", profile_id)),
            close_for_volume_sync=lambda volume: events.append(("close", volume.uuid)))
        outcome = RunOutcome(journal.RunRecord("r", "p", "Profile", "auto", 1.0),
                             plans={"auto-job": JobPlan()})
        outcome.record.status = journal.OK
        with patch.object(auto_controller, "Runner") as runner, \
                patch.object(auto_controller.journal, "last_run", return_value=None), \
                patch.object(auto_controller.journal, "append", side_effect=lambda record: events.append(("journal", record.reason))), \
                patch.object(auto_controller.devices, "eject_sync", side_effect=lambda *_: events.append(("eject",)) or auto_controller.devices.EJECT_POWERED_OFF), \
                patch.object(auto_controller.GLib, "idle_add", side_effect=lambda callback, *args: self.loop.idles.append((callback, args))):
            runner.return_value.run.return_value = outcome
            self.profile.auto.eject_after_success = True
            self.controller._check_then_countdown(self.profile, self.volume, ["auto-job"])
            self.loop.drain()
        self.assertEqual(events[:2], [("journal", auto_controller.NO_CHANGES), ("release", "p")])
        self.assertEqual(events[2:], [("close", "disk"), ("eject",)])
        self.assertEqual(self.controller.countdowns, {})

    def test_pause_cancels_countdown_and_resume_clears_pause(self):
        self.controller._start_countdown(self.profile, ["auto-job"])
        self.controller.pause(3600)
        self.assertNotIn("p", self.controller.countdowns)
        self.assertEqual(self.controller.paused_until, 4600.0)
        self.controller.pause(None)
        self.assertIsNone(self.controller.paused_until)

    def test_pause_for_four_hours_and_until_exit_sets_deadline(self):
        self.controller.pause(4 * 3600)
        self.assertEqual(self.controller.paused_until, 15400.0)
        # «До выхода» в меню трея — бесконечный срок, а не None (None означает «возобновить»).
        self.controller.pause(float("inf"))
        self.assertEqual(self.controller.paused_until, float("inf"))

    def test_pause_until_exit_cancels_running_countdown(self):
        self.controller._start_countdown(self.profile, ["auto-job"])
        timer, note = self.controller.countdowns["p"]
        self.controller.pause(float("inf"))
        self.assertNotIn("p", self.controller.countdowns)
        self.assertNotIn(timer, self.loop.timers)
        self.assertTrue(note.closed)

    def test_resume_keeps_existing_countdown_running(self):
        # pause(None) — «возобновить автоматическое резервирование»: идущий отсчёт не трогается.
        self.controller._start_countdown(self.profile, ["auto-job"])
        timer, note = self.controller.countdowns["p"]
        self.controller.pause(None)
        self.assertIsNone(self.controller.paused_until)
        self.assertIn("p", self.controller.countdowns)
        self.assertIn(timer, self.loop.timers)
        self.assertFalse(note.closed)

    def test_finished_failure_never_ejects_but_clean_success_can(self):
        self.profile.auto.eject_after_success = True
        ejects = []
        self.controller._eject_after = lambda profile: ejects.append(profile.id)
        failed = RunOutcome(journal.RunRecord("r1", "p", "Profile", "auto", 1.0))
        failed.record.status = journal.ERROR
        self.controller._finished(self.profile, failed)
        self.assertEqual(ejects, [])
        clean = RunOutcome(journal.RunRecord("r2", "p", "Profile", "auto", 2.0))
        clean.record.status = journal.OK
        clean.record.jobs = [journal.JobResult("j", "s", "d", status=journal.OK, copied_files=1)]
        self.controller._finished(self.profile, clean)
        self.assertEqual(ejects, ["p"])
        self.profile.auto.eject_after_success = False
        self.controller._finished(self.profile, clean)
        self.assertEqual(ejects, ["p"])


@unittest.skipUnless(GTK_AVAILABLE, "нужен GTK 3")
class RunControllerCharacterization(unittest.TestCase):
    def setUp(self):
        self.loop = VirtualGLib()
        self.profile = make_profile()
        self.volume = make_volume()
        self.app = SimpleNamespace(config_store=SimpleNamespace(config=SimpleNamespace(global_excludes=[])),
                                   profiles_store=SimpleNamespace(get=lambda _id: self.profile), vaults=None)
        self.events = []
        self.window = SimpleNamespace(get_application=lambda: self.app,
                                      on_run_state_changed=lambda profile_id: self.events.append(("state", profile_id)),
                                      on_run_progress=lambda profile_id, info: self.events.append(("progress", profile_id, info)),
                                      show_info=lambda message: self.events.append(("info", message)),
                                      show_error=lambda message: self.events.append(("error", message)))
        self.controller = run_controller.RunController(self.window)
        self.idle_patch = patch.object(run_controller.GLib, "idle_add", self.loop.idle_add)
        self.idle_patch.start()
        self.addCleanup(self.idle_patch.stop)

    def test_repeated_profile_start_is_ignored_and_request_keeps_arguments(self):
        runner_obj = SimpleNamespace(run=lambda: None, cancel=lambda *_: None)
        with patch.object(run_controller, "Runner", return_value=runner_obj) as runner, \
                patch.object(run_controller.threading, "Thread", side_effect=lambda **kw: SimpleNamespace(start=kw["target"])):
            self.controller.start(self.profile, self.volume, job_ids=["auto-job"], trigger="manual")
            first = self.controller.active["p"]
            self.controller.start(self.profile, self.volume, job_ids=["manual-job"])
        self.assertIs(self.controller.active["p"], first)
        request = runner.call_args.args[0]
        self.assertIsInstance(request, RunRequest)
        self.assertEqual(request.job_ids, ["auto-job"])
        self.assertEqual(request.trigger, "manual")
        self.assertEqual(len(self.events), 1)

    def test_second_start_while_vault_opens_is_ignored(self):
        callbacks = {}
        self.profile.encryption.mode = "gocryptfs"
        self.app.vaults = SimpleNamespace(acquire=lambda profile, volume, window, opened, **kwargs:
                                          callbacks.update(opened=opened, profile=profile))
        runner_obj = SimpleNamespace(run=lambda: None, cancel=lambda *_: None)
        with patch.object(run_controller, "Runner", return_value=runner_obj) as runner, \
                patch.object(run_controller.threading, "Thread", side_effect=lambda **kw: SimpleNamespace(start=lambda: None)):
            self.controller.start(self.profile, self.volume)
            self.controller.start(self.profile, self.volume)
            self.assertIn("p", self.controller.opening)
            callbacks["opened"]()
        self.assertEqual(runner.call_count, 1)
        self.assertNotIn("p", self.controller.opening)
        self.assertIn("p", self.controller.vault_held)

    def test_preview_and_single_job_are_reflected_in_request_and_progress(self):
        runner_obj = SimpleNamespace(run=lambda: None, cancel=lambda *_: None)
        with patch.object(run_controller, "Runner", return_value=runner_obj) as runner, \
                patch.object(run_controller.threading, "Thread", side_effect=lambda **kw: SimpleNamespace(start=lambda: None)):
            self.controller.start(self.profile, self.volume, job_ids=["manual-job"], preview=True)
        request = runner.call_args.args[0]
        self.assertTrue(request.preview)
        self.assertEqual(request.job_ids, ["manual-job"])
        self.assertTrue(self.controller.progress["p"]["preview"])
        self.controller.on_progress("p", {"phase": "copy", "percent": 50})
        self.assertEqual(self.events[-1][0:2], ("progress", "p"))
        self.assertTrue(self.events[-1][2]["preview"])

    def test_worker_progress_is_queued_for_main_loop_delivery(self):
        self.controller.active["p"] = object()
        self.controller.progress["p"] = {"preview": False}
        callbacks = run_controller.GuiCallbacks(self.controller, self.profile)
        callbacks.progress({"phase": "copy", "percent": 25})
        self.assertEqual(self.events, [])
        self.loop.drain()
        self.assertEqual(self.events[-1][0:2], ("progress", "p"))
        self.assertEqual(self.events[-1][2]["percent"], 25)

    def test_manual_start_cancels_countdown_but_auto_does_not(self):
        auto_fake = SimpleNamespace(cancelled=[], cancel_countdown=lambda profile_id: auto_fake.cancelled.append(profile_id))
        self.app.auto = auto_fake
        runner_obj = SimpleNamespace(run=lambda: None, cancel=lambda *_: None)
        with patch.object(run_controller, "Runner", return_value=runner_obj), \
                patch.object(run_controller.threading, "Thread", side_effect=lambda **kw: SimpleNamespace(start=lambda: None)):
            self.controller.start(self.profile, self.volume, trigger="manual")
        self.assertEqual(auto_fake.cancelled, ["p"])
        self.controller.active.clear()
        self.controller.progress.clear()
        with patch.object(run_controller, "Runner", return_value=runner_obj), \
                patch.object(run_controller.threading, "Thread", side_effect=lambda **kw: SimpleNamespace(start=lambda: None)):
            self.controller.start(self.profile, self.volume, trigger="auto")
        self.assertEqual(auto_fake.cancelled, ["p"])

    def test_worker_exception_clears_running_state_and_calls_completion(self):
        def fail():
            raise RuntimeError("worker failed")

        runner_obj = SimpleNamespace(run=fail, cancel=lambda *_: None)
        done = []
        with patch.object(run_controller, "Runner", return_value=runner_obj), \
                patch.object(run_controller.threading, "Thread", side_effect=lambda **kw: SimpleNamespace(start=kw["target"])):
            self.controller.start(self.profile, self.volume, on_done=done.append)
        self.loop.drain()
        self.assertFalse(self.controller.is_running("p"))
        self.assertEqual(len(done), 1)
        self.assertIsNone(done[0])
        self.assertIn(("error", "worker failed"), self.events)

    def test_marker_write_clears_pending_and_auto_completion_skips_summary(self):
        self.profile.device_sync_pending = True
        saves = []
        self.app.profiles_store = SimpleNamespace(get=lambda _id: self.profile, save=lambda: saves.append(True))
        outcome = RunOutcome(journal.RunRecord("r", "p", "Profile", "auto", 1.0), marker_written=True)
        self.controller.active["p"] = object()
        self.controller.progress["p"] = {"phase": "copy"}
        self.controller._show_result(self.profile, False, outcome, None, lambda result: self.events.append(("done", result)))
        self.assertFalse(self.profile.device_sync_pending)
        self.assertEqual(saves, [True])
        self.assertFalse(any(event[0] in ("summary", "preview") for event in self.events))
        self.assertEqual(self.events[-1], ("done", outcome))

    def test_manual_success_shows_summary_while_nothing_changed_shows_info(self):
        outcome = RunOutcome(journal.RunRecord("r", "p", "Profile", "manual", 1.0))
        outcome.record.status = journal.OK
        outcome.record.jobs = [journal.JobResult("j", "s", "d", status=journal.OK, copied_files=1)]
        dialog = SimpleNamespace(run=lambda: self.events.append(("summary",)), destroy=lambda: None)
        with patch.object(run_controller, "SummaryDialog", return_value=dialog):
            self.controller._show_result(self.profile, False, outcome, None)
        self.assertIn(("summary",), self.events)
        self.events.clear()
        outcome.record.jobs[0].copied_files = 0
        self.controller._show_result(self.profile, False, outcome, None)
        self.assertTrue(any(event[0] == "info" for event in self.events))

    def test_stop_requests_cancel_on_active_runner(self):
        calls = []
        self.controller.active["p"] = SimpleNamespace(cancel=lambda reason: calls.append(reason))
        self.controller.stop("p")
        self.assertEqual(calls, ["user"])


if __name__ == "__main__":
    unittest.main()
