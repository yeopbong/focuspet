"""Explicit engineering rhythm index; no physiological claims."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping
import math

DEFAULT_A = 1.0
DEFAULT_TAU_MINUTES = 12.0
REMINDER_REFERENCE = 100.0
RECOVERY_BUFFER = -10.0
GROWTH_RATES = {"Focused": 5 / 3, "Normal": 1.0, "Distracted": 0.6}


@dataclass(frozen=True)
class WorkloadParameters:
    a_user: float = DEFAULT_A
    tau_user: float = DEFAULT_TAU_MINUTES
    version: str = "defaults-v1"

    def __post_init__(self) -> None:
        if not 0.7 <= self.a_user <= 1.3 or not 8 <= self.tau_user <= 25:
            raise ValueError("Workload parameters outside documented bounds")


class Workload:
    def __init__(self, value: float = 0, parameters: WorkloadParameters | None = None):
        if not math.isfinite(value):
            raise ValueError("Workload must be finite")
        self.value = max(RECOVERY_BUFFER, float(value))
        self.parameters = parameters or WorkloadParameters()
        self.stale = False

    def advance(
        self,
        duration_s: float,
        components: Mapping[str, float] | None = None,
        rest: bool = False,
        valid: bool = True,
    ) -> float:
        if not math.isfinite(duration_s) or duration_s < 0:
            raise ValueError("Elapsed duration must be nonnegative and finite")
        if not valid:
            self.stale = True
            return self.value
        self.stale = False
        minutes = duration_s / 60
        if rest:
            self.value = RECOVERY_BUFFER + (self.value - RECOVERY_BUFFER) * math.exp(
                -minutes / self.parameters.tau_user
            )
        elif components is not None:
            weights = {k: float(components.get(k, 0)) for k in GROWTH_RATES}
            if any(not math.isfinite(x) or x < 0 for x in weights.values()):
                raise ValueError("Invalid workload component")
            total = sum(weights.values())
            if total:
                growth = sum(GROWTH_RATES[k] * p / total for k, p in weights.items())
                self.value = max(RECOVERY_BUFFER, self.value + minutes * self.parameters.a_user * growth)
        return self.value

    @property
    def display(self) -> str:
        return "120+" if self.value > 120 else f"{self.value:.0f}"

    def reset(self) -> None:
        self.value = 0.0
        self.stale = False

    def project_minutes(self, components: Mapping[str, float]) -> float | None:
        if self.stale:
            return None
        rate = self.parameters.a_user * sum(GROWTH_RATES[k] * components.get(k, 0) for k in GROWTH_RATES)
        return max(0, (REMINDER_REFERENCE - self.value) / rate) if rate > 0 else None


class LoadBand:
    """Display-only 5-point hysteresis; retains the unmodified continuous load."""

    def __init__(self):
        self.band = 0

    def update(self, value: float) -> int:
        levels = (0, 40, 80, 100, 120)
        while self.band < 4 and value >= levels[self.band + 1]:
            self.band += 1
        while self.band > 0 and value < levels[self.band] - 5:
            self.band -= 1
        return levels[self.band]
