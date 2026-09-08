"""Explainable, persistent interruption policy shared by every automatic overlay."""

from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime
from hashlib import sha256
import math
import random
import time
from zoneinfo import ZoneInfo

from focuspet.domain.types import StateSnapshot, FeatureWindow, Prediction


@dataclass
class Notification:
    id: str
    action: str
    kind: str
    text: str
    at: float
    target_start: float | None = None
    target_end: float | None = None
    mechanism: str = "threshold"
    version: str = "reminders-v1"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class QueryCandidate:
    id: str
    start: float
    end: float
    created_at: float
    reason: str
    mechanism: str
    feature_id: str
    version: str = "active-query-v1"

    def to_dict(self) -> dict:
        return asdict(self)


class ActiveQuerySelector:
    """Reserve a reproducible random audit fraction; no unanswered-item labels."""

    def __init__(self, seed: int = 7, audit_fraction: float = 0.25):
        self.rng = random.Random(seed)
        self.audit_fraction = audit_fraction

    def select(
        self,
        windows_and_predictions: list[tuple[FeatureWindow, Prediction]],
        now: float,
        labeled_feature_ids: set[str] | None = None,
    ) -> QueryCandidate | None:
        labeled = labeled_feature_ids or set()
        candidates = [
            (f, p)
            for f, p in windows_and_predictions
            if f.end < now
            and now - f.end <= 1800
            and f.coverage >= 0.6
            and f.id not in labeled
            and p.state != "Rest"
            and not f.missing_reason
        ]
        if not candidates:
            return None
        audit = self.rng.random() < self.audit_fraction
        if audit:
            f, p = self.rng.choice(candidates)
            mechanism, reason = (
                "random-audit",
                "Random audit of a completed observable interval; hide the model guess until answered.",
            )
        else:
            # Favor uncertainty and novelty relative to other recent candidate vectors.
            def utility(item):
                feature, prediction = item
                vector = feature.vector()
                others = [other.vector() for other, _ in candidates if other.id != feature.id]
                nearest = min(
                    (sum((a - b) ** 2 for a, b in zip(vector, other)) for other in others), default=1
                )
                return prediction.uncertainty + 0.15 * min(math.sqrt(nearest), 2)

            f, p = max(candidates, key=utility)
            mechanism, reason = (
                "uncertainty-diversity",
                "Uncertain completed interval with distinct recent activity statistics.",
            )
        identity = sha256(f"{f.mode}|{f.id}|{mechanism}".encode()).hexdigest()[:24]
        return QueryCandidate(identity, f.start, f.end, now, reason, mechanism, f.id)


