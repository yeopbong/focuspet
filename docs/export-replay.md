# Replaying an exported history

`focuspet replay` accepts a built-in synthetic scenario, a `scenario-v1` JSON file, or an actual `export-v1` file produced by the application's local export action. An archive retains its original `real`, `synthetic-demo`, or `test` namespace. Mixed-mode records are rejected.

## Recorded historical playback

```sh
focuspet replay history.json --recorded --output recorded-history.json
```

Recorded playback is the default for exports. It preserves the saved snapshot values, original prediction IDs, original model and parameter version references, state, Focus and Work Load. It attaches retained features by their original references. It does **not** re-run inference or invent missing model artifacts. Missing or expired feature records are marked unavailable. This remains useful after the raw seven-day bucket history or a historical model has been deleted.

The result explicitly says `recorded historical playback` and `computation: none; original snapshots preserved`. Recorded snapshot durations are disjoint emitted intervals, not overlapping feature-window lengths. Snapshot state labels summarize those intervals; deleted fine-grained transitions cannot be reconstructed. No saved notification is delivered again.

Current exports include an ingestion sequence number, so a wall-clock rollback does not reorder the historical stream. Older exports lacking this ordering cannot support strict recomputation of overlapping/rolled-back timestamps.

## Strict historical core recomputation

```sh
focuspet replay history.json --recompute-history --profile Mixed \
  --model-dir original-model-directory \
  --parameter-dir original-parameter-directory \
  --output verified-history.json
```

This mode requires retained aggregate activity buckets, historical workload integration steps and their starting load, the original feature schema, every referenced personal model artifact, and every referenced nondefault parameter version. The installed generic prior supports its own explicitly recorded version only. Model artifacts must pass their original registry, mode, checksum, dependency, feature-schema and class-order checks. Parameter registries must pass their checksum and mode checks. A single original parameter version can alternatively be supplied using `--parameters original-parameters.json`, including its original `version` field.

Exports intentionally omit personal settings. Therefore `--profile` is required: supply the profile that was used for the exported interval. There is no automatic guessing of an old profile from current settings. `--model-version` is an override for re-evaluation and cannot override the original versions in strict history mode.

The same production Engine, FeatureBuilder, classifier and Workload dynamics process the retained bucket stream. Explicit recorded rest, pause and reset actions are replayed in ingestion order. Later state feedback and wants-rest labels are never used as historical feature inputs or converted into a past declaration. Every available workload step is numerically checked; emitted states, Focus, components and model/parameter versions are compared to saved snapshots before a result can be called `verified historical recomputation`.

Exports do not include arbitrary hidden runtime context, earlier smoothing history, or every possible mid-window version transition. A truncated interval, changed profile, historical schema/configuration change, retrospective rest correction, or missing earlier context can therefore fail strict verification even when some artifacts remain. That is a reported limitation, not permission to substitute the current model or silently claim that new analysis is old history.

Safe error codes include `MISSING_HISTORICAL_MODEL`, `MISSING_HISTORICAL_PARAMETERS`, `HISTORICAL_PROFILE_REQUIRED`, `HISTORICAL_SCHEMA_MISMATCH`, `HISTORICAL_CONTEXT_UNAVAILABLE`, `AMBIGUOUS_EVENT_ORDER` and `HISTORY_REPRODUCTION_MISMATCH`. Messages describe the missing evidence and the `--recorded`/`--reevaluate` alternatives without printing private paths or event contents. A failed strict run leaves the archive unchanged.

## Explicit re-evaluation

```sh
focuspet replay history.json --reevaluate --profile Mixed \
  --model-dir chosen-model-directory --model-version personal-example \
  --parameters chosen-parameters.json --output re-evaluated-history.json
```

Re-evaluation uses the same core with the chosen model/version, chosen parameters and profile. Without `--model-version`, a supplied model directory selects its current valid active model, with the application's normal prior fallback. Without `--parameters`, a supplied parameter directory (or the model directory's `parameters` subdirectory) selects current valid parameters; otherwise defaults apply. If a supplied parameter JSON changes values but omits a version, the CLI assigns a stable `ad-hoc-…` version instead of mislabeling it as defaults.

The result explicitly says `re-evaluation`. It starts from the recorded starting load when retained workload steps provide one; otherwise from zero with an explicit initial-condition field. Earlier feature and smoothing context may be absent. Results go to a new output; original snapshots, corrections and version references remain unchanged. No feedback labels are fed into current or past features, no training is run, and policy decisions are suppressed. This is an analysis of a frozen history, not evidence that an unobserved reminder would have worked.

For built-in scenarios, normal replay already executes the shared core. Supplying a different model or parameter configuration marks that result as re-evaluation. The static demo's corrected branch also uses the production engine: its explicit five-minute Normal declaration begins halfway through shorter scenarios (including the 35-minute reading example), capped at 50 minutes for longer scenarios. This is a clearly labeled simulated branch, not a rewritten original prediction.
