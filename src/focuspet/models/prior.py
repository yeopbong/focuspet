from __future__ import annotations
import math
from dataclasses import dataclass
from focuspet.domain.types import FeatureWindow, Prediction, WORK_STATES

PROFILES = ("Coding", "Research / Reading", "Office / Writing", "Creative", "Mixed")


@dataclass(frozen=True)
class PriorConfig:

    version: str = "generic-prior-v1"
    minimum_coverage: float = 0.45
    minimum_observed_seconds: float = 20.0
    stable_evidence: float = 0.30
    steady_evidence: float = 0.35
    conflicting_evidence: float = 0.60
    rejection_component: float = 0.40


class GenericPrior:
    version = "generic-prior-v1"

    def __init__(self, profile: str = "Mixed", config: PriorConfig | None = None):
        if profile not in PROFILES:
            raise ValueError("Unknown initial work profile")
        self.profile = profile
        self.config = config or PriorConfig()
        self.version = self.config.version

    def predict(self, window: FeatureWindow) -> Prediction:
        v = window.values
        scores = [0.05, 0.55, -0.15]
        reasons = []
        if (
            window.coverage < self.config.minimum_coverage
            or window.valid_duration_s < self.config.minimum_observed_seconds
        ):
            return Prediction(
                dict(zip(WORK_STATES, (0.30, 0.40, 0.30))),
                source="Insufficient data",
                coverage=window.coverage,
                reason=["Activity coverage is insufficient."],
                feature_id=window.id,
            )
        active = v.get("active_ratio", 0)
        stability = v.get("category_stability", 0)
        switching = v.get("switch_rate", 0)
        continuity = v.get("continuity_minutes", 0)
        bursts = v.get("burstiness", 0)
        if stability > 0.7 and continuity >= 1:
            scores[0] += self.config.stable_evidence
            reasons.append("The recent application category has been stable.")
        if active >= 0.25 and bursts < 1.5:
            scores[0] += self.config.steady_evidence
            scores[1] += 0.15
            reasons.append("Observed input has a relatively steady rhythm.")
        if active < 0.15:
            scores[1] += 0.2
            reasons.append("Low input can include reading or thinking.")
            if self.profile == "Research / Reading" and stability > 0.7:
                scores[0] += 0.15
        if switching > 8 and stability < 0.5 and abs(v.get("activity_change", 0)) > 0.25:
            scores[2] += self.config.conflicting_evidence
            reasons.append("Application switching and activity rhythm changed together.")
        if bursts > 2.5:
            scores[0] -= 0.2
            reasons.append("Input bursts make interpretation less reliable.")
        if self.profile == "Creative" and v.get("pointer_log_rate", 0) > 2 and stability > 0.6:
            scores[1] += 0.10
        exp = [math.exp(s) for s in scores]
        components = {k: e / sum(exp) for k, e in zip(WORK_STATES, exp)}
        entropy = -sum(p * math.log(p) for p in components.values()) / math.log(3)
        ambiguous = (
            active < 0.08 or (bursts > 3) or max(components.values()) < self.config.rejection_component
        )
        state = "Unknown" if ambiguous else max(components, key=lambda key: components[key])
        if not reasons:
            reasons = ["Recent aggregate activity supports only a weak initial estimate."]
        return Prediction(
            components,
            state=state,
            coverage=window.coverage,
            uncertainty=entropy,
            model_version=self.version,
            reason=reasons,
            feature_id=window.id,
        )
