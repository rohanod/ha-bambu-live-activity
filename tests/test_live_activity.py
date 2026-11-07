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
    return module


activity = load_activity()


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
        if self.tasks:
            await asyncio.gather(*self.tasks)
            self.tasks.clear()

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

    async def test_pause_resume_no_push_or_clear(self):
        await self.start()
        for status in ("pause", "running", "pause", "running"):
            self.states["print_status"] = status
            self.la._on_change(SimpleNamespace(data={"entity_id": "print_status", "new_state": self.get_state("print_status")}))
        await self.advance(601)
        self.assertEqual(len(self.pushes), 1)
        self.assertTrue(self.la._active)

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
        self.la.options = {"eta_drift_minutes": 5}
        await self.start()
        self.eta(-240)
        await self.advance(601)
        self.assertEqual(len(self.pushes), 1)
        self.eta(-300)
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
