"""Privacy-limited immutable records shared by every runtime adapter."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4
from hashlib import sha256
import json
import math

CATEGORIES = ("IDE", "Browser", "Reader", "Office", "Creative", "Communication", "Entertainment", "Other")
WORK_STATES = ("Focused", "Normal", "Distracted")
STATES = WORK_STATES + ("Rest", "Unknown")
OBSERVATIONS = ("Active", "No-input", "Locked", "Paused", "Missing")
MODES = ("real", "synthetic-demo", "test")


def new_id() -> str:
    return uuid4().hex


@dataclass
class ActivityBucket:
    start: float
    end: float
    duration_s: float = 10.0
    keyboard: int | None = 0
    clicks: int | None = 0
    scroll: float | None = 0.0
    pointer: float | None = 0.0
    idle_s: float | None = 0.0
    app_dwell: dict[str, float] = field(default_factory=dict)
    app_switches: int | None = 0
    coverage: dict[str, float] = field(default_factory=dict)
    observation: str = "Active"
    session_id: str = "default"
    mode: str = "real"
    missing_reason: str | None = None
    interaction_s: float = 0.0
    id: str = ""
    schema: str = "activity-v1"
    timezone: str = "UTC"

    def __post_init__(self) -> None:
        if not self.id:
            identity = [
                self.mode,
                self.session_id,
                self.start,
                self.end,
                self.duration_s,
                self.keyboard,
                self.clicks,
                self.scroll,
                self.pointer,
                self.idle_s,
                self.app_dwell,
                self.app_switches,
                self.observation,
                self.coverage,
            ]
            self.id = sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
        if self.mode not in MODES or self.observation not in OBSERVATIONS:
            raise ValueError("Invalid activity mode or observation")
        if not all(math.isfinite(x) for x in (self.start, self.end, self.duration_s)):
            raise ValueError("Activity times must be finite")
        if not 0 <= self.duration_s <= 120:
            raise ValueError("Bucket duration must be fresh elapsed time, at most 120 seconds")
        if any(k not in CATEGORIES for k in self.app_dwell):
            raise ValueError("Only aggregate application categories may be stored")
        for x in (
            self.keyboard,
            self.clicks,
            self.scroll,
            self.pointer,
            self.app_switches,
            self.idle_s,
            self.interaction_s,
        ):
            if x is not None and (not math.isfinite(x) or x < 0):
                raise ValueError("Aggregate values must be nonnegative and finite")
        if any(not math.isfinite(v) or not 0 <= v <= 1 for v in self.coverage.values()):
            raise ValueError("Signal coverage must be between zero and one")
        if any(not math.isfinite(v) or v < 0 or v > self.duration_s + 1e-6 for v in self.app_dwell.values()):
            raise ValueError("Category dwell duration exceeds bucket")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FeatureWindow:
    start: float
    end: float
    values: dict[str, float]
    coverage: float = 1.0
    valid_duration_s: float = 60.0
    session_id: str = "default"
    mode: str = "real"
    schema: str = "features-v1"
    missing_reason: str | None = None
    id: str = field(default_factory=new_id)
    bucket_ids: list[str] = field(default_factory=list)

    def vector(self) -> list[float]:
        from focuspet.features import FEATURE_NAMES

        return [float(self.values.get(name, 0.0)) for name in FEATURE_NAMES]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Prediction:
    components: dict[str, float]
    state: str = "Unknown"
    source: str = "Generic prior"
    uncertainty: float = 1.0
    coverage: float = 0.0
    model_version: str = "generic-prior-v1"
    reason: list[str] = field(default_factory=list)
    id: str = field(default_factory=new_id)
    feature_id: str | None = None

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError("Invalid work state")
        if any(not math.isfinite(v) or v < 0 for v in self.components.values()):
            raise ValueError("Prediction components must be nonnegative and finite")
        total = sum(self.components.get(k, 0) for k in WORK_STATES)
        self.components = (
            {k: self.components.get(k, 0) / total for k in WORK_STATES}
            if total
            else {k: 1 / 3 for k in WORK_STATES}
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StateSnapshot:
    start: float
    end: float
    observation: str
    state: str
    focus: float | None
    workload: float
    workload_stale: bool
    prediction: Prediction
    feature: FeatureWindow
    session_id: str = "default"
    mode: str = "real"
    parameter_version: str = "defaults-v1"
    rest_active: bool = False
    duration_s: float = 30.0
    id: str = field(default_factory=new_id)

    @property
    def coverage(self) -> float:
        return self.prediction.coverage

    @property
    def source(self) -> str:
        return self.prediction.source

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
