# Privacy and control

Focus Pet runs locally without an account, telemetry, automatic uploads, an HTTP server, or cloud inference. After dependencies are installed, the desktop core and static demo work offline. Local storage is **not encrypted by this application**.

Collection starts only after the in-app consent choice. macOS may additionally require Input Monitoring in System Settings. Declining consent leaves the application usable, including an isolated synthetic demo. `focuspet doctor` checks capability status without requesting permission or reading activity.

## Allowed observations

The listen-only macOS callback increments keyboard-event counts, mouse-click counts and scroll-event counts. Pointer activity is the summed magnitude of event deltas. It does not read keycodes, Unicode, text, passwords, clipboard contents, window titles, screen images, browser history, page bodies, audio or video. Coordinates and event objects never enter a queue, database or log. Scroll represents event activity, not pixels of document content.

A once-per-second foreground application query immediately maps the bundle identifier to one of eight categories. The previous identifier exists transiently in memory for **application switch** counting. The identifier is not included in activity records. User-entered category mappings remain in local settings; default export omits those settings. Browser is not assumed to mean distracted, and IDE is not assumed to mean focused.

Signal availability is independent: supported, granted, denied, unavailable or error. Missing input permission produces nullable counts and a gap, not zeros. Permission revocation disables the event tap without repeated permission prompts. Pause disables input capture, stops polling application and idle state, and clears pending counters. Using the companion is excluded from work evidence; the first global click may arrive before Qt, so the corresponding bucket is conservatively excluded.

Sleep notifications and available lock notifications suppress observation. Initial lock state is not publicly detected reliably by this version. System Do Not Disturb and full-screen state are not inferred; use in-app quiet mode, meeting mode and quiet hours. No Screen Recording permission is requested.

## Local files and deletion

Default application-managed location on macOS: `~/Library/Application Support/Focus Pet/`. Each of `real`, `synthetic-demo` and `test` has its own database and model tree. `FOCUSPET_DATA_HOME` or `--data-dir` can select another parent directory.

- Aggregate 10-second input buckets: 7 days by default.
- Causal features and derived prediction/load history: 90 days by default.
- Explicit feedback and model versions: 365 days by default, configurable.

Derived workload steps contain already-estimated components and integrated durations, not raw input counts. Retention removes expired feature copies and marks dependent feedback untrainable. Deleting a range removes overlapping records, linked features and feedback, and invalidates dependent model/parameter artifacts. Full deletion covers the application's managed records, model versions and owned backups. It does not claim removal from external system backups or secure physical-media erasure. The application pauses after deletion.

Export presents a field preview and writes a user-chosen local JSON file. Default export excludes settings and retains opaque session/episode relationship references. Aggregates can still reveal work routines; exporting is an explicit user action. Nothing is published automatically.
