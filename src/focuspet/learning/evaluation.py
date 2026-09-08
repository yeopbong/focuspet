from __future__ import annotations

import hashlib
import json
import platform
import time
from collections import defaultdict
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from focuspet.learning.data import (CLASSES, chronological_split, episode_weights,
                                    matrix, support, targets, utc_day)
from focuspet.learning.synthetic import load_dataset, work_dataset
from focuspet.learning.training import fit_models, metrics, priors
from focuspet.models.personal import atomic_json, canonical
from focuspet.optimization.calibration import (DEFAULTS, calibration_status, clean_feedback,
                                               objective_loss, search, split_feedback)


def _episode_groups(records):
    groups = defaultdict(list)
    for r in records:
        groups[r["episode_id"]].append(r)
    return list(groups.values())


def _class_comparison(train, validation, test, names, seed):
    if set(targets(train).tolist()) != {0, 1, 2}:
        return {"status": "missing-training-class", "support": support(train)}
    scaler, models = fit_models(train, names, seed)
    x_val, x_test = matrix(validation, names), matrix(test, names)
    p_val, p_test = priors(validation, names), priors(test, names)
    result = {"training_support": support(train), "validation_support": support(validation),
              "test_support": support(test), "generic-prior": metrics(test, p_test)}
    choices = []
    for kind, model in models.items():
        personal_val = model.predict_proba(scaler.transform(x_val))
        personal_test = model.predict_proba(scaler.transform(x_test))
        result[kind] = metrics(test, personal_test)
        for alpha in (0, .25, .5, .75, 1):
            validation_metrics = metrics(validation, (1 - alpha) * p_val + alpha * personal_val)
            choices.append((validation_metrics["macro_f1"], -validation_metrics["log_loss"],
                            kind == "logistic", -alpha, kind, alpha, personal_test))
    best = max(choices, key=lambda c: c[:4])
    result["fusion"] = {"selected_kind": best[4], "alpha": best[5],
                        **metrics(test, (1 - best[5]) * p_test + best[5] * best[6])}
    return result


