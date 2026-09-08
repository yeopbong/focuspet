# Architecture

The installed Python package uses `importlib.resources` for bundled sprites and static demo templates. No component assumes the shell's working directory for application resources.

```text
NativeCollector / ReplayCollector
    ↓ scalar-only in-memory counters
ActivityBucket (fresh elapsed time + UTC interval)
    ↓
FeatureBuilder (60 seconds + 300-second past context)
    ↓ coverage/missing gate
GenericPrior / PersonalPredictor
    ↓ independent state, components, entropy and coverage
Engine → causal Focus smoothing → explicit Workload integration
    ↓ StateSnapshot and immutable WorkloadStep
AppService → Store single-writer actor → SQLite
    ↓ cached dictionaries / bounded events
Qt pet + tray + status + dashboard + settings

Explicit feedback → latest independent episodes → spawned fitting/search process
    → temporal candidate validation → later independent shadow evidence
    → atomic registry pointer update / retained previous version / rollback
```

`domain`, `features`, `models`, `policy`, `learning` and `optimization` have no Qt or native collector dependency. The desktop and Python replay call the same `Engine`, prior, workload dynamics and reminder policy. Replay suppresses notification delivery; deterministic snapshot IDs allow trajectory comparisons.

`AppService` runs the collector, state estimation and persistence coordination on a background thread. UI calls enqueue commands into a bounded queue (128) and reads copied cached dictionaries. A separate bounded queue (32) carries user-facing events. Qt's main thread handles presentation and direct input only. The SQLite actor has a single connection and a bounded queue; transactions group related feature, prediction and load writes. Training/search runs in a spawned process with cooperative cancellation plus bounded termination on shutdown.

Wall times are UTC epoch values; native interval duration comes from an injectable monotonic clock. Session IDs identify process/work-period boundaries. A restart restores the last workload as stale, never subtracts monotonic timestamps from a previous process, and lets the user preserve the cycle, explicitly confirm a bounded past rest or start a new cycle. Sleep/missing intervals hold the load. Explicitly declared rests are bounded by their declared end and fresh covered duration.

Model artifacts contain numeric JSON, feature ordering, class ordering, scaler, dependency versions, checksums and metadata. No arbitrary external pickle/joblib import exists. Registries update atomically. Corruption, incompatible schema or missing source data falls back to a retained valid version or the generic prior.

The static demo embeds Python-generated trajectory data in a local script file. Its JavaScript renders values, frames and timeline position; it contains no second classification or workload formula. It works by opening `demo/index.html` directly.

See [data dictionary](data-dictionary.md), [model and parameter details](models-and-parameters.md) and [privacy](privacy.md).
