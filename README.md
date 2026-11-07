# Bambu Live Activity

Home Assistant integration for one quiet Bambu Lab print Live Activity, completion notifications, and a matching hidden dashboard. Version 0.2.0 replaces percentage-triggered pushes and the one-minute heartbeat with ETA-drift updates.

## Install and update with HA Marketplace

On Home Assistant **2026.11+**, open **Settings → Marketplace → Add from link**, enter `https://github.com/rohanod/ha-bambu-live-activity`, and install **Bambu Live Activity**. Restart Home Assistant, then add the integration under **Settings → Devices & services** if it is not already configured.

Existing HACS installations migrate automatically through Home Assistant's Marketplace migration. Keep the existing integration entry: the domain, printer identity, notification target, and Live Activity tag stay unchanged. Do not add a second entry or recreate the old YAML package. The repository retains `hacs.json` because **Marketplace still requires it**; older HACS installations remain compatible.

Sources: [Marketplace installation and HACS migration](https://www.home-assistant.io/integrations/marketplace/), [Companion Live Activities](https://companion.home-assistant.io/docs/notifications/live-activities).

Requirements:

- Home Assistant 2026.7+ (native Marketplace requires 2026.11+).
- [ha-bambulab](https://github.com/greghesp/ha-bambulab), configured for the printer.
- iOS Companion App with Live Activities enabled and a working `notify.mobile_app_*` action.

## Update policy

A routine push requires **both**:

- At least **10 minutes** since the last successful notification action.
- At least **3 minutes** of absolute ETA drift from the last published completion timestamp, in either direction.

Change the drift threshold under the integration's **Configure** options. The 10-minute cooldown is fixed; it is **not** an update schedule. A stable ETA produces no further push, even for an hour. Percentage, temperature, layers, model-name changes and the decreasing remaining-time countdown do not trigger pushes. No heartbeat or keep-alive is required.

One deferred timer rechecks the *current* ETA when the cooldown expires. Further changes do not queue pushes. A recovered or unavailable ETA discards the pending update. If a print began without a usable ETA, the first valid ETA may be published after the cooldown.

Starts, ends, and planned renewal before iOS's eight-hour limit are lifecycle operations, outside the routine-update gate. Pause/resume preserves the same activity and does not independently send pushes; a later qualifying ETA update includes the current status. Confirmed finish/cancel/failure events end the activity immediately. A status-only stop has a 30-second debounce to tolerate transient `idle`/`finish` flaps. Existing print-finished phone/Watch and HA persistent notifications remain intact.

Notification failures retain the last successful baseline and back off for 10 minutes. Home Assistant's notification action does **not** acknowledge delivery or rendering on the iPhone: the persisted success means the action returned successfully, not that APNs/iOS displayed it. Apple may throttle or drop updates independently.

## Persistence and dashboard

The integration owns these resources; no manual helpers, automations, scripts, Lovelace resources, HACS cards, or kiosk-mode setup are needed:

- Two diagnostic timestamp sensors: **Last successful Live Activity update** and **Last published print ETA**. They expose publication state saved atomically through Home Assistant's storage API, including across restarts. They remain available after a print ends.
- One **Live Activity model** sensor, using the same model-name resolver as the push and updating directly from printer events.
- One hidden dashboard panel per printer, at `/bambu-live-activity-<lowercase printer serial>/print`. It contains exactly **one** read-only card, with the same title, status colours, icon, percentage, finish time, remaining time, and progress bar. The layout adapts to iPhone widths. No navigation or printer controls are added.

The panel reads current entity states and uses a local display clock. It does not depend on push frequency and makes no notification calls. The Live Activity itself remains the last published snapshot between qualifying updates.

Every real start/update includes the Companion-supported **`data.url`** relative dashboard path. This routes supported Lock Screen and Dynamic Island taps into the originating server in the Companion App; it is not Android `clickAction`. Actual device taps depend on the installed Companion version and require an iPhone test.

Restart/reload restores the open activity, cooldown, last published ETA, title, and renewal deadline without issuing another start. Unloading removes the integration's panel/listeners, without ending the phone activity. An ambiguous in-flight start is recovered conservatively using its existing tag.

For an A1 mini with serial `0309DA480600983`, the dashboard path is `/bambu-live-activity-0309da480600983/print`.

## Debug actions

Run in **Developer tools → Actions**. Optional `entry_id` selects one printer; omitted means all configured printers.

| Action | Purpose |
|---|---|
| `bambu_live_activity.get_status` | Inspect state, publication timestamps, pending update, start count and last 25 pushes. Sends nothing. |
| `bambu_live_activity.restart_activity` | Explicitly reopen a dismissed activity for a running print. |
| `bambu_live_activity.end_activity` | Explicitly end real and test activities. |
| `bambu_live_activity.test_activity` | Play a fake print on a separate `_test` tag. **Uses iOS push budget; do not automate.** |
| `bambu_live_activity.test_finished_notification` | Send a sample finished alert. |

An unavailable, `restored: true` legacy automation/script/helper is not a running sender. Old `packages/a1mini-live-activity.yaml` installations must remain disabled to avoid competing senders. This integration does not remove legacy entities or modify other printer automations.

## Verification and rollback

```sh
python3 -m py_compile custom_components/bambu_live_activity/*.py
python3 -m unittest discover -s tests -v
node --test tests/test_panel.mjs
```

Tests use fake HA state, clock, storage, and notification actions: they send **no real pushes**. Browser checks can run against the bundled panel with simulated HA entity states. Full HA startup, physical printing, iOS delivery and actual Lock Screen/Dynamic Island taps require device verification; local tests cannot establish those results.

To roll back, select the previous release in Marketplace and restart HA. Existing config entries and saved publication state are retained; older releases ignore the new storage. Returning to 0.1.9 also restores its frequent update policy and removes the bundled dashboard. Diagnostic entities from 0.2.0 may appear unavailable on the older version. Do not run both senders or manually clear/restart an activity during migration.

## Licence

MIT
