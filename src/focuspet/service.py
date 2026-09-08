"""Bounded background application coordinator. The UI consumes cached dictionaries only."""

from __future__ import annotations

import copy
import json
import multiprocessing as mp
import os
import queue
import sys
import threading
import time
from collections import deque
from pathlib import Path

from focuspet.collectors.base import UnsupportedCollector, validate_categories
from focuspet.collectors.replay import ReplayCollector
from focuspet.domain import ActivityBucket, Engine, Workload, WorkloadParameters
from focuspet.policy import ReminderManager, ActiveQuerySelector
from focuspet.storage import Store
from focuspet.diagnostics import Diagnostics

DEFAULT_SETTINGS = dict(
    consent=False,
    character="mira",
    profile="Mixed",
    scale=3,
    quiet=False,
    reminders_enabled=True,
    quiet_hours=[],
    bucket_days=7,
    feature_days=90,
    startup=False,
    click_through=False,
    meeting=False,
    categories={},
    position=None,
    feedback_days=365,
    model_days=365,
    reminder_cooldown=30,
    timezone="UTC",
    always_on_top=True,
    privacy_paused=False,
)


def local_timezone() -> str:
    try:
        resolved = str(Path("/etc/localtime").resolve())
        if "/zoneinfo/" in resolved:
            return resolved.split("/zoneinfo/", 1)[1]
    except OSError:
        pass
    return "UTC"


def data_root() -> Path:
    override = os.environ.get("FOCUSPET_DATA_HOME")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Focus Pet"
    return Path.home() / ".local" / "share" / "focuspet"


def _job_entry(kind, data, output, mode, cancel, results, automatic=False, profile="Mixed"):
    # Spawned process: fitting/search never shares the UI process or collector callbacks.
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    try:
        if kind == "train":
            from focuspet.learning.training import train_records

            result = train_records(
                data, output, mode=mode, cancel=cancel, automatic=automatic, profile=profile
            )
        else:
            from focuspet.optimization import calibrate_file

            result = calibrate_file(data, output, mode=mode, cancel=cancel)
        results.put(result)
    except Exception as error:
        results.put(
            {"status": "cancelled" if cancel.is_set() else "failed", "error_type": type(error).__name__}
        )


def safe_job_result(value):
    """Keep useful job metrics while excluding private filesystem locations."""
    if isinstance(value, dict):
        return {
            key: safe_job_result(item)
            for key, item in value.items()
            if key
            not in {"path", "filesystem_path", "artifact_path", "model_path", "data_path", "output_path"}
            and not key.endswith("_path")
        }
    if isinstance(value, (list, tuple)):
        return [safe_job_result(item) for item in value]
    if isinstance(value, str) and (value.startswith("/") or (len(value) > 2 and value[1:3] in (":\\", ":/"))):
        return "[local artifact]"
    return value


