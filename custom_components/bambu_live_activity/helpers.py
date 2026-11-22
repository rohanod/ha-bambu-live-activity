"""Resolve printers through their registered source and hardware serial."""

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import BAMBU_DOMAIN, BAMBUDDY_DOMAIN

COMMON_KEYS = (
    "end_time", "remaining_time", "current_layer", "bed_temp", "nozzle_temp",
    "pause", "resume", "stop", "chamber_light",
)
SOURCE_KEYS = {
    BAMBU_DOMAIN: {
        "print_status": "print_status", "print_progress": "print_progress",
        "subtask_name": "subtask_name", "stage": "current_stage",
        "total_layers": "total_layer_count", "speed": "printing_speed",
    },
    BAMBUDDY_DOMAIN: {
        "print_status": "status", "print_progress": "progress",
        "subtask_name": "current_print", "stage": "stage",
        "total_layers": "total_layers", "speed": "print_speed", "hms": "hms_status",
        "filament": "active_filament", "ams_status": "ams_status",
        "maintenance": "maintenance",
    },
}


def printer_identity(device, source):
    """Use the physical serial across integrations, including existing entries."""
    if device is None:
        return None
    if source == BAMBU_DOMAIN:
        return next((identifier[1] for identifier in device.identifiers
                     if len(identifier) == 2 and identifier[0] == source), None)
    if any(identifier and identifier[0] == source for identifier in device.identifiers):
        return device.serial_number
    return None


def resolve_progress_entities(hass, serial, source=BAMBU_DOMAIN):
    """Anchor Bambuddy’s entity IDs to the status sensor on the matching device."""
    registry = er.async_get(hass)
    entries = list(registry.entities.values())
    keys = {key: key for key in COMMON_KEYS} | SOURCE_KEYS[source]
    if source == BAMBU_DOMAIN:
        prefix = f"{serial}_"
        device_id = None
    else:
        devices = {
            device.id for device in dr.async_get(hass).devices.values()
            if (printer_identity(device, source) or "").casefold() == serial.casefold()
        }
        status_entries = [
            entry for entry in entries if entry.platform == source and entry.device_id in devices
            and entry.domain == "sensor" and entry.unique_id.endswith("_status")
            and not entry.unique_id.endswith(("_hms_status", "_ams_status"))
            and not entry.disabled_by
        ]
        # The per-printer status key is "status"; other sensors can end in "_status".
        status_entries = [entry for entry in status_entries if any(
            sibling.platform == source and sibling.device_id == entry.device_id
            and sibling.unique_id == entry.unique_id.removesuffix("status") + "progress"
            for sibling in entries
        )]
        if len(status_entries) != 1:
            return {}
        status_entry = status_entries[0]
        prefix = status_entry.unique_id.removesuffix("status")
        device_id = status_entry.device_id

    domains = {"chamber_light": "light", "speed": "select",
               **dict.fromkeys(("pause", "resume", "stop"), "button")}
    wanted = {(domains.get(key, "sensor"), prefix + source_key): key
              for key, source_key in keys.items()}
    return {
        wanted[(entry.domain, entry.unique_id)]: entry.entity_id
        for entry in entries
        if entry.platform == source and (device_id is None or entry.device_id == device_id)
        and not entry.disabled_by and (entry.domain, entry.unique_id) in wanted
    }


def normalize_status(status):
    """Bambuddy exposes different enum names for the same printer states."""
    return {
        "preparing": "prepare", "printing": "running",
        "paused": "pause", "finished": "finish",
    }.get(status, status)
