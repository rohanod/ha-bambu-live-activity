"""Quiet progress Live Activity; preserves the existing completion notifier."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime
import math
import logging
from typing import Any, Awaitable, Callable

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import DEFAULT_DRIFT_PERCENT, DOMAIN, IMMEDIATE_DRIFT_PERCENT, dashboard_path
from .helpers import normalize_status

_LOGGER = logging.getLogger(__name__)

ACTIVE_STATES = {"prepare", "running", "pause", "slicing"}
STOPPED_STATES = {"finish", "failed", "idle"}
COALESCE_SECONDS = 2.0
MINUTES_DISPLAY_VERSION = 1
MIN_UPDATE_INTERVAL = 600.0
NAME_WAIT_SECONDS = 30.0
LONG_PRINT_ROLLOVER_SECONDS = 7.5 * 60 * 60
# A stopped status must hold this long before the activity is ended, so a
# brief idle/finish flap cannot end + restart (= a second activity on device).
STOP_DEBOUNCE_SECONDS = 30.0
# Fresh start pushes are capped per printer; further requests wait it out.
MIN_START_INTERVAL = 120.0


# Per-state look: (accent for icon + bar, Lock Screen card, status word).
STYLES = {
    "running": ("#3DDC84", "#0B1A12", "Printing"),
    "pause": ("#FFB84D", "#1F1606", "Paused"),
    "prepare": ("#7FA8FF", "#0C1322", "Preparing"),
    "slicing": ("#7FA8FF", "#0C1322", "Preparing"),
    "finish": ("#3DDC84", "#0B1A12", "Finished"),
    "failed": ("#FF6B6B", "#220C0C", "Failed"),
    "idle": ("#AEB8B2", "#141A16", "Stopped"),
}


def build_payload(
    tag: str, title: str, status: str, progress: int, starting: bool,
    end_timestamp: float | None = None,
    detail: str | None = None,
    hms: bool = False,
) -> dict[str, Any]:
    accent, card, word = STYLES.get(status, STYLES["running"])
    if status == "running":
        message = "Printing" if end_timestamp is not None else "ETA unavailable"
    elif status == "pause":
        message = f"{word} at {progress}%"
    elif status in STOPPED_STATES:
        message = word
    else:
        message = f"{word}…"
    if detail:
        message = f"{message} · {detail}"
    data: dict[str, Any] = {
        "tag": tag,
        "live_update": True,
        "critical_text": "Paused" if status == "pause" else "Prep" if status in ("prepare", "slicing") else "--:--",
        "chronometer": False,
        "alert_once": True,
        "notification_icon": "mdi:printer-pause" if status == "pause" else "mdi:printer-3d-nozzle",
        "notification_icon_color": accent,
        "progress_bar_color": accent,
        "background_color": card,
        "text_color": "#F2F5F3",
    }
    if status in {"running", "prepare", "slicing"} and end_timestamp is not None:
        minutes = max(1, math.ceil((end_timestamp - dt_util.now().timestamp()) / 60))
        hours, minutes = divmod(minutes, 60)
        data["critical_text"] = f"{hours}h {minutes}m" if hours else f"{minutes}m"
    elif status in STOPPED_STATES:
        data["critical_text"] = word
    if hms:
        if status == "pause":
            data["critical_text"] = "HMS"
        data["notification_icon"] = "mdi:alert"
        data["notification_icon_color"] = "#FFB84D"
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
        options: dict[str, Any] | None = None,
        *,
        entities: dict[str, str] | None = None,
        on_sensor_finish: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.hass = hass
        self.serial = serial
        self.get_name = get_name
        self.notify = notify
        self.options = options or {}
        self._store = Store(hass, 1, f"bambu_live_activity.{serial}")
        self._last_eta: float | None = None
        self._last_status: str | None = None
        self._retry_after = 0.0
        self._started_at = 0.0
        self._last_attempt_at = 0.0
        self.entities = entities or {}
        self.on_sensor_finish = on_sensor_finish
        self._last_hms: tuple[str, ...] = ()
        # Same tag as 0.1.4, so it updates rather than multiplying activities.
        self.tag = f"bambu_print_{''.join(c for c in serial if c.isalnum())[-16:]}"
        self._unsub: Any | None = None
        self._update_unsub: Any | None = None
        self._update_task: asyncio.Task | None = None
        self._rollover_unsub: Any | None = None
        self._start_task: asyncio.Task | None = None
        self._push_lock = asyncio.Lock()
        self._generation = 0
        self._active = False
        self._stopped = False
        self._title: str | None = None
        self._last_push_at = 0.0
        self._next_rollover_at = 0.0
        self._retry_unsub: Any | None = None
        # True once this run has seen the printer actually printing.
        self._print_seen = False
        self._stop_unsub: Any | None = None
        # A start push may have reached the phone even if we never saw success.
        self._maybe_open = False
        self._last_start_at: float | None = None
        self._demo_lock = asyncio.Lock()
        self.starts = 0
        self.history: deque[str] = deque(maxlen=25)

    def _log(self, event: str) -> None:
        self.history.append(f"{dt_util.now().strftime('%m-%d %H:%M:%S')} {event}")

    @property
    def activity_title(self) -> str | None:
        return self._title

    async def async_start(self) -> None:
        if "print_status" not in self.entities or "print_progress" not in self.entities:
            _LOGGER.warning("Missing Bambu print status/progress sensors; Live Activity disabled")
            return
        saved = await self._store.async_load() or {}
        self._active = self._maybe_open = bool(saved.get("active"))
        self._title = saved.get("title")
        self._last_push_at = saved.get("last_push_at", 0.0)
        self._last_eta = saved.get("last_eta")
        if saved.get("display_version") == MINUTES_DISPLAY_VERSION:
            self._last_status = saved.get("last_status")
        self._last_hms = tuple(saved.get("last_hms", ()))
        self._retry_after = saved.get("retry_after", 0.0)
        self._started_at = saved.get("started_at", 0.0)
        self._last_attempt_at = saved.get("last_attempt_at", 0.0)
        self._last_start_at = saved.get("last_start_at")
        self._print_seen = bool(saved.get("print_seen"))
        self._unsub = async_track_state_change_event(
            self.hass,
            list(self.entities.values()),
            self._on_change,
        )
        if self._state("print_status") in ACTIVE_STATES:
            self._print_seen = True
            if self._active:
                self._schedule_rollover()
                self._schedule_update()
            else:
                self._start_new_activity()
        elif self._state("print_status") in STOPPED_STATES:
            if self._active:
                self._schedule_update()
            self._schedule_stop()

    async def _async_save(self) -> None:
        await self._store.async_save({
            "active": self._active or self._maybe_open,
            "title": self._title,
            "last_push_at": self._last_push_at,
            "last_eta": self._last_eta,
            "last_status": self._last_status,
            "display_version": MINUTES_DISPLAY_VERSION,
            "last_hms": self._last_hms,
            "retry_after": self._retry_after,
            "started_at": self._started_at,
            "last_attempt_at": self._last_attempt_at,
            "last_start_at": self._last_start_at,
            "print_seen": self._print_seen,
        })
        async_dispatcher_send(self.hass, f"{DOMAIN}_{self.serial}_updated")

    def consume_print_seen(self) -> bool:
        """True once per observed print; blocks replayed/stale finish events."""
        seen, self._print_seen = self._print_seen, False
        return seen

    async def async_stop(self, *, end_activity: bool = False) -> None:
        self._stopped = True
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        # Ordinary reloads keep the activity open. A printer switch retires its old tag.
        task = self._start_task
        self._cancel_work()
        if task is not None and task is not asyncio.current_task():
            await asyncio.gather(task, return_exceptions=True)
        # Drain an in-flight notify/save before another runtime restores state.
        async with self._push_lock:
            pass
        if end_activity:
            self.consume_print_seen()
            await self._async_clear(retiring=True)

    @callback
    def on_print_started(self) -> None:
        """Called by the existing ha-bambulab lifecycle event listener."""
        if self._state("print_status") in ACTIVE_STATES:
            self._print_seen = True
            if not self._active:
                self._start_new_activity()

    async def async_finish(self) -> None:
        await self._async_clear()

    async def async_end(self) -> None:
        """Debug: end the real and test activities on the phone, even if unsure."""
        self._maybe_open = True
        await self._async_clear()
        await self.notify({"message": "clear_notification", "data": {"tag": f"{self.tag}_test"}})

    async def async_restart(self) -> None:
        """Debug: (re)open the activity now, e.g. after it was swiped away."""
        if self._state("print_status") not in ACTIVE_STATES:
            raise HomeAssistantError("No print is running, so there is nothing to restart")
        self._cancel_work()
        self._active = False
        self._title = None
        self._print_seen = True
        self._start_new_activity(force=True)

    def snapshot(self) -> dict[str, Any]:
        loop_now = dt_util.now().timestamp()
        return {
            "tag": self.tag,
            "print_status": self._state("print_status"),
            "progress": self._progress(),
            "activity_open": self._active,
            "title": self._title,
            "starts_since_reload": self.starts,
            "seconds_since_last_push": round(loop_now - self._last_push_at) if self._last_push_at else None,
            "seconds_since_last_start": round(loop_now - self._last_start_at) if self._last_start_at else None,
            "last_published_eta": self._last_eta,
            "last_published_status": self._last_status,
            "last_successful_update": self._last_push_at or None,
            "drift_threshold_percent": self.options.get("eta_drift_percent", DEFAULT_DRIFT_PERCENT),
            "update_pending": self._update_unsub is not None,
            "stop_pending": self._stop_unsub is not None,
            "entities": self.entities,
            "recent": list(self.history),
        }

    @callback
    def _on_change(self, event: Event) -> None:
        entity_id = event.data.get("entity_id")
        new = event.data.get("new_state")
        if new is None:
            return

        if entity_id == self.entities.get("print_status"):
            status = normalize_status(str(new.state).lower())
            if status in ACTIVE_STATES:
                self._print_seen = True
                if not self._active and (self._start_task is None or self._start_task.done()):
                    self._start_new_activity()
                elif self._active:
                    self._schedule_update()
                self._cancel_timer("_stop_unsub")
            elif status in STOPPED_STATES:
                if self._active:
                    self._schedule_update()
                self._schedule_stop()
            return

        if not self._active or self._state("print_status") not in ACTIVE_STATES:
            return
        if entity_id in {self.entities.get("end_time"), self.entities.get("hms")}:
            self._schedule_update()
        # Progress, name and countdown changes never cause pushes.

    @callback
    def _schedule_stop(self) -> None:
        if self._stop_unsub is not None or not (self._active or self._maybe_open or self._start_task):
            return

        @callback
        def tick(_: datetime) -> None:
            self._stop_unsub = None
            if self._state("print_status") in STOPPED_STATES:
                self.hass.async_create_task(self._async_sensor_stop())

        self._stop_unsub = async_call_later(self.hass, STOP_DEBOUNCE_SECONDS, tick)

    @callback
    def _start_new_activity(self, *, force: bool = False) -> None:
        if self._stopped or self._active or (self._start_task and not self._start_task.done()):
            return
        if not force and self._retry_unsub is not None:
            return
        self._generation += 1
        gen = self._generation
        self._start_task = self.hass.async_create_task(self._async_begin(gen, force))

    async def _async_begin(self, gen: int, force: bool = False) -> None:
        """Wait briefly for a real model name before setting the static title."""
        try:
            # Bambu may still expose the PREVIOUS model name/progress on its
            # initial PREPARE update. Allow fresh MQTT metadata to arrive.
            await asyncio.sleep(1 if force else 6)
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
                self._retry_start_later(delay=30)
                return
            self._title = self.get_name() or "A1 mini"
            if not force and self._last_start_at is not None:
                wait = MIN_START_INTERVAL - (dt_util.now().timestamp() - self._last_start_at)
                if wait > 0:
                    self._log(f"start delayed {wait:.0f}s (rate limit)")
                    await asyncio.sleep(wait)
                    if gen != self._generation or self._state("print_status") not in ACTIVE_STATES:
                        return
            await self._async_push(starting=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Could not start Bambu Live Activity")

    async def _async_sensor_stop(self) -> None:
        status = self._state("print_status")
        if status not in STOPPED_STATES:
            return
        if status == "finish" and self.on_sensor_finish and self.consume_print_seen():
            await self.on_sensor_finish()
        else:
            await self._async_clear()

    def _eta_drift_percent(self) -> float:
        if self._state("print_status") not in ACTIVE_STATES:
            return 0
        eta = self._valid_end_timestamp()
        if eta is None or self._last_eta is None:
            return 0
        # Compare both predictions at the same instant. A one-minute floor
        # matches the printer's ETA resolution near the old finish time.
        previous_remaining = max(60, self._last_eta - dt_util.now().timestamp())
        return abs(eta - self._last_eta) * 100 / previous_remaining

    def _eta_has_drifted(self) -> bool:
        return self._eta_drift_percent() >= self.options.get("eta_drift_percent", DEFAULT_DRIFT_PERCENT)

    def _hms_errors(self) -> dict[str, str] | None:
        entity = self.entities.get("hms")
        state = self.hass.states.get(entity) if entity else None
        if state is None or state.state in {"unavailable", "unknown"}:
            return None
        return {
            error["code"]: str(error.get("description") or error["code"])
            for error in state.attributes.get("errors") or []
            if isinstance(error, dict) and isinstance(error.get("code"), str) and error["code"]
        }

    def _needs_immediate_update(self) -> bool:
        # Compare with successful publication, not repeated MQTT state events.
        status = self._state("print_status")
        errors = self._hms_errors() if status in ACTIVE_STATES else None
        return (
            status != self._last_status
            or (self._last_eta is None and self._valid_end_timestamp() is not None and status in ACTIVE_STATES)
            or self._eta_drift_percent() > IMMEDIATE_DRIFT_PERCENT
            or (errors is not None and tuple(sorted(errors)) != self._last_hms)
        )

    @callback
    def _schedule_update(self) -> None:
        if self._stopped or not self._active or self._state("print_status") not in ACTIVE_STATES | STOPPED_STATES:
            return
        immediate = self._needs_immediate_update()
        if not immediate and not self._eta_has_drifted():
            self._cancel_timer("_update_unsub")
            return
        now = dt_util.now().timestamp()
        if immediate and now >= self._retry_after:
            self._cancel_timer("_update_unsub")
            if self._update_task is None or self._update_task.done() or self._update_task is asyncio.current_task():
                self._update_task = self.hass.async_create_task(self._async_push())
            return
        if self._update_unsub is not None:
            return
        gap = self._retry_after - now if immediate else max(
            self._retry_after - now,
            MIN_UPDATE_INTERVAL - (now - max(self._last_push_at, self._last_attempt_at)),
        )

        @callback
        def send(_: datetime) -> None:
            self._update_unsub = None
            self.hass.async_create_task(self._async_push())

        self._update_unsub = async_call_later(self.hass, max(COALESCE_SECONDS if gap <= 0 else 0, gap), send)

    @callback
    def _schedule_rollover(self) -> None:
        if self._stopped:
            return
        self._cancel_timer("_rollover_unsub")
        self._next_rollover_at = self._started_at + LONG_PRINT_ROLLOVER_SECONDS

        @callback
        def tick(_: datetime) -> None:
            self._rollover_unsub = None
            if self._active and self._state("print_status") in ACTIVE_STATES:
                self.hass.async_create_task(self._async_rollover())

        self._rollover_unsub = async_call_later(
            self.hass, max(0, self._next_rollover_at - dt_util.now().timestamp()), tick
        )

    @callback
    def _retry_start_later(self, *, delay: float = MIN_UPDATE_INTERVAL) -> None:
        if self._stopped:
            return
        self._cancel_timer("_retry_unsub")

        @callback
        def retry(_: datetime) -> None:
            self._retry_unsub = None
            if not self._active and self._state("print_status") in ACTIVE_STATES:
                self._start_new_activity()

        self._retry_unsub = async_call_later(self.hass, delay, retry)

    async def _async_rollover(self) -> None:
        """Apple expires an activity after eight hours; renew only then."""
        if self._stopped or self._state("print_status") not in ACTIVE_STATES:
            return
        await self._async_clear()
        if self._stopped:
            return
        if self._active:
            @callback
            def retry(_: datetime) -> None:
                self._rollover_unsub = None
                self.hass.async_create_task(self._async_rollover())

            self._rollover_unsub = async_call_later(self.hass, MIN_UPDATE_INTERVAL, retry)
            return
        if self._state("print_status") in ACTIVE_STATES:
            self._start_new_activity(force=True)  # planned renewal, not a flap

    async def _async_push(self, *, starting: bool = False) -> None:
        async with self._push_lock:
            if self._stopped or self._state("print_status") not in ACTIVE_STATES | STOPPED_STATES:
                return
            if starting and self._state("print_status") not in ACTIVE_STATES:
                return
            if not starting and not self._active:
                return  # queued update from before a clear; would reopen a closed activity
            now = dt_util.now().timestamp()
            if not starting:
                # Recheck publication need/backoff under the lock to deduplicate tasks.
                if now < self._retry_after:
                    self._schedule_update()
                    return
                if not self._needs_immediate_update():
                    if not self._eta_has_drifted():
                        return
                    if now - max(self._last_push_at, self._last_attempt_at) < MIN_UPDATE_INTERVAL:
                        self._schedule_update()
                        return
            progress = self._progress()
            status = self._state("print_status")
            end_timestamp = self._valid_end_timestamp()
            title = self._title or self.get_name() or "A1 mini"

            errors = self._hms_errors() if status in ACTIVE_STATES else None
            codes = tuple(sorted(errors)) if errors is not None else self._last_hms
            new_errors = [code for code in codes if code not in self._last_hms] if errors else []
            stage = self._state("stage")
            detail = next(iter(errors.values())) if errors else (
                stage.removeprefix("paused_").replace("_", " ").capitalize()
                if status == "pause" and stage.startswith("paused_") else None
            )
            payload = build_payload(
                self.tag, title, status, progress, starting, end_timestamp, detail, bool(codes)
            )
            payload["data"]["url"] = dashboard_path(self.serial)
            self._last_attempt_at = now
            if starting:
                self._maybe_open = True
                self._last_start_at = self._started_at = now
                self.starts += 1
                self._print_seen = True
            # Persist before sending too: a crash during notify must not restart.
            await self._async_save()
            try:
                await self.notify(payload)
            except Exception:
                _LOGGER.exception("Failed to update Bambu Live Activity")
                self._log(f"{'start' if starting else 'update'} FAILED")
                self._retry_after = dt_util.now().timestamp() + MIN_UPDATE_INTERVAL
                await self._async_save()
                if starting:
                    self._retry_start_later()
                else:
                    self._schedule_update()
                return
            self._log(f"{'START' if starting else 'update'} {progress}% {self._state('print_status')}")
            self._active = True
            self._title = title
            self._last_push_at = dt_util.now().timestamp()
            self._last_eta = end_timestamp
            self._last_status = status
            self._last_hms = codes
            self._retry_after = 0.0
            await self._async_save()
            if new_errors:
                await self._async_notify_hms(errors, new_errors)
            if starting:
                self._schedule_rollover()
            self._schedule_update()

    async def _async_notify_hms(self, errors: dict[str, str], codes: list[str]) -> None:
        try:
            await self.notify({
                "title": "Printer needs attention",
                "message": "\n".join(f"{code}: {errors[code]}" for code in codes),
                "data": {"tag": f"{self.tag}_hms", "url": dashboard_path(self.serial)},
            })
        except Exception:
            _LOGGER.exception("Failed to send Bambuddy HMS notification")

    async def async_demo(self) -> None:
        """Play a short fake print on its own tag so users can preview the look."""
        if self._demo_lock.locked():
            raise HomeAssistantError("A test activity is already playing")
        async with self._demo_lock:
            await self._play_demo()

    async def _play_demo(self) -> None:
        tag = f"{self.tag}_test"
        self._log("test activity")
        steps = [("prepare", 0, 12), ("running", 20, 10), ("running", 55, 5),
                 ("pause", 55, 5), ("running", 80, 2), ("running", 100, 1)]
        for i, (status, progress, rem) in enumerate(steps):
            end = dt_util.now().timestamp() + rem * 60
            await self.notify(build_payload(tag, "Test print", status, progress, i == 0, end))
            await asyncio.sleep(4)
        await self.notify({"message": "clear_notification", "data": {"tag": tag}})

    @callback
    def _cancel_work(self) -> None:
        self._generation += 1
        task = self._start_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
        self._start_task = None
        for attr in ("_update_unsub", "_rollover_unsub", "_retry_unsub", "_stop_unsub"):
            self._cancel_timer(attr)

    async def _async_clear(self, *, retiring: bool = False) -> None:
        self._cancel_work()
        async with self._push_lock:
            if self._stopped and not retiring:
                return
            if self._active or self._maybe_open:
                try:
                    await self.notify({"message": "clear_notification", "data": {"tag": self.tag}})
                    self._log("clear")
                except Exception:
                    _LOGGER.warning("Unable to clear Bambu Live Activity", exc_info=True)
                    if retiring:
                        return

                    @callback
                    def retry_clear(_: datetime) -> None:
                        self._retry_unsub = None
                        if self._state("print_status") not in ACTIVE_STATES:
                            self.hass.async_create_task(self._async_clear())

                    # Don't clear a subsequent running print on a delayed retry.
                    self._retry_unsub = async_call_later(
                        self.hass, MIN_UPDATE_INTERVAL, retry_clear,
                    )
                    return
            self._active = False
            self._maybe_open = False
            self._title = None
            await self._async_save()

    @callback
    def _cancel_timer(self, attr: str) -> None:
        cancel = getattr(self, attr)
        if cancel is not None:
            cancel()
            setattr(self, attr, None)

    def _state(self, key: str) -> str:
        entity_id = self.entities.get(key)
        obj = self.hass.states.get(entity_id) if entity_id else None
        value = str(obj.state).lower() if obj is not None else "unknown"
        return normalize_status(value) if key == "print_status" else value

    def _progress(self) -> int:
        try:
            return max(0, min(100, int(float(self._state("print_progress")))))
        except (TypeError, ValueError, OverflowError):
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