class AppService:
    def __init__(self, mode="real", data_dir=None, scenario="workday", clock=None, diagnostics_path=None):
        if mode not in ("real", "synthetic-demo", "test"):
            raise ValueError("Invalid mode")
        self.mode = mode
        self.clock = clock
        self.root = Path(data_dir) if data_dir else data_root()
        self.scenario_name = scenario
        destination = diagnostics_path or os.environ.get("FOCUSPET_DIAGNOSTICS_PATH")
        self._diagnostics = Diagnostics(destination) if destination else None
        self._commands: queue.Queue = queue.Queue(128)
        self._events: queue.Queue = queue.Queue(32)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._history: deque = deque(maxlen=3000)
        self._feedback: list = []
        self._cache = dict(
            mode=mode,
            consent=False,
            profile="Mixed",
            state="Unknown",
            observed_state="Paused",
            focus=None,
            workload=0.0,
            workload_stale=True,
            source="Insufficient data",
            evidence=["Choose a companion and review collection consent."],
            uncertainty=1.0,
            coverage=0.0,
            permissions={},
            history=[],
            feedback=[],
            learning={},
            settings=copy.deepcopy(DEFAULT_SETTINGS),
            status="Starting",
            query=None,
        )
        self._thread = threading.Thread(target=self._run, name="rhythm-coordinator", daemon=True)
        self._thread.start()

    def wait_ready(self, timeout=10):
        return self._ready.wait(timeout)

    def snapshot(self) -> dict:
        diagnostics = getattr(self, "_diagnostics", None)
        if diagnostics is not None:
            diagnostics.ui_read()
        with self._lock:
            return copy.deepcopy(self._cache)

    def command(self, name: str, **kwargs):
        try:
            self._commands.put_nowait((name, kwargs))
            return True
        except queue.Full:
            self._emit({"kind": "error", "text": "Action queue is busy. Please try again."})
            return False

    def drain_events(self) -> list:
        result = []
        while True:
            try:
                result.append(self._events.get_nowait())
            except queue.Empty:
                return result

    def close(self):
        if not self.command("close"):
            self._stop.set()  # A saturated action queue cannot keep acquisition alive after exit.
        self._thread.join(8)

    def _emit(self, value):
        try:
            self._events.put_nowait(value)
        except queue.Full:
            pass  # bounded UI notifications; never blocks collection

    def _update(self, **values):
        with self._lock:
            self._cache.update(copy.deepcopy(values))

    def _make_collector(self):
        if self.mode == "synthetic-demo":
            return ReplayCollector(self.scenario_name)
        if sys.platform == "darwin" and self.mode == "real":
            from focuspet.collectors.macos import NativeCollector

            return NativeCollector(clock=self.engine.clock, categories=self.settings.get("categories", {}))
        return UnsupportedCollector()

    def _initialize(self):
        self.store = Store(self.root, mode=self.mode)
        self.settings = {
            **copy.deepcopy(DEFAULT_SETTINGS),
            "timezone": local_timezone(),
            **self.store.settings(),
        }
        try:
            self.settings["categories"] = validate_categories(self.settings["categories"])
        except ValueError:
            self.settings["categories"] = {}
            self.store.save_settings(self.settings)
        self.engine = Engine(
            profile=self.settings["profile"],
            mode=self.mode,
            clock=getattr(self, "clock", None),
            workload=Workload(value=float(self.settings.get("last_workload", 0))),
        )
        self.engine.workload.stale = bool(self.settings.get("last_seen"))
        self.policy = ReminderManager(
            state=self.settings.get("policy"), timezone=self.settings.get("timezone", local_timezone())
        )
        self.query_selector = ActiveQuerySelector(seed=7)
        self._recent: deque = deque(maxlen=60)
        self._query = None
        self._asked_features = set()
        self._rest_id = None
        self._timed_rest = None
        self._privacy_paused = bool(self.settings.get("privacy_paused", False))
        self._last_retention = self.engine.clock.monotonic()
        self._job = None
        self._job_kind = None
        self._job_temp = None
        self._model_registry = None
        self._parameter_registry = None
        self._last_model_attempt = 0.0
        self._last_cache_refresh = 0.0
        self._last_demo_tick = 0.0
        self._demo_done = set()
        self.store.retention(
            bucket_days=self.settings["bucket_days"],
            feature_days=self.settings["feature_days"],
            feedback_days=self.settings["feedback_days"],
            model_days=self.settings["model_days"],
        )
        self._restore_history()
        self._reload_versions()
        self.collector = self._make_collector()
        if self.mode == "synthetic-demo" or (
            self.settings["consent"] and not self._privacy_paused and not self.settings.get("declared_rest")
        ):
            self.collector.start(request_permission=False)
        if self._privacy_paused:
            self.engine.pause(True)
        self._restore_declared_rest()
        self._apply_policy()
        self._refresh()
        self._update(
            status="Ready",
            restart_gap=bool(self.settings.get("last_seen")) and self._timed_rest is None,
            evidence=["Waiting for the first valid activity window."]
            if self.settings["consent"]
            else self._cache["evidence"],
        )
        self._ready.set()

    def _restore_history(self):
        events = self.store.get_events("StateSnapshot", limit=3000)
        for event in reversed(events):
            payload = event.get("payload", event)
            if "state" in payload:
                self._history.append(self._history_row(payload))

    def _apply_policy(self):
        self.policy.set_quiet(bool(self.settings.get("quiet")))
        self.policy.enabled = bool(self.settings.get("reminders_enabled", True))
        self.policy.meeting = bool(self.settings.get("meeting"))
        self.policy.rest_cooldown_s = max(1800, int(self.settings.get("reminder_cooldown", 30)) * 60)
        self.policy.quiet_hours = self.settings.get("quiet_hours", [])
        self.policy.privacy_paused = (
            self._privacy_paused
            or self.engine.paused
            or not (self.settings["consent"] or self.mode == "synthetic-demo")
        )

    @staticmethod
    def _history_row(row):
        return {
            k: row.get(k)
            for k in (
                "id",
                "start",
                "end",
                "duration_s",
                "state",
                "focus",
                "workload",
                "workload_stale",
                "observation",
                "session_id",
                "parameter_version",
            )
        } | {"source": row.get("prediction", {}).get("source", row.get("source", "Insufficient data"))}

    def _reload_versions(self):
        model_root = self.store.model_dir
        self._model_registry = None
        self.engine.predictor = None
        try:
            if (model_root / "model-registry.json").exists():
                from focuspet.learning.registry import ModelRegistry

                registry = ModelRegistry(model_root, self.mode)
                self.engine.predictor = registry.active_predictor(self.settings["profile"])
                self._model_registry = registry
        except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
            self.engine.predictor = None
            self._model_registry = None
        self._parameter_registry = None
        self.engine.workload.parameters = WorkloadParameters()
        try:
            parameter_root = model_root / "parameters"
            if parameter_root.exists():
                from focuspet.optimization import ParameterRegistry

                parameter_registry = ParameterRegistry(parameter_root, self.mode)
                self.engine.workload.parameters = WorkloadParameters(**parameter_registry.active_parameters())
                self._parameter_registry = parameter_registry
        except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
            self._parameter_registry = None
            self.engine.workload.parameters = WorkloadParameters()

    def _refresh(self):
        records = self.store.training_records()
        if records:
            from focuspet.learning.data import status_records

            learning = status_records(records)
        else:
            learning = {
                "episodes": 0,
                "days": 0,
                "sessions": 0,
                "eligible": False,
                "missing": [
                    "30 independent work-state episodes",
                    "2 days and 3 work sessions",
                    "At least 5 episodes of each work-state class",
                ],
            }
        model_status = self._model_registry.status() if self._model_registry else {}
        versions = model_status.get("versions", {})
        last_training = max((v.get("created_at", 0) for v in versions.values()), default=None)
        learning.update(
            version=model_status.get("active") or "generic-prior-v1",
            validation=(
                "Collecting independent shadow evidence"
                if model_status.get("candidate")
                else "Enabled after subsequent audit validation"
                if model_status.get("active")
                else "Generic prior; personal evidence not established"
            ),
            last_training=last_training,
            parameter_version=self.engine.workload.parameters.version,
        )
        self._feedback = self.store.feedback()
        self._update(
            mode=self.mode,
            consent=self.settings["consent"],
            profile=self.settings["profile"],
            settings=self.settings,
            history=list(self._history),
            feedback=self._feedback,
            learning=learning,
            permissions=self.collector.capabilities() if hasattr(self, "collector") else {},
            query=self._query,
            workload=self.engine.workload.value,
            workload_stale=self.engine.workload.stale,
        )
        self._last_cache_refresh = time.monotonic()

    def _persist_engine_events(self):
        while self.engine.workload_steps:
            step = self.engine.workload_steps[0]
            self.store.save_event(
                "WorkloadStep",
                step,
                start=step.get("start", step.get("at", self._now())),
                end=step.get("end", step.get("at", self._now())),
                session_id=step.get("session_id", self._session()),
            )
            if "value" in step:
                self.settings.update(last_workload=step["value"], last_seen=step.get("end", self._now()))
            self.engine.workload_steps.pop(0)
        while self.engine.events:
            event = dict(self.engine.events[0])
            kind = event.pop("kind")
            self.store.save_event(
                kind,
                event,
                start=event.get("start", event.get("at")),
                end=event.get("end"),
                session_id=event.get("session_id", self._session()),
            )

            self.engine.events.pop(0)

    def _session(self):
        return getattr(self.collector, "session_id", "demo-session")

    def _run(self):
        try:
            self._initialize()
            while not self._stop.is_set():
                try:
                    name, args = self._commands.get(timeout=0.05)
                    self._handle(name, args)
                except queue.Empty:
                    pass
                except Exception as error:
                    if self._diagnostics is not None:
                        self._diagnostics.errors["action:" + type(error).__name__] += 1
                    self._update(status="Action could not complete: " + type(error).__name__)
                    self._emit(
                        {"kind": "error", "text": "The action could not complete safely. Please retry."}
                    )
                if self._stop.is_set():
                    break
                try:
                    self._tick()
                    self._poll_job()
                    if self.engine.clock.monotonic() - self._last_retention >= 86400:
                        self._apply_retention()
                    if time.monotonic() - self._last_cache_refresh > 30:
                        self._refresh()
                except Exception as error:
                    if self._diagnostics is not None:
                        self._diagnostics.errors["observation:" + type(error).__name__] += 1
                    # A disk/collector failure is visible and pauses acquisition; the UI remains usable.
                    self.collector.pause(True)
                    self.engine.pause(True)
                    self._privacy_paused = True
                    self._timed_rest = None
                    self._last_cache_refresh = time.monotonic()
                    self._update(
                        status="Collection paused after a local error: " + type(error).__name__,
                        state="Unknown",
                        observed_state="Paused",
                        focus=None,
                        workload_stale=True,
                        evidence=[
                            "A local collector or storage error interrupted observation. Resume after resolving it."
                        ],
                    )
                    self._emit(
                        {
                            "kind": "error",
                            "text": "Observation paused after a local error. Settings and data controls remain available.",
                        }
                    )
                if self._diagnostics is not None:
                    self._diagnostics.loops += 1
                    self._diagnostics.write(self)
        except Exception as error:
            if self._diagnostics is not None:
                self._diagnostics.errors["initialization:" + type(error).__name__] += 1
            self._update(status="Unable to initialize: " + type(error).__name__)
            self._emit({"kind": "error", "text": "Local storage or collector could not initialize."})
            self._ready.set()
        finally:
            if self._diagnostics is not None:
                self._diagnostics.write(self, force=True, stopped=True)
            if getattr(self, "_job", None):
                self._cancel_job()
            if hasattr(self, "collector"):
                self.collector.stop()
            if hasattr(self, "store"):
                self.store.close()

    def _tick(self):
        if self._timed_rest is not None:
            self._advance_declared_rest()
            return
        if self.mode == "synthetic-demo":
            if time.monotonic() - self._last_demo_tick < 0.25:
                return
            self._last_demo_tick = time.monotonic()
        bucket = self.collector.sample()
        if bucket is None:
            return
        if self.mode == "synthetic-demo":
            for i, declaration in enumerate(self.collector.scenario.get("declarations", [])):
                if i not in self._demo_done and bucket.start >= declaration["at"]:
                    self.engine.start_rest(declaration["minutes"], now=declaration["at"])
                    self._demo_done.add(i)
        self._process_bucket(bucket)

    def _process_bucket(self, bucket):
        diagnostics = getattr(self, "_diagnostics", None)
        if diagnostics is None:
            return self._process_bucket_inner(bucket)
        started = time.perf_counter()
        try:
            return self._process_bucket_inner(bucket)
        finally:
            diagnostics.processing_ms.append((time.perf_counter() - started) * 1000)

    def _process_bucket_inner(self, bucket):
        diagnostics = getattr(self, "_diagnostics", None)
        self.store.save_bucket(bucket)
        inference_started = time.perf_counter() if diagnostics is not None else 0
        snapshot = self.engine.process(bucket)
        if diagnostics is not None:
            diagnostics.buckets += 1
            diagnostics.inference_ms.append((time.perf_counter() - inference_started) * 1000)
            diagnostics.snapshots += int(snapshot is not None)
        self._persist_engine_events()
        if snapshot is None:
            return
        self.store.save_snapshot(snapshot)
        self._history.append(self._history_row(snapshot.to_dict()))
        self._recent.append((snapshot.feature, snapshot.prediction))
        notification = self.policy.consider(snapshot, now=bucket.end, replay=self.mode != "real")
        if notification:
            self.store.save_event("NotificationEvent", notification.to_dict(), start=notification.at)
            if self.mode == "real":
                self._emit({"kind": "notification", **notification.to_dict()})
        if (
            self._query is None
            and self.mode == "real"
            and self._timed_rest is None
            and snapshot.observation in ("Active", "No-input")
            and snapshot.coverage >= 0.6
        ):
            candidate = self.query_selector.select(
                self._query_candidates(), now=bucket.end, labeled_feature_ids=self._asked_features
            )
            if candidate:
                query_notification = self.policy.consider_query(candidate, now=bucket.end)
                if query_notification:
                    self._asked_features.add(candidate.feature_id)
                    self._query = query_notification.to_dict() | {
                        "reason": candidate.reason,
                        "selection_version": candidate.version,
                    }
                    self.store.save_event("Query", self._query, start=query_notification.at)
                    self._emit({"kind": "query", **self._query})
        if self._query and bucket.end - self._query.get("at", bucket.end) > 1800:
            self.store.save_event("QueryExpired", {"query_id": self._query["id"], "response": None})
            self._query = None
        self.settings.update(
            last_workload=snapshot.workload, last_seen=bucket.end, policy=self.policy.to_dict()
        )
        self.store.save_settings(self.settings)
        self._update(
            state=snapshot.state,
            observed_state=snapshot.observation,
            focus=snapshot.focus,
            workload=snapshot.workload,
            workload_stale=snapshot.workload_stale,
            source=snapshot.prediction.source,
            evidence=snapshot.prediction.reason,
            uncertainty=snapshot.prediction.uncertainty,
            coverage=snapshot.prediction.coverage,
            history=list(self._history),
            rest_active=snapshot.rest_active,
            permissions=self.collector.capabilities(),
            query=self._query,
            settings=self.settings,
        )

    def _query_candidates(self):
        # Manual, audit and active feedback are all real answers; never spend an
        # automatic request on an interval that already overlaps an active answer.
        seen = set()
        intervals = []
        for feedback in self._feedback:
            episode = feedback["episode_id"]
            if episode in seen:
                continue
            seen.add(episode)
            if not feedback["withdrawn"]:
                intervals.append((feedback["start"], feedback["end"]))
        return [
            (feature, prediction)
            for feature, prediction in self._recent
            if not any(feature.start < end and feature.end > start for start, end in intervals)
        ]

    def _apply_retention(self):
        self._cancel_job()
        self.store.retention(
            now=self._now(),
            bucket_days=int(self.settings["bucket_days"]),
            feature_days=int(self.settings["feature_days"]),
            feedback_days=self.settings.get("feedback_days"),
            model_days=self.settings.get("model_days"),
        )
        self._history.clear()
        self._restore_history()
        cutoff = self._now() - int(self.settings["feature_days"]) * 86400
        self._recent = deque(((f, p) for f, p in self._recent if f.end >= cutoff), maxlen=60)
        self._reload_versions()
        self._last_retention = self.engine.clock.monotonic()
        self._refresh()

    def _save_rest_checkpoint(self, at):
        if self._timed_rest:
            record = {k: v for k, v in self._timed_rest.items() if k != "last_mono"}
            parameters = self.engine.workload.parameters
            record["parameters"] = {
                "a_user": parameters.a_user,
                "tau_user": parameters.tau_user,
                "version": parameters.version,
            }
            self.settings["declared_rest"] = record
        self.settings.update(last_workload=self.engine.workload.value, last_seen=at)
        self.store.save_settings(self.settings)

    def _restore_declared_rest(self):
        record = self.settings.get("declared_rest")
        if self.mode == "synthetic-demo" or not isinstance(record, dict):
            return
        try:
            start, end = float(record["start"]), float(record["end"])
            now = self.engine.clock.utc()
            if not 0 < end - start <= 14400:
                raise ValueError("Invalid declared rest bounds")
            checkpoint = max(
                start, float(record.get("last_utc", start)), float(self.settings.get("last_seen") or start)
            )
            until = min(end, now)
            seconds = max(0, until - checkpoint)
            if seconds:
                before = self.engine.workload.value
                current = self.engine.workload.parameters
                self.engine.workload.parameters = WorkloadParameters(
                    **record.get(
                        "parameters",
                        {"a_user": current.a_user, "tau_user": current.tau_user, "version": current.version},
                    )
                )
                self.engine.workload.advance(seconds, rest=True)
                version = self.engine.workload.parameters.version
                self.engine.workload.parameters = current
                self.store.save_event(
                    "WorkloadStep",
                    {
                        "start": checkpoint,
                        "end": until,
                        "duration_s": seconds,
                        "effective_seconds": seconds,
                        "rest": True,
                        "valid": True,
                        "value_before": before,
                        "value": self.engine.workload.value,
                        "parameter_version": version,
                        "source": "restored user declaration",
                        "mode": self.mode,
                    },
                    start=checkpoint,
                    end=until,
                    session_id=self._session(),
                )
                payload = {
                    "id": f"declared-rest-{start}-{checkpoint}-{until}",
                    "start": checkpoint,
                    "end": until,
                    "duration_s": seconds,
                    "state": "Rest",
                    "focus": None,
                    "workload": self.engine.workload.value,
                    "workload_stale": False,
                    "observation": "Missing",
                    "source": "User declaration",
                    "parameter_version": version,
                    "session_id": self._session(),
                    "mode": self.mode,
                }
                self.store.save_event(
                    "StateSnapshot", payload, start=checkpoint, end=until, session_id=self._session()
                )
                self._history.append(self._history_row(payload))
            if start <= now < end:
                self.engine.pause(False)
                self.engine.rest_started, self.engine.rest_until = start, end
                self.engine._rest_remaining_s = end - now
                self._rest_id = str(start)
                self._timed_rest = {**record, "last_utc": now, "last_mono": self.engine.clock.monotonic()}
                self.collector.pause(True)
                self._update(
                    state="Rest",
                    focus=None,
                    rest_active=True,
                    source="User declaration",
                    evidence=["Continuing a previously declared timed rest; no activity is read."],
                )
                self._save_rest_checkpoint(now)
            else:
                self.settings["declared_rest"] = None
                self._save_rest_checkpoint(until)
                self.engine.workload.stale = now > end or now < start
                if record.get("resume_collection") and self.settings["consent"] and not self._privacy_paused:
                    self.collector.start(request_permission=False)
        except (ValueError, TypeError, KeyError):
            self.settings["declared_rest"] = None
            self.store.save_settings(self.settings)
            self.engine.workload.stale = True

    def _advance_declared_rest(self, force=False, finish=True):
        timer = self._timed_rest
        if timer is None:
            return
        mono, now = self.engine.clock.monotonic(), self.engine.clock.utc()
        seconds = min(
            max(0, mono - timer["last_mono"]),
            max(0, now - timer["last_utc"]),
            max(0, timer["end"] - timer["last_utc"]),
        )
        if seconds < 10 and not force and now < timer["end"]:
            return
        while seconds >= 10 or seconds > 0 and (force or now >= timer["end"]):
            elapsed = min(10, seconds)
            start = timer["last_utc"]
            bucket = ActivityBucket(
                start,
                start + elapsed,
                duration_s=elapsed,
                keyboard=None,
                clicks=None,
                scroll=None,
                pointer=None,
                idle_s=None,
                app_switches=None,
                observation="Missing",
                missing_reason="Explicit timed rest; no activity was read",
                session_id=self._session(),
                mode=self.mode,
            )
            self._process_bucket(bucket)
            timer["last_utc"] += elapsed
            timer["last_mono"] += elapsed
            seconds -= elapsed
        self._save_rest_checkpoint(timer["last_utc"])
        self._update(workload=self.engine.workload.value, workload_stale=self.engine.workload.stale)
        if finish and now >= timer["end"]:
            self._finish_declared_rest(advance=False)

    def _finish_declared_rest(self, resume=True, advance=True):
        timer = self._timed_rest
        if timer is None:
            return
        if advance:
            self._advance_declared_rest(force=True, finish=False)
        at = self.engine.clock.utc()
        self.engine.end_rest(at)
        self._timed_rest = None
        self._rest_id = None
        self.settings["declared_rest"] = None
        if (
            resume
            and timer.get("resume_collection")
            and self.settings["consent"]
            and not self._privacy_paused
        ):
            self.collector.stop()
            self.collector.start(request_permission=False)
            self.engine.pause(False)
        else:
            self.engine.pause(True)
        self._save_rest_checkpoint(at)
        self._persist_engine_events()
        self._update(
            state="Unknown",
            focus=None,
            rest_active=False,
            observed_state="Missing" if resume else "Paused",
            workload=self.engine.workload.value,
            workload_stale=self.engine.workload.stale,
            source="Insufficient data",
            evidence=["The declared rest ended. Waiting for new observations."],
        )

    def _now(self):
        if self.mode == "synthetic-demo" and self.engine.latest:
            return self.engine.latest.end
        return self.engine.clock.utc()

    def _handle(self, name, args):
        diagnostics = getattr(self, "_diagnostics", None)
        if diagnostics is not None:
            diagnostics.action(name)
        now = self._now()
        if name == "close":
            if self._timed_rest:
                self._advance_declared_rest(force=True, finish=False)
            self.settings.update(last_workload=self.engine.workload.value)
            self.store.save_settings(self.settings)
            self._stop.set()
        elif name == "interaction":
            if hasattr(self.collector, "suppress_interaction"):
                self.collector.suppress_interaction()
            return  # Input suppression never performs SQL reads or a history refresh.
        elif name == "consent":
            if self._timed_rest:
                self._finish_declared_rest(resume=False)
            self._privacy_paused = not bool(args["allowed"])
            self.settings["privacy_paused"] = self._privacy_paused
            self.settings.update(
                consent=bool(args["allowed"]),
                profile=args.get("profile", self.settings["profile"]),
                character=args.get("character", self.settings["character"]),
            )
            self.store.save_settings(self.settings)
            from focuspet.models.prior import GenericPrior

            self.engine.prior = GenericPrior(self.settings["profile"])
            if self.settings["consent"]:
                self.collector.stop()
                self.collector.start(request_permission=True)
                self.engine.pause(False)
            else:
                self.collector.stop()
                self.engine.pause(True)
            self.store.save_event("ConsentEvent", {"allowed": self.settings["consent"]}, start=now)
        elif name == "demo":
            self._cancel_job()
            self.collector.stop()
            self.store.close()
            self.mode = "synthetic-demo"
            self._history.clear()
            self._initialize()
        elif name in ("pause", "resume"):
            paused = name == "pause"
            if not paused and not self.settings["consent"] and self.mode == "real":
                self._update(status="Authorize collection in Settings before resuming.")
                return
            if self._timed_rest:
                self._finish_declared_rest(resume=False)
            self._privacy_paused = paused
            self.settings["privacy_paused"] = paused
            if paused:
                self.collector.pause(True)
            else:
                self.collector.stop()
                self.collector.start(request_permission=False)
            self.engine.pause(paused)
            self.store.save_event("CollectionGap", {"paused": paused}, start=now)
            self._update(
                state="Unknown",
                observed_state="Paused" if paused else "Missing",
                focus=None,
                workload_stale=True,
                source="Insufficient data",
                evidence=["Collection paused." if paused else "Waiting for new observations."],
            )
        elif name == "rest":
            minutes = float(args.get("minutes", 5))
            if self._timed_rest:
                self._finish_declared_rest(resume=False)
            self.engine.pause(False)
            self.engine.start_rest(minutes, now=now)
            self._rest_id = str(now)
            if self.mode != "synthetic-demo":
                self.collector.pause(True)
                self._timed_rest = {
                    "start": now,
                    "end": now + minutes * 60,
                    "last_utc": now,
                    "last_mono": self.engine.clock.monotonic(),
                    "resume_collection": bool(self.settings["consent"] and not self._privacy_paused),
                }
                self.settings["declared_rest"] = {
                    k: v for k, v in self._timed_rest.items() if k != "last_mono"
                }
            self._update(
                state="Rest",
                focus=None,
                rest_active=True,
                source="User declaration",
                evidence=["A timed rest was explicitly started."],
            )
        elif name == "end_rest":
            if self._timed_rest:
                self._finish_declared_rest()
            else:
                self.engine.end_rest(now)
                self._rest_id = None
                self._update(state="Unknown", focus=None, rest_active=False)
        elif name == "reset_cycle":
            if self._timed_rest:
                self._finish_declared_rest()
            import uuid

            if hasattr(self.collector, "session_id"):
                self.collector.session_id = uuid.uuid4().hex
            self.engine.reset_workload(now)
            self.store.save_event("CycleStart", {"explicit": True}, start=now, session_id=self._session())
            self._update(workload=0.0, workload_stale=False, restart_gap=False)
        elif name == "continue_cycle":
            self._update(restart_gap=False)
            self.store.save_event("CycleContinued", {"load_preserved": self.engine.workload.value}, start=now)
        elif name == "confirm_recent_rest":
            minutes = float(args.get("minutes", 5))
            last_seen = float(self.settings.get("last_seen", now))
            seconds = min(minutes * 60, max(0, now - last_seen))
            if not 0 < seconds <= 14400:
                raise ValueError("Confirm only a bounded interval within the unobserved gap")
            before = self.engine.workload.value
            self.engine.workload.advance(seconds, rest=True)
            self.store.save_event(
                "RestSession",
                {"start": now - seconds, "end": now, "source": "retrospective user confirmation"},
                start=now - seconds,
                end=now,
            )
            self.store.save_event(
                "WorkloadStep",
                {
                    "start": now - seconds,
                    "end": now,
                    "duration_s": seconds,
                    "effective_seconds": seconds,
                    "rest": True,
                    "valid": True,
                    "value_before": before,
                    "value": self.engine.workload.value,
                    "parameter_version": self.engine.workload.parameters.version,
                },
                start=now - seconds,
                end=now,
            )
            self._update(restart_gap=False, workload=self.engine.workload.value, workload_stale=False)
        elif name == "feedback":
            self._cancel_job()
            end = float(args.get("end", now))
            start = float(args.get("start", end - float(args.get("minutes", 5)) * 60))
            if end > now + 1:
                raise ValueError("Feedback target must be an ended interval")
            self.store.add_state_feedback(start, end, args["label"], revises=args.get("revision_of"))
            if self._query and self._query["target_start"] < end and self._query["target_end"] > start:
                self.store.save_event(
                    "QueryDismissed",
                    {
                        "query_id": self._query["id"],
                        "response": None,
                        "reason": "The interval was manually labeled",
                    },
                    start=now,
                )
                self._query = None
            self._reload_versions()
            self._update(status="Feedback saved; original prediction preserved.")
            self._review_learning()
        elif name == "retract_feedback":
            self._cancel_job()
            self.store.retract_feedback(args["id"])
            self._model_registry = None
            self._parameter_registry = None
            self.engine.predictor = None
            self.engine.workload.parameters = WorkloadParameters()
            self._review_learning()
        elif name == "load_feedback":
            self.store.add_load_feedback(
                args["answer"], at=now, session_id=self._session(), rest_id=self._rest_id
            )
            if self._parameter_registry:
                from focuspet.optimization import dataset_from_events

                dataset = dataset_from_events(self.store.get_events(limit=100000), mode=self.mode)
                self._parameter_registry.evaluate_shadow(
                    dataset["trajectory"], dataset["feedback"], now=time.time()
                )
                self.engine.workload.parameters = WorkloadParameters(**self._parameter_registry.current())
            self._update(status="Rest preference saved separately from work-state labels.")
        elif name in ("query_answer", "query_skip"):
            if self._query and args.get("query_id") == self._query["id"]:
                if name == "query_answer":
                    self._cancel_job()
                    mechanism = self._query.get("selection", self._query.get("mechanism", "active"))
                    self.store.add_state_feedback(
                        self._query["target_start"],
                        self._query["target_end"],
                        args["label"],
                        source="audit"
                        if "audit" in str(mechanism) or self._query.get("kind") == "audit"
                        else "active-query",
                    )
                self.store.save_event(
                    "QueryResponse",
                    {
                        "query_id": args["query_id"],
                        "answer": args.get("label") if name == "query_answer" else None,
                        "outcome": "answered" if name == "query_answer" else "skipped",
                    },
                    start=now,
                )
                self._query = None
                self._reload_versions()
                self._review_learning()
        elif name == "settings":
            values = dict(args["values"])
            if "categories" in values:
                values["categories"] = validate_categories(values["categories"])
            self.settings.update({k: copy.deepcopy(v) for k, v in values.items() if k in DEFAULT_SETTINGS})
            if "profile" in values:
                from focuspet.models.prior import GenericPrior

                self.engine.prior = GenericPrior(self.settings["profile"])
                if self._model_registry:
                    self.engine.predictor = self._model_registry.active_predictor(self.settings["profile"])
            if "categories" in values and hasattr(self.collector, "categories"):
                from focuspet.collectors.macos import DEFAULT_CATEGORIES

                self.collector.categories = {**DEFAULT_CATEGORIES, **values["categories"]}
            if "consent" in values and not values["consent"]:
                if self._timed_rest:
                    self._finish_declared_rest(resume=False)
                self._privacy_paused = True
                self.settings["privacy_paused"] = True
                self.collector.stop()
                self.engine.pause(True)
            if values.get("startup"):
                from focuspet.startup import set_startup

                set_startup(True)
            elif "startup" in values:
                from focuspet.startup import set_startup

                set_startup(False)
            self.store.save_settings(self.settings)
            if {"bucket_days", "feature_days", "feedback_days", "model_days"}.intersection(values):
                self._apply_retention()
        elif name == "snooze":
            self.policy.snooze(int(args.get("minutes", 15)), now=now)
            self.settings["policy"] = self.policy.to_dict()
            self.store.save_settings(self.settings)
        elif name == "classify_app":
            validate_categories({args["app_id"]: args["category"]})
            self.settings["categories"][args["app_id"]] = args["category"]
            if hasattr(self.collector, "categories"):
                self.collector.categories[args["app_id"]] = args["category"]
            self.store.save_settings(self.settings)
        elif name in ("train", "calibrate"):
            self._start_job(name)
        elif name == "cancel_job":
            self._cancel_job()
        elif name == "rollback_model":
            if self._model_registry:
                self._model_registry.rollback()
                self._reload_versions()
        elif name == "rollback_parameters":
            if self._parameter_registry:
                self._parameter_registry.rollback()
                self._reload_versions()
        elif name == "export":
            self.store.export(Path(args["path"]))
            self._update(status="Local export saved. No upload was performed.")
        elif name in ("delete_data", "delete_range"):
            if not args.get("confirm"):
                return
            self._cancel_job()
            self.collector.pause(True)
            self._timed_rest = None
            self._privacy_paused = True
            self._rest_id = None
            if name == "delete_range":
                self.store.delete_range(float(args["start"]), float(args["end"]))
            else:
                self.store.delete_all()
                self.settings = copy.deepcopy(DEFAULT_SETTINGS)
            self.settings.update(last_workload=0.0, last_seen=None, declared_rest=None, privacy_paused=True)
            self._history.clear()
            self._restore_history()
            self._feedback.clear()
            self._recent.clear()
            self._asked_features.clear()
            self._model_registry = self._parameter_registry = None
            self.engine = Engine(profile=self.settings["profile"], mode=self.mode, clock=self.engine.clock)
            self.engine.pause(True)
            self._query = None
            self.policy = ReminderManager(timezone=self.settings.get("timezone", "UTC"))
            self.settings.pop("policy", None)
            # Save only reset controls, not deleted data or stale runtime load.
            self.store.save_settings(self.settings)
            self._update(
                state="Unknown",
                observed_state="Paused",
                focus=None,
                workload=0.0,
                workload_stale=True,
                source="Insufficient data",
                rest_active=False,
                restart_gap=False,
                evidence=["Data deleted. Collection is paused."],
            )
        if name in (
            "reset_cycle",
            "confirm_recent_rest",
            "continue_cycle",
            "rest",
            "end_rest",
            "pause",
            "resume",
        ):
            self.settings.update(last_workload=self.engine.workload.value, last_seen=now)
            self.store.save_settings(self.settings)
        self._persist_engine_events()
        self._apply_policy()
        self._refresh()

    def _review_learning(self):
        if self._model_registry:
            records = self.store.training_records()
            self._model_registry.evaluate_shadow(records, now=time.time(), profile=self.settings["profile"])
            self._model_registry.assess_active(records, now=time.time(), profile=self.settings["profile"])
            self.engine.predictor = self._model_registry.active_predictor(self.settings["profile"])
        self._refresh()
        if self._cache["learning"].get("eligible"):
            self._start_job("train", automatic=True)

    def _start_job(self, kind, automatic=False):
        data: list[dict] | Path
        if self._job is not None:
            self._update(status="A learning job is already running.")
            return
        if kind == "train":
            data = self.store.training_records()
            output = self.store.model_dir
        else:
            from focuspet.optimization import dataset_from_events

            data = self.store.model_dir / "calibration-input.json"
            data.write_text(
                json.dumps(dataset_from_events(self.store.get_events(limit=100000), mode=self.mode))
            )
            self._job_temp = data
            output = self.store.model_dir / "parameters"
        context = mp.get_context("spawn")
        self._cancel = context.Event()
        self._results = context.Queue(1)
        self._job = context.Process(
            target=_job_entry,
            args=(
                kind,
                data,
                output,
                self.mode,
                self._cancel,
                self._results,
                automatic,
                self.settings["profile"],
            ),
            daemon=True,
        )
        self._job.start()
        diagnostics = getattr(self, "_diagnostics", None)
        if diagnostics is not None:
            diagnostics.training_generation += 1
        self._job_kind = kind
        self._update(
            job_running=True,
            status="Training candidate…" if kind == "train" else "Comparing load parameters…",
        )

    def _poll_job(self):
        if self._job is None:
            return
        try:
            result = self._results.get_nowait()
        except queue.Empty:
            if self._job.is_alive():
                return
            result = {"status": "failed", "reason": "Worker stopped before returning a result"}
        self._job.join(1)
        self._job = None
        self._reload_versions()
        if self._job_temp:
            self._job_temp.unlink(missing_ok=True)
            self._job_temp = None
        result = safe_job_result(result)
        self.store.save_event("ModelVersion" if self._job_kind == "train" else "ParameterVersion", result)
        self._update(job_running=False, status=result.get("status", "Finished"), last_job=result)
        self._refresh()

    def _cancel_job(self):
        if self._job is None:
            return
        self._cancel.set()
        self._job.join(1)
        if self._job.is_alive():
            self._job.terminate()
            self._job.join(2)
        self._job = None
        if self._job_temp:
            self._job_temp.unlink(missing_ok=True)
            self._job_temp = None
        self._update(job_running=False, status="Learning job cancelled. Active versions retained.")
