# Platform and validation status

The native release target is **macOS 14+ on Apple Silicon**. Verification on September 8, 2026 used macOS 26.5.2 (25F84), an Apple M2 with 8 logical cores and 16 GiB RAM, Python 3.11.9 and native Cocoa Qt/PySide6 6.8.3. Other macOS versions and physical display configurations were not exercised on this device.

## Completed checks

| Area | Evidence and scope |
| --- | --- |
| Regression | 112 tests passed locally, including 16 native Qt tests. Seven opt-in browser cases passed separately; repeated CI runs are not additional distinct tests. Ruff and mypy passed. Upstream SciPy solver deprecation warnings remain. |
| Real collection | Source and extracted standalone app separately reported Input Monitoring available. In-app consent preceded collection. Both produced keyboard, click, scroll, pointer, idle, application-switch and broad-category aggregates, features, predictions, Focus and persistent workload updates. Stored bucket fields were inspected; no text, keycodes, titles, screenshots or coordinates were present. These observations are not human ground-truth work labels. |
| Native controls | Isolated native Qt tests cover onboarding, character resources, dragging, double-click, tray recovery, state/feedback persistence, rest, settings, pause/resume and restart. A separate 412.47-second paced synthetic UI probe completed 20 operations but retained three toggle-state assertion timeouts. A subsequent controlled flow and three explicit rest/quiet toggle cycles passed; the timed probe is not reported as entirely passing. |
| Short resource measurement | A consented standalone app was sampled for 120.016 seconds: 25 complete samples, healthy listener throughout, no stale diagnostics, queue backlog, UI polling stall or write failure. Mean main-process CPU was 0.81% of one logical core; peak RSS was 108.83 MiB. This is a short sample, not a long-term performance guarantee. |
| Training cost | A separate frozen CLI training process on 36 synthetic records took 2.142 seconds including imports and persistence, used 1.730 CPU seconds, and peaked at 154.03 MiB RSS. Its model was registered as a candidate and was not automatically activated. |
| Clean installation | Public checkout and a new locked environment, followed by a second non-editable wheel installation. Source, wheel and standalone CLI runs exercised doctor, scenarios, deterministic replay, changed parameters, training/reload, corrupted-artifact and namespace rejection, calibration, evaluation, and static export. Their baseline replay JSON matched; all four sprite atlases matched byte for byte. |
| Experiments | Five seeds (7, 17, 27, 37, 47), 30/60/90-label budgets, and 64 trials per optimizer per seed were rerun. [Metrics, support, rejection coverage and trial records](../experiments/results/report.md). These are synthetic comparisons. |
| Browser and hosting | The [public Pages demo](https://yeopbong.github.io/focuspet/) passed seven Chromium 134 HTTPS tests: all scenarios/characters, playback, seeking, reset, precomputed correction, missing/rest display, refresh, project subpath and widths 375/768/1440. No console errors, failed resources or external-origin requests were observed. Local HTTP tests also passed. |
| Packaging | The extracted package was launched outside the checkout. Its native binaries are arm64, resources and dependency imports are present, and the ad-hoc signature verifies. It contains an exact source-commit record and excludes local editable-install provenance and private activity. |

## Limits and retained failures

- **A two-hour continuous validation was not completed.** The short measurements above must not be read as a two-hour result.
- An earlier resource monitor stopped after 477 seconds with an `OSError`; its original operation and errno were not retained. Monitoring now records operation-specific errors, nullable missing measurements and write failures; fault-injection regressions and fresh native probes passed. The historical cause remains unknown.
- The timed UI probe treated the rest toggle as an idempotent end command after its state had already changed; that was a test precondition error. Its two quiet-state assertions did not record the initial state and remain unattributed. Three controlled rest/quiet cycles subsequently matched both the database and menu state, without reproducing a product defect. Physical multi-monitor removal, cross-application focus under every condition, live permission revocation and sleep/wake were not comprehensively verified.
- Direct `file://` interaction could not be verified. The offline archive contains local assets and no server dependency; HTTP/HTTPS success does not establish direct-file browser behavior.
- The package is **not Developer ID signed or notarized**. Its ad-hoc signature is an integrity mechanism, not verified publisher identity.
- Windows and Linux collectors report unavailable capabilities. Intel macOS is not supported by the arm64 archive.
- System Do Not Disturb, full-screen detection and reliable initial screen-lock detection are unavailable. In-app quiet/meeting controls remain available.
- Real personal-model effectiveness requires independent feedback. Synthetic tests do not establish accuracy for a person or improvements to work habits.

[Actions](https://github.com/yeopbong/focuspet/actions) cover dependency installation, lint/types, core and Qt tests, wheel/CLI workflows, and browser export. CI does not grant or verify native system consent. The [release](https://github.com/yeopbong/focuspet/releases/tag/v0.1.0) verification attachment identifies the exact source revision, CI runs and archive hashes without publishing real activity or local paths.
