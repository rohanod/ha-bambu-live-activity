"""Config flow for Bambu Print Notifications."""

from __future__ import annotations

from typing import Any
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector
from homeassistant.core import callback
from .const import (
    BAMBU_DOMAIN, BAMBUDDY_DOMAIN, CONF_NOTIFY_SERVICE, CONF_PRINTER_SERIAL,
    CONF_SOURCE, DEFAULT_DRIFT_PERCENT, DOMAIN,
)
from .helpers import printer_identity, resolve_progress_entities

CONF_PRINTER_DEVICE = "printer_device"


def _mobile_notify_actions(hass) -> list[str]:
    services = hass.services.async_services().get("notify", {})
    return sorted(f"notify.{name}" for name in services if name.startswith("mobile_app_"))


class BambuPrintNotificationsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return BambuLiveActivityOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            device = dr.async_get(self.hass).async_get(user_input[CONF_PRINTER_DEVICE])
            source = user_input[CONF_SOURCE]
            serial = printer_identity(device, source)
            title = (device.name_by_user or device.name or "Bambu printer") if device else "Bambu printer"
            if serial is None:
                errors["base"] = "not_bambu_device"
            elif not {"print_status", "print_progress"} <= resolve_progress_entities(self.hass, serial, source).keys():
                errors["base"] = "missing_bambu_entities"
            elif not self._valid_notify_action(user_input[CONF_NOTIFY_SERVICE]):
                errors["base"] = "notify_action_unavailable"
            else:
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=title, data={
                    CONF_PRINTER_SERIAL: serial,
                    CONF_SOURCE: source,
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
                vol.Required(CONF_PRINTER_DEVICE): selector.DeviceSelector(selector.DeviceSelectorConfig(filter=[{"integration": BAMBU_DOMAIN}, {"integration": BAMBUDDY_DOMAIN}])),
                vol.Required(CONF_NOTIFY_SERVICE): notify_selector,
                vol.Required(CONF_SOURCE, default=BAMBU_DOMAIN): _source_selector(),
            }),
            errors=errors,
        )

    def _valid_notify_action(self, action: str) -> bool:
        if not action.startswith("notify."):
            return False
        _, service = action.split(".", 1)
        return self.hass.services.has_service("notify", service)


def _source_selector():
    return selector.SelectSelector(selector.SelectSelectorConfig(options=[
        {"value": BAMBU_DOMAIN, "label": "ha-bambulab"},
        {"value": BAMBUDDY_DOMAIN, "label": "Bambuddy"},
    ], mode=selector.SelectSelectorMode.DROPDOWN))


class BambuLiveActivityOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        entry = self.config_entry
        errors = {}
        if user_input is not None:
            entities = resolve_progress_entities(self.hass, entry.data[CONF_PRINTER_SERIAL], user_input[CONF_SOURCE])
            if {"print_status", "print_progress"} <= entities.keys():
                return self.async_create_entry(title="", data=user_input)
            errors[CONF_SOURCE] = "missing_bambu_entities"

        source = entry.options.get(CONF_SOURCE, entry.data.get(CONF_SOURCE, BAMBU_DOMAIN))
        return self.async_show_form(step_id="init", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_SOURCE, default=source): _source_selector(),
            vol.Required("eta_drift_percent", default=entry.options.get("eta_drift_percent", DEFAULT_DRIFT_PERCENT)):
                vol.All(vol.Coerce(float), vol.Range(min=1, max=50)),
        }))
