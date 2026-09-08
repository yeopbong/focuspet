from __future__ import annotations
from collections import deque
from math import log1p
from statistics import pstdev
from focuspet.domain.types import ActivityBucket, FeatureWindow, CATEGORIES

FEATURE_NAMES = (
    (
        "keyboard_log_rate",
        "click_log_rate",
        "scroll_log_rate",
        "pointer_log_rate",
        "active_ratio",
        "switch_rate",
        "burstiness",
        "continuity_minutes",
        "context_active_ratio",
        "activity_change",
        "category_stability",
    )
    + tuple("app_" + category.lower() for category in CATEGORIES)
    + (
        "input_coverage",
        "app_coverage",
        "idle_coverage",
        "keyboard_missing",
        "clicks_missing",
        "scroll_missing",
        "pointer_missing",
        "app_missing",
        "idle_missing",
    )
)


class FeatureBuilder:
    def __init__(self, window_s: float = 60, context_s: float = 300):
        self.window_s, self.context_s = window_s, context_s
        self.buckets: deque[ActivityBucket] = deque()
        self._continuous = 0.0
        self._last_end: float | None = None
        self._session: str | None = None

    def clear(self) -> None:
        self.buckets.clear()
        self._continuous = 0.0
        self._last_end = None
        self._session = None

    def add(self, bucket: ActivityBucket) -> FeatureWindow | None:
        if self._last_end is not None and (
            bucket.start < self._last_end - 1e-6
            or bucket.start - self._last_end > 30
            or self._session != bucket.session_id
        ):
            self.clear()
        self.buckets.append(bucket)
        self._last_end, self._session = bucket.end, bucket.session_id
        total = sum(b.duration_s for b in self.buckets)
        while len(self.buckets) > 1 and total - self.buckets[0].duration_s >= self.context_s:
            total -= self.buckets.popleft().duration_s
        if bucket.observation == "Active" and bucket.interaction_s == 0:
            self._continuous += bucket.duration_s
        else:
            self._continuous = 0.0
        recent: list[tuple[ActivityBucket, float]] = []
        remaining = self.window_s
        for b in reversed(self.buckets):
            weight = min(b.duration_s, remaining)
            if weight > 0:
                recent.append((b, weight))
            remaining -= weight
            if remaining <= 0:
                break
        if not recent:
            return None
        span = sum(w for _, w in recent)
        values: dict[str, float] = {}
        coverages = []
        for signal, name in [
            ("keyboard", "keyboard_log_rate"),
            ("clicks", "click_log_rate"),
            ("scroll", "scroll_log_rate"),
            ("pointer", "pointer_log_rate"),
        ]:
            seconds = count = 0.0
            for b, w in recent:
                value = getattr(b, signal)
                usable = b.observation not in ("Paused", "Missing", "Locked") and b.interaction_s <= 0
                coverage = b.coverage.get(signal, 1.0 if value is not None else 0.0) if usable else 0.0
                if value is not None:
                    count += value * (w / max(b.duration_s, 1e-9)) * coverage
                    seconds += w * coverage
            cov = seconds / span
            values[name] = log1p(count * 60 / seconds) if seconds else 0.0
            values[signal + "_missing"] = 1 - cov
            coverages.append(cov)
        input_cov = sum(coverages) / len(coverages)
        idle_seconds = activity_seconds = 0.0
        category_seconds = {k: 0.0 for k in CATEGORIES}
        app_seconds = switches = 0.0
        rates = []
        for b, w in recent:
            usable = b.observation not in ("Paused", "Missing", "Locked") and b.interaction_s <= 0
            idle_cov = b.coverage.get("idle", 1.0 if b.idle_s is not None else 0.0) if usable else 0.0
            if b.idle_s is not None:
                idle_seconds += w * idle_cov
                activity_seconds += max(0, 1 - min(1, b.idle_s / max(b.duration_s, 1e-9))) * w * idle_cov
            app_cov = (
                b.coverage.get("app", b.coverage.get("application", 1.0 if b.app_dwell else 0.0))
                if usable
                else 0.0
            )
            app_seconds += w * app_cov
            for k, dwell in b.app_dwell.items():
                category_seconds[k] += dwell * w / max(b.duration_s, 1e-9) * app_cov
            if b.app_switches is not None:
                switches += b.app_switches * w / max(b.duration_s, 1e-9) * app_cov
            if b.keyboard is not None and usable:
                rates.append(b.keyboard / max(b.duration_s, 1))
        active = activity_seconds / idle_seconds if idle_seconds else 0.0
        context_seconds = sum(
            b.duration_s
            for b in self.buckets
            if b.idle_s is not None
            and b.observation not in ("Paused", "Missing", "Locked")
            and not b.interaction_s
        )
        context_active = sum(
            max(0, b.duration_s - (b.idle_s or 0))
            for b in self.buckets
            if b.idle_s is not None
            and b.observation not in ("Paused", "Missing", "Locked")
            and not b.interaction_s
        ) / max(context_seconds, 1)
        values.update(
            active_ratio=active,
            switch_rate=switches * 60 / max(app_seconds, 1),
            burstiness=pstdev(rates) / (sum(rates) / len(rates) + 0.1) if rates else 0,
            continuity_minutes=min(self._continuous, 3600) / 60,
            context_active_ratio=context_active,
            activity_change=active - context_active,
            category_stability=max(category_seconds.values()) / max(sum(category_seconds.values()), 1),
            input_coverage=input_cov,
            app_coverage=app_seconds / span,
            idle_coverage=idle_seconds / span,
            app_missing=1 - app_seconds / span,
            idle_missing=1 - idle_seconds / span,
        )
        denominator = max(sum(category_seconds.values()), 1)
        values.update({"app_" + k.lower(): v / denominator for k, v in category_seconds.items()})
        coverage = (input_cov + app_seconds / span + idle_seconds / span) / 3
        valid_s = span * coverage
        return FeatureWindow(
            start=min(b.start for b, _ in recent),
            end=bucket.end,
            values=values,
            coverage=coverage,
            valid_duration_s=valid_s,
            session_id=bucket.session_id,
            mode=bucket.mode,
            missing_reason=bucket.missing_reason,
            bucket_ids=[b.id for b, _ in reversed(recent)],
        )
