"""Constants for Bambu Print Notifications."""

DOMAIN = "bambu_live_activity"
BAMBU_DOMAIN = "bambu_lab"

CONF_PRINTER_SERIAL = "printer_serial"
CONF_NOTIFY_SERVICE = "notify_service"

KEY_PRINT_STATUS = "print_status"


def dashboard_url_path(serial: str) -> str:
    """One hidden dashboard per printer; survives reloads and upgrades."""
    return f"bambu-live-activity-{''.join(c for c in serial if c.isalnum()).lower()}"


def dashboard_path(serial: str) -> str:
    return f"/{dashboard_url_path(serial)}/print"
