# Known limitations

- Activity counts cannot reliably identify intent. Browser reading and entertainment can look alike. The initial classifier is a configurable rule prior, and ambiguous or missing observations can produce Unknown.
- Personal models, active querying and workload calibration have simulation and lifecycle tests. Real accuracy, long-term adaptation, probability calibration and improved work habits require independent real-use evidence.
- Work Load and its defaults are engineering choices. Rest self-reports express preferences, not medical fatigue. Frozen-history optimization is not a reminder intervention experiment.
- Native global collection is implemented for macOS. Windows and Linux report unavailable capabilities; Intel macOS is not covered by the arm64 package.
- System Do Not Disturb, full-screen state and reliable initial screen-lock state are unavailable. Use in-app quiet hours and meeting mode where needed.
- Physical monitor changes, cross-application focus, permission changes, sleep/wake and long sessions need native device testing. Current completed checks are listed in [validation status](platform-status.md).
- Local data is not application-encrypted. Deleting application-managed data does not delete external backups or guarantee secure media erasure.
- Bounded queues and artifact limits control resource use. Storage or collection errors pause processing and expose the error; disk exhaustion can still interrupt recording.
- Strict historical recomputation needs retained context and original model/parameter versions. Recorded snapshots remain distinguishable from new re-evaluation results.
- The public demo uses synthetic precomputed trajectories. It cannot read a visitor's desktop and is not a replacement for the native collector.

Release signing, notarization, two-hour longevity, browser verification and remote deployment are tracked in one place: [platform and validation status](platform-status.md).
