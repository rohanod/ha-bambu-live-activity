"""Runtime for Bambu Live Activity."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    ACTIVE_STATES,
    CONF_DEBOUNCE_SECONDS,
    CONF_ETA_THRESHOLD_MINUTES,
    CONF_NOTIFY_FAILED,
    CONF_NOTIFY_FINISHED,
    CONF_NOTIFY_SERVICE,
    CONF_PRINTER_SERIAL,
    CONF_PROGRESS_STEP,
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_ETA_THRESHOLD_MINUTES,
    DEFAULT_NOTIFY_FAILED,
    DEFAULT_NOTIFY_FINISHED,
    DEFAULT_PROGRESS_STEP,
    FAILED_STATES,
    FINISHED_STATES,
    IDLE_STATES,
    KEY_END_TIME,
    KEY_PRINT_PROGRESS,
    KEY_PRINT_STATUS,
    KEY_REMAINING_TIME,
    KEY_SUBTASK_NAME,
    LIVE_ACTIVITY_TAG_PREFIX,
    ROLLOVER_SECONDS,
    START_DELAY_SECONDS,
)
from .helpers import resolve_bambu_entities

_LOGGER = logging.getLogger(__name__)


class BambuLiveActivityRuntime:
    """Listen to ha-bambulab and maintain one Home Assistant Live Activity."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.serial: str = entry.data[CONF_PRINTER_SERIAL]
        self.notify_action: str = entry.data[CONF_NOTIFY_SERVICE]
        self.entities = resolve_bambu_entities(hass, self.serial)

        options = entry.options
        self.progress_step = int(
            options.get(CONF_PROGRESS_STEP, DEFAULT_PROGRESS_STEP)
        )
        self.eta_threshold = int(
            options.get(
                CONF_ETA_THRESHOLD_MINUTES, DEFAULT_ETA_THRESHOLD_MINUTES
            )
        )
        self.debounce_seconds = int(
            options.get(CONF_DEBOUNCE_SECONDS, DEFAULT_DEBOUNCE_SECONDS)
        )
        self.notify_finished = bool(
            options.get(CONF_NOTIFY_FINISHED, DEFAULT_NOTIFY_FINISHED)
        )
        self.notify_failed = bool(
            options.get(CONF_NOTIFY_FAILED, DEFAULT_NOTIFY_FAILED)
        )

        serial_fragment = "".join(ch for ch in self.serial if ch.isalnum())[-16:]
        self.tag = f"{LIVE_ACTIVITY_TAG_PREFIX}_{serial_fragment}"

        self._unsubs: list[Any] = []
        self._debounce_unsub: Any | None = None
        self._rollover_unsub: Any | None = None
        self._start_task: asyncio.Task | None = None
        self._activity_active = False
        self._activity_title: str | None = None

        self._last_progress: int | None = None
        self._last_remaining: int | None = None
        self._last_finish_ts: float | None = None

    async def async_start(self) -> None:
        """Start listeners and recover an activity after an HA restart."""
        self._unsubs.append(
            async_track_state_change_event(
                self.hass,
                list(self.entities.values()),
                self._handle_state_change,
            )
        )

        if self._status() in ACTIVE_STATES:
            self._queue_begin_activity(delay=3)

    async def async_stop(self, *, clear: bool = True) -> None:
        """Stop listeners and timers."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

        self._cancel_debounce()
        self._cancel_rollover()

        if self._start_task and not self._start_task.done():
            self._start_task.cancel()
            self._start_task = None

        if clear and self._activity_active:
            await self._async_clear_activity()

    @callback
    def _handle_state_change(self, event: Event) -> None:
        """Handle Bambu entity changes."""
        entity_id = event.data["entity_id"]
        old_state_obj = event.data.get("old_state")
        new_state_obj = event.data.get("new_state")
        old_value = old_state_obj.state if old_state_obj is not None else None
        new_value = new_state_obj.state if new_state_obj is not None else None

        if entity_id == self.entities[KEY_PRINT_STATUS]:
            self._handle_status_change(old_value, new_value)
            return

        if self._status() not in ACTIVE_STATES:
            return

        if entity_id == self.entities[KEY_PRINT_PROGRESS]:
            progress = self._safe_int(new_value)
            if progress is None:
                return
            if (
                self._last_progress is not None
                and abs(progress - self._last_progress) < self.progress_step
            ):
                return
            self._schedule_update()
            return

        if entity_id == self.entities[KEY_REMAINING_TIME]:
            remaining = self._safe_int(new_value)
            if remaining is None:
                return
            if (
                self._last_remaining is not None
                and abs(remaining - self._last_remaining) < self.eta_threshold
            ):
                return
            self._schedule_update()
            return

        if entity_id == self.entities[KEY_END_TIME]:
            finish_ts = self._parse_timestamp(new_value)
            if finish_ts is None:
                return
            if (
                self._last_finish_ts is not None
                and abs(finish_ts - self._last_finish_ts)
                < self.eta_threshold * 60
            ):
                return
            self._schedule_update()
            return

        if entity_id == self.entities[KEY_SUBTASK_NAME]:
            # Live Activity titles are static after launch. If the first push
            # had to fall back to the printer name, restart once the real task
            # name arrives.
            title = self._clean_text(new_value)
            if (
                self._activity_active
                and title
                and self._activity_title
                and title != self._activity_title
            ):
                self.hass.async_create_task(self._async_restart_activity())
            return

    @callback
    def _handle_status_change(
        self, old_status: str | None, new_status: str | None
    ) -> None:
        """Start, update, or end an activity on print-state changes."""
        if new_status in ACTIVE_STATES:
            if old_status not in ACTIVE_STATES:
                self._queue_begin_activity(delay=START_DELAY_SECONDS)
            else:
                # Pause/resume and prepare->running should show promptly.
                self._schedule_update(delay=1)
            return

        if new_status in FINISHED_STATES:
            self.hass.async_create_task(self._async_finish())
            return

        if new_status in FAILED_STATES:
            self.hass.async_create_task(self._async_fail())
            return

        if new_status in IDLE_STATES:
            self.hass.async_create_task(self._async_clear_activity())

    def _queue_begin_activity(self, *, delay: int | float) -> None:
        """Start or replace an activity after Bambu has populated metadata."""
        if self._start_task and not self._start_task.done():
            self._start_task.cancel()

        self._start_task = self.hass.async_create_task(
            self._async_begin_activity(delay=delay)
        )

    async def _async_begin_activity(self, *, delay: int | float) -> None:
        """Clear a stale activity, wait for metadata, then start a fresh one."""
        try:
            await self._async_clear_activity()
            if delay:
                await asyncio.sleep(delay)
            if self._status() not in ACTIVE_STATES:
                return
            await self._async_push_activity(starting=True)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never break HA for a notification
            _LOGGER.exception("Failed to start Bambu Live Activity")

    @callback
    def _schedule_update(self, *, delay: int | float | None = None) -> None:
        """Debounce a burst of Bambu MQTT sensor updates."""
        if not self._activity_active:
            return

        self._cancel_debounce()
        wait = self.debounce_seconds if delay is None else delay

        @callback
        def _send(_: datetime) -> None:
            self._debounce_unsub = None
            if self._status() in ACTIVE_STATES:
                self.hass.async_create_task(self._async_push_activity())

        self._debounce_unsub = async_call_later(self.hass, wait, _send)

    def _cancel_debounce(self) -> None:
        if self._debounce_unsub is not None:
            self._debounce_unsub()
            self._debounce_unsub = None

    def _cancel_rollover(self) -> None:
        if self._rollover_unsub is not None:
            self._rollover_unsub()
            self._rollover_unsub = None

    @callback
    def _schedule_rollover(self) -> None:
        """Restart before Apple's 8-hour Live Activity expiry."""
        self._cancel_rollover()

        @callback
        def _roll(_: datetime) -> None:
            self._rollover_unsub = None
            self.hass.async_create_task(self._async_rollover())

        self._rollover_unsub = async_call_later(
            self.hass, ROLLOVER_SECONDS, _roll
        )

    async def _async_rollover(self) -> None:
        """Replace a long-running activity with a fresh one."""
        if self._status() not in ACTIVE_STATES:
            return
        await self._async_clear_activity()
        await asyncio.sleep(2)
        if self._status() in ACTIVE_STATES:
            await self._async_push_activity(starting=True)

    async def _async_restart_activity(self) -> None:
        """Restart so a newly available static title can be applied."""
        if self._status() not in ACTIVE_STATES:
            return
        await self._async_clear_activity()
        await asyncio.sleep(2)
        if self._status() in ACTIVE_STATES:
            await self._async_push_activity(starting=True)

    async def _async_push_activity(self, *, starting: bool = False) -> None:
        """Send the current printer state to the HA Companion app."""
        progress = self._current_progress()
        remaining = self._current_remaining()
        finish_ts = self._current_finish_timestamp(remaining)
        title = self._current_title()
        finish_text = self._format_finish_time(finish_ts)
        remaining_text = self._format_remaining(remaining)

        payload = {
            "title": title,
            "message": f"{progress}% · {finish_text}",
            "data": {
                "tag": self.tag,
                "live_update": True,
                "critical_text": remaining_text,
                "progress": progress,
                "progress_max": 100,
                "notification_icon": "mdi:printer-3d",
            },
        }

        await self._async_notify(payload)

        self._activity_active = True
        if starting or self._activity_title is None:
            self._activity_title = title
            self._schedule_rollover()

        self._last_progress = progress
        self._last_remaining = remaining
        self._last_finish_ts = finish_ts

    async def _async_finish(self) -> None:
        """Send the finished alert, then end the Live Activity."""
        self._cancel_debounce()

        if self.notify_finished:
            title = self._current_title()
            await self._async_notify(
                {
                    "title": "Bambu printer",
                    "message": f"{title} finished printing.",
                    "data": {
                        "notification_icon": "mdi:printer-3d",
                        "tag": f"{self.tag}_finished",
                    },
                }
            )

        await self._async_clear_activity()

    async def _async_fail(self) -> None:
        """Send an optional failed alert, then end the Live Activity."""
        self._cancel_debounce()

        if self.notify_failed:
            title = self._current_title()
            await self._async_notify(
                {
                    "title": "Bambu printer",
                    "message": f"{title} failed to print.",
                    "data": {
                        "notification_icon": "mdi:printer-3d",
                        "tag": f"{self.tag}_failed",
                    },
                }
            )

        await self._async_clear_activity()

    async def _async_clear_activity(self) -> None:
        """End the current Live Activity."""
        self._cancel_debounce()
        self._cancel_rollover()

        try:
            await self._async_notify(
                {
                    "message": "clear_notification",
                    "data": {"tag": self.tag},
                }
            )
        except HomeAssistantError:
            _LOGGER.debug("Could not clear Live Activity", exc_info=True)

        self._activity_active = False
        self._activity_title = None
        self._last_progress = None
        self._last_remaining = None
        self._last_finish_ts = None

    async def _async_notify(self, data: dict[str, Any]) -> None:
        """Call the selected notify.mobile_app_* action."""
        try:
            domain, service = self.notify_action.split(".", 1)
        except ValueError as err:
            raise HomeAssistantError(
                f"Invalid notify action: {self.notify_action}"
            ) from err

        if domain != "notify":
            raise HomeAssistantError(
                f"Expected a notify.* action, got {self.notify_action}"
            )

        if not self.hass.services.has_service(domain, service):
            raise HomeAssistantError(
                f"Notify action is unavailable: {self.notify_action}"
            )

        await self.hass.services.async_call(
            domain,
            service,
            data,
            blocking=True,
        )

    def _status(self) -> str:
        state = self.hass.states.get(self.entities[KEY_PRINT_STATUS])
        return state.state.lower() if state is not None else "unknown"

    def _current_progress(self) -> int:
        state = self.hass.states.get(self.entities[KEY_PRINT_PROGRESS])
        value = self._safe_int(state.state if state is not None else None)
        return max(0, min(100, value if value is not None else 0))

    def _current_remaining(self) -> int:
        state = self.hass.states.get(self.entities[KEY_REMAINING_TIME])
        value = self._safe_int(state.state if state is not None else None)
        return max(0, value if value is not None else 0)

    def _current_title(self) -> str:
        state = self.hass.states.get(self.entities[KEY_SUBTASK_NAME])
        task = self._clean_text(state.state if state is not None else None)
        return task or self.entry.title or "Bambu printer"

    def _current_finish_timestamp(self, remaining: int) -> float:
        state = self.hass.states.get(self.entities[KEY_END_TIME])
        parsed = self._parse_timestamp(state.state if state is not None else None)
        if parsed is not None:
            return parsed
        return (dt_util.now() + timedelta(minutes=remaining)).timestamp()

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        try:
            if value in (None, "", "unknown", "unavailable"):
                return None
            return int(round(float(value)))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _clean_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text or text.lower() in {"unknown", "unavailable", "none"}:
            return None
        return text

    @staticmethod
    def _parse_timestamp(value: Any) -> float | None:
        if value in (None, "", "unknown", "unavailable"):
            return None
        parsed = dt_util.parse_datetime(str(value))
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        return parsed.timestamp()

    @staticmethod
    def _format_remaining(minutes: int) -> str:
        if minutes >= 60:
            hours, mins = divmod(minutes, 60)
            return f"{hours}h {mins:02d}m"
        return f"{minutes}m"

    @staticmethod
    def _format_finish_time(timestamp: float) -> str:
        local = dt_util.as_local(datetime.fromtimestamp(timestamp, dt_util.UTC))
        return local.strftime("%H:%M")
