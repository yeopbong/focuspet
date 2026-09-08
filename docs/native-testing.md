# Native test checklist

This is a repeatable manual checklist. Actual completed checks belong in [platform and validation status](platform-status.md). A Qt widget test does not establish global input access or behavior on every display configuration.

| Check | Procedure and expected result |
| --- | --- |
| Consent and capability status | Start with a fresh local data directory. Decline collection, use the demo, then separately exercise explicit consent. Unavailable signals remain missing. |
| Character interaction | Exercise all eight actions for each character, click, double-click, drag, right-click, scaling and tray commands. Dragging must not trigger a click response. |
| Transparent regions and recovery | Click through alpha-transparent regions, enable full click-through, and recover using the tray. Test with tray availability removed. |
| Focus and multiple displays | Type in another application during automatic animations. Move the pet between displays, unplug a display, and verify position clamping and keyboard focus. |
| Real aggregation | In a consented session, exercise keyboard, click, scroll, pointer movement and application changes. Verify aggregate counters, features and snapshots while excluding content. |
| Pause and permission revocation | Pause collection and revoke Input Monitoring during use. Confirm reads stop, the status explains the gap and the app does not repeatedly request access. |
| Sleep and lock | Lock/unlock and sleep/wake the device. Unknown gaps must not become work or confirmed rest, and elapsed time must not be counted twice. |
| Feedback and restart | Submit, revise and withdraw past corrections; restart the app and verify versions, original predictions and workload continuity. |
| Local data controls | Export after field preview, delete a range and delete all managed data. Check dependent feedback/features/artifacts and paused state. |
| Standalone package | Launch the packaged app outside the source checkout. Confirm resources, tray, menus, model loading and native collector capability status. |
| Offline demo | Open `demo/index.html` with networking disabled. Exercise scenarios, play/pause, speed, seek, correction and parameter controls; confirm no requests. |

## Two-hour longevity run

Launch the packaged application for an actual consented daily-use session, then run:

```sh
python scripts/soak.py --pid <application-pid> --minutes 120 --output <local-report.json>
```

The script samples process CPU, memory and responsiveness evidence; it does not collect input contents. Keep any report describing a real routine private. Exercise pause, sleep/wake, screen changes and tray recovery during the run, and record actual duration, memory trend, restarts, queue stalls and visible responsiveness. Process sampling alone cannot certify UI responsiveness. An accelerated replay is a separate check.
