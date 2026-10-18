"""Button that plays a sample Live Activity."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([TestLiveActivityButton(hass.data[DOMAIN][entry.entry_id], entry)])


class TestLiveActivityButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "test_live_activity"
    _attr_icon = "mdi:printer-3d-nozzle"

    def __init__(self, runtime, entry: ConfigEntry) -> None:
        self._runtime = runtime
        self._attr_unique_id = f"{entry.entry_id}_test_live_activity"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name=entry.title)

    async def async_press(self) -> None:
        await self._runtime.live_activity.async_demo()
