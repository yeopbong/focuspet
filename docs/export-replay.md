# Replaying an exported history

`focuspet replay` accepts a built-in scenario, a `scenario-v1` JSON file, or an `export-v1` file from the app's export action. Exports retain their `real`, `synthetic-demo`, or `test` mode; mixed-mode records are rejected.

## Recorded playback

```sh
focuspet replay history.json --recorded --output recorded-history.json
```

This is the default for exports. It preserves saved snapshots, predictions and model/parameter references without running inference. Missing or expired features are marked unavailable. It remains usable after raw activity or historical model files have been deleted.

The output is labeled `recorded historical playback`. Snapshots describe disjoint emitted intervals; deleted fine-grained transitions cannot be reconstructed. Saved notifications are never delivered again.

## Recompute with original versions

```sh
focuspet replay history.json --recompute-history --profile Mixed \
  --model-dir original-model-directory \
  --parameter-dir original-parameter-directory \
  --output verified-history.json
```

Strict recomputation needs retained activity buckets, workload steps and starting load, the original feature schema, and all referenced personal model and nondefault parameter versions. Supply the original profile with `--profile`, since exports omit personal settings. A single historical parameter version can instead be passed with `--parameters original-parameters.json`, including its `version` field.

The shared engine replays buckets and explicit rest, pause and reset actions in ingestion order, then compares available workload steps and snapshots. Later feedback is excluded from historical inputs. Original model and parameter files must pass their registry, mode, checksum and compatibility checks.

Truncated history, missing smoothing context, changed profiles or schemas, retrospective rest corrections and some mid-window version changes can prevent exact recomputation. Older exports without ingestion ordering cannot strictly reproduce overlapping or rolled-back timestamps.

Errors include `MISSING_HISTORICAL_MODEL`, `MISSING_HISTORICAL_PARAMETERS`, `HISTORICAL_PROFILE_REQUIRED`, `HISTORICAL_SCHEMA_MISMATCH`, `HISTORICAL_CONTEXT_UNAVAILABLE`, `AMBIGUOUS_EVENT_ORDER` and `HISTORY_REPRODUCTION_MISMATCH`. Use recorded playback when original evidence is unavailable, or choose re-evaluation. A failed run leaves the archive unchanged.

## Re-evaluate with chosen versions

```sh
focuspet replay history.json --reevaluate --profile Mixed \
  --model-dir chosen-model-directory --model-version personal-example \
  --parameters chosen-parameters.json --output re-evaluated-history.json
```

Without `--model-version`, a supplied model directory selects its valid active model with normal prior fallback. Without `--parameters`, the parameter directory (or the model directory's `parameters` subdirectory) selects current valid parameters; otherwise defaults apply. Parameter JSON without a version receives a stable `ad-hoc-…` version.

The output is labeled `re-evaluation`. It starts at the retained workload starting load, or zero with an explicit initial-condition field. Earlier feature and smoothing context may be absent. The original archive is unchanged, feedback is not used as a feature, and replay does not train models or deliver reminders.

Built-in scenarios run the same core. Supplying other model or parameter versions marks their result as re-evaluation. The browser demo's corrected branch is a simulated five-minute Normal declaration, starting halfway through short scenarios and at most 50 minutes into longer ones.
