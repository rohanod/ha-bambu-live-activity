"""Helpers for Bambu Live Activity."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import BAMBU_DOMAIN, REQUIRED_BAMBU_KEYS


def resolve_bambu_entities(hass: HomeAssistant, serial: str) -> dict[str, str]:
    """Resolve required ha-bambulab entities by their stable unique IDs.

    ha-bambulab creates printer sensor unique IDs in the form:
      <serial>_<entity-description-key>

    Resolving through the entity registry means this integration keeps working
    even if the user renames the Home Assistant entity IDs.
    """
    registry = er.async_get(hass)
    wanted = {f"{serial}_{key}": key for key in REQUIRED_BAMBU_KEYS}
    resolved: dict[str, str] = {}

    for entry in registry.entities.values():
        if entry.platform != BAMBU_DOMAIN:
            continue
        key = wanted.get(entry.unique_id)
        if key is not None:
            resolved[key] = entry.entity_id

    return resolved


def missing_bambu_keys(hass: HomeAssistant, serial: str) -> set[str]:
    """Return required Bambu sensor keys that are not registered."""
    return REQUIRED_BAMBU_KEYS - resolve_bambu_entities(hass, serial).keys()
