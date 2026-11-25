"""Config flow for Bambu Print Notifications."""

from __future__ import annotations

from typing import Any
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
from homeassistant.core import callback
from .const import (
    BAMBU_DOMAIN, BAMBUDDY_DOMAIN, CONF_NOTIFY_SERVICE, CONF_PRINTER_DEVICE, CONF_PRINTER_SERIAL,
    CONF_SOURCE, DEFAULT_DRIFT_PERCENT, DOMAIN,
)
from .helpers import printer_identity, resolve_progress_entities

def _mobile_notify_actions(hass) -> list[str]:
    services = hass.services.async_services().get("notify", {})
    return sorted(f"notify.{name}" for name in services if name.startswith("mobile_app_"))


def _printer_in_use(hass, serial, exclude_entry_id=None):
    return any(
        entry.entry_id != exclude_entry_id
        and entry.data[CONF_PRINTER_SERIAL].casefold() == serial.casefold()
        for entry in hass.config_entries.async_entries(DOMAIN)
    )


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
            elif not {"print_status", "print_progress"} <= resolve_progress_entities(self.hass, serial, source, device.id).keys():
                errors["base"] = "missing_bambu_entities"
            elif not self._valid_notify_action(user_input[CONF_NOTIFY_SERVICE]):
                errors["base"] = "notify_action_unavailable"
            else:
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured()
                if _printer_in_use(self.hass, serial):
                    return self.async_abort(reason="already_configured")
                return self.async_create_entry(title=title, data={
                    CONF_PRINTER_SERIAL: serial,
                    CONF_PRINTER_DEVICE: device.id,
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
                vol.Required(CONF_PRINTER_DEVICE): _printer_selector(),
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


def _printer_selector():
    return selector.DeviceSelector(selector.DeviceSelectorConfig(filter=[
        {"integration": BAMBU_DOMAIN}, {"integration": BAMBUDDY_DOMAIN},
    ]))


def _source_selector():
    return selector.SelectSelector(selector.SelectSelectorConfig(options=[
        {"value": BAMBU_DOMAIN, "label": "ha-bambulab"},
        {"value": BAMBUDDY_DOMAIN, "label": "Bambuddy"},
    ], mode=selector.SelectSelectorMode.DROPDOWN))


class BambuLiveActivityOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        entry = self.config_entry
        errors = {}
        current_serial = entry.data[CONF_PRINTER_SERIAL]
        current_source = entry.options.get(CONF_SOURCE, entry.data.get(CONF_SOURCE, BAMBU_DOMAIN))
        if user_input is not None:
            source = user_input[CONF_SOURCE]
            device_id = user_input.get(CONF_PRINTER_DEVICE)
            if not device_id and source == current_source:
                device_id = entry.data.get(CONF_PRINTER_DEVICE)
            device = dr.async_get(self.hass).async_get(device_id) if device_id else None
            serial = printer_identity(device, source) if device_id else current_serial
            if not serial:
                errors[CONF_PRINTER_DEVICE] = "not_bambu_device"
            else:
                # Preserve the existing tag, dashboard and storage identity across source changes.
                if serial.casefold() == current_serial.casefold():
                    serial = current_serial
                entities = resolve_progress_entities(self.hass, serial, source, device_id)
                if not {"print_status", "print_progress"} <= entities.keys():
                    errors["base"] = "missing_bambu_entities"
                elif _printer_in_use(self.hass, serial, entry.entry_id):
                    errors[CONF_PRINTER_DEVICE] = "already_configured"
                else:
                    status = er.async_get(self.hass).async_get(entities["print_status"])
                    options = {key: value for key, value in user_input.items() if key != CONF_PRINTER_DEVICE}
                    data = {
                        **entry.data,
                        CONF_SOURCE: source,
                        CONF_PRINTER_SERIAL: serial,
                        CONF_PRINTER_DEVICE: status.device_id,
                    }
                    title = (device.name_by_user or device.name or serial) if device else entry.title
                    # Save identity and options together so the update listener reloads only once.
                    self.hass.config_entries.async_update_entry(
                        entry, data=data, options=options, unique_id=serial, title=title,
                    )
                    return self.async_create_entry(title="", data=options)

        schema = vol.Schema({
            vol.Required(CONF_SOURCE, default=current_source): _source_selector(),
            vol.Optional(CONF_PRINTER_DEVICE): _printer_selector(),
            vol.Required("eta_drift_percent", default=entry.options.get("eta_drift_percent", DEFAULT_DRIFT_PERCENT)):
                vol.All(vol.Coerce(float), vol.Range(min=1, max=50)),
        })
        suggested = user_input if user_input is not None else {}
        if user_input is None and (device_id := entry.data.get(CONF_PRINTER_DEVICE)):
            suggested = {CONF_PRINTER_DEVICE: device_id}
        return self.async_show_form(
            step_id="init", errors=errors,
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
        )
