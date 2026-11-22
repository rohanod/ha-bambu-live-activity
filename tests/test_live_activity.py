"""Live Activity regression tests. Fake HA/notify: never sends a real push."""
import asyncio
import copy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / "custom_components/bambu_live_activity"


def load_activity():
    modules = {}
    for name in ("homeassistant", "homeassistant.core", "homeassistant.exceptions",
                 "homeassistant.helpers", "homeassistant.helpers.entity_registry",
                 "homeassistant.helpers.device_registry",
                 "homeassistant.helpers.event", "homeassistant.helpers.storage",
                 "homeassistant.helpers.dispatcher",
                 "homeassistant.util", "homeassistant.util.dt", "bambu_test"):
        modules[name] = ModuleType(name)
    modules["bambu_test"].__path__ = [str(ROOT)]
    modules["homeassistant.core"].Event = object
    modules["homeassistant.core"].HomeAssistant = object
    modules["homeassistant.core"].callback = lambda f: f
    modules["homeassistant.exceptions"].HomeAssistantError = RuntimeError
    modules["homeassistant.helpers.entity_registry"].async_get = lambda hass: SimpleNamespace(entities={})
    modules["homeassistant.helpers.event"].async_call_later = lambda *args: lambda: None
    modules["homeassistant.helpers.event"].async_track_state_change_event = lambda *args: lambda: None
    modules["homeassistant.helpers.storage"].Store = lambda *args: None
    modules["homeassistant.helpers.dispatcher"].async_dispatcher_send = lambda *args: None
    dt = modules["homeassistant.util.dt"]
    dt.now = lambda: datetime.now(timezone.utc)
    dt.UTC = dt.DEFAULT_TIME_ZONE = timezone.utc
    dt.as_local = lambda d: d
    dt.parse_datetime = lambda s: datetime.fromisoformat(s) if s not in {"unknown", "unavailable"} else None
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location("bambu_test.live_activity", ROOT / "live_activity.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.test_helpers = sys.modules["bambu_test.helpers"]
    return module


activity = load_activity()


class TestPrinterResolution(unittest.TestCase):
    def test_bambuddy_resolution_uses_stable_ids_and_the_matching_device(self):
        helpers = activity.test_helpers
        prefix = "ENTRY_pSERIAL_"
        def entity(domain, key, name, device="printer", disabled_by=None):
            return SimpleNamespace(platform="bambuddy", domain=domain, device_id=device,
                                   unique_id=prefix + key, entity_id=f"{domain}.{name}", disabled_by=disabled_by)
        entries = [
            entity("sensor", "status", "renamed_status"),
            entity("sensor", "progress", "renamed_progress"),
            entity("sensor", "hms_status", "renamed_hms"),
            entity("button", "pause", "renamed_pause"),
            entity("button", "stop", "disabled_stop", disabled_by="user"),
            entity("light", "chamber_light", "another_device_light", device="other"),
        ]
        device = SimpleNamespace(id="printer", serial_number="SERIAL", identifiers={ ("bambuddy", "ENTRY_printer_SERIAL") })
        unrelated = SimpleNamespace(id="other", serial_number=None, identifiers={("other_integration", "network", "node")})
        with patch.object(helpers.er, "async_get", return_value=SimpleNamespace(entities=dict(enumerate(entries)))), \
             patch.object(helpers.dr, "async_get", create=True, return_value=SimpleNamespace(devices={"other": unrelated, "printer": device})):
            self.assertEqual(helpers.resolve_progress_entities(None, "SERIAL", "bambuddy"), {
                "print_status": "sensor.renamed_status", "print_progress": "sensor.renamed_progress",
                "hms": "sensor.renamed_hms", "pause": "button.renamed_pause",
            })


class Store:
    def __init__(self):
        self.data = None

    async def async_load(self):
        return copy.deepcopy(self.data)

    async def async_save(self, data):
        self.data = copy.deepcopy(data)


class TestActivity(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = 1_800_000_000.0
        self.states = {"print_status": "running", "print_progress": "25",
                       "end_time": datetime.fromtimestamp(self.clock + 7200, timezone.utc).isoformat(),
                       "remaining_time": "120"}
        self.pushes = []
        self.timers = []
        self.tasks = []
        self.store = Store()
        self.hass = SimpleNamespace(states=SimpleNamespace(get=self.get_state), async_create_task=self.task,
                                    services=SimpleNamespace(async_call=self.service))
        self.patches = [patch.object(activity.dt_util, "now", self.now),
                        patch.object(activity, "async_call_later", self.later)]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        self.la = self.make_activity()

    def now(self):
        return datetime.fromtimestamp(self.clock, timezone.utc)

    def get_state(self, entity):
        value = self.states.get(entity)
        return SimpleNamespace(state=value, attributes={}) if value is not None else None

    async def service(self, *args, **kwargs):
        pass

    async def notify(self, payload):
        self.pushes.append(payload)

    def task(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.append(task)
        return task

    def later(self, hass, delay, callback):
        timer = SimpleNamespace(at=self.clock + delay, callback=callback, cancelled=False)
        self.timers.append(timer)
        def cancel():
            timer.cancelled = True
        return cancel

    def make_activity(self):
        la = activity.PrintLiveActivity(self.hass, "SERIAL", lambda: "Cube", self.notify)
        la.entities = {key: key for key in self.states}
        la._store = self.store
        return la

    async def start(self):
        await self.la._async_push(starting=True)

    async def advance(self, seconds):
        self.clock += seconds
        for timer in list(self.timers):
            if not timer.cancelled and timer.at <= self.clock:
                timer.cancelled = True
                timer.callback(self.now())
        while self.tasks:
            tasks, self.tasks = self.tasks, []
            await asyncio.gather(*tasks)

    def eta(self, drift):
        self.states["end_time"] = datetime.fromtimestamp(1_800_000_000 + 7200 + drift, timezone.utc).isoformat()
        self.la._on_change(SimpleNamespace(data={"entity_id": "end_time", "new_state": self.get_state("end_time")}))

    async def test_progress_and_stable_eta_never_push(self):
        await self.start()
        self.states["print_progress"] = "80"
        self.la._on_change(SimpleNamespace(data={"entity_id": "print_progress", "new_state": self.get_state("print_progress")}))
        await self.advance(3600)
        await self.la._async_push()
        self.assertEqual(len(self.pushes), 1)

    async def test_running_shows_watch_minutes_without_seconds_or_progress(self):
        await self.start()
        data = self.pushes[0]["data"]
        self.assertFalse(data["chronometer"])
        self.assertNotIn("when", data)
        self.assertNotIn("progress", data)
        self.assertEqual(data["critical_text"], "2h 0m")
        await self.advance(3600)
        self.assertEqual(len(self.pushes), 1)

    async def test_smaller_percentage_drift_waits_for_cooldown(self):
        await self.start()
        self.eta(300)
        await self.advance(599)
        self.assertEqual(len(self.pushes), 1)
        await self.advance(1)
        self.assertEqual(self.pushes[-1]["data"]["critical_text"], "1h 55m")
        self.assertTrue(self.pushes[-1]["data"]["silent"])

    async def test_drift_over_fifty_percent_is_immediate(self):
        await self.start()
        self.eta(-3601)
        await self.advance(0)
        self.assertEqual(len(self.pushes), 2)
        self.assertEqual(self.pushes[-1]["data"]["critical_text"], "1h 0m")

    async def test_exactly_fifty_percent_still_waits(self):
        await self.start()
        self.eta(3600)
        await self.advance(0)
        self.assertEqual(len(self.pushes), 1)

    async def test_bambuddy_hms_warns_once_and_recovers(self):
        self.states["print_status"] = "printing"
        self.la.entities["hms"] = "hms"
        await self.start()
        errors = [{"code": "07FF_8010", "description": "Check the filament spool"}]
        current = SimpleNamespace(state="1", attributes={"errors": errors})
        with patch.object(self.hass.states, "get", side_effect=lambda key: current if key == "hms" else self.get_state(key)):
            event = SimpleNamespace(data={"entity_id": "hms", "new_state": current})
            self.la._on_change(event)
            await self.advance(0)
            self.assertEqual(self.pushes[-2]["data"]["notification_icon"], "mdi:alert")
            self.assertIn("07FF_8010", self.pushes[-1]["message"])
            self.la._on_change(event)
            await self.advance(0)
            self.assertEqual(len(self.pushes), 3)
            current.attributes["errors"] = []
            self.la._on_change(event)
            await self.advance(0)
            self.assertEqual(len(self.pushes), 4)
            self.assertEqual(self.pushes[-1]["data"]["notification_icon"], "mdi:printer-3d-nozzle")

    async def test_bambuddy_finished_signal_runs_completion_once(self):
        finished = []
        async def on_finish():
            finished.append(True)
            await self.la.async_finish()
        self.la.on_sensor_finish = on_finish
        self.states["print_status"] = "printing"
        await self.start()
        self.states["print_status"] = "finished"
        event = SimpleNamespace(data={"entity_id": "print_status", "new_state": self.get_state("print_status")})
        self.la._on_change(event)
        await self.advance(30)
        self.la._on_change(event)
        await self.advance(30)
        self.assertEqual(finished, [True])
        self.assertEqual(self.pushes[-1]["message"], "clear_notification")

    async def test_pause_and_unknown_eta_disable_native_countdown(self):
        for status in ("pause", "prepare", "slicing", "running"):
            with self.subTest(status=status):
                self.states["print_status"] = status
                self.states["end_time"] = "unavailable"
                await self.la._async_push(starting=True)
                data = self.pushes[-1]["data"]
                self.assertIs(data.get("chronometer"), False)
                self.assertNotIn("when", data)
                if status == "running":
                    self.assertEqual(self.pushes[-1]["message"], "ETA unavailable")

    async def test_preparing_with_valid_eta_shows_minutes(self):
        self.states["print_status"] = "prepare"
        await self.start()
        self.assertEqual(self.pushes[-1]["data"]["critical_text"], "2h 0m")

    async def test_paused_payload_has_no_live_timer(self):
        self.states["print_status"] = "pause"
        await self.start()
        self.assertIs(self.pushes[-1]["data"].get("chronometer"), False)
        self.assertNotIn("when", self.pushes[-1]["data"])

    async def test_small_drift_no_update(self):
        await self.start()
        await self.advance(601)
        self.eta(179)
        await self.advance(3)
        self.assertEqual(len(self.pushes), 1)

    async def test_drift_burst_one_update_at_cooldown(self):
        await self.start()
        self.eta(300)
        self.eta(360)
        self.eta(420)
        await self.advance(599)
        self.assertEqual(len(self.pushes), 1)
        await self.advance(1)
        self.assertEqual(len(self.pushes), 2)
        self.assertEqual(self.la._last_eta, 1_800_000_000 + 7620)

    async def test_pending_drift_can_disappear(self):
        await self.start()
        self.eta(300)
        self.eta(60)
        await self.advance(600)
        self.assertEqual(len(self.pushes), 1)

    async def test_unknown_eta_does_not_push(self):
        await self.start()
        self.states["end_time"] = "unavailable"
        await self.advance(601)
        await self.la._async_push()
        self.assertEqual(len(self.pushes), 1)

    async def test_restart_restores_cooldown_and_does_not_start(self):
        await self.start()
        await self.la.async_stop()
        self.la = self.make_activity()
        await self.la.async_start()
        self.eta(300)
        await self.advance(599)
        self.assertEqual(len(self.pushes), 1)
        await self.advance(1)
        self.assertEqual(len(self.pushes), 2)
        self.assertEqual(self.la.starts, 0)

    async def test_pause_resume_update_immediately_without_clear_or_restart(self):
        await self.start()
        for status in ("pause", "running"):
            self.states["print_status"] = status
            self.la._on_change(SimpleNamespace(data={"entity_id": "print_status", "new_state": self.get_state("print_status")}))
            await self.advance(0)
            self.assertEqual(self.pushes[-1]["data"]["critical_text"], "2h 0m" if status == "running" else "Paused")
        self.assertEqual(len(self.pushes), 3)
        self.assertEqual(self.la.starts, 1)
        self.assertFalse(any(p["message"] == "clear_notification" for p in self.pushes))
        self.assertTrue(self.la._active)

    async def test_prep_to_running_updates_immediately_with_unchanged_eta(self):
        self.states["print_status"] = "prepare"
        await self.start()
        self.states["print_status"] = "running"
        self.la._on_change(SimpleNamespace(data={"entity_id":"print_status", "new_state":self.get_state("print_status")}))
        await self.advance(0)
        self.assertEqual(len(self.pushes), 2)
        self.assertEqual(self.pushes[-1]["message"], "Printing")
        self.assertEqual(self.la.starts, 1)
        self.assertEqual(self.pushes[-1]["data"]["tag"], self.pushes[0]["data"]["tag"])

    async def test_first_valid_eta_updates_immediately(self):
        self.states["end_time"] = "unavailable"
        await self.start()
        self.eta(0)
        await self.advance(0)
        self.assertEqual(len(self.pushes), 2)
        self.assertEqual(self.pushes[-1]["data"]["critical_text"], "2h 0m")
        self.eta(60)
        await self.advance(0)
        self.assertEqual(len(self.pushes), 2)

    async def test_same_status_events_do_not_push(self):
        await self.start()
        for _ in range(20):
            self.la._on_change(SimpleNamespace(data={"entity_id":"print_status", "new_state":self.get_state("print_status")}))
        await self.advance(0)
        self.assertEqual(len(self.pushes), 1)

    async def test_state_change_failure_retries_with_backoff(self):
        await self.start()
        attempts = []
        async def fail(payload):
            attempts.append(payload)
            raise RuntimeError("offline")
        self.la.notify = fail
        self.states["print_status"] = "pause"
        self.la._on_change(SimpleNamespace(data={"entity_id":"print_status", "new_state":self.get_state("print_status")}))
        with self.assertLogs(activity.__name__, level="ERROR"):
            await self.advance(0)
        for _ in range(20):
            self.la._on_change(SimpleNamespace(data={"entity_id":"print_status", "new_state":self.get_state("print_status")}))
        await self.advance(599)
        self.assertEqual(len(attempts), 1)
        self.la.notify = self.notify
        await self.advance(1)
        self.assertEqual(len(self.pushes), 2)
        self.assertEqual(self.pushes[-1]["data"]["critical_text"], "Paused")

    async def test_state_failure_backoff_survives_restart(self):
        await self.start()
        async def fail(payload):
            raise RuntimeError("offline")
        self.la.notify = fail
        self.states["print_status"] = "pause"
        with self.assertLogs(activity.__name__, level="ERROR"):
            await self.la._async_push()
        await self.la.async_stop()
        self.la = self.make_activity()
        await self.la.async_start()
        await self.advance(599)
        self.assertEqual(len(self.pushes), 1)
        await self.advance(1)
        self.assertEqual(len(self.pushes), 2)
        self.assertEqual(self.la.starts, 0)

    async def test_upgrade_synchronizes_existing_activity_once(self):
        await self.start()
        self.store.data.pop("display_version")
        await self.la.async_stop()
        self.la = self.make_activity()
        await self.la.async_start()
        await self.advance(0)
        self.assertEqual(len(self.pushes), 2)
        self.assertEqual(self.la.starts, 0)
        await self.la.async_stop()
        self.la = self.make_activity()
        await self.la.async_start()
        await self.advance(0)
        self.assertEqual(len(self.pushes), 2)

    async def test_state_changed_during_notify_is_published_next(self):
        await self.start()
        async def resume_while_sending(payload):
            self.pushes.append(payload)
            if payload["data"]["critical_text"] == "Paused":
                self.states["print_status"] = "running"
                self.la._on_change(SimpleNamespace(data={"entity_id":"print_status", "new_state":self.get_state("print_status")}))
        self.la.notify = resume_while_sending
        self.states["print_status"] = "pause"
        self.la._on_change(SimpleNamespace(data={"entity_id":"print_status", "new_state":self.get_state("print_status")}))
        await self.advance(0)
        self.assertEqual(len(self.pushes), 3)
        self.assertEqual(self.la.snapshot()["last_published_status"], "running")
        self.assertEqual(self.pushes[-1]["data"]["critical_text"], "2h 0m")

    async def test_terminal_clear_serialized_once(self):
        await self.start()
        published = (self.la._last_push_at, self.la._last_eta)
        await asyncio.gather(self.la.async_finish(), self.la.async_finish())
        self.assertEqual([p["message"] for p in self.pushes].count("clear_notification"), 1)
        self.assertFalse(self.store.data["active"])
        self.assertEqual((self.store.data["last_push_at"], self.store.data["last_eta"]), published)

    async def test_failed_clear_does_not_end_then_restart(self):
        await self.start()
        async def fail(payload):
            raise RuntimeError("offline")
        self.la.notify = fail
        with self.assertLogs(activity.__name__, level="WARNING"):
            await self.la.async_finish()
        self.assertTrue(self.la._active)
        self.assertTrue(self.store.data["active"])

    async def test_duplicate_start_event_sends_once(self):
        self.la._start_new_activity()
        self.la.on_print_started()
        self.la.on_print_started()
        with patch.object(activity.asyncio, "sleep", return_value=None):
            await asyncio.gather(*self.tasks)
        self.assertEqual(len(self.pushes), 1)

    async def test_failed_start_events_cannot_bypass_backoff(self):
        attempts = []
        async def fail(payload):
            attempts.append(payload)
            raise RuntimeError("offline")
        self.la.notify = fail
        with self.assertLogs(activity.__name__, level="ERROR"):
            await self.la._async_push(starting=True)
        self.la.on_print_started()
        self.assertIsNone(self.la._start_task)
        self.assertEqual(len(attempts), 1)

    async def test_unload_blocks_queued_pushes(self):
        await self.start()
        self.eta(300)
        await self.la.async_stop()
        self.clock += 601
        await self.la._async_push()
        self.la.on_print_started()
        self.assertEqual(len(self.pushes), 1)
        self.assertIsNone(self.la._start_task)

    async def test_status_cleanup_preserves_completion_event_eligibility(self):
        await self.start()
        await self.la.async_finish()
        self.assertTrue(self.la.consume_print_seen())
        self.assertFalse(self.la.consume_print_seen())

    async def test_rollover_deadline_survives_restart(self):
        await self.start()
        await self.advance(7200)
        await self.la.async_stop()
        self.la = self.make_activity()
        await self.la.async_start()
        self.assertEqual(self.la._next_rollover_at, 1_800_000_000 + activity.LONG_PRINT_ROLLOVER_SECONDS)

    async def test_configurable_drift_and_negative_drift(self):
        self.la.options = {"eta_drift_percent": 5}
        await self.start()
        self.eta(-240)
        await self.advance(601)
        self.assertEqual(len(self.pushes), 1)
        self.eta(-340)
        await self.advance(2)
        self.assertEqual(len(self.pushes), 2)

    async def test_failed_clear_retry_does_not_clear_new_running_print(self):
        await self.start()
        self.states["print_status"] = "failed"
        async def fail(payload):
            raise RuntimeError("offline")
        self.la.notify = fail
        with self.assertLogs(activity.__name__, level="WARNING"):
            await self.la.async_finish()
        self.la.notify = self.notify
        self.states["print_status"] = "running"
        await self.advance(600)
        self.assertEqual(len(self.pushes), 1)

    async def test_concurrent_updates_recheck_gate_under_lock(self):
        await self.start()
        self.eta(300)
        self.clock += 600
        await asyncio.gather(self.la._async_push(), self.la._async_push())
        self.assertEqual(len(self.pushes), 2)

    async def test_stopped_status_flap_does_not_clear_or_restart(self):
        await self.start()
        for status in ("idle", "running"):
            self.states["print_status"] = status
            self.la._on_change(SimpleNamespace(data={"entity_id":"print_status","new_state":self.get_state("print_status")}))
        await self.advance(31)
        self.assertEqual(len(self.pushes), 1)

    async def test_confirmed_stopped_status_clears_after_debounce(self):
        await self.start()
        self.states["print_status"] = "finish"
        self.la._on_change(SimpleNamespace(data={"entity_id":"print_status","new_state":self.get_state("print_status")}))
        await self.advance(30)
        self.assertEqual(self.pushes[-1]["message"], "clear_notification")

    async def test_stopped_state_publishes_immediately_and_flap_resumes_in_place(self):
        await self.start()
        self.states["print_status"] = "idle"
        self.la._on_change(SimpleNamespace(data={"entity_id":"print_status", "new_state":self.get_state("print_status")}))
        await self.advance(0)
        self.assertEqual(len(self.pushes), 2)
        self.assertEqual(self.pushes[-1]["message"], "Stopped")
        self.assertFalse(self.pushes[-1]["data"]["chronometer"])
        self.assertTrue(self.la._active)
        self.states["print_status"] = "running"
        self.la._on_change(SimpleNamespace(data={"entity_id":"print_status", "new_state":self.get_state("print_status")}))
        await self.advance(31)
        self.assertEqual(len(self.pushes), 3)
        self.assertEqual(self.la.starts, 1)
        self.assertFalse(any(p["message"] == "clear_notification" for p in self.pushes))

    async def test_terminal_state_labels_update_immediately(self):
        for status, label in (("finish", "Finished"), ("failed", "Failed")):
            self.states["print_status"] = "running"
            await self.start()
            self.states["print_status"] = status
            self.la._on_change(SimpleNamespace(data={"entity_id":"print_status", "new_state":self.get_state("print_status")}))
            await self.advance(0)
            self.assertEqual(self.pushes[-1]["message"], label)
            self.assertEqual(self.pushes[-1]["data"]["critical_text"], label)

    async def test_drift_while_notify_in_flight_is_rechecked(self):
        await self.start()
        self.eta(300)
        async def changing_notify(payload):
            self.pushes.append(payload)
            self.eta(600)
        self.la.notify = changing_notify
        await self.advance(600)
        self.assertEqual(len(self.pushes), 2)
        self.assertIsNotNone(self.la._update_unsub)

    async def test_failure_keeps_published_baseline_and_backs_off(self):
        await self.start()
        baseline = self.la._last_eta
        async def fail(payload):
            raise RuntimeError("offline")
        self.la.notify = fail
        self.states["end_time"] = datetime.fromtimestamp(baseline + 300, timezone.utc).isoformat()
        await self.advance(600)
        with self.assertLogs(activity.__name__, level="ERROR"):
            await self.la._async_push()
        self.assertEqual(self.la._last_eta, baseline)
        self.assertEqual(self.store.data["last_eta"], baseline)
        self.la.notify = self.notify
        await self.la._async_push()
        self.assertEqual(len(self.pushes), 1)

    async def test_url_on_start_and_update(self):
        await self.start()
        self.eta(300)
        await self.advance(600)
        self.assertEqual(len(self.pushes), 2)
        self.assertTrue(all(p["data"].get("url") == "/bambu-live-activity-serial/print" for p in self.pushes))


if __name__ == "__main__":
    unittest.main()
