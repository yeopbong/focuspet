"""Opt-in, local scalar diagnostics for wall-clock validation; no activity payloads."""

from __future__ import annotations

import json
import os
import time
from collections import Counter, deque
from pathlib import Path


class Diagnostics:
    def __init__(self, destination=None):
        self.destination = Path(destination).expanduser() if destination else None
        self.started = time.monotonic()
        self.last_write = 0.0
        self.ui_last = None
        self.ui_max_interval = 0.0
        self.ui_polls = 0
        self.loops = self.buckets = self.snapshots = self.write_errors = 0
        self.training_generation = 0
        self.actions: Counter = Counter()
        self.errors: Counter = Counter()
        self.inference_ms: deque = deque(maxlen=240)
        self.processing_ms: deque = deque(maxlen=240)

    def ui_read(self):
        now = time.monotonic()
        if self.ui_last is not None:
            self.ui_max_interval = max(self.ui_max_interval, now - self.ui_last)
        self.ui_last = now
        self.ui_polls += 1

    def action(self, name):
        if name in {
            "consent",
            "pause",
            "resume",
            "rest",
            "end_rest",
            "reset_cycle",
            "feedback",
            "revise_feedback",
            "retract_feedback",
            "query_answer",
            "query_skip",
            "interaction",
            "settings",
            "delete_data",
            "delete_range",
            "train",
            "calibrate",
            "cancel_job",
            "close",
        }:
            self.actions[name] += 1

    @staticmethod
    def _latencies(values):
        ordered = sorted(values)
        return {
            "recent_count": len(ordered),
            "recent_p95_ms": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] if ordered else None,
            "recent_max_ms": ordered[-1] if ordered else None,
        }

    def write(self, service, *, force=False, stopped=False):
        now = time.monotonic()
        if self.destination is None or (not force and now - self.last_write < 5):
            return
        self.last_write = now
        collector = getattr(service, "collector", None)
        store = getattr(service, "store", None)
        # Cache inspection only: no permission requests, desktop reads, database queries or payloads.
        record = {
            "schema": "local-diagnostics-v1",
            "pid": os.getpid(),
            "mode": service.mode,
            "wall_utc": time.time(),
            "heartbeat_monotonic": now,
            "elapsed_s": now - self.started,
            "stopped": stopped,
            "coordinator_alive": service._thread.is_alive(),
            "loop_count": self.loops,
            "bucket_count": self.buckets,
            "snapshot_count": self.snapshots,
            "command_queue": service._commands.qsize(),
            "command_capacity": service._commands.maxsize,
            "event_queue": service._events.qsize(),
            "event_capacity": service._events.maxsize,
            "consent": bool(getattr(service, "settings", {}).get("consent")),
            "privacy_paused": bool(getattr(service, "_privacy_paused", True)),
            "declared_rest": getattr(service, "_timed_rest", None) is not None,
            "training_active": getattr(service, "_job", None) is not None,
            "training_generation": self.training_generation,
            "ui_cache_poll_count": self.ui_polls,
            "ui_cache_poll_age_s": None if self.ui_last is None else max(0.0, now - self.ui_last),
            "ui_cache_poll_max_interval_s": self.ui_max_interval,
            "ui_interaction_validation": "manual confirmation required",
            "inference": self._latencies(self.inference_ms),
            "bucket_processing": self._latencies(self.processing_ms),
            "actions": dict(self.actions),
            "action_semantics": "Command attempts; successful interaction requires observation.",
            "errors": dict(self.errors),
            "diagnostic_write_errors": self.write_errors,
            "collector": collector.diagnostics()
            if collector is not None and hasattr(collector, "diagnostics")
            else {
                "adapter": "replay" if service.mode == "synthetic-demo" else "unavailable",
            },
            "storage": store.diagnostics() if store is not None else {},
        }
        temporary = self.destination.with_name(self.destination.name + ".tmp")
        try:
            self.destination.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(record, sort_keys=True, allow_nan=False), encoding="utf-8")
            temporary.replace(self.destination)
        except OSError:
            self.write_errors += 1  # Diagnostics must never interrupt observation or controls.
