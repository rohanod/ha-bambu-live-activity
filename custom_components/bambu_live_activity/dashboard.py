"""A hidden, integration-owned dashboard. No Lovelace config to maintain."""

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.loader import async_get_integration

from .const import DOMAIN, dashboard_url_path
from .live_activity import STYLES

ASSET_URL = f"/{DOMAIN}/panel.js"


async def async_setup_assets(hass):
    await hass.http.async_register_static_paths([
        StaticPathConfig(ASSET_URL, str(Path(__file__).parent / "frontend" / "panel.js"), True),
    ])


async def async_setup_dashboard(hass, activity):
    integration = await async_get_integration(hass, DOMAIN)
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=dashboard_url_path(activity.serial),
        webcomponent_name="bambu-live-activity-panel",
        module_url=f"{ASSET_URL}?v={integration.version}",
        sidebar_title=None,
        config={"entities": dict(activity.entities), "styles": STYLES},
    )


def async_remove_dashboard(hass, serial):
    frontend.async_remove_panel(hass, dashboard_url_path(serial))
