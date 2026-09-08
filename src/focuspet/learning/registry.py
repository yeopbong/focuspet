from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from focuspet.learning.data import CLASSES, latest_records, matrix, support, utc_day
from focuspet.models.personal import NumericModel, PersonalPredictor, atomic_json, load_artifact, save_artifact


class ModelRegistry:
    def __init__(self, root: Path, mode: str = "real"):
        self.root = Path(root)
        self.mode = mode
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "model-registry.json"
        try:
            self.state = json.loads(self.path.read_text())
            if not isinstance(self.state, dict) or self.state.get("format") != "registry-v1" \
                    or not isinstance(self.state.get("versions"), dict) \
                    or not isinstance(self.state.get("previous"), list):
                raise ValueError("Registry schema mismatch")
        except (OSError, ValueError, TypeError):
            self.state = {"format": "registry-v1", "mode": mode, "active": None, "previous": [],
                          "candidate": None, "versions": {}, "last_auto_day": None,
                          "last_auto_fingerprint": None}
        if self.state.get("mode") != mode:
            raise ValueError("Existing model directory belongs to a different data mode")

    def _save(self) -> None:
        atomic_json(self.path, self.state)

    def artifact_path(self, version: str) -> Path:
        if not version.startswith("personal-") or not version[9:].isalnum():
            raise ValueError("Invalid local model version")
        return self.root / f"{version}.json"

    def status(self) -> dict[str, Any]:
        return dict(self.state)

    def auto_allowed(self, fingerprint: str, now: float) -> bool:
        return (not self.state.get("candidate") and self.state.get("last_auto_day") != utc_day(now) and
                self.state.get("last_auto_fingerprint") != fingerprint)

    def mark_auto_attempt(self, fingerprint: str, now: float) -> None:
        self.state.update(last_auto_day=utc_day(now), last_auto_fingerprint=fingerprint)
        self._save()

    def add_candidate(self, payload: dict[str, Any], eligible_shadow: bool) -> None:
        if payload["mode"] != self.mode:
            raise ValueError("Model mode mismatch")
        version = payload["version"]
        digest = save_artifact(self.artifact_path(version), payload)
        meta = payload["metadata"]
        self.state["versions"][version] = {"status": "shadow" if eligible_shadow else "rejected-validation",
            "sha256": digest, "created_at": meta["created_at"], "metadata": meta,
            "alpha": payload["alpha"], "kind": payload["kind"]}
        if eligible_shadow:
            old_candidate = self.state.get("candidate")
            if old_candidate and old_candidate in self.state["versions"]:
                self.state["versions"][old_candidate]["status"] = "superseded"
            self.state["candidate"] = version
        self._save()

    def _load(self, version: str, names: list[str] | None = None) -> dict:
        if version not in self.state["versions"]:
            raise ValueError("Unregistered model")
        payload = load_artifact(self.artifact_path(version), names, self.mode)
        import hashlib
        from focuspet.models.personal import canonical
        if hashlib.sha256(canonical(payload)).hexdigest() != self.state["versions"][version]["sha256"]:
            raise ValueError("Registry checksum mismatch")
        return payload

    def active_numeric(self, names: list[str] | None = None) -> NumericModel | None:
        version = self.state.get("active")
        while version:
            try:
                return NumericModel(self._load(version, names))
            except (OSError, ValueError, KeyError, TypeError, IndexError):
                self.state["versions"].setdefault(version, {})["status"] = "invalid-fallback"
                previous = self.state.get("previous", [])
                self.state["active"] = previous.pop() if previous else None
                self._save()
                version = self.state["active"]
        return None

    def active_predictor(self, profile: str = "Mixed") -> PersonalPredictor | None:
        from focuspet.features import FEATURE_NAMES
        model = self.active_numeric(list(FEATURE_NAMES))
        return PersonalPredictor(model.payload, profile) if model else None

    def rollback(self, now: float | None = None, reason: str = "user-request") -> dict:
        old = self.state.get("active")
        previous = self.state.get("previous", [])
        self.state["active"] = previous.pop() if previous else None
        if old:
            self.state["versions"][old].update(status="rolled-back", rollback_reason=reason,
                                               rollback_at=time.time() if now is None else now)
        self._save()
        return {"status": "rolled-back", "active": self.state["active"], "previous_active": old}

    def invalidate_dependencies(self, episode_ids: set[str] | None = None) -> None:
        for version, details in self.state["versions"].items():
            used = set(details["metadata"].get("episode_ids", []))
            if episode_ids is None or used & episode_ids:
                details["status"] = "invalid-deleted-data"
                self.artifact_path(version).unlink(missing_ok=True)
                if self.state.get("active") == version:
                    self.state["active"] = None
                if self.state.get("candidate") == version:
                    self.state["candidate"] = None
                self.state["previous"] = [v for v in self.state["previous"] if v != version]
        self._save()

    def evaluate_shadow(self, records: list[dict], now: float | None = None,
                        profile: str = "Mixed") -> dict:
        from focuspet.learning.training import metrics, priors
        now = time.time() if now is None else now
        version = self.state.get("candidate")
        if not version:
            return {"status": "no-shadow-candidate"}
        try:
            candidate = NumericModel(self._load(version))
        except (OSError, ValueError, KeyError, TypeError):
            self.state["versions"][version]["status"] = "invalid-fallback"
            self.state["candidate"] = None
            self._save()
            return {"status": "invalid-candidate"}
        meta = candidate.payload["metadata"]
        used = set(meta["episode_ids"])
        cutoff = max(meta["created_at"], meta["validation_through"]) + 300
        available = [r for r in records if float(r.get("labeled_at", r["end"])) <= now
                     and float(r["end"]) <= now]
        evidence = [r for r in latest_records(available) if r.get("source") == "audit"
                    and r.get("mode", self.mode) == self.mode and r["episode_id"] not in used
                    and float(r["start"]) >= cutoff and float(r.get("labeled_at", r["end"])) <= now
                    and float(r["end"]) <= now]
        s = support(evidence)
        if s["episodes"] < 12 or s["days"] < 2 or any(s["class_support"][c] < 2 for c in CLASSES):
            return {"status": "collecting-shadow-evidence", "support": s,
                    "required": "12 subsequent audit episodes across 2 days, at least 2 per class"}
        names = candidate.payload["feature_names"]
        x, p_prior = matrix(evidence, names), priors(evidence, names, profile)
        old = self.active_numeric(names)
        base = metrics(evidence, p_prior)
        current = metrics(evidence, old.combined(x, p_prior)) if old else base
        proposed = metrics(evidence, candidate.combined(x, p_prior))
        passed = (proposed["macro_f1"] >= max(base["macro_f1"], current["macro_f1"]) + .03
                  and proposed["log_loss"] <= current["log_loss"] and proposed["accepted_coverage"] >= .6)
        assessment = {"support": s, "prior": base, "current": current, "candidate": proposed,
                      "at": now, "episode_ids": sorted({r["episode_id"] for r in evidence})}
        self.state["versions"][version]["shadow_assessment"] = assessment
        self.state["candidate"] = None
        if passed:
            if self.state.get("active"):
                self.state["previous"].append(self.state["active"])
            self.state["active"] = version
            self.state["versions"][version].update(status="active", active_from=now)
        else:
            self.state["versions"][version]["status"] = "rejected-shadow"
        self._save()
        return {"status": "activated" if passed else "retained-prior-or-current", "assessment": assessment}

    def assess_active(self, records: list[dict], now: float | None = None, profile: str = "Mixed") -> dict:
        from focuspet.learning.training import metrics, priors
        active = self.active_numeric()
        if active is None:
            return {"status": "using-prior"}
        now = time.time() if now is None else now
        details = self.state["versions"][self.state["active"]]
        cutoff = max(details.get("active_from", now), details.get("last_monitor_at", 0)) + 300
        available = [r for r in records if float(r.get("labeled_at", r["end"])) <= now
                     and float(r["end"]) <= now]
        evidence = [r for r in latest_records(available) if r.get("source") == "audit"
                    and r.get("mode", self.mode) == self.mode and r["start"] >= cutoff and r["end"] <= now]
        s = support(evidence)
        if s["episodes"] < 12 or s["days"] < 2 or any(s["class_support"][c] < 2 for c in CLASSES):
            return {"status": "collecting-monitor-evidence", "support": s}
        names = active.payload["feature_names"]
        p_prior, x = priors(evidence, names, profile), matrix(evidence, names)
        baseline, current = metrics(evidence, p_prior), metrics(evidence, active.combined(x, p_prior))
        details["last_monitor_at"] = now
        details["monitor"] = {"prior": baseline, "active": current, "at": now}
        self._save()
        if current["macro_f1"] < baseline["macro_f1"] - .05 or current["log_loss"] > baseline["log_loss"] + .2:
            return self.rollback(now, reason="subsequent-audit-regression")
        return {"status": "retained-active", "assessment": details["monitor"]}
