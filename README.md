# Bambu Live Activity

A small Home Assistant custom integration that mirrors Bambu Lab print progress to the Home Assistant iOS Companion App as a Live Activity, including the Apple Watch Smart Stack presentation.

It intentionally creates **no Home Assistant entities, scripts, helpers, or automations**, so it does not clutter **Settings → Automations & scenes**.

## Live Activity layout

The payload is designed around a compact Watch/iPhone layout:

```text
Flexagon                         9m
47% · 16:26
██████████░░░░
```

- **Title:** Bambu print/subtask name
- **Critical text:** remaining time
- **Message:** percentage · estimated finish time
- **Progress:** 0–100%

A normal Home Assistant notification is sent when the print finishes. By default, failed prints also generate a notification.

## Requirements

- Home Assistant **2026.7.0 or newer**
- [ha-bambulab](https://github.com/greghesp/ha-bambulab), configured for your printer
- Home Assistant iOS Companion App
- Live Activities enabled in iOS / the Companion App
- A `notify.mobile_app_*` action for the target iPhone

The integration resolves ha-bambulab entities through their stable unique IDs, so renaming the entities in Home Assistant does not break it.

## Installation with HACS

Until this repository is accepted into the default HACS store:

1. HACS → Integrations → **⋮** → Custom repositories
2. Add:
   `https://github.com/OWNER/ha-bambu-live-activity`
3. Category: **Integration**
4. Install **Bambu Live Activity**
5. Restart Home Assistant
6. Settings → Devices & services → Add integration → **Bambu Live Activity**

## Configuration

Setup asks for only:

- the Bambu Lab printer
- the target `notify.mobile_app_*` action

The integration automatically finds these ha-bambulab printer sensors:

- Print status
- Print progress
- Remaining time
- End time
- Subtask name

### Options

From the integration entry → **Configure**:

- progress update step, default **1%**
- ETA-change threshold, default **1 minute**
- MQTT burst coalescing, default **3 seconds**
- finished notification
- failed-print notification

## Behaviour

- Starts automatically when Bambu print status becomes `prepare`, `running`, `pause`, or `slicing`.
- Waits briefly for the print name/ETA to populate.
- Debounces related MQTT updates so progress, ETA, and finish-time changes arriving together become one push.
- Updates when progress changes by the configured percentage threshold.
- Updates every 1% by default, whenever ETA/finish time shifts by the configured threshold, and at least once every minute while printing.
- Updates promptly on pause/resume.
- Sends a normal notification when the print reaches `finish`.
- Clears the Live Activity on finish, failure, or idle.
- Restarts the Live Activity after about **7.5 hours** so long prints continue past Apple's per-activity 8-hour expiry.

## Apple update limits

This integration cannot bypass ActivityKit limits imposed by iOS. Home Assistant Local Push is not subject to the same remote push throttling, so updates are most responsive when Local Push is available. The default 1-minute ETA threshold is intentionally configurable for long or very frequent prints.

## Manual installation

Copy:

```text
custom_components/bambu_live_activity
```

to:

```text
/config/custom_components/bambu_live_activity
```

Restart Home Assistant, then add **Bambu Live Activity** from Devices & services.

## Development

GitHub Actions validate the repository with:

- HACS Action
- Home Assistant hassfest

Before the first commit, replace the `OWNER` placeholder:

```bash
python3 scripts/set-owner.py "$(gh api user --jq .login)"
```

## Licence

MIT
