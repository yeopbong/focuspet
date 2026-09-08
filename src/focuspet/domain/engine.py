"""One causal pipeline reused for native collection, testing, and replay."""

from __future__ import annotations
from dataclasses import replace
from hashlib import sha256
from focuspet.domain.clock import Clock, SystemClock
from focuspet.domain.types import ActivityBucket, Prediction, StateSnapshot, WORK_STATES
from focuspet.domain.workload import Workload
from focuspet.features import FeatureBuilder
from focuspet.models.prior import GenericPrior


def stable_id(*parts: object) -> str:
    return sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:24]


class StateSmoother:
    def __init__(self, confirmations: int = 2, focus_tau_s: float = 45):
        self.state = "Unknown"
        self.pending = "Unknown"
        self.count = 0
        self.confirmations = confirmations
        self.focus: float | None = None
        self.tau_s = focus_tau_s

    def update(self, prediction: Prediction, duration_s: float) -> tuple[str, float | None]:
        import math

        if prediction.state in ("Rest", "Unknown"):
            self.state = prediction.state
            self.focus = None
            self.pending, self.count = prediction.state, 0
            return self.state, None
        if prediction.state != self.state:
            self.count = self.count + 1 if prediction.state == self.pending else 1
            self.pending = prediction.state
            if self.state == "Unknown" or self.count >= self.confirmations:
                self.state = prediction.state
                self.count = 0
        else:
            self.count = 0
        raw = 100 * (prediction.components["Focused"] + 0.5 * prediction.components["Normal"])
        alpha = 1 - math.exp(-duration_s / self.tau_s)
        self.focus = raw if self.focus is None else self.focus + alpha * (raw - self.focus)
        return self.state, self.focus


