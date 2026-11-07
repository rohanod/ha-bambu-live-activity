"""Bambu Print Notifications integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.const import Platform

from .const import CONF_PRINTER_SERIAL, DOMAIN
from .helpers import resolve_print_status_entity
from .runtime import BambuPrintNotificationsRuntime
from .services import async_register_services
from .dashboard import async_setup_assets, async_setup_dashboard, async_remove_dashboard

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    await async_setup_assets(hass)
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    serial = entry.data[CONF_PRINTER_SERIAL]
    if resolve_print_status_entity(hass, serial) is None:
        raise ConfigEntryNotReady("ha-bambulab print status entity is not ready")
    runtime = BambuPrintNotificationsRuntime(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    try:
        await runtime.async_start()
        await hass.config_entries.async_forward_entry_setups(entry, [Platform.SENSOR])
        await async_setup_dashboard(hass, runtime.live_activity)
    except Exception:
        # A setup retry must not leave a second sender listening for this printer.
        await runtime.async_stop()
        await hass.config_entries.async_unload_platforms(entry, [Platform.SENSOR])
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, [Platform.SENSOR]):
        return False
    runtime: BambuPrintNotificationsRuntime | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if runtime is not None:
        await runtime.async_stop()
    async_remove_dashboard(hass, entry.data[CONF_PRINTER_SERIAL])
    return True
