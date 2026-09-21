"""Runtime for Bambu Live Activity."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import math
from pathlib import PurePath
import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    ACTIVE_STATES,
    BAMBU_DOMAIN,
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

_BAD_TITLE_PATTERNS = (
    re.compile(r"^\d+#profileid[-_#]", re.IGNORECASE),
    re.compile(r"^profileid[-_#]", re.IGNORECASE),
    re.compile(r"^\d{6,}$"),
)


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
        self._coalesce_unsub: Any | None = None
        self._minute_unsub: Any | None = None
        self._rollover_unsub: Any | None = None
        self._start_task: asyncio.Task | None = None
        self._activity_active = False
        self._activity_title: str | None = None

        self._last_progress: int | None = None
        self._last_remaining: int | None = None
        self._last_finish_ts: float | None = None
        self._last_payload: tuple[Any, ...] | None = None

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

        self._cancel_coalesce()
        self._cancel_minute_tick()
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
            # Every 1% by default. Multiple MQTT fields that arrive together
            # are coalesced into one push instead of continually resetting a
            # debounce timer.
            self._schedule_update()
            return

        if entity_id in {
            self.entities[KEY_REMAINING_TIME],
            self.entities[KEY_END_TIME],
        }:
            # ETA/end-time changes are intentionally NOT separate push
            # triggers. The next 1% progress update or one-minute heartbeat
            # carries the newest ETA. This keeps the Live Activity current
            # without making every Bambu MQTT change feel like a notification.
            return

        if entity_id == self.entities[KEY_SUBTASK_NAME]:
            # The ActivityKit title is static. Do NOT clear/recreate the Live
            # Activity when Bambu metadata changes; doing so feels like a new
            # notification. The next print will use the newest title.
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
                self._schedule_update(delay=0.5)
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

            # Bambu can enter PREPARE/RUNNING before the human task title
            # arrives. Wait up to 30 seconds so ActivityKit's static title is
            # the real print name instead of "A1 mini" or a profile ID.
            for _ in range(15):
                if self._human_title() is not None:
                    break
                await asyncio.sleep(2)
                if self._status() not in ACTIVE_STATES:
                    return

            await self._async_push_activity(starting=True, force=True)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never break HA for a notification
            _LOGGER.exception("Failed to start Bambu Live Activity")

    @callback
    def _schedule_update(self, *, delay: int | float | None = None) -> None:
        """Coalesce a burst of MQTT changes without starving updates.

        Important difference from a classic debounce: once an update has been
        scheduled, later MQTT events do not push it farther into the future.
        This guarantees that a busy printer cannot leave the activity stale.
        """
        if not self._activity_active:
            return

        wait = self.debounce_seconds if delay is None else delay

        if self._coalesce_unsub is not None:
            # An urgent status transition can shorten an already queued update.
            if wait <= 1:
                self._cancel_coalesce()
            else:
                return

        @callback
        def _send(_: datetime) -> None:
            self._coalesce_unsub = None
            if self._status() in ACTIVE_STATES:
                self.hass.async_create_task(self._async_push_activity())

        self._coalesce_unsub = async_call_later(self.hass, wait, _send)

    def _cancel_coalesce(self) -> None:
        if self._coalesce_unsub is not None:
            self._coalesce_unsub()
            self._coalesce_unsub = None

    def _cancel_minute_tick(self) -> None:
        if self._minute_unsub is not None:
            self._minute_unsub()
            self._minute_unsub = None

    @callback
    def _schedule_minute_tick(self) -> None:
        """Force a refresh once a minute while a print is active."""
        self._cancel_minute_tick()

        @callback
        def _tick(_: datetime) -> None:
            self._minute_unsub = None
            if self._activity_active and self._status() in ACTIVE_STATES:
                self.hass.async_create_task(
                    self._async_push_activity(force=True)
                )
                self._schedule_minute_tick()

        self._minute_unsub = async_call_later(self.hass, 60, _tick)

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
            await self._async_push_activity(starting=True, force=True)

    async def _async_push_activity(
        self, *, starting: bool = False, force: bool = False
    ) -> None:
        """Send the current printer state to the HA Companion app."""
        progress = self._current_progress()
        remaining = self._current_remaining()
        finish_ts = self._current_finish_timestamp(remaining)
        title = self._activity_title or self._current_title()
        finish_text = self._format_finish_time(finish_ts)
        remaining_text = self._format_remaining(remaining)

        payload_key = (
            title,
            progress,
            remaining_text,
            finish_text,
        )
        if not force and payload_key == self._last_payload:
            return

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
                "alert_once": True,
                "push": {
                    "sound": "none",
                    "interruption-level": "passive",
                },
            },
        }

        await self._async_notify(payload)

        self._activity_active = True
        self._activity_title = title
        self._last_payload = payload_key
        self._last_progress = progress
        self._last_remaining = remaining
        self._last_finish_ts = finish_ts

        if starting:
            self._schedule_rollover()
            self._schedule_minute_tick()

    async def _async_finish(self) -> None:
        """Send the finished alert, then end the Live Activity."""
        self._cancel_coalesce()
        self._cancel_minute_tick()

        if self.notify_finished:
            title = self._activity_title or self._current_title()
            await self._async_notify(
                {
                    "title": "A1 mini",
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
        self._cancel_coalesce()
        self._cancel_minute_tick()

        if self.notify_failed:
            title = self._activity_title or self._current_title()
            await self._async_notify(
                {
                    "title": "A1 mini",
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
        self._cancel_coalesce()
        self._cancel_minute_tick()
        self._cancel_rollover()

        if self._activity_active:
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
        self._last_payload = None

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
        """Return remaining minutes, preferring the authoritative end time.

        Some Bambu print paths temporarily expose remaining_time == 0 while
        end_time is already valid. Using the end timestamp also makes the
        displayed minutes count down correctly between MQTT ETA updates.
        """
        end_state = self.hass.states.get(self.entities[KEY_END_TIME])
        end_ts = self._parse_timestamp(
            end_state.state if end_state is not None else None
        )
        if end_ts is not None:
            seconds = end_ts - dt_util.now().timestamp()
            if seconds > 0:
                return max(1, math.ceil(seconds / 60))

        state = self.hass.states.get(self.entities[KEY_REMAINING_TIME])
        value = self._safe_int(state.state if state is not None else None)
        return max(0, value if value is not None else 0)

    def _bambu_print_job(self) -> Any | None:
        """Best-effort access to ha-bambulab's current PrintJob model."""
        registry = er.async_get(self.hass)
        status_entry = registry.async_get(self.entities[KEY_PRINT_STATUS])
        if status_entry is None or status_entry.config_entry_id is None:
            return None

        coordinator = self.hass.data.get(BAMBU_DOMAIN, {}).get(
            status_entry.config_entry_id
        )
        if coordinator is None:
            return None

        try:
            return coordinator.get_model().print_job
        except (AttributeError, KeyError):
            return None

    def _human_title(self) -> str | None:
        """Return a human-readable Bambu print title if available."""
        job = self._bambu_print_job()
        candidates: list[Any] = []

        if job is not None:
            task_data = getattr(job, "_task_data", None)
            if isinstance(task_data, dict):
                candidates.extend(
                    [
                        task_data.get("title"),
                        task_data.get("designTitle"),
                        task_data.get("plateName"),
                    ]
                )

            candidates.extend(
                [
                    getattr(job, "subtask_name", None),
                    getattr(job, "gcode_file", None),
                ]
            )

        state = self.hass.states.get(self.entities[KEY_SUBTASK_NAME])
        if state is not None:
            candidates.append(state.state)

        for candidate in candidates:
            cleaned = self._clean_title(candidate)
            if cleaned is not None:
                return cleaned

        return None

    def _current_title(self) -> str:
        """Return the human print title, with a concise fallback."""
        human = self._human_title()
        if human is not None:
            return human

        if self.entry.title.upper().startswith("A1MINI"):
            return "A1 mini"
        return self.entry.title or "Bambu printer"

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
    def _clean_title(value: Any) -> str | None:
        text = BambuLiveActivityRuntime._clean_text(value)
        if text is None:
            return None

        text = text.replace("\\", "/")
        if "/" in text:
            text = PurePath(text).name

        # Strip common Bambu/slicer file suffixes.
        lower = text.lower()
        for suffix in (".gcode.3mf", ".gcode", ".3mf"):
            if lower.endswith(suffix):
                text = text[: -len(suffix)].strip()
                lower = text.lower()
                break

        if not text:
            return None

        for pattern in _BAD_TITLE_PATTERNS:
            if pattern.search(text):
                return None

        # Bambu sometimes embeds the profile marker after a useful prefix.
        marker = re.search(r"#profileid[-_#]", text, re.IGNORECASE)
        if marker:
            useful = text[: marker.start()].strip(" _-#")
            if useful and not useful.isdigit():
                text = useful
            else:
                return None

        return text[:80]

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
