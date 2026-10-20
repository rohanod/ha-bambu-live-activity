# Bambu Live Activity

HACS-compatible Home Assistant custom integration that adds a quiet Live Activity for Bambu Lab printers **without automations, scripts, helpers, or extra entities**. It builds on the v0.1.5 dedicated print-finished notifications; existing config entries remain valid.

## Requirements

- Home Assistant 2026.7 or newer, the Home Assistant iOS Companion App, and its Live Activities permission
- [ha-bambulab](https://github.com/greghesp/ha-bambulab) configured for the printer
- A working `notify.mobile_app_*` action configured in this integration

## Live Activity layout

```text
cone-customised                   9m
47% · 16:26
██████████░░░░
```

- **Apple Watch:** iOS 18+ mirrors the activity to the Watch Smart Stack automatically. Text is kept short (title, "16:26 finish", remaining time) so it stays readable there.
- **Human-readable model name:** uses the actual Bambu task/filename (e.g. `cone-customised.gcode.3mf` → `cone-customised`), rejecting stale profile IDs and generic text such as `1 piece`.
- **Progress and ETA:** a current percentage, estimated finish time, remaining-time text, and progress bar.
- **Quiet updates:** same stable `tag`, `alert_once: true`, and `silent: true` on updates. One Live Activity is retained for the whole print; title changes do not restart it.
- **Frequency:** when a full percentage point changes (with a 15-second minimum interval to avoid bursts), or after 60 seconds without another push. Additional MQTT fields are combined into one update.
- **Remaining time:** derived from valid printer end time when possible; otherwise read from Bambu's remaining-time sensor with unit conversion (hours/minutes).
- **Long prints:** renew once at about 7½ hours, before Apple's 8-hour activity limit.
- **Completion:** a genuine `bambu_lab_event`/`event_print_finished` triggers one ordinary iPhone/Watch notification **and** one Home Assistant persistent notification. A stale `finish` state or a replayed finish event (e.g. after a reconnect or restart) never triggers an alert: only prints this integration saw running do.

*Important:* Home Assistant's Companion App says quiet updates use lower push priority and iOS may delay or throttle them outside Local Push. The initial Live Activity may still generate one alert; subsequent updates are silent. iOS also applies an independent push-to-start budget. This integration cannot bypass Apple's limits.

## Debug actions

Run these from **Developer tools → Actions** (or any script). All take an optional `entry_id` (printer); empty means every printer.

| Action | Does |
|---|---|
| `bambu_live_activity.test_activity` | Plays a ~25 s fake print on a separate tag. Uses iOS's push budget, so don't spam it. |
| `bambu_live_activity.restart_activity` | Reopens the Live Activity for the running print right now (e.g. after you swiped it away). |
| `bambu_live_activity.end_activity` | Ends the print and test Live Activities on the iPhone, even if the integration thinks none is open. |
| `bambu_live_activity.test_finished_notification` | Sends a sample finished notification ("Test print", "Finished at HH:MM"). |
| `bambu_live_activity.get_status` | Returns state, start count and the last 25 pushes. Check this if duplicates appear: more than one `START` per print means Home Assistant sent them. |

The old *Test Live Activity* button entity is gone; delete its leftover unavailable entity in **Settings → Devices & services → Entities**.

## Duplicate protection

- Every push for a printer uses one `tag`; an update never opens a new activity unless the previous one was ended.
- Reloading or restarting Home Assistant does **not** end the activity; it keeps updating the same one.
- A brief `idle`/`finish` status flap must last 30 s before the activity is ended, and fresh starts are limited to one per 2 minutes per printer.

## Install

HACS → Custom repositories → add this GitHub repository as **Integration** → install **Bambu Live Activity** → restart Home Assistant → **Settings → Devices & services → Add integration**.

Existing Bambu Print Notifications (v0.1.5) users only need to update in HACS and restart. There is **no new setup**; the config-entry domain is unchanged. If you previously installed the old `packages/a1mini-live-activity.yaml` package, remove or disable it first to prevent duplicate pushes from two separate implementations.

## Licence

MIT
