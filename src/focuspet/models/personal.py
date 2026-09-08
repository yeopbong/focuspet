"""Portable numeric model artifacts. No pickle, joblib, code, or external imports."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np

from focuspet.learning.data import CLASSES


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as f:
        f.write(canonical(value))
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)


def save_artifact(path: Path, payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(canonical(payload)).hexdigest()
    atomic_json(path, {"sha256": digest, "payload": payload})
    return digest


def load_artifact(path: Path, feature_names: list[str] | tuple[str, ...] | None = None,
                  mode: str | None = None) -> dict[str, Any]:
    if path.stat().st_size > 25_000_000:
        raise ValueError("Model artifact exceeds resource limit")
    envelope = json.loads(path.read_text())
    payload = envelope["payload"]
    if hashlib.sha256(canonical(payload)).hexdigest() != envelope["sha256"]:
        raise ValueError("Model checksum mismatch")
    if payload.get("producer") != "focuspet" or payload.get("format") != "numeric-model-v1":
        raise ValueError("Unsupported model artifact")
    if payload.get("classes") != list(CLASSES) or payload.get("schema") != "features-v1":
        raise ValueError("Model class or schema mismatch")
    if feature_names is not None and payload["feature_names"] != list(feature_names):
        raise ValueError("Feature order mismatch")
    if mode is not None and payload["mode"] != mode:
        raise ValueError("Model mode mismatch")
    # Conservative compatibility policy even though numeric inference is independent.
    from importlib.metadata import version
    if payload["dependencies"]["scikit-learn"].split(".")[:2] != version("scikit-learn").split(".")[:2]:
        raise ValueError("Model training dependency incompatible; retrain safely")
    NumericModel(payload).validate()
    return payload


def estimator_payload(estimator: Any, scaler: Any, *, kind: str, names: list[str],
                      mode: str, version: str, alpha: float, metadata: dict[str, Any]) -> dict[str, Any]:
    import sklearn
    result = {"producer": "focuspet", "format": "numeric-model-v1", "schema": "features-v1",
              "feature_names": list(names), "classes": list(CLASSES), "mode": mode,
              "version": version, "kind": kind, "alpha": alpha,
              "dependencies": {"scikit-learn": sklearn.__version__, "numpy": np.__version__},
              "scaler": {"mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist()},
              "metadata": metadata}
    if list(estimator.classes_) != [0, 1, 2]:
        raise ValueError("All three classes are required")
    if kind == "logistic":
        result["coefficients"] = estimator.coef_.tolist()
        result["intercepts"] = estimator.intercept_.tolist()
    elif kind == "random-forest":
        result["trees"] = [{"left": t.tree_.children_left.tolist(), "right": t.tree_.children_right.tolist(),
                            "feature": t.tree_.feature.tolist(), "threshold": t.tree_.threshold.tolist(),
                            "value": t.tree_.value[:, 0, :].tolist()} for t in estimator.estimators_]
    else:
        raise ValueError("Unknown model kind")
    return result


class NumericModel:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        self.mean = np.asarray(payload["scaler"]["mean"], dtype=float)
        self.scale = np.asarray(payload["scaler"]["scale"], dtype=float)

    def validate(self) -> None:
        p = self.payload
        n = len(p["feature_names"])
        if not 1 <= n <= 256 or len(set(p["feature_names"])) != n:
            raise ValueError("Invalid feature schema")
        if self.mean.shape != (n,) or self.scale.shape != (n,) or np.any(self.scale <= 0):
            raise ValueError("Invalid scaler")
        if not np.isfinite(self.mean).all() or not np.isfinite(self.scale).all():
            raise ValueError("Invalid scaler values")
        if p["alpha"] not in (0, .25, .5, .75, 1):
            raise ValueError("Invalid fusion weight")
        if p["kind"] == "logistic":
            coef, intercept = np.asarray(p["coefficients"]), np.asarray(p["intercepts"])
            if coef.shape != (3, n) or intercept.shape != (3,) or not np.isfinite(coef).all() \
                    or not np.isfinite(intercept).all():
                raise ValueError("Invalid linear coefficients")
        elif p["kind"] == "random-forest":
            if not 1 <= len(p["trees"]) <= 128:
                raise ValueError("Invalid forest size")
            for t in p["trees"]:
                count = len(t["left"])
                if not 1 <= count <= 4095 or any(len(t[k]) != count for k in t):
                    raise ValueError("Invalid tree dimensions")
                for i, (left, right, feature) in enumerate(zip(t["left"], t["right"], t["feature"])):
                    if left == right == -1:
                        continue
                    if not (i < left < count and i < right < count and 0 <= feature < n):
                        raise ValueError("Invalid or cyclic tree")
                values = np.asarray(t["value"])
                if values.shape != (count, 3) or not np.isfinite(values).all() or (values < 0).any() \
                        or (values.sum(axis=1) <= 0).any() or not np.isfinite(t["threshold"]).all():
                    raise ValueError("Invalid tree values")
        else:
            raise ValueError("Unknown numeric model")

    def predict_proba(self, values: np.ndarray) -> np.ndarray:
        x = (np.asarray(values, dtype=float) - self.mean) / self.scale
        if x.ndim != 2 or x.shape[1] != len(self.mean) or not np.isfinite(x).all():
            raise ValueError("Invalid inference input")
        p = self.payload
        if p["kind"] == "logistic":
            logits = x @ np.asarray(p["coefficients"]).T + np.asarray(p["intercepts"])
            logits -= logits.max(axis=1, keepdims=True)
            result = np.exp(logits)
            return result / result.sum(axis=1, keepdims=True)
        result = np.zeros((len(x), 3))
        # sklearn trees use float32 input at prediction, including threshold ties.
        x = x.astype(np.float32)
        for t in p["trees"]:
            for i, row in enumerate(x):
                node = 0
                while t["left"][node] != -1:
                    node = t["left"][node] if row[t["feature"][node]] <= t["threshold"][node] \
                        else t["right"][node]
                leaf = np.asarray(t["value"][node])
                result[i] += leaf / leaf.sum()
        return result / len(p["trees"])

    def combined(self, values: np.ndarray, prior: np.ndarray) -> np.ndarray:
        alpha = self.payload["alpha"]
        return (1 - alpha) * prior + alpha * self.predict_proba(values)


class PersonalPredictor:
    def __init__(self, payload: dict[str, Any], profile: str = "Mixed"):
        from focuspet.models import GenericPrior
        self.model = NumericModel(payload)
        self.prior = GenericPrior(profile)
        self.version = payload["version"]

    def predict(self, window: Any) -> Any:
        from focuspet.domain import Prediction
        prior = self.prior.predict(window)
        if window.mode != self.model.payload["mode"]:
            return prior
        if window.coverage < .6 or window.missing_reason:
            return prior
        names = self.model.payload["feature_names"]
        x = np.asarray([[window.values[name] for name in names]])
        p = self.model.combined(x, np.asarray([[prior.components[c] for c in CLASSES]]))[0]
        uncertainty = float(-(p * np.log(np.maximum(p, 1e-12))).sum() / np.log(3))
        state = CLASSES[int(p.argmax())] if p.max() >= .45 else "Unknown"
        return Prediction(components=dict(zip(CLASSES, map(float, p))), state=state,
                          source="personal-model", uncertainty=uncertainty, coverage=window.coverage,
                          model_version=self.version, reason=prior.reason + ["Locally learned from explicit feedback"],
                          feature_id=window.id)
