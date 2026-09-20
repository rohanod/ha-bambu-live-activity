"""Constants for Bambu Live Activity."""

from __future__ import annotations

DOMAIN = "bambu_live_activity"

CONF_PRINTER_SERIAL = "printer_serial"
CONF_NOTIFY_SERVICE = "notify_service"

CONF_PROGRESS_STEP = "progress_step"
CONF_ETA_THRESHOLD_MINUTES = "eta_threshold_minutes"
CONF_DEBOUNCE_SECONDS = "debounce_seconds"
CONF_NOTIFY_FINISHED = "notify_finished"
CONF_NOTIFY_FAILED = "notify_failed"

DEFAULT_PROGRESS_STEP = 1
DEFAULT_ETA_THRESHOLD_MINUTES = 1
DEFAULT_DEBOUNCE_SECONDS = 8
DEFAULT_NOTIFY_FINISHED = True
DEFAULT_NOTIFY_FAILED = True

LIVE_ACTIVITY_TAG_PREFIX = "bambu_print"
ROLLOVER_SECONDS = 7.5 * 60 * 60
START_DELAY_SECONDS = 8

BAMBU_DOMAIN = "bambu_lab"

KEY_PRINT_STATUS = "print_status"
KEY_PRINT_PROGRESS = "print_progress"
KEY_REMAINING_TIME = "remaining_time"
KEY_END_TIME = "end_time"
KEY_SUBTASK_NAME = "subtask_name"

REQUIRED_BAMBU_KEYS = {
    KEY_PRINT_STATUS,
    KEY_PRINT_PROGRESS,
    KEY_REMAINING_TIME,
    KEY_END_TIME,
    KEY_SUBTASK_NAME,
}

ACTIVE_STATES = {"prepare", "running", "pause", "slicing"}
FINISHED_STATES = {"finish"}
FAILED_STATES = {"failed"}
IDLE_STATES = {"idle"}
