"""Config flow for Bambu Print Notifications."""

from __future__ import annotations

from typing import Any
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector
from .const import BAMBU_DOMAIN, CONF_NOTIFY_SERVICE, CONF_PRINTER_SERIAL, DOMAIN
from .helpers import resolve_print_status_entity

CONF_PRINTER_DEVICE = "printer_device"


def _mobile_notify_actions(hass) -> list[str]:
    services = hass.services.async_services().get("notify", {})
    return sorted(f"notify.{name}" for name in services if name.startswith("mobile_app_"))


def _serial_from_device(hass, device_id: str) -> tuple[str | None, str]:
    registry = dr.async_get(hass)
    device = registry.async_get(device_id)
    if device is None:
        return None, "Bambu printer"
    serial = next((identifier for domain, identifier in device.identifiers if domain == BAMBU_DOMAIN), None)
    title = device.name_by_user or device.name or "Bambu printer"
    return serial, title


class BambuPrintNotificationsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            serial, title = _serial_from_device(self.hass, user_input[CONF_PRINTER_DEVICE])
            if serial is None:
                errors["base"] = "not_bambu_device"
            elif resolve_print_status_entity(self.hass, serial) is None:
                errors["base"] = "missing_bambu_entities"
            elif not self._valid_notify_action(user_input[CONF_NOTIFY_SERVICE]):
                errors["base"] = "notify_action_unavailable"
            else:
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=title, data={
                    CONF_PRINTER_SERIAL: serial,
                    CONF_NOTIFY_SERVICE: user_input[CONF_NOTIFY_SERVICE],
                })

        actions = _mobile_notify_actions(self.hass)
        notify_selector = (
            selector.SelectSelector(selector.SelectSelectorConfig(options=actions, mode=selector.SelectSelectorMode.DROPDOWN))
            if actions else selector.TextSelector(selector.TextSelectorConfig())
        )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_PRINTER_DEVICE): selector.DeviceSelector(selector.DeviceSelectorConfig(filter={"integration": BAMBU_DOMAIN})),
                vol.Required(CONF_NOTIFY_SERVICE): notify_selector,
            }),
            errors=errors,
        )

    def _valid_notify_action(self, action: str) -> bool:
        if not action.startswith("notify."):
            return False
        _, service = action.split(".", 1)
        return self.hass.services.has_service("notify", service)
