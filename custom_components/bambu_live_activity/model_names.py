"""Human-readable names for Bambu print jobs (no HA dependencies)."""

from __future__ import annotations

from pathlib import PurePosixPath
import re
from typing import Any


_BAD_GENERIC = re.compile(
    r"^(?:(?:a1\s*[- ]?mini|bambu(?: lab)?|plate\s*\d+)[, :–-]*\s*)?"
    r"(?:(?:\d+\s*(?:pieces?|items?|objects?))|(?:plate\s*\d+))$",
    re.IGNORECASE,
)
_BAD_PROFILE = re.compile(r"(?:^|[#_-])profileid[-_#]", re.IGNORECASE)
_BAD_PLATE_FILE = re.compile(r"^(?:plate[_\s-]*\d+|metadata)$", re.IGNORECASE)
_SUFFIXES = (".gcode.3mf", ".gcode.gcode", ".gcode", ".3mf", ".stl", ".obj", ".step", ".stp")


def clean_title(value: Any) -> str | None:
    """Remove raw IDs/file suffixes and reject generic Bambu display labels."""
    if value is None:
        return None
    title = str(value).strip()
    if not title or title.lower() in {"unknown", "unavailable", "none", "null"}:
        return None

    title = PurePosixPath(title.replace("\\", "/")).name
    if title.lower() in {"a1 mini", "a1-mini", "a1mini", "1 piece"}:
        return None
    if _BAD_GENERIC.fullmatch(title):
        return None

    marker = re.search(r"#profileid[-_#]", title, re.IGNORECASE)
    if marker is not None:
        title = title[:marker.start()].strip(" _-#")

    for _ in range(4):
        for suffix in _SUFFIXES:
            if title.lower().endswith(suffix):
                title = title[:-len(suffix)].strip()
                break
        else:
            break

    # Local file names sometimes begin with a numeric cache ID.
    title = re.sub(r"^\d{5,}[-_ ]+", "", title).strip(" _-")

    if (
        not title
        or title.isdigit()
        or _BAD_GENERIC.fullmatch(title)
        or _BAD_PROFILE.search(title)
        or _BAD_PLATE_FILE.fullmatch(title)
    ):
        return None
    return title[:85]


def title_from_job(job: Any | None, fallback_sensor: Any = None) -> str | None:
    """Prioritise real model labels, never stale cloud tasks or plate counters."""
    values: list[Any] = []
    if job is not None:
        data = getattr(job, "_task_data", None)
        if isinstance(data, dict):
            task_id = str(getattr(job, "task_id", "") or "")
            cloud_id = str(data.get("id") or "")
            # Prevent last week's Bambu Cloud print from supplying today's
            # model name after a network reconnect.
            if task_id and cloud_id and task_id == cloud_id:
                values.append(data.get("designTitle"))
        values.extend((
            getattr(job, "subtask_name", None),
            fallback_sensor,
            getattr(job, "gcode_file_downloaded", None),
            getattr(job, "gcode_file", None),
        ))
        if isinstance(data, dict):
            task_id = str(getattr(job, "task_id", "") or "")
            cloud_id = str(data.get("id") or "")
            if task_id and cloud_id and task_id == cloud_id:
                values.extend((data.get("title"), data.get("plateName")))
    else:
        values.append(fallback_sensor)

    for value in values:
        title = clean_title(value)
        if title:
            return title
    return None
