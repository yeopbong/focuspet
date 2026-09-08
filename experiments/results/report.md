# Focus Pet simulation results

These are actual seeded simulations, not real-user validation.
Completed 2026-09-08T12:25:06.333305+00:00. Seeds: 7, 17, 27, 37, 47.
Runtime: Python 3.11.9, numpy 2.2.4, scipy 1.17.1, scikit-learn 1.6.1, optuna 4.2.1.
Configuration, runtime and its configuration hash are saved in [summary.json](summary.json).

| Independent labels | Generic prior | Logistic regression | Random forest | Validation-selected fusion |
|---:|---:|---:|---:|---:|
| 30 | 0.279 ± 0.035 | 0.376 ± 0.081 | 0.343 ± 0.058 | 0.356 ± 0.078 |
| 60 | 0.279 ± 0.035 | 0.390 ± 0.075 | 0.361 ± 0.036 | 0.395 ± 0.065 |
| 90 | 0.279 ± 0.035 | 0.426 ± 0.078 | 0.409 ± 0.058 | 0.427 ± 0.052 |

Scores are equal-episode weighted macro-F1 (mean ± sample SD across seeds).
Per-class precision/recall, confusion matrices, accepted/rejected coverage, exact splits and all trial losses are in each seed directory.

| Seed / detailed results | Evaluation episodes | Focused | Normal | Distracted |
|---|---:|---:|---:|---:|
| [7](seed-7/results.json) | 48 | 18 | 12 | 18 |
| [17](seed-17/results.json) | 48 | 20 | 16 | 12 |
| [27](seed-27/results.json) | 48 | 20 | 19 | 9 |
| [37](seed-37/results.json) | 48 | 16 | 20 | 12 |
| [47](seed-47/results.json) | 48 | 21 | 13 | 14 |

Rejection behavior at 90 labels (means across seeds):

| Method | Accepted coverage | Rejected coverage | Accepted-only macro-F1 |
|---|---:|---:|---:|
| generic-prior | 0.277 | 0.723 | 0.172 |
| logistic | 0.879 | 0.121 | 0.434 |
| random-forest | 0.754 | 0.246 | 0.426 |
| fusion | 0.675 | 0.325 | 0.432 |

Accepted-only scores use different subsets and should be read alongside full-set scores and coverage.

| Query budget | Random | Uncertainty + diversity (25% audit) |
|---:|---:|---:|
| 30 | 0.376 | 0.404 |
| 60 | 0.390 | 0.424 |
| 90 | 0.426 | 0.425 |

Both strategies use the production query selector, two overlays a day four hours apart, and the same independent evaluation set.

| Calibration | Fit objective | Later-days objective | a mean (SD) | tau mean (SD) |
|---|---:|---:|---:|---:|
| default | 0.5105 | 0.6799 | 1.000 (0.000) | 12.00 (0.00) |
| random | 0.3407 | 0.4135 | 1.216 (0.053) | 17.94 (1.53) |
| tpe | 0.3355 | 0.4155 | 1.231 (0.082) | 17.01 (1.19) |

Each optimizer used 64 objective evaluations per seed. Parameters are selected using fitting loss; later days are not used for search.
Parameter variation includes different fictional user styles and observation noise, not measured human stability.

## Limits

- No real-user data or effectiveness validation
- Model selection uses validation only; a fixed test set is scored for predeclared comparisons
- A simulator is not an identifiable human fatigue model
- No probability calibration claim; independent real audit labels are unavailable
- Search evaluations are frozen-history computations, not real intervention experiments
- Improvement is neither guaranteed nor monotonic with label budget. No experiment activates a real personal model.
- Future shadow activation and regression rollback are tested separately; these historical simulation scores are not that evidence.