def _query_stream(pool, names, seed, strategy):
    from focuspet.domain import FeatureWindow, Prediction
    from focuspet.policy import ActiveQuerySelector
    selector = ActiveQuerySelector(seed=seed, audit_fraction=1 if strategy == "random" else .25)
    groups = _episode_groups(pool)
    opportunities = defaultdict(list)
    for group in groups:
        r = group[-1]
        day = utc_day(r["end"])
        opportunities[(day, int(r["start"] % 86400 // 14400))].append(group)
    selected, query_log, snapshots = [], [], {}
    estimator = scaler = None
    for _, candidates in sorted(opportunities.items()):
        available = []
        for group in candidates:
            r = group[-1]
            values = r["values"]
            f = FeatureWindow(id=r["feature_id"], start=r["start"], end=r["end"], values=values,
                              coverage=r["coverage"], valid_duration_s=30, session_id=r["session_id"],
                              mode="synthetic-demo")
            p = np.asarray([r["prior"][c] for c in CLASSES])
            if estimator is not None and scaler is not None:
                p = estimator.predict_proba(scaler.transform(matrix([r], names)))[0]
            uncertainty = float(-(p * np.log(np.maximum(p, 1e-12))).sum() / np.log(3))
            prediction = Prediction(components=dict(zip(CLASSES, map(float, p))), state=CLASSES[int(p.argmax())],
                                    source="synthetic-experiment", uncertainty=uncertainty, coverage=f.coverage)
            available.append((f, prediction))
        at = max(f.end for f, _ in available) + 30
        query = selector.select(available, now=at)
        if query is None:
            continue
        group = next(group for group in candidates if group[-1]["feature_id"] == query.feature_id)
        selected.extend(group)
        query_log.append({"episode_id": group[0]["episode_id"], "at": at, "mechanism": query.mechanism,
                          "target_end": query.end, "counted_labels": 1})
        n = len(query_log)
        if set(targets(selected).tolist()) == {0, 1, 2}:
            scaler = StandardScaler().fit(matrix(selected, names), sample_weight=episode_weights(selected))
            estimator = LogisticRegression(C=1, max_iter=300, random_state=seed)
            estimator.fit(scaler.transform(matrix(selected, names)), targets(selected),
                          sample_weight=episode_weights(selected))
        if n in (30, 60, 90):
            snapshots[n] = list(selected)
    return snapshots, query_log


def run_seed(seed: int, output: Path, budgets=(30, 60, 90), search_budget: int = 64) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    dataset = work_dataset(seed)
    names, records = dataset["feature_names"], dataset["records"]
    development, test = chronological_split(records, fraction=52/60)
    pool, validation = chronological_split(development, fraction=45/52)
    atomic_json(output / "work-data.json", dataset)
    split = {"training_pool": sorted({r["episode_id"] for r in pool}),
             "validation": sorted({r["episode_id"] for r in validation}),
             "test": sorted({r["episode_id"] for r in test}), "purge_s": 300}
    atomic_json(output / "split.json", split)
    random_snapshots, random_log = _query_stream(pool, names, seed, "random")
    active_snapshots, active_log = _query_stream(pool, names, seed, "uncertainty-diversity")
    classification, active_comparison = {}, {}
    for budget in budgets:
        if budget not in random_snapshots or budget not in active_snapshots:
            raise ValueError("Simulation query budget did not produce required independent labels")
        baseline = _class_comparison(random_snapshots[budget], validation, test, names, seed)
        classification[str(budget)] = baseline
        other = _class_comparison(active_snapshots[budget], validation, test, names, seed)
        active_comparison[str(budget)] = {"random": baseline.get("logistic"),
            "uncertainty-diversity": other.get("logistic"),
            "random_support": support(random_snapshots[budget]),
            "active_support": support(active_snapshots[budget]),
            "label_budget": budget, "overlay_budget": budget,
            "queries_per_day": 2, "minimum_spacing_s": 14400}
    atomic_json(output / "query-log.json", {"random": random_log, "uncertainty-diversity": active_log})
    calibration = load_dataset(seed)
    atomic_json(output / "load-data.json", calibration)
    feedback = clean_feedback(calibration["feedback"])
    fitting, heldout = split_feedback(feedback)
    trajectory = calibration["trajectory"]
    calibration_results = {"default": {"parameters": DEFAULTS,
        "fit_loss": objective_loss(trajectory, fitting, DEFAULTS),
        "validation_loss": objective_loss(trajectory, heldout, DEFAULTS)},
        "fit_support": calibration_status(fitting), "validation_support": calibration_status(heldout)}
    for sampler in ("random", "tpe"):
        result = search(trajectory, fitting, sampler, seed, search_budget,
                        learn_tau=calibration_status(fitting)["learn_tau"])
        result["validation_loss"] = objective_loss(trajectory, heldout, result["parameters"])
        calibration_results[sampler] = result
    result = {"seed": seed, "mode": "synthetic-demo", "dataset_sha256": hashlib.sha256(canonical(dataset)).hexdigest(),
              "split": split, "classification": classification, "active_learning": active_comparison,
              "calibration": calibration_results}
    atomic_json(output / "results.json", result)
    return result


def evaluate_config(config: str | Path | dict) -> dict:
    settings = json.loads(Path(config).read_text()) if not isinstance(config, dict) else dict(config)
    seeds = settings.get("seeds", [7, 17, 27, 37, 47])
    output = Path(settings.get("output", "experiments/results"))
    budgets = tuple(settings.get("label_budgets", [30, 60, 90]))
    start = time.perf_counter()
    results = [run_seed(int(seed), output / f"seed-{seed}", budgets,
                        int(settings.get("search_budget", 64))) for seed in seeds]
    summary: dict[str, Any] = {"mode": "synthetic-demo", "seeds": seeds, "label_budgets": list(budgets),
               "completed_at_utc": datetime.now(timezone.utc).isoformat(),
               "configuration": settings, "configuration_sha256": hashlib.sha256(canonical(settings)).hexdigest(),
               "runtime": {"python": platform.python_version(), "platform": platform.system(),
                           "machine": platform.machine(), "dependencies": {
                               package: version(package) for package in ("numpy", "scipy", "scikit-learn", "optuna")}},
               "elapsed_s": time.perf_counter() - start, "classification": {}, "active_learning": {},
               "calibration": {}, "limitations": ["No real-user data or effectiveness validation",
               "Model selection uses validation only; a fixed test set is scored for predeclared comparisons",
               "A simulator is not an identifiable human fatigue model",
               "No probability calibration claim; independent real audit labels are unavailable",
               "Search evaluations are frozen-history computations, not real intervention experiments"]}
    for budget in budgets:
        summary["classification"][str(budget)] = {}
        for method in ("generic-prior", "logistic", "random-forest", "fusion"):
            scores = [r["classification"][str(budget)][method]["macro_f1"] for r in results]
            summary["classification"][str(budget)][method] = {
                "macro_f1_mean": float(np.mean(scores)), "macro_f1_std": float(np.std(scores, ddof=1)) if len(scores)>1 else 0}
        summary["active_learning"][str(budget)] = {}
        for strategy in ("random", "uncertainty-diversity"):
            scores = [r["active_learning"][str(budget)][strategy]["macro_f1"] for r in results]
            summary["active_learning"][str(budget)][strategy] = float(np.mean(scores))
    for method in ("default", "random", "tpe"):
        summary["calibration"][method] = {}
        for key in ("fit_loss", "validation_loss"):
            values = [r["calibration"][method][key] for r in results]
            summary["calibration"][method][key] = {"mean": float(np.mean(values)), "std": float(np.std(values))}
        summary["calibration"][method]["parameter_stability"] = {k: {
            "mean": float(np.mean([r["calibration"][method]["parameters"][k] for r in results])),
            "std": float(np.std([r["calibration"][method]["parameters"][k] for r in results]))} for k in DEFAULTS}
    atomic_json(output / "summary.json", summary)
    lines = ["# Focus Pet simulation results", "", "These are actual seeded simulations, not real-user validation.",
             f"Completed {summary['completed_at_utc']}. Seeds: {', '.join(map(str, seeds))}.",
             f"Runtime: Python {summary['runtime']['python']}, " + ", ".join(
                 f"{package} {value}" for package, value in summary['runtime']['dependencies'].items()) + ".",
             "Configuration, runtime and its configuration hash are saved in [summary.json](summary.json).",
             "", "| Independent labels | Generic prior | Logistic regression | Random forest | Validation-selected fusion |",
             "|---:|---:|---:|---:|---:|"]
    for budget, methods in summary["classification"].items():
        lines.append("| " + budget + " | " + " | ".join(f"{methods[m]['macro_f1_mean']:.3f} ± {methods[m]['macro_f1_std']:.3f}"
                     for m in ("generic-prior", "logistic", "random-forest", "fusion")) + " |")
    lines += ["", "Scores are equal-episode weighted macro-F1 (mean ± sample SD across seeds).",
              "Per-class precision/recall, confusion matrices, accepted/rejected coverage, exact splits and all trial losses are in each seed directory.",
              "", "| Seed / detailed results | Evaluation episodes | Focused | Normal | Distracted |",
              "|---|---:|---:|---:|---:|"]
    largest_budget = str(max(budgets))
    for result in results:
        test_support = result["classification"][largest_budget]["test_support"]
        lines.append(f"| [{result['seed']}](seed-{result['seed']}/results.json) | {test_support['episodes']} | " +
                     " | ".join(str(test_support["class_support"][c]) for c in CLASSES) + " |")
    lines += ["", f"Rejection behavior at {largest_budget} labels (means across seeds):", "",
              "| Method | Accepted coverage | Rejected coverage | Accepted-only macro-F1 |",
              "|---|---:|---:|---:|"]
    for method in ("generic-prior", "logistic", "random-forest", "fusion"):
        observations = [result["classification"][largest_budget][method] for result in results]
        accepted_scores = [row["accepted_macro_f1"] for row in observations if row["accepted_macro_f1"] is not None]
        accepted_score = f"{np.mean(accepted_scores):.3f}" if accepted_scores else "NA"
        lines.append(f"| {method} | {np.mean([row['accepted_coverage'] for row in observations]):.3f} | "
                     f"{np.mean([row['rejection_coverage'] for row in observations]):.3f} | {accepted_score} |")
    lines += ["", "Accepted-only scores use different subsets and should be read alongside full-set scores and coverage.",
              "", "| Query budget | Random | Uncertainty + diversity (25% audit) |", "|---:|---:|---:|"]
    for budget, methods in summary["active_learning"].items():
        lines.append(f"| {budget} | {methods['random']:.3f} | {methods['uncertainty-diversity']:.3f} |")
    lines += ["", "Both strategies use the production query selector, two overlays a day four hours apart, and the same independent evaluation set.",
              "", "| Calibration | Fit objective | Later-days objective | a mean (SD) | tau mean (SD) |", "|---|---:|---:|---:|---:|"]
    for method, calibration_values in summary["calibration"].items():
        a = calibration_values["parameter_stability"]["a_user"]
        tau = calibration_values["parameter_stability"]["tau_user"]
        lines.append(f"| {method} | {calibration_values['fit_loss']['mean']:.4f} | {calibration_values['validation_loss']['mean']:.4f} | "
                     f"{a['mean']:.3f} ({a['std']:.3f}) | {tau['mean']:.2f} ({tau['std']:.2f}) |")
    lines += ["", f"Each optimizer used {settings.get('search_budget', 64)} objective evaluations per seed. Parameters are selected using fitting loss; later days are not used for search.",
              "Parameter variation includes different fictional user styles and observation noise, not measured human stability.",
              "", "## Limits", "", *["- " + limit for limit in summary["limitations"]],
              "- Improvement is neither guaranteed nor monotonic with label budget. No experiment activates a real personal model.",
              "- Future shadow activation and regression rollback are tested separately; these historical simulation scores are not that evidence."]
    (output / "report.md").write_text("\n".join(lines) + "\n")
    return summary
