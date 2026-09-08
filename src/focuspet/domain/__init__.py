from .types import (
    ActivityBucket,
    FeatureWindow,
    Prediction,
    StateSnapshot,
    CATEGORIES,
    WORK_STATES,
    STATES,
    MODES,
)
from .clock import Clock, SystemClock, FakeClock
from .workload import Workload, WorkloadParameters, LoadBand


def __getattr__(name):
    if name in ("Engine", "StateSmoother"):
        from .engine import Engine, StateSmoother

        return {"Engine": Engine, "StateSmoother": StateSmoother}[name]
    raise AttributeError(name)


__all__ = [
    "ActivityBucket",
    "FeatureWindow",
    "Prediction",
    "StateSnapshot",
    "Clock",
    "SystemClock",
    "FakeClock",
    "Workload",
    "WorkloadParameters",
    "LoadBand",
    "Engine",
    "StateSmoother",
    "CATEGORIES",
    "WORK_STATES",
    "STATES",
    "MODES",
]