class Engine:
    def __init__(
        self,
        profile: str = "Mixed",
        predictor=None,
        clock: Clock | None = None,
        workload: Workload | None = None,
        mode: str = "real",
    ):
        self.prior = GenericPrior(profile)
        self.predictor = predictor
        self.clock = clock or SystemClock()
        self.workload = workload or Workload()
        self.mode = mode
        self.feature_builder = FeatureBuilder()
        self.smoother = StateSmoother()
        self.latest: StateSnapshot | None = None
        self.paused = False
        self.rest_until: float | None = None
        self.rest_started: float | None = None
        self._rest_remaining_s = 0.0
        self._declared: tuple[str, float] | None = None
        self._elapsed_since_emit = 0.0
        self._last_interval: tuple[float, float, str] | None = None
        self._seen: set[str] = set()
        self._seen_order: list[str] = []
        self._last_session: str | None = None
        self.events: list[dict] = []
        self.workload_steps: list[dict] = []
        self._emission_start: float | None = None

    def _now(self, now: float | None) -> float:
        return self.clock.utc() if now is None else now

    def start_rest(self, minutes: float = 5, now: float | None = None) -> None:
        if not 0 < minutes <= 240:
            raise ValueError("Rest duration must be between 0 and 240 minutes")
        at = self._now(now)
        self.rest_started, self.rest_until = at, at + minutes * 60
        self._rest_remaining_s = minutes * 60
        self.events.append(
            {"kind": "RestSession", "start": at, "end": self.rest_until, "declared_minutes": minutes}
        )
        if self.latest:
            self.latest = replace(
                self.latest,
                state="Rest",
                focus=None,
                rest_active=True,
                prediction=replace(
                    self.latest.prediction,
                    state="Rest",
                    source="User declaration",
                    reason=["A timed rest was explicitly started."],
                ),
            )

    def end_rest(self, now: float | None = None) -> None:
        self.events.append({"kind": "RestSessionEnd", "at": self._now(now)})
        self.rest_until = self.rest_started = None
        self._rest_remaining_s = 0.0
        self.feature_builder.clear()
        self.smoother = StateSmoother()

    def pause(self, paused: bool = True) -> None:
        self.paused = paused
        self.feature_builder.clear()
        self.smoother = StateSmoother()
        self.workload.stale = True
        if self.latest:
            self.latest = replace(
                self.latest,
                observation="Paused" if paused else "Missing",
                state="Unknown",
                focus=None,
                workload_stale=True,
                rest_active=False,
                prediction=replace(
                    self.latest.prediction,
                    state="Unknown",
                    source="Insufficient data",
                    reason=["Collection is paused." if paused else "Waiting for a new activity window."],
                ),
            )

    def declare_state(self, state: str, minutes: float = 5, now: float | None = None) -> None:
        if state == "Rest":
            self.start_rest(minutes, now)
            return
        if state not in WORK_STATES or not 0 < minutes <= 240:
            raise ValueError("Invalid state declaration")
        self._declared = (state, self._now(now) + minutes * 60)

    def reset_workload(self, now: float | None = None) -> None:
        self.workload.reset()
        self.events.append({"kind": "WorkloadReset", "at": self._now(now), "value": 0})
        self.workload_steps.append(
            {
                "start": self._now(now),
                "end": self._now(now),
                "duration_s": 0,
                "effective_seconds": 0,
                "components": {},
                "rest": False,
                "valid": True,
                "value_before": 0,
                "value": 0,
                "parameter_version": self.workload.parameters.version,
                "reset": True,
                "session_id": self._last_session or "default",
                "mode": self.mode,
            }
        )
        if self.latest:
            self.latest = replace(self.latest, workload=0, workload_stale=False)

    def process(self, bucket: ActivityBucket) -> StateSnapshot | None:
        if bucket.mode != self.mode:
            raise ValueError("Activity mode differs from engine mode")
        interval = (bucket.start, bucket.end, bucket.session_id)
        if bucket.id in self._seen or interval == self._last_interval:
            return None
        if self._emission_start is None:
            self._emission_start = bucket.start
        self._seen.add(bucket.id)
        self._seen_order.append(bucket.id)
        if len(self._seen_order) > 4096:
            self._seen.discard(self._seen_order.pop(0))
        clock_discontinuity = False
        if self._last_session is not None and self._last_session != bucket.session_id:
            self.feature_builder.clear()
            self.smoother = StateSmoother()
            self.workload.stale = True
            self._elapsed_since_emit = 0
            self._emission_start = bucket.start
        if self._last_interval and self._last_session == bucket.session_id:
            previous_end = self._last_interval[1]
            if bucket.start < previous_end - 1e-6:
                # Any overlap or wall-clock rollback is an explicit gap, never negative elapsed work.
                clock_discontinuity = True
            elif bucket.start - previous_end > 30:
                self.workload.stale = True
                self.feature_builder.clear()
                self.smoother = StateSmoother()
                self._elapsed_since_emit = 0
                self._emission_start = bucket.start
        self._last_interval = interval
        self._last_session = bucket.session_id
        if self.paused or clock_discontinuity:
            bucket = replace(
                bucket,
                observation="Paused" if self.paused else "Missing",
                keyboard=None,
                clicks=None,
                scroll=None,
                pointer=None,
                idle_s=None,
                app_dwell={},
                app_switches=None,
                coverage={},
                missing_reason="privacy-pause" if self.paused else "clock-discontinuity",
            )
        feature = self.feature_builder.add(bucket)
        if feature is None:
            return None
        feature.id = stable_id("feature", self.mode, bucket.session_id, feature.start, feature.end)
        prediction = self.prior.predict(feature)
        if self.predictor and prediction.source != "Insufficient data":
            try:
                candidate = self.predictor.predict(feature)
                if not isinstance(candidate, Prediction):
                    raise ValueError("Predictor returned invalid record")
                prediction = candidate
            except Exception:
                prediction.reason.append("Personal model unavailable; using the versioned generic prior.")
        value_before = self.workload.value
        rest_seconds = 0.0
        if self.rest_until is not None and self.rest_started is not None and not self.paused:
            # Clamp both to declaration UTC bounds and remaining fresh elapsed duration.
            overlap = max(0.0, min(bucket.end, self.rest_until) - max(bucket.start, self.rest_started))
            rest_seconds = min(bucket.duration_s, overlap, self._rest_remaining_s)
            self._rest_remaining_s -= rest_seconds
        rest = rest_seconds > 0
        effective_seconds = rest_seconds
        integrated_valid = rest
        valid_observation = bucket.observation in ("Active", "No-input") and bucket.interaction_s <= 0
        if rest:
            self.workload.advance(rest_seconds, rest=True)
            prediction = Prediction(
                {k: 1 / 3 for k in WORK_STATES},
                state="Rest",
                source="User declaration",
                coverage=feature.coverage,
                uncertainty=0,
                reason=["A timed rest was explicitly started."],
                feature_id=feature.id,
            )
            # Unobserved remainder beyond a declared rest is not recovery.
            if rest_seconds < bucket.duration_s and not valid_observation:
                self.workload.stale = True
        elif self._declared and bucket.end <= self._declared[1] and valid_observation:
            state = self._declared[0]
            prediction = Prediction(
                {k: float(k == state) for k in WORK_STATES},
                state=state,
                source="User declaration",
                coverage=feature.coverage,
                uncertainty=0,
                reason=["The current state was explicitly declared."],
                feature_id=feature.id,
            )
            self.workload.advance(bucket.duration_s, prediction.components)
            effective_seconds, integrated_valid = bucket.duration_s, True
        else:
            valid = valid_observation and prediction.state != "Unknown" and feature.coverage >= 0.45
            if not valid_observation:
                prediction = Prediction(
                    prediction.components,
                    state="Unknown",
                    source="Insufficient data",
                    coverage=feature.coverage,
                    uncertainty=1,
                    reason=[bucket.missing_reason or "The current activity cannot be observed."],
                    feature_id=feature.id,
                )
            # Apply only this bucket's available coverage, never the overlapping feature duration.
            current_coverages = [
                bucket.coverage.get(k, 1 if getattr(bucket, k) is not None else 0)
                for k in ("keyboard", "clicks", "scroll", "pointer")
            ]
            effective_coverage = sum(current_coverages) / 4
            effective_seconds, integrated_valid = bucket.duration_s * effective_coverage, valid
            self.workload.advance(effective_seconds, prediction.components, valid=valid)
        self.workload_steps.append(
            {
                "id": stable_id("workload-step", bucket.id, self.workload.parameters.version),
                "start": bucket.start,
                "end": bucket.end,
                "duration_s": bucket.duration_s,
                "effective_seconds": effective_seconds if integrated_valid else 0,
                "components": dict(prediction.components),
                "rest": rest,
                "valid": integrated_valid,
                "value_before": value_before,
                "value": self.workload.value,
                "parameter_version": self.workload.parameters.version,
                "reset": False,
                "session_id": bucket.session_id,
                "mode": self.mode,
                "coverage": feature.coverage,
                "missing_reason": bucket.missing_reason,
            }
        )
        prediction.feature_id = feature.id
        prediction.id = stable_id("prediction", feature.id, prediction.model_version, prediction.source)
        self._elapsed_since_emit += bucket.duration_s
        if self._elapsed_since_emit < 30:
            return None
        elapsed = self._elapsed_since_emit
        self._elapsed_since_emit %= 30
        state, focus = self.smoother.update(prediction, elapsed)
        self.latest = StateSnapshot(
            start=self._emission_start if self._emission_start is not None else bucket.start,
            end=bucket.end,
            observation=bucket.observation,
            state=state,
            focus=focus,
            workload=self.workload.value,
            workload_stale=self.workload.stale,
            prediction=prediction,
            feature=feature,
            session_id=bucket.session_id,
            mode=bucket.mode,
            parameter_version=self.workload.parameters.version,
            rest_active=rest,
            duration_s=elapsed,
            id=stable_id("snapshot", prediction.id, self.workload.parameters.version),
        )
        self._emission_start = bucket.end
        return self.latest
