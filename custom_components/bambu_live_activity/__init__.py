"""Bambu Print Notifications integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_PRINTER_SERIAL, DOMAIN
from .helpers import resolve_print_status_entity
from .runtime import BambuPrintNotificationsRuntime


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    serial = entry.data[CONF_PRINTER_SERIAL]
    if resolve_print_status_entity(hass, serial) is None:
        raise ConfigEntryNotReady("ha-bambulab print status entity is not ready")
    runtime = BambuPrintNotificationsRuntime(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    await runtime.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime: BambuPrintNotificationsRuntime | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if runtime is not None:
        await runtime.async_stop()
    return True
