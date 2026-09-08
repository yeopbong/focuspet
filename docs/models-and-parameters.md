# Personal learning and workload calibration

Focus Pet starts with a versioned rule prior. State corrections train local classifiers; separate wants-rest feedback calibrates workload parameters. Synthetic artifacts stay in `synthetic-demo` mode.

## State feedback and training

A correction labels an ended 1, 5, or 15 minute interval. Training uses the latest valid revision, excluding withdrawn, unavailable, low-coverage, Rest and Not sure labels. Overlapping targets form one episode; their feature windows share one total weight. Original predictions are retained.

The first candidate requires 30 independent episodes across two UTC days and three sessions, including five episodes per work state. Validation needs two episodes per class. Complete days and episodes are split chronologically with a 300-second purge; scalers use training rows only.

Candidates are L2 Logistic Regression (`C=1`, LBFGS, 300 iterations maximum) and a 48-tree Random Forest (`max_depth=7`, `min_samples_leaf=2`). Validation compares each model and its fusion with the prior at alpha 0, 0.25, 0.5, 0.75 and 1. Macro-F1 selects the candidate, with log loss and the simpler linear model as tie-breaks.

A candidate enters shadow when validation macro-F1 improves on both prior and current by at least 0.01 without materially worsening current log loss. Shadow activation requires new random-audit feedback beginning after candidate creation and validation completion plus 300 seconds: 12 episodes over two later days, with two per class. Candidate macro-F1 must exceed prior and current by 0.03, log loss must be no worse than current, and accepted coverage must reach 0.6. Each candidate receives one eligible review.

Passing activates an atomic registry pointer. Rejected candidates and old versions remain available. Later independent audits can trigger rollback; missing or damaged artifacts fall back through valid previous versions to the prior. Deleting source data invalidates dependent models.

Automatic attempts require changed feedback and occur at most once per UTC day. A pending shadow candidate pauses automatic replacements. Manual training and cancellation are available. Fits run in a separate process and are capped at 20,000 windows.

## Numeric model storage

Model JSON records class and feature order, scaler, coefficients or tree arrays, alpha, source mode, dependency versions and validation metadata. Loaders check checksums, shapes, finite values, tree structure, resource limits and dependency compatibility. Prediction uses numeric arrays without pickle or joblib loading.

## Work Load calibration

Only explicit Yes/No wants-rest reports enter calibration. Same-minute reports within a work segment count once. Training requires 20 reports over three days, including five Yes and five No. Recovery parameter tau can vary only when the fitting partition has five rest segments with before/after feedback; otherwise it stays at 12.

Calibration replays recorded disjoint workload steps in ingestion order using the production Workload class. Retained starting load supplies the initial condition. The objective averages binary cross entropy on `sigmoid((L-100)/20)` within work segments and then across segments, adding `0.05 * sum(log(theta/default)^2)`. The reference line 100, link width 20 and state growth rates remain fixed.

Optuna RandomSampler and TPESampler each receive 64 evaluations of the same fitting history. Every trial records parameters, loss, status, seed, elapsed time and running best. Fitting loss selects the optimizer result; held-out later days provide one gate.

Parameter activation then requires ten subsequent reports over two days, including three Yes and three No, supporting lower loss than both current and defaults. One activation changes growth by at most 0.1 and recovery by at most three minutes. Activation affects future calculation; historical values keep their original versions. Rollback restores the previous pointer.

These defaults and thresholds are engineering choices. Simulation results and sufficient feedback counts alone do not establish real personal accuracy or the effectiveness of rest reminders.

## Maintainer APIs

- `learning.train_records(records, output, mode, profile='Mixed', cancel=None, automatic=False)` and `train_file(path, output, mode)` train a candidate. `status_records(records)` lists unmet conditions.
- `ModelRegistry(root, mode)` provides `active_predictor(profile)`, `evaluate_shadow(records, now, profile)`, `assess_active(...)` and `rollback()`.
- `optimization.dataset_from_events(events, mode)` creates a calibration dataset; `calibrate_records(trajectory, feedback, output, mode)` and `calibrate_file(...)` run search.
- `ParameterRegistry(root, mode)` provides `active_parameters()`, `evaluate_shadow(trajectory, feedback, now)` and `rollback()`.