class ReminderManager:
    def __init__(self, state: dict | None = None, timezone: str = "UTC", seed: int = 7):
        self.timezone = timezone
        self.enabled = True
        self.quiet = False
        self.meeting = False
        self.privacy_paused = False
        self.quiet_hours: list[tuple[str, str]] = []
        self.snooze_until: float | None = None
        self.snooze_id: str | None = None
        self.history: list[dict] = []
        self.seen: set[str] = set()
        self.rest_cooldown_s: float = 1800
        self.last_rest: float | None = None
        self.last_query: float | None = None
        self.previous_load: float | None = None
        self.pending_threshold: int | None = None
        self.crossing_serial = 0
        self.pending_since: float | None = None
        self.last_processed: float | None = None
        if state:
            for key in (
                "enabled",
                "quiet",
                "meeting",
                "privacy_paused",
                "quiet_hours",
                "snooze_until",
                "snooze_id",
                "history",
                "last_rest",
                "rest_cooldown_s",
                "last_query",
                "previous_load",
                "pending_threshold",
                "crossing_serial",
                "pending_since",
                "last_processed",
                "timezone",
            ):
                if key in state:
                    setattr(self, key, state[key])
            self.seen = set(state.get("seen", []))

    def set_quiet(self, quiet: bool) -> None:
        self.quiet = quiet

    def snooze(self, minutes: int, now: float | None = None) -> None:
        if minutes not in (5, 15, 30):
            raise ValueError("Snooze supports 5, 15, or 30 minutes")
        at = time.time() if now is None else now
        self.snooze_until = at + minutes * 60
        self.snooze_id = sha256(f"user-snooze|{at}|{minutes}".encode()).hexdigest()[:24]

    def _quiet_now(self, at: float) -> bool:
        if not self.enabled or self.quiet or self.meeting or self.privacy_paused:
            return True
        local = datetime.fromtimestamp(at, ZoneInfo(self.timezone))
        minute = local.hour * 60 + local.minute
        for start, end in self.quiet_hours:
            sh, sm = map(int, start.split(":"))
            eh, em = map(int, end.split(":"))
            a, b = sh * 60 + sm, eh * 60 + em
            if a <= minute < b if a <= b else minute >= a or minute < b:
                return True
        return False

    def _budget(self, at: float, query: bool = False) -> bool:
        self.history = [h for h in self.history if at - h["at"] < 172800]
        day = datetime.fromtimestamp(at, ZoneInfo(self.timezone)).date()
        automatic = [h for h in self.history if h.get("mechanism") != "user-snooze"]
        today = [
            h for h in automatic if datetime.fromtimestamp(h["at"], ZoneInfo(self.timezone)).date() == day
        ]
        hourly = [h for h in automatic if 0 <= at - h["at"] < 3600]
        if len(today) >= 6 or len(hourly) >= 2:
            return False
        if query and (
            sum(h["kind"] == "label-query" for h in today) >= 2
            or self.last_query is not None
            and at - self.last_query < 5400
        ):
            return False
        return True

    def _emit(self, notification: Notification) -> Notification | None:
        if notification.id in self.seen:
            return None
        self.seen.add(notification.id)
        self.history.append(notification.to_dict())
        if notification.kind == "rest":
            self.last_rest = notification.at
        if notification.kind == "label-query":
            self.last_query = notification.at
        return notification

    def consider(
        self, snapshot: StateSnapshot, now: float | None = None, replay: bool = False
    ) -> Notification | None:
        at = snapshot.end if now is None else now
        if replay or (self.last_processed is not None and snapshot.end <= self.last_processed):
            return None
        self.last_processed = snapshot.end
        if (
            snapshot.state in ("Unknown", "Rest")
            or snapshot.workload_stale
            or snapshot.observation in ("Missing", "Paused", "Locked")
        ):
            return None
        load = snapshot.workload
        previous = self.previous_load
        self.previous_load = load
        for threshold in (100, 120):
            # A fresh process at already-high load does not re-notify historical crossings.
            if previous is not None and previous < threshold <= load:
                self.pending_threshold = threshold
                self.pending_since = at
                self.crossing_serial += 1
        if load < 95:
            self.pending_threshold = None
        if self._quiet_now(at):
            return None
        if self.snooze_until is not None:
            if at < self.snooze_until:
                return None
            notification = Notification(
                self.snooze_id or "",
                "A2",
                "rest",
                "Your requested reminder: a short rest is available.",
                at,
                mechanism="user-snooze",
            )
            self.snooze_until = None
            self.snooze_id = None
            return self._emit(notification)
        if self.pending_threshold is None or not self._budget(at):
            return None
        if self.last_rest is not None and at - self.last_rest < max(1800, self.rest_cooldown_s):
            return None
        if self.pending_since is not None and at - self.pending_since > 1800:
            self.pending_threshold = None
            return None
        threshold = self.pending_threshold
        self.pending_threshold = None
        identity = sha256(
            f"{snapshot.mode}|rest|{self.crossing_serial}|{self.pending_since}|{threshold}".encode()
        ).hexdigest()[:24]
        return self._emit(
            Notification(
                identity,
                "A3" if threshold >= 120 else "A2",
                "rest",
                "Your work rhythm load is above the reference line. A short rest may help."
                if threshold >= 120
                else "You have reached your work rhythm reference line. Would you like a short rest?",
                at,
            )
        )

    def consider_query(
        self, candidate: QueryCandidate, now: float | None = None, replay: bool = False
    ) -> Notification | None:
        at = time.time() if now is None else now
        if replay or candidate.end >= at or at - candidate.end > 1800 or self._quiet_now(at):
            return None
        if self.snooze_until is not None and at < self.snooze_until or not self._budget(at, query=True):
            return None
        # Candidate ID is persisted for idempotency, including audit requests.
        text = "How was the highlighted completed interval? You can skip this question."
        return self._emit(
            Notification(
                candidate.id,
                "A2",
                "label-query",
                text,
                at,
                candidate.start,
                candidate.end,
                candidate.mechanism,
            )
        )

    def to_dict(self) -> dict:
        keys = (
            "timezone",
            "enabled",
            "quiet",
            "meeting",
            "privacy_paused",
            "quiet_hours",
            "snooze_until",
            "snooze_id",
            "history",
            "last_rest",
            "rest_cooldown_s",
            "last_query",
            "previous_load",
            "pending_threshold",
            "crossing_serial",
            "pending_since",
            "last_processed",
        )
        return {key: getattr(self, key) for key in keys} | {
            "seen": sorted(self.seen),
            "version": "reminders-v1",
        }
