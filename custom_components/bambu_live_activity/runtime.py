"""Runtime for Bambu Print Notifications."""

from __future__ import annotations

from pathlib import PurePath
import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .const import BAMBU_DOMAIN, CONF_NOTIFY_SERVICE, CONF_PRINTER_SERIAL
from .helpers import resolve_print_status_entity

_BAD_TITLE_PATTERNS = (
    re.compile(r"^\d+#profileid[-_#]", re.IGNORECASE),
    re.compile(r"^profileid[-_#]", re.IGNORECASE),
    re.compile(r"^\d{6,}$"),
)


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

    async def async_start(self) -> None:
        self._unsub = self.hass.bus.async_listen(
            f"{BAMBU_DOMAIN}_event", self._handle_bambu_event
        )

    async def async_stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    @callback
    def _handle_bambu_event(self, event: Event) -> None:
        if self.device_id is None or event.data.get("device_id") != self.device_id:
            return

        event_type = event.data.get("type")
        if event_type == "event_print_started":
            self._completion_handled = False
            return
        if event_type != "event_print_finished" or self._completion_handled:
            return

        self._completion_handled = True
        self.hass.async_create_task(self._async_notify_finished())

    async def _async_notify_finished(self) -> None:
        model_name = self._current_model_name()

        await self._async_mobile_notify({
            "title": model_name,
            "message": "Finished printing.",
            "data": {
                "notification_icon": "mdi:printer-3d",
                "tag": f"bambu_print_finished_{self.serial[-8:]}",
            },
        })

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

    def _current_model_name(self) -> str:
        job = self._bambu_print_job()
        candidates: list[Any] = []

        if job is not None:
            task_data = getattr(job, "_task_data", None)
            if isinstance(task_data, dict):
                candidates.extend([
                    task_data.get("designTitle"),
                    task_data.get("title"),
                    task_data.get("plateName"),
                ])
            candidates.extend([
                getattr(job, "subtask_name", None),
                getattr(job, "gcode_file", None),
            ])

        for candidate in candidates:
            cleaned = self._clean_title(candidate)
            if cleaned is not None:
                return cleaned
        return "A1 mini"

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
        text = BambuPrintNotificationsRuntime._clean_text(value)
        if text is None:
            return None
        text = text.replace("\\", "/")
        if "/" in text:
            text = PurePath(text).name
        lower = text.lower()
        for suffix in (".gcode.3mf", ".gcode", ".3mf"):
            if lower.endswith(suffix):
                text = text[:-len(suffix)].strip()
                break
        if not text:
            return None
        for pattern in _BAD_TITLE_PATTERNS:
            if pattern.search(text):
                return None
        marker = re.search(r"#profileid[-_#]", text, re.IGNORECASE)
        if marker:
            useful = text[:marker.start()].strip(" _-#")
            if useful and not useful.isdigit():
                text = useful
            else:
                return None
        return text[:100]
