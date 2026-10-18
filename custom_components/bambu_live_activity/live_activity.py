"""Quiet progress Live Activity; preserves the existing completion notifier."""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import math
from typing import Any, Awaitable, Callable

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import BAMBU_DOMAIN

_LOGGER = logging.getLogger(__name__)

ACTIVE_STATES = {"prepare", "running", "pause", "slicing"}
STOPPED_STATES = {"finish", "failed", "idle"}
COALESCE_SECONDS = 2.0
MIN_UPDATE_INTERVAL = 15.0
HEARTBEAT_SECONDS = 60.0
NAME_WAIT_SECONDS = 30.0
LONG_PRINT_ROLLOVER_SECONDS = 7.5 * 60 * 60


def resolve_progress_entities(hass: HomeAssistant, serial: str) -> dict[str, str]:
    """Follow ha-bambulab unique IDs instead of fragile user-renamable IDs."""
    keys = {"print_status", "print_progress", "end_time", "remaining_time", "subtask_name"}
    wanted = {f"{serial}_{key}": key for key in keys}
    result: dict[str, str] = {}
    for entry in er.async_get(hass).entities.values():
        if entry.platform == BAMBU_DOMAIN and entry.unique_id in wanted:
            result[wanted[entry.unique_id]] = entry.entity_id
    return result


# Per-state look: (accent for icon + bar, Lock Screen card, status word).
STYLES = {
    "running": ("#3DDC84", "#0B1A12", "Printing"),
    "pause": ("#FFB84D", "#1F1606", "Paused"),
    "prepare": ("#7FA8FF", "#0C1322", "Preparing"),
    "slicing": ("#7FA8FF", "#0C1322", "Preparing"),
}


def build_payload(
    tag: str, title: str, status: str, progress: int, rem_text: str, finish_text: str, starting: bool
) -> dict[str, Any]:
    accent, card, word = STYLES.get(status, STYLES["running"])
    if status == "running":
        message = f"{finish_text} finish"  # short: Watch Smart Stack truncates long lines
    elif status == "pause":
        message = f"{word} at {progress}%"
    else:
        message = f"{word}…"
    data: dict[str, Any] = {
        "tag": tag,
        "live_update": True,
        "progress": progress,
        "progress_max": 100,
        # Compact view (Dynamic Island, Watch): keep it to one short token.
        "critical_text": "Paused" if status == "pause" else "Prep" if status in ("prepare", "slicing") else rem_text,
        "alert_once": True,
        "notification_icon": "mdi:printer-pause" if status == "pause" else "mdi:printer-3d-nozzle",
        "notification_icon_color": accent,
        "progress_bar_color": accent,
        "background_color": card,
        "text_color": "#F2F5F3",
    }
    # Updates are quiet; the initial push may alert once.
    if not starting:
        data["silent"] = True
    return {"title": title, "message": message, "data": data}


