# Personal learning and workload calibration

Focus Pet starts with a versioned, readable GenericPrior. It is a weak rule-based prior, not a population-trained model. The application has no general pretrained classifier, account, cloud inference, or external model download. Synthetic data and artifacts belong exclusively to the `synthetic-demo` mode.

## Feedback and independent evidence

State feedback labels the already-ended 1, 5, or 15 minute target. Training keeps the latest valid revision and excludes withdrawn, unavailable, low-coverage, Rest and Not sure labels. A long annotation can yield multiple feature windows, whose weights sum to one per independent episode. Overlapping annotation targets are connected into a single episode; they cannot inflate the independent count. The original predictions remain historical facts. Shadow and post-activation reviews use only labels already submitted by the review time, taking the latest available revision.

The first candidate requires at least 30 independent episodes on two UTC days and three sessions, with at least five episodes of each work state. These thresholds permit a training attempt; they do not establish reliability. A validation partition must have at least two independent episodes per class.

Whole days and complete episodes are split in time order. Episodes crossing the boundary are removed, with a minimum 300 second purge on both sides. The training scaler is fit only on training rows, using episode weights. No random split of overlapping windows is used. The selected model is not refit on validation data.

## Classifier and lifecycle

The default candidate is L2 Logistic Regression (`C=1`, LBFGS, at most 300 iterations). A 48-tree Random Forest (`max_depth=7`, `min_samples_leaf=2`, one CPU worker) is the only tree comparison. Both use the same training rows and scaler. The validation comparison evaluates pure models and prior fusion with alpha in 0, 0.25, 0.5, 0.75, 1. Macro-F1 chooses the candidate, followed by log loss and a preference for the simpler linear model on ties. Alpha is not a progress indicator or a function of days used.

Temporal validation must improve macro-F1 over both prior and current by at least 0.01 without materially worsening current log loss. Otherwise the prior/current stays enabled. A qualifying candidate enters shadow, with no user-visible effect.

Shadow activation needs new, explicitly identified random-audit feedback whose target starts after both candidate creation and validation completion plus 300 seconds. It needs at least 12 independent episodes over two later days, with at least two per class. A fixed review requires candidate macro-F1 to exceed prior and current by 0.03, log loss no worse than current, and accepted coverage at least 0.6. These are conservative engineering gates, not statistical proof. Each candidate receives one eligible shadow review. Failed candidates are retained as rejected and cannot be repeatedly retested until a favorable subset appears.

An atomic registry pointer update activates a passing candidate. Old artifacts remain available for rollback. Subsequent independent audits can trigger rollback when the current model substantially regresses versus the prior. Invalid or missing active files also fall back through previous versions to the prior. Deleting dependent data invalidates local model artifacts through the storage coordinator.

Automatic candidate attempts require changed explicit feedback and occur at most once per UTC day, including failed and cancelled attempts. A pending shadow candidate pauses automatic replacements so later audits can accumulate; manual training may supersede it. Manual attempts are also cancellable. Fits are capped at 20,000 windows; cancellation is checked before/after the bounded linear fit and every eight trees. The application runs training in a separate terminable process.

## Numeric model storage

The local model format is JSON numeric arrays, never pickle or joblib. It records class order, feature schema/order, scaler, coefficients or bounded tree arrays, alpha, source mode, dependency versions and validation metadata. SHA-256 checksums in both envelope and registry detect accidental corruption. The loader checks shapes, finite values, class ordering, tree acyclicity, resource limits and dependency major/minor compatibility. Checksums are integrity checks, not encryption or cryptographic proof of an author's identity. There is no arbitrary model import UI. Predictions require no training-library import beyond NumPy; compatibility checks use installed package metadata.

## Work Load calibration

Work-state labels never become wants-rest labels. Only separate explicit Yes/No self-reports enter calibration; Not sure and missing responses are excluded. Same-minute reports within a work segment count once. Calibration starts at 20 reports over three days with at least five Yes and five No. Tau can vary only with at least five rest segments having both adjacent before/after self-reports in the fitting partition; otherwise tau remains 12.

The frozen sequence consists of the same disjoint workload integration steps used live, including effective covered seconds, state components, declared rest, missingness and explicit resets. The adapter ignores overlapping display summaries. A retained step's recorded starting load supplies the fixed initial condition when earlier history has expired. Persisted ingestion sequence preserves causal order across wall-clock rollback; a report can reference only a step stored before that report. Older records without sequence use their available chronological ordering.

Each parameter candidate replays the production Workload class. The objective is the mean within each independent work segment, then the mean across segments, of binary cross entropy on `sigmoid((L-100)/20)`, plus `0.05 * sum(log(theta/default)^2)`. The display threshold 100, link width 20 and state growth rates stay fixed. The link is a fitting function, not displayed as a fatigue probability.

Optuna RandomSampler and TPESampler each receive 64 evaluations of the same frozen fitting data. Every trial saves parameters, loss, status, seed, elapsed time and running best loss. The fitting partition selects the optimizer result; held-out later days provide one gate. A qualifying parameter version remains in shadow until at least ten subsequent self-reports over two days, including three Yes and three No already answered by the review time, support lower loss than both current and defaults. Single activation changes are limited to 0.1 in growth and three minutes in recovery. Parameter activation affects only subsequent calculation; historical events retain their original versions. User-requested rollback restores the previous pointer.

These computations do not test reminder interventions that never happened. Sixty-four replay evaluations are not 64 human experiments. Long-term personal performance remains unverified until independent real-use evidence exists.

## Maintainer APIs

`learning.train_records(records, output, mode, profile='Mixed', cancel=None, automatic=False)` and `train_file(path, output, mode)` produce a candidate result. `learning.status_records(records)` explains unmet training conditions. `ModelRegistry(root, mode).active_predictor(profile)` supplies an engine-compatible predictor or None; `evaluate_shadow(records, now, profile)` assesses future audit evidence, `assess_active(...)` monitors drift, and `rollback()` restores prior behavior.

`optimization.dataset_from_events(events, mode)` converts retained events to a version 1 calibration dataset. `calibrate_records(trajectory, feedback, output, mode)` and `calibrate_file(...)` run search. `ParameterRegistry(root, mode).active_parameters()` supplies `a_user`, `tau_user`, and `version`; `evaluate_shadow(trajectory, feedback, now)` reviews subsequent evidence; `rollback()` restores the prior parameters.

Official APIs checked for the pinned versions: [LogisticRegression 1.6.1](https://scikit-learn.org/1.6/modules/generated/sklearn.linear_model.LogisticRegression.html), [RandomForestClassifier 1.6.1](https://scikit-learn.org/1.6/modules/generated/sklearn.ensemble.RandomForestClassifier.html), [Optuna TPESampler 4.2.1](https://optuna.readthedocs.io/en/v4.2.1/reference/samplers/generated/optuna.samplers.TPESampler.html).
