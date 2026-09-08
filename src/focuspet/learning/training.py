from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from focuspet.learning.data import (CLASSES, chronological_split, episode_weights, latest_records,
                                    matrix, status_records, support, targets)
from focuspet.learning.control import TrainingCancelled, check_cancel
from focuspet.models.personal import canonical, estimator_payload

__all__ = ["TrainingCancelled", "check_cancel", "priors", "metrics", "fit_models", "train_records", "train_file"]


def priors(records: list[dict], names: list[str], profile: str = "Mixed") -> np.ndarray:
    from focuspet.domain import FeatureWindow
    from focuspet.models import GenericPrior
    result = []
    prior = GenericPrior(profile)
    for r in records:
        if "prior" in r or "prior_components" in r:
            raw = r.get("prior", r.get("prior_components"))
            p = [raw[c] for c in CLASSES] if isinstance(raw, dict) else raw
        else:
            raw = r.get("values", r.get("features"))
            values = raw if isinstance(raw, dict) else dict(zip(names, raw))
            window = FeatureWindow(id=str(r.get("feature_id", r["episode_id"])), start=r["start"],
                                   end=r["end"], values=values, coverage=r.get("coverage", 1),
                                   valid_duration_s=30, session_id=r["session_id"],
                                   mode=r.get("mode", "real"))
            p = [prior.predict(window).components[c] for c in CLASSES]
        p = np.asarray(p, dtype=float)
        if p.shape != (3,) or not np.isfinite(p).all() or np.any(p < 0) or p.sum() <= 0:
            raise ValueError("Invalid prior components")
        result.append(p / p.sum())
    return np.asarray(result)


def metrics(records: list[dict], probabilities: np.ndarray, rejection_threshold: float = .45) -> dict:
    from sklearn.metrics import confusion_matrix, f1_score, log_loss, precision_recall_fscore_support
    y, w = targets(records), episode_weights(records)
    predicted = np.argmax(probabilities, axis=1)
    precision, recall, fscore, counts = precision_recall_fscore_support(
        y, predicted, labels=[0, 1, 2], sample_weight=w, zero_division=0)
    present = counts > 0
    per_class = {c: {"precision": float(precision[i]) if present[i] else None,
                     "recall": float(recall[i]) if present[i] else None,
                     "f1": float(fscore[i]) if present[i] else None,
                     "support": float(counts[i])} for i, c in enumerate(CLASSES)}
    accepted = probabilities.max(axis=1) >= rejection_threshold
    return {"macro_f1": float(np.mean(fscore[present])) if present.any() else None,
            "log_loss": float(log_loss(y, probabilities, labels=[0, 1, 2], sample_weight=w)),
            "per_class": per_class,
            "confusion_matrix": confusion_matrix(y, predicted, labels=[0, 1, 2], sample_weight=w).tolist(),
            "accepted_coverage": float(np.average(accepted, weights=w)),
            "rejection_coverage": float(np.average(~accepted, weights=w)),
            "accepted_macro_f1": float(f1_score(y[accepted], predicted[accepted], labels=[0, 1, 2],
                 average="macro", sample_weight=w[accepted], zero_division=0)) if accepted.any() else None,
            "support": support(records), "probability_calibration": "Not established; components are uncalibrated"}


def fit_models(records: list[dict], names: list[str], seed: int = 7,
               cancel: threading.Event | None = None) -> tuple[Any, dict[str, Any]]:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    check_cancel(cancel)
    if len(records) > 20_000:
        raise ValueError("Training resource bound: at most 20,000 valid feature windows per attempt")
    x, y, weight = matrix(records, names), targets(records), episode_weights(records)
    if set(y.tolist()) != {0, 1, 2}:
        raise ValueError("Training partition needs all three work-state classes")
    scaler = StandardScaler().fit(x, sample_weight=weight)
    scaled = scaler.transform(x)
    logistic = LogisticRegression(C=1.0, penalty="l2", solver="lbfgs", max_iter=300, random_state=seed)
    logistic.fit(scaled, y, sample_weight=weight)
    check_cancel(cancel)
    forest = RandomForestClassifier(n_estimators=8, max_depth=7, min_samples_leaf=2, n_jobs=1,
                                    random_state=seed, warm_start=True)
    for size in range(8, 49, 8):
        check_cancel(cancel)
        forest.set_params(n_estimators=size)
        forest.fit(scaled, y, sample_weight=weight)
    return scaler, {"logistic": logistic, "random-forest": forest}


