"""Runtime for Bambu Print Notifications."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .const import BAMBU_DOMAIN, CONF_NOTIFY_SERVICE, CONF_PRINTER_SERIAL
from .helpers import resolve_print_status_entity
from .live_activity import PrintLiveActivity
from .model_names import clean_title, title_from_job

class BambuPrintNotificationsRuntime:
    """Send exactly one completion notification per real completed print."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.serial: str = entry.data[CONF_PRINTER_SERIAL]
        self.notify_action: str = entry.data[CONF_NOTIFY_SERVICE]

        status_entity = resolve_print_status_entity(hass, self.serial)
        if status_entity is None:
            raise HomeAssistantError("Bambu print status entity is unavailable")

        registry = er.async_get(hass)
        status_entry = registry.async_get(status_entity)
        self.device_id = status_entry.device_id if status_entry is not None else None
        self.config_entry_id = status_entry.config_entry_id if status_entry is not None else None
        self._unsub = None
        self._completion_handled = False
        self.live_activity = PrintLiveActivity(
            hass,
            self.serial,
            self._human_model_name,
            self._async_mobile_notify,
        )

    async def async_start(self) -> None:
        self._unsub = self.hass.bus.async_listen(
            f"{BAMBU_DOMAIN}_event", self._handle_bambu_event
        )
        await self.live_activity.async_start()

    async def async_stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        await self.live_activity.async_stop()

    @callback
    def _handle_bambu_event(self, event: Event) -> None:
        if self.device_id is None or event.data.get("device_id") != self.device_id:
            return

        event_type = event.data.get("type")
        if event_type == "event_print_started":
            self._completion_handled = False
            self.live_activity.on_print_started()
            return
        if event_type in {"event_print_failed", "event_print_canceled"}:
            self.live_activity.consume_print_seen()
            self.hass.async_create_task(self.live_activity.async_finish())
            return
        if event_type != "event_print_finished" or self._completion_handled:
            return
        # ha-bambulab can replay a finish event (MQTT reconnect, HA restart)
        # long after the print; only alert for a print we watched run.
        if not self.live_activity.consume_print_seen():
            return

        self._completion_handled = True
        self.hass.async_create_task(self._async_notify_finished())

    async def _async_notify_finished(self) -> None:
        model_name = self._human_model_name() or self.live_activity.activity_title or "A1 mini"
        await self.live_activity.async_finish()

        try:
            await self._async_mobile_notify({
                "title": model_name,
                "message": "Finished printing.",
                "data": {
                    "notification_icon": "mdi:printer-3d",
                    "tag": f"bambu_print_finished_{self.serial[-8:]}",
                },
            })
        except Exception:
            # The HA notification must still appear if APNs/mobile notify is
            # temporarily unavailable.
            logging.getLogger(__name__).exception("Mobile print-finished notification failed")

        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": model_name,
                "message": "Finished printing.",
                "notification_id": f"bambu_print_finished_{self.serial[-8:]}",
            },
            blocking=True,
        )

    def _human_model_name(self) -> str | None:
        """Prefer an actual print filename over generic Bambu plate labels."""
        job = self._bambu_print_job()
        # For the current A1 mini, this is the real model filename even when
        # the Bambu Cloud task title just says "1 piece".
        task_entity = self.live_activity.entities.get("subtask_name")
        state = self.hass.states.get(task_entity) if task_entity else None
        task_name = state.state if state is not None else None
        return title_from_job(job, task_name)

    def _current_model_name(self) -> str:
        return self._human_model_name() or "A1 mini"

    def _bambu_print_job(self) -> Any | None:
        if self.config_entry_id is None:
            return None
        coordinator = self.hass.data.get(BAMBU_DOMAIN, {}).get(self.config_entry_id)
        if coordinator is None:
            return None
        try:
            return coordinator.get_model().print_job
        except (AttributeError, KeyError):
            return None

    async def _async_mobile_notify(self, data: dict[str, Any]) -> None:
        try:
            domain, service = self.notify_action.split(".", 1)
        except ValueError as err:
            raise HomeAssistantError(f"Invalid notify action: {self.notify_action}") from err
        if domain != "notify":
            raise HomeAssistantError(f"Expected a notify.* action, got {self.notify_action}")
        if not self.hass.services.has_service(domain, service):
            raise HomeAssistantError(f"Notify action is unavailable: {self.notify_action}")
        await self.hass.services.async_call(domain, service, data, blocking=True)

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
        return clean_title(value)
