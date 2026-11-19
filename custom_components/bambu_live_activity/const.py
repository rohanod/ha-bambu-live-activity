"""Constants for Bambu Print Notifications."""

DOMAIN = "bambu_live_activity"
BAMBU_DOMAIN = "bambu_lab"
BAMBUDDY_DOMAIN = "bambuddy"
CONF_SOURCE = "printer_source"
DEFAULT_DRIFT_PERCENT = 3
IMMEDIATE_DRIFT_PERCENT = 50

CONF_PRINTER_SERIAL = "printer_serial"
CONF_NOTIFY_SERVICE = "notify_service"


def dashboard_url_path(serial: str) -> str:
    """One hidden dashboard per printer; survives reloads and upgrades."""
    return f"bambu-live-activity-{''.join(c for c in serial if c.isalnum()).lower()}"


def dashboard_path(serial: str) -> str:
    return f"/{dashboard_url_path(serial)}/print"
