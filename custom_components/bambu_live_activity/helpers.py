"""Helpers for Bambu Print Notifications."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import BAMBU_DOMAIN, KEY_PRINT_STATUS


def resolve_print_status_entity(hass: HomeAssistant, serial: str) -> str | None:
    """Resolve ha-bambulab's print status entity by stable unique ID."""
    registry = er.async_get(hass)
    wanted_unique_id = f"{serial}_{KEY_PRINT_STATUS}"

    for entry in registry.entities.values():
        if entry.platform == BAMBU_DOMAIN and entry.unique_id == wanted_unique_id:
            return entry.entity_id

    return None
