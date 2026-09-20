"""Config flow for Bambu Live Activity."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector

from .const import (
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
    DOMAIN,
)
from .helpers import missing_bambu_keys


CONF_PRINTER_DEVICE = "printer_device"


def _mobile_notify_actions(hass) -> list[str]:
    """Return notify.mobile_app_* actions currently registered."""
    services = hass.services.async_services().get("notify", {})
    return sorted(
        f"notify.{name}" for name in services if name.startswith("mobile_app_")
    )


def _serial_from_device(hass, device_id: str) -> tuple[str | None, str]:
    """Return ha-bambulab serial and a friendly title for a selected device."""
    registry = dr.async_get(hass)
    device = registry.async_get(device_id)
    if device is None:
        return None, "Bambu printer"

    serial = next(
        (
            identifier
            for domain, identifier in device.identifiers
            if domain == BAMBU_DOMAIN
        ),
        None,
    )
    title = device.name_by_user or device.name or "Bambu printer"
    return serial, title


def _setup_schema(hass, suggested: dict[str, Any] | None = None) -> vol.Schema:
    """Build the setup form."""
    notify_actions = _mobile_notify_actions(hass)

    fields: dict[Any, Any] = {
        vol.Required(CONF_PRINTER_DEVICE): selector.DeviceSelector(
            selector.DeviceSelectorConfig(
                filter={"integration": BAMBU_DOMAIN},
            )
        ),
    }

    if notify_actions:
        fields[vol.Required(CONF_NOTIFY_SERVICE)] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=notify_actions,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
    else:
        fields[vol.Required(CONF_NOTIFY_SERVICE)] = selector.TextSelector(
            selector.TextSelectorConfig()
        )

    schema = vol.Schema(fields)
    if suggested:
        return config_entries.ConfigFlow.add_suggested_values_to_schema(
            schema, suggested
        )
    return schema


class BambuLiveActivityConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Bambu Live Activity."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up a printer Live Activity."""
        errors: dict[str, str] = {}

        if user_input is not None:
            serial, title = _serial_from_device(
                self.hass, user_input[CONF_PRINTER_DEVICE]
            )

            if serial is None:
                errors["base"] = "not_bambu_device"
            elif missing_bambu_keys(self.hass, serial):
                errors["base"] = "missing_bambu_entities"
            elif not self._valid_notify_action(user_input[CONF_NOTIFY_SERVICE]):
                errors["base"] = "notify_action_unavailable"
            else:
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_PRINTER_SERIAL: serial,
                        CONF_NOTIFY_SERVICE: user_input[CONF_NOTIFY_SERVICE],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_setup_schema(self.hass, user_input),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow the notification action to be changed."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            if not self._valid_notify_action(user_input[CONF_NOTIFY_SERVICE]):
                errors["base"] = "notify_action_unavailable"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_NOTIFY_SERVICE: user_input[CONF_NOTIFY_SERVICE]
                    },
                )

        notify_actions = _mobile_notify_actions(self.hass)
        current = entry.data[CONF_NOTIFY_SERVICE]

        if current not in notify_actions:
            notify_actions.append(current)
            notify_actions.sort()

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_NOTIFY_SERVICE,
                        default=current,
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=notify_actions,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            errors=errors,
        )

    def _valid_notify_action(self, action: str) -> bool:
        """Validate a selected notify action."""
        if not action.startswith("notify."):
            return False
        _, service = action.split(".", 1)
        return self.hass.services.has_service("notify", service)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> "BambuLiveActivityOptionsFlow":
        """Create the options flow."""
        return BambuLiveActivityOptionsFlow()


class BambuLiveActivityOptionsFlow(OptionsFlowWithReload):
    """Options for Live Activity update behaviour."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage update options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_PROGRESS_STEP,
                    default=DEFAULT_PROGRESS_STEP,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=10,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="%",
                    )
                ),
                vol.Required(
                    CONF_ETA_THRESHOLD_MINUTES,
                    default=DEFAULT_ETA_THRESHOLD_MINUTES,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=30,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                ),
                vol.Required(
                    CONF_DEBOUNCE_SECONDS,
                    default=DEFAULT_DEBOUNCE_SECONDS,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=60,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="s",
                    )
                ),
                vol.Required(
                    CONF_NOTIFY_FINISHED,
                    default=DEFAULT_NOTIFY_FINISHED,
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_NOTIFY_FAILED,
                    default=DEFAULT_NOTIFY_FAILED,
                ): selector.BooleanSelector(),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, self.config_entry.options
            ),
        )