def train_records(records: list[dict], output: str | Path, mode: str = "real", profile: str = "Mixed",
                  feature_names: list[str] | None = None, cancel: threading.Event | None = None,
                  automatic: bool = False, now: float | None = None, seed: int = 7) -> dict:
    from focuspet.features import FEATURE_NAMES
    from focuspet.learning.registry import ModelRegistry
    if mode not in {"real", "synthetic-demo", "test"}:
        raise ValueError("Unknown data mode")
    if any(r.get("mode", mode) != mode for r in records):
        raise ValueError("Mixed data modes cannot train a personal model")
    if mode == "real" and any(r.get("source") == "synthetic-oracle" for r in records):
        raise ValueError("Synthetic oracle labels cannot train a real personal model")
    names = list(feature_names or FEATURE_NAMES)
    if names != list(FEATURE_NAMES):
        raise ValueError("Feature schema does not match the installed core")
    started = time.time() if now is None else now
    registry = ModelRegistry(Path(output), mode=mode)
    clean = latest_records(records)
    gate = status_records(records)
    if not gate["eligible"]:
        return {"status": "insufficient-evidence", **gate}
    feedback_signature = sorted({(str(r["episode_id"]), r["label"],
        float(r.get("labeled_at", r["end"])), int(r.get("revision", 0))) for r in clean})
    fingerprint = hashlib.sha256(canonical(feedback_signature)).hexdigest()
    if automatic and not registry.auto_allowed(fingerprint, started):
        return {"status": "not-due", "reason": "Pending shadow candidate, daily limit, or no changed explicit feedback"}
    if automatic:
        registry.mark_auto_attempt(fingerprint, started)
    check_cancel(cancel)
    train, validation = chronological_split(clean)
    validation_support = support(validation)
    if len(train) == 0 or len(validation) == 0 or any(
            validation_support["class_support"][c] < 2 for c in CLASSES):
        return {"status": "insufficient-validation", "training": support(train),
                "validation": validation_support, "reason": "Need later complete days with two episodes per class"}
    scaler, models = fit_models(train, names, seed, cancel)
    x_val = matrix(validation, names)
    p_prior = priors(validation, names, profile)
    baseline = metrics(validation, p_prior)
    current = registry.active_numeric(names)
    current_metrics = metrics(validation, current.combined(x_val, p_prior)) if current else baseline
    comparison = {"generic-prior": baseline, "current": current_metrics}
    candidates: list[dict[str, Any]] = []
    for kind, model in models.items():
        p_personal = model.predict_proba(scaler.transform(x_val))
        comparison[kind] = metrics(validation, p_personal)
        for alpha in (0, .25, .5, .75, 1):
            result = metrics(validation, (1 - alpha) * p_prior + alpha * p_personal)
            candidates.append({"kind": kind, "alpha": alpha, "metrics": result})
    best = max(candidates, key=lambda r: (r["metrics"]["macro_f1"], -r["metrics"]["log_loss"],
                                        r["kind"] == "logistic", -r["alpha"]))
    check_cancel(cancel)
    version = "personal-" + uuid.uuid4().hex[:12]
    metadata = {"created_at": started, "trained_through": max(r["end"] for r in train),
                "validation_through": max(r["end"] for r in validation), "seed": seed,
                "feedback_fingerprint": fingerprint, "episode_ids": sorted({r["episode_id"] for r in clean}),
                "training": support(train), "validation": validation_support, "comparison": comparison,
                "alpha_candidates": candidates, "selected_metrics": best["metrics"], "purge_s": 300,
                "scaler_fit": "training-partition-only", "probability_calibration": "not-established"}
    payload = estimator_payload(models[best["kind"]], scaler, kind=best["kind"], names=names, mode=mode,
                                version=version, alpha=best["alpha"], metadata=metadata)
    improves = (best["alpha"] > 0 and best["metrics"]["macro_f1"] >=
                max(baseline["macro_f1"], current_metrics["macro_f1"]) + .01 and
                best["metrics"]["log_loss"] <= current_metrics["log_loss"] + .02)
    registry.add_candidate(payload, eligible_shadow=improves)
    return {"status": "shadow" if improves else "retained-prior-or-current", "version": version,
            "model_kind": best["kind"], "alpha": best["alpha"], "comparison": comparison,
            "selected": best["metrics"], "training": support(train), "validation": validation_support,
            "activation": "Requires new independent audit feedback after candidate creation",
            "path": str(registry.artifact_path(version))}


def train_file(path: str | Path, output: str | Path, mode: str = "synthetic-demo", **kwargs: Any) -> dict:
    dataset = json.loads(Path(path).read_text())
    if dataset.get("mode") != mode or dataset.get("version") != 1:
        raise ValueError("Dataset mode or version mismatch")
    return train_records(dataset["records"], output, mode, feature_names=dataset["feature_names"], **kwargs)
