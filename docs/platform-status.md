# Platform and validation status

The native release target is **macOS 14+ on Apple Silicon**, using Python 3.11 and Qt 6.8.3. A source-compatible core or successful simulation does not establish native collection support on another platform.

## Current validation

| Area | Evidence |
| --- | --- |
| Personal learning, calibration, simulation and exported-history replay | 31 focused tests passed in the publication verification run. Includes causal split/scaler checks, independent evidence gates, future-label exclusion, clock rollback ordering, cancellation, namespace rejection, safe artifacts, rollback and strict historical replay. |
| Five-seed experiments | Rerun with seeds 7, 17, 27, 37, 47; 30/60/90 independent labels and 64 trials per optimizer per seed. [Actual metrics and trial logs](../experiments/results/report.md). |
| Native UI and collector | Final publication-run evidence pending. |
| Consented real activity through the full pipeline | Final publication-run evidence pending. |
| Two-hour native longevity | Final publication-run evidence pending; simulated time and short launches do not satisfy this check. |
| Offline browser interaction and hosted demo | Final publication-run evidence pending. |
| Standalone package, signing and notarization | Final release verification pending. An ad-hoc signature alone is not Developer ID signing or notarization. |
| Remote build, Pages and Release | Repository: [yeopbong/focuspet](https://github.com/yeopbong/focuspet). Deployment and release verification pending. |

The focused test command was:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest \
  tests/test_learning.py tests/test_optimization.py \
  tests/test_experiments.py tests/test_export_replay.py -q
```

This run reported a disabled-plugin `qt_api` configuration warning and an upstream SciPy solver deprecation warning. Neither was a test failure. Native tests and their environment are reported separately.

## Support boundaries

- Windows and Linux collectors explicitly report unavailable capabilities. They do not silently simulate real activity.
- Intel macOS and macOS versions before 14 are not supported by the arm64 package.
- Screen Recording permission is neither requested nor used.
- System Do Not Disturb, full-screen detection and reliable initial screen-lock detection are unavailable. In-app quiet and meeting controls cover explicit quiet periods.
- Model effectiveness and workload calibration for real users require independent personal feedback. Synthetic results do not establish those claims.

Use `focuspet doctor` to inspect the current installation without reading activity or requesting permission. See the [native test checklist](native-testing.md) for device-level checks and [known limitations](known-limitations.md) for product limits.
