# Bambu Print Notifications

A minimal Home Assistant custom integration for Bambu Lab printers.

It creates **no automations, scripts, helpers, entities, or Live Activities**.

When ha-bambulab emits its real `event_print_finished` lifecycle event, it sends one normal `notify.mobile_app_*` notification and one Home Assistant persistent notification. The title prefers Bambu Cloud's `designTitle` (the model/design name).

Using the dedicated lifecycle event instead of the cached `finish` state prevents old completed prints from replaying completion alerts after reconnects or restarts.

## Completion message

```text
<Model name>
Finished printing.
```

## Licence

MIT
