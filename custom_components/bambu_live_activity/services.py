"""Debug and maintenance actions."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN

ATTR_ENTRY_ID = "entry_id"
SCHEMA = vol.Schema({vol.Optional(ATTR_ENTRY_ID): cv.string})


def _runtimes(hass: HomeAssistant, call: ServiceCall) -> list[Any]:
    loaded: dict[str, Any] = hass.data.get(DOMAIN, {})
    entry_id = call.data.get(ATTR_ENTRY_ID)
    found = [loaded[entry_id]] if entry_id in loaded else [] if entry_id else list(loaded.values())
    if not found:
        raise ServiceValidationError("No loaded Bambu Live Activity printer matches")
    return found


def async_register_services(hass: HomeAssistant) -> None:
    async def test_activity(call: ServiceCall) -> None:
        for runtime in _runtimes(hass, call):
            await runtime.live_activity.async_demo()

    async def restart_activity(call: ServiceCall) -> None:
        for runtime in _runtimes(hass, call):
            await runtime.live_activity.async_restart()

    async def end_activity(call: ServiceCall) -> None:
        for runtime in _runtimes(hass, call):
            await runtime.live_activity.async_end()

    async def test_finished(call: ServiceCall) -> None:
        for runtime in _runtimes(hass, call):
            await runtime.async_send_finished("Test print")

    async def get_status(call: ServiceCall) -> dict[str, Any]:
        return {rt.serial: rt.live_activity.snapshot() for rt in _runtimes(hass, call)}

    for name, handler, response in (
        ("test_activity", test_activity, SupportsResponse.NONE),
        ("restart_activity", restart_activity, SupportsResponse.NONE),
        ("end_activity", end_activity, SupportsResponse.NONE),
        ("test_finished_notification", test_finished, SupportsResponse.NONE),
        ("get_status", get_status, SupportsResponse.ONLY),
    ):
        hass.services.async_register(DOMAIN, name, handler, schema=SCHEMA, supports_response=response)