class PrintLiveActivity:
    """Keep one Live Activity updated without repeated banners/vibrations."""

    def __init__(
        self,
        hass: HomeAssistant,
        serial: str,
        get_name: Callable[[], str | None],
        notify: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self.hass = hass
        self.serial = serial
        self.get_name = get_name
        self.notify = notify
        self.entities = resolve_progress_entities(hass, serial)
        # Same tag as 0.1.4, so it updates rather than multiplying activities.
        self.tag = f"bambu_print_{''.join(c for c in serial if c.isalnum())[-16:]}"
        self._unsub: Any | None = None
        self._update_unsub: Any | None = None
        self._heartbeat_unsub: Any | None = None
        self._rollover_unsub: Any | None = None
        self._start_task: asyncio.Task | None = None
        self._push_lock = asyncio.Lock()
        self._generation = 0
        self._active = False
        self._title: str | None = None
        self._last_push_at = 0.0
        self._last_progress: int | None = None
        self._last_payload: tuple[Any, ...] | None = None
        self._next_rollover_at = 0.0
        self._retry_unsub: Any | None = None
        # True once this run has seen the printer actually printing.
        self._print_seen = False

    @property
    def activity_title(self) -> str | None:
        return self._title

    async def async_start(self) -> None:
        if "print_status" not in self.entities or "print_progress" not in self.entities:
            _LOGGER.warning("Missing Bambu print status/progress sensors; Live Activity disabled")
            return
        self._unsub = async_track_state_change_event(
            self.hass,
            list(self.entities.values()),
            self._on_change,
        )
        if self._state("print_status") in ACTIVE_STATES:
            self._print_seen = True
            self._start_new_activity()

    def consume_print_seen(self) -> bool:
        """True once per observed print; blocks replayed/stale finish events."""
        seen, self._print_seen = self._print_seen, False
        return seen

    async def async_stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        await self._async_clear()

    @callback
    def on_print_started(self) -> None:
        """Called by the existing ha-bambulab lifecycle event listener."""
        if self._state("print_status") in ACTIVE_STATES and not self._active:
            self._start_new_activity()

    async def async_finish(self) -> None:
        await self._async_clear()

    @callback
    def _on_change(self, event: Event) -> None:
        entity_id = event.data.get("entity_id")
        new = event.data.get("new_state")
        old = event.data.get("old_state")
        if new is None:
            return

        if entity_id == self.entities.get("print_status"):
            status = str(new.state).lower()
            old_status = str(old.state).lower() if old else ""
            if status in ACTIVE_STATES:
                self._print_seen = True
                if not self._active and (self._start_task is None or self._start_task.done()):
                    self._start_new_activity()
                elif self._active and old_status != status:
                    self._schedule_update(urgent=True)
            elif status in STOPPED_STATES:
                self.hass.async_create_task(self._async_clear())
            return

        if not self._active or self._state("print_status") not in ACTIVE_STATES:
            return
        if entity_id == self.entities.get("print_progress"):
            progress = self._progress()
            if self._last_progress is None or progress != self._last_progress:
                self._schedule_update()
        # ETA and remaining-time sensor changes do not start their own push;
        # the next percentage change or minute refresh carries the newest ETA.

    @callback
    def _start_new_activity(self) -> None:
        if self._active or (self._start_task and not self._start_task.done()):
            return
        self._generation += 1
        gen = self._generation
        self._start_task = self.hass.async_create_task(self._async_begin(gen))

    async def _async_begin(self, gen: int) -> None:
        """Wait briefly for a real model name before setting the static title."""
        try:
            # Bambu may still expose the PREVIOUS model name/progress on its
            # initial PREPARE update. Allow fresh MQTT metadata to arrive.
            await asyncio.sleep(6)
            for _ in range(int((NAME_WAIT_SECONDS - 6) / 2)):
                if gen != self._generation or self._state("print_status") not in ACTIVE_STATES:
                    return
                name = self.get_name()
                if name and self._progress() < 100:
                    break
                await asyncio.sleep(2)
            if gen != self._generation or self._state("print_status") not in ACTIVE_STATES:
                return
            if self._progress() >= 100:
                # Ignore stale 100% from the preceding completed print.
                self._retry_start_later()
                return
            self._title = self.get_name() or "A1 mini"
            await self._async_push(starting=True, force=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Could not start Bambu Live Activity")

    @callback
    def _schedule_update(self, *, urgent: bool = False) -> None:
        if not self._active or self._state("print_status") not in ACTIVE_STATES:
            return
        # Never reset an already-scheduled update on each incoming MQTT event.
        if self._update_unsub is not None:
            return
        now = asyncio.get_running_loop().time()
        remaining_gap = max(0.0, MIN_UPDATE_INTERVAL - (now - self._last_push_at))
        delay = 0.5 if urgent else max(COALESCE_SECONDS, remaining_gap)

        @callback
        def send(_: datetime) -> None:
            self._update_unsub = None
            if self._active and self._state("print_status") in ACTIVE_STATES:
                self.hass.async_create_task(self._async_push())

        self._update_unsub = async_call_later(self.hass, delay, send)

    @callback
    def _schedule_heartbeat(self) -> None:
        self._cancel_timer("_heartbeat_unsub")

        @callback
        def tick(_: datetime) -> None:
            self._heartbeat_unsub = None
            if self._active and self._state("print_status") in ACTIVE_STATES:
                self.hass.async_create_task(self._async_push(force=True))

        self._heartbeat_unsub = async_call_later(self.hass, HEARTBEAT_SECONDS, tick)

    @callback
    def _schedule_rollover(self) -> None:
        self._cancel_timer("_rollover_unsub")
        self._next_rollover_at = asyncio.get_running_loop().time() + LONG_PRINT_ROLLOVER_SECONDS

        @callback
        def tick(_: datetime) -> None:
            self._rollover_unsub = None
            if self._active and self._state("print_status") in ACTIVE_STATES:
                self.hass.async_create_task(self._async_rollover())

        self._rollover_unsub = async_call_later(
            self.hass, LONG_PRINT_ROLLOVER_SECONDS, tick
        )

    @callback
    def _retry_start_later(self) -> None:
        self._cancel_timer("_retry_unsub")

        @callback
        def retry(_: datetime) -> None:
            self._retry_unsub = None
            if not self._active and self._state("print_status") in ACTIVE_STATES:
                self._start_new_activity()

        self._retry_unsub = async_call_later(self.hass, 30, retry)

    async def _async_rollover(self) -> None:
        """Apple expires an activity after eight hours; renew only then."""
        if self._state("print_status") not in ACTIVE_STATES:
            return
        await self._async_clear()
        if self._state("print_status") in ACTIVE_STATES:
            self._start_new_activity()

    async def _async_push(self, *, starting: bool = False, force: bool = False) -> None:
        async with self._push_lock:
            if self._state("print_status") not in ACTIVE_STATES:
                return
            progress = self._progress()
            end_timestamp = self._valid_end_timestamp()
            remaining = self._remaining_minutes(end_timestamp)
            finish_text = (
                dt_util.as_local(datetime.fromtimestamp(end_timestamp, dt_util.UTC)).strftime("%H:%M")
                if end_timestamp is not None else "--:--"
            )
            rem_text = (
                f"{remaining // 60}h {remaining % 60:02d}m"
                if remaining is not None and remaining >= 60
                else f"{remaining}m" if remaining is not None else "--"
            )
            title = self._title or self.get_name() or "A1 mini"
            data_key = (progress, rem_text, finish_text, self._state("print_status"))
            if not force and self._last_payload == data_key:
                return

            payload = build_payload(
                self.tag, title, self._state("print_status"), progress, rem_text, finish_text, starting
            )
            try:
                await self.notify(payload)
            except Exception:
                _LOGGER.exception("Failed to update Bambu Live Activity")
                if self._state("print_status") in ACTIVE_STATES:
                    if starting:
                        self._retry_start_later()
                    else:
                        # Keep retrying on the once-a-minute heartbeat if
                        # mobile notifications temporarily fail.
                        self._schedule_heartbeat()
                return
            self._active = True
            self._title = title
            self._last_payload = data_key
            self._last_progress = progress
            self._last_push_at = asyncio.get_running_loop().time()
            self._schedule_heartbeat()
            if starting:
                self._schedule_rollover()

    async def async_demo(self) -> None:
        """Play a short fake print on its own tag so users can preview the look."""
        tag = f"{self.tag}_test"
        steps = [("prepare", 0, "12m"), ("running", 20, "10m"), ("running", 55, "5m"),
                 ("pause", 55, "5m"), ("running", 80, "2m"), ("running", 100, "1m")]
        finish = dt_util.now().strftime("%H:%M")
        for i, (status, progress, rem) in enumerate(steps):
            await self.notify(build_payload(tag, "Test print", status, progress, rem, finish, i == 0))
            await asyncio.sleep(4)
        await self.notify({"message": "clear_notification", "data": {"tag": tag}})

    async def _async_clear(self) -> None:
        self._generation += 1
        task = self._start_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
        self._start_task = None
        self._cancel_timer("_update_unsub")
        self._cancel_timer("_heartbeat_unsub")
        self._cancel_timer("_rollover_unsub")
        self._cancel_timer("_retry_unsub")
        async with self._push_lock:
            if self._active:
                try:
                    await self.notify({"message": "clear_notification", "data": {"tag": self.tag}})
                except Exception:
                    _LOGGER.warning("Unable to clear Bambu Live Activity", exc_info=True)
            self._active = False
            self._title = None
            self._last_payload = None
            self._last_progress = None
            self._last_push_at = 0.0

    @callback
    def _cancel_timer(self, attr: str) -> None:
        cancel = getattr(self, attr)
        if cancel is not None:
            cancel()
            setattr(self, attr, None)

    def _state(self, key: str) -> str:
        entity_id = self.entities.get(key)
        obj = self.hass.states.get(entity_id) if entity_id else None
        return str(obj.state).lower() if obj is not None else "unknown"

    def _progress(self) -> int:
        try:
            return max(0, min(100, int(float(self._state("print_progress")))))
        except (TypeError, ValueError):
            return 0

    def _valid_end_timestamp(self) -> float | None:
        entity_id = self.entities.get("end_time")
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None:
            return None
        raw = str(state.state)
        parsed = dt_util.parse_datetime(raw)
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        timestamp = parsed.timestamp()
        # Ignore the previous print's expired ETA and absurd dates.
        diff = timestamp - dt_util.now().timestamp()
        return timestamp if 0 < diff < 14 * 24 * 3600 else None

    def _remaining_minutes(self, end_timestamp: float | None) -> int | None:
        if end_timestamp is not None:
            return max(1, math.ceil((end_timestamp - dt_util.now().timestamp()) / 60))
        entity_id = self.entities.get("remaining_time")
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None:
            return None
        try:
            remaining = float(state.state)
        except (TypeError, ValueError):
            return None
        unit = str(state.attributes.get("unit_of_measurement", "min")).lower()
        if unit in ("h", "hour", "hours"):
            remaining *= 60
        elif unit in ("s", "sec", "seconds"):
            remaining /= 60
        return max(0, math.ceil(remaining)) if remaining > 0 else None
