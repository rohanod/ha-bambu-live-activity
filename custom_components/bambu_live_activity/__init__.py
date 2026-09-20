"""Bambu Live Activity integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_PRINTER_SERIAL, DOMAIN, REQUIRED_BAMBU_KEYS
from .helpers import resolve_bambu_entities
from .runtime import BambuLiveActivityRuntime


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Bambu Live Activity from a config entry."""
    serial = entry.data[CONF_PRINTER_SERIAL]
    entities = resolve_bambu_entities(hass, serial)

    missing = REQUIRED_BAMBU_KEYS - entities.keys()
    if missing:
        raise ConfigEntryNotReady(
            "Required ha-bambulab entities are not registered yet: "
            + ", ".join(sorted(missing))
        )

    runtime = BambuLiveActivityRuntime(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    await runtime.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Bambu Live Activity config entry."""
    runtime: BambuLiveActivityRuntime | None = hass.data.get(DOMAIN, {}).pop(
        entry.entry_id, None
    )
    if runtime is not None:
        await runtime.async_stop(clear=True)
    return True
