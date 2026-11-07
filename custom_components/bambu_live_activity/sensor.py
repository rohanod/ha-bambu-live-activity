"""Persistent publication timestamps, owned by the integration."""

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import callback
from homeassistant.const import EntityCategory
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    activity = hass.data[DOMAIN][entry.entry_id].live_activity
    async_add_entities([
        PublicationTimestamp(activity, "last_push_at", "Last successful Live Activity update"),
        PublicationTimestamp(activity, "last_eta", "Last published print ETA"),
        CurrentModel(activity),
    ])


class PublicationTimestamp(SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, activity, key, name):
        self.activity = activity
        self.key = key
        self._attr_unique_id = f"{activity.serial}_{key}"
        self._attr_name = f"{activity.serial} {name}"

    @property
    def native_value(self):
        timestamp = getattr(self.activity, f"_{self.key}")
        return datetime.fromtimestamp(timestamp, dt_util.UTC) if timestamp else None

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(
            self.hass, f"{DOMAIN}_{self.activity.serial}_updated", self.async_write_ha_state,
        ))


class CurrentModel(SensorEntity):
    """Use the same title resolver as the push, independently of push cadence."""

    _attr_should_poll = False

    def __init__(self, activity):
        self.activity = activity
        self._attr_unique_id = f"{activity.serial}_live_activity_model"
        self._attr_name = f"{activity.serial} Live Activity model"

    @property
    def native_value(self):
        return self.activity.get_name() or "A1 mini"

    async def async_added_to_hass(self):
        sources = list(self.activity.entities.values())
        self.activity.entities["model_name"] = self.entity_id
        self.async_on_remove(async_track_state_change_event(
            self.hass, sources, self._handle_change,
        ))

    @callback
    def _handle_change(self, event):
        self.async_write_ha_state()
