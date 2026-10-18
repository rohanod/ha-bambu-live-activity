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
- **Completion:** a genuine `bambu_lab_event`/`event_print_finished` triggers one ordinary iPhone/Watch notification **and** one Home Assistant persistent notification. A stale `finish` state never triggers a completion alert.

*Important:* Home Assistant's Companion App says quiet updates use lower push priority and iOS may delay or throttle them outside Local Push. The initial Live Activity may still generate one alert; subsequent updates are silent. iOS also applies an independent push-to-start budget. This integration cannot bypass Apple's limits.

## Preview

Press **Test Live Activity** on the integration's device page to play a ~25 s fake print (prepare → running → paused → running → done) on a separate tag. Repeated tests count against iOS's Live Activity push budget.

## Install

HACS → Custom repositories → add this GitHub repository as **Integration** → install **Bambu Live Activity** → restart Home Assistant → **Settings → Devices & services → Add integration**.

Existing Bambu Print Notifications (v0.1.5) users only need to update in HACS and restart. There is **no new setup**; the config-entry domain is unchanged. If you previously installed the old `packages/a1mini-live-activity.yaml` package, remove or disable it first to prevent duplicate pushes from two separate implementations.

## Licence

MIT
