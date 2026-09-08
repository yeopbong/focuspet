"""Replay orchestration calls the same domain engine and reminder policy as live use."""

from __future__ import annotations

import json
import copy
import hashlib
import math
from collections import Counter, defaultdict
from dataclasses import fields
from pathlib import Path
from typing import Any

from focuspet.collectors.replay import ReplayCollector, load_scenario
from focuspet.domain import ActivityBucket, Engine, FakeClock, Workload, WorkloadParameters
from focuspet.policy import ReminderManager


def replay_scenario(
    scenario="workday",
    seed=7,
    profile="Mixed",
    parameters=None,
    predictor=None,
    reevaluation=False,
    correction=False,
) -> dict:
    data = load_scenario(scenario, seed) if not isinstance(scenario, dict) else scenario
    collector = ReplayCollector(data)
    clock = FakeClock(data["buckets"][0]["start"] if data["buckets"] else 0)
    engine = Engine(
        profile=profile,
        clock=clock,
        predictor=predictor,
        mode=data["mode"],
        workload=Workload(parameters=parameters or WorkloadParameters()),
    )
    policy = ReminderManager(seed=seed)
    collector.start()
    snapshots = []
    timeline: Counter[str] = Counter()
    declarations = list(data.get("declarations", []))
    done = set()
    elapsed = 0.0
    correction_at = min(3000.0, sum(b["duration_s"] for b in data["buckets"]) * 0.5)
    for _ in data["buckets"]:
        bucket = collector.sample()
        if bucket is None:
            continue
        clock.wall = bucket.start
        for i, d in enumerate(declarations):
            if i not in done and bucket.start >= d["at"]:
                if d["kind"] == "rest":
                    engine.start_rest(d["minutes"], now=d["at"])
                done.add(i)
        if correction and elapsed <= correction_at < elapsed + bucket.duration_s:
            engine.declare_state("Normal", minutes=5, now=bucket.start)
        snapshot = engine.process(bucket)
        elapsed += bucket.duration_s
        clock.advance(bucket.duration_s, wall_seconds=bucket.end - bucket.start)
        if snapshot:
            row = snapshot.to_dict()
            row["elapsed_s"] = elapsed
            # Replay records policy decisions, but never delivers a historical notification.
            notification = policy.consider(snapshot, now=bucket.end, replay=True)
            row["notification"] = notification.to_dict() if notification else None
            snapshots.append(row)
        observed = "Missing" if bucket.observation in ("Missing", "Paused", "Locked") else bucket.observation
        timeline[observed] += bucket.duration_s
    return {
        "schema": "trajectory-v1",
        "mode": data["mode"],
        "name": data.get("name", "archive"),
        "seed": data.get("seed", seed),
        "description": data.get("description", ""),
        "replay_kind": "re-evaluation" if reevaluation or correction else "historical reproduction",
        "correction": {
            "at_elapsed_s": correction_at,
            "label": "Normal",
            "minutes": 5,
            "source": "synthetic user declaration",
        }
        if correction
        else None,
        "engine_version": "0.1.0",
        "prior_version": engine.prior.version,
        "parameter_version": engine.workload.parameters.version,
        "duration_s": elapsed,
        "observation_seconds": dict(timeline),
        "snapshots": snapshots,
        "declarations": declarations,
        "claims": "Synthetic simulation; no measured personal improvement or physiological diagnosis.",
    }


def save_trajectory(trajectory: dict, output: str | Path):
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(trajectory, indent=2, allow_nan=False))
    return path


class ArchiveReplayError(ValueError):
    """An actionable safe error code; messages contain no personal paths or event content."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.safe_message = message
        super().__init__(f"{code}: {message}")


def load_replay_input(source: str | Path | dict, seed: int = 7) -> dict:
    if isinstance(source, dict):
        data = copy.deepcopy(source)
    elif Path(source).is_file():
        data = json.loads(Path(source).read_text())
    else:
        data = load_scenario(source, seed)
    if data.get("schema") not in ("export-v1", "scenario-v1"):
        raise ArchiveReplayError(
            "UNSUPPORTED_ARCHIVE_SCHEMA", "Use a Focus Pet export-v1 or scenario-v1 JSON file."
        )
    if data.get("mode") not in ("real", "synthetic-demo", "test"):
        raise ArchiveReplayError("INVALID_ARCHIVE_MODE", "The archive must declare one isolated data mode.")
    return data


def _events(data: dict) -> list[dict]:
    events = data.get("events", [])
    if not isinstance(events, list):
        raise ArchiveReplayError("INVALID_ARCHIVE_EVENTS", "The exported event list is invalid.")
    for event in events:
        if event.get("mode", data["mode"]) != data["mode"] or not isinstance(event.get("payload"), dict):
            raise ArchiveReplayError(
                "ARCHIVE_MODE_MISMATCH", "Events from different data modes cannot be replayed together."
            )
    for feature in data.get("features", []):
        if feature.get("mode", data["mode"]) != data["mode"]:
            raise ArchiveReplayError(
                "ARCHIVE_MODE_MISMATCH", "Feature data does not belong to the archive mode."
            )
    if events and all(isinstance(event.get("sequence"), int) for event in events):
        if len({event["sequence"] for event in events}) != len(events):
            raise ArchiveReplayError("AMBIGUOUS_EVENT_ORDER", "Exported sequence numbers must be unique.")
        return sorted(events, key=lambda event: event["sequence"])
    # Older v1 exports lacked ingestion order. Their existing order may support
    # ordinary monotone histories, but must never pretend to resolve clock rollback.
    return list(events)


def _archive_buckets(events: list[dict], mode: str) -> list[ActivityBucket]:
    names = {field.name for field in fields(ActivityBucket)}
    result = []
    seen = set()
    for event in events:
        if event["kind"] != "ActivityBucket":
            continue
        payload = event["payload"]
        if payload.get("mode", mode) != mode:
            raise ArchiveReplayError("ARCHIVE_MODE_MISMATCH", "An activity bucket has a different data mode.")
        bucket = ActivityBucket(**{k: v for k, v in payload.items() if k in names})
        if bucket.id not in seen:
            result.append(bucket)
            seen.add(bucket.id)
    return result


def _recorded_snapshots(data: dict, events: list[dict]) -> list[dict]:
    feature_map = {feature["id"]: feature for feature in data.get("features", [])}
    result = []
    seen = set()
    elapsed = 0.0
    for event in events:
        if event["kind"] != "StateSnapshot" or event["id"] in seen:
            continue
        row = copy.deepcopy(event["payload"])
        if row.get("mode", data["mode"]) != data["mode"]:
            raise ArchiveReplayError("ARCHIVE_MODE_MISMATCH", "A snapshot has a different data mode.")
        seen.add(event["id"])
        elapsed += float(row.get("duration_s", max(0, row["end"] - row["start"])))
        row["elapsed_s"] = elapsed
        row["feature_available"] = row.get("feature_id") in feature_map
        if row["feature_available"]:
            row["feature"] = copy.deepcopy(feature_map[row["feature_id"]])
        # Preserve original prediction IDs, versions and numerical values exactly.
        result.append(row)
    return result


def _history_versions(events: list[dict], snapshots: list[dict]) -> tuple[set[str], set[str]]:
    models = {row.get("prediction", {}).get("model_version", "generic-prior-v1") for row in snapshots}
    parameters = {row.get("parameter_version", "defaults-v1") for row in snapshots}
    for event in events:
        payload = event["payload"]
        if event["kind"] == "PredictionEvent":
            models.add(payload.get("model_version", "generic-prior-v1"))
        elif event["kind"] == "WorkloadStep":
            parameters.add(payload.get("parameter_version", "defaults-v1"))
    return models, parameters


def _load_historical_models(versions: set[str], root: Path | None, mode: str, profile: str) -> dict:
    from focuspet.models.prior import GenericPrior

    supported_prior = GenericPrior(profile).version
    result: dict[str, Any] = {supported_prior: None}
    for version in versions:
        if version == supported_prior:
            continue
        if (
            not version.startswith("personal-")
            or root is None
            or not (root / "model-registry.json").is_file()
        ):
            raise ArchiveReplayError(
                "MISSING_HISTORICAL_MODEL",
                "The historical model is unavailable. Supply its original --model-dir, use --recorded playback, or explicitly --reevaluate.",
            )
        try:
            from focuspet.learning.registry import ModelRegistry
            from focuspet.models.personal import PersonalPredictor
            from focuspet.features import FEATURE_NAMES

            registry = ModelRegistry(root, mode)
            result[version] = PersonalPredictor(registry._load(version, list(FEATURE_NAMES)), profile)
        except (ValueError, OSError, KeyError, TypeError):
            raise ArchiveReplayError(
                "MISSING_HISTORICAL_MODEL",
                "A referenced historical model is missing, invalid or incompatible. Restore its original local artifact or use --recorded playback.",
            ) from None
    return result


def _load_historical_parameters(
    versions: set[str], root: Path | None, mode: str, supplied: WorkloadParameters | None
) -> dict:
    result = {"defaults-v1": WorkloadParameters()}
    if supplied:
        result[supplied.version] = supplied
    registry = None
    if root is not None and (root / "parameter-registry.json").is_file():
        try:
            from focuspet.models.personal import canonical

            raw = json.loads((root / "parameter-registry.json").read_text())
            checksum = raw.pop("checksum", None)
            if (
                raw.get("mode") != mode
                or raw.get("format") != "parameters-v1"
                or hashlib.sha256(canonical(raw)).hexdigest() != checksum
            ):
                raise ValueError("Invalid registry")
            registry = raw
        except (OSError, ValueError, KeyError, TypeError):
            raise ArchiveReplayError(
                "INVALID_HISTORICAL_PARAMETERS",
                "The historical parameter registry failed its mode, format or integrity check.",
            ) from None
    for version in versions:
        if version in result:
            continue
        if registry and version in registry.get("versions", {}):
            try:
                p = registry["versions"][version]["parameters"]
                result[version] = WorkloadParameters(
                    a_user=p["a_user"], tau_user=p["tau_user"], version=version
                )
                continue
            except (KeyError, TypeError, ValueError):
                pass
        raise ArchiveReplayError(
            "MISSING_HISTORICAL_PARAMETERS",
            "A referenced parameter version is unavailable. Supply its original --parameter-dir or matching --parameters JSON; use --recorded to view saved values.",
        )
    return result


def replay_export(
    data: dict,
    *,
    seed: int = 7,
    profile: str | None = None,
    parameters: WorkloadParameters | None = None,
    predictor=None,
    reevaluation: bool = False,
    recompute_history: bool = False,
    model_dir: Path | None = None,
    parameter_dir: Path | None = None,
) -> dict:
    """Replay an export without using later corrections as historical feature input.

    Default: immutable recorded snapshots. Re-evaluation: the current Engine with
    explicit selected versions. Strict history: actual historical artifacts and a
    numerical cross-check against retained snapshots; unavailable context is an error.
    """
    if data.get("schema") != "export-v1":
        raise ArchiveReplayError(
            "UNSUPPORTED_ARCHIVE_SCHEMA", "Historical archive replay requires export-v1."
        )
    if reevaluation and recompute_history:
        raise ArchiveReplayError(
            "CONFLICTING_REPLAY_MODES", "Choose historical recomputation or re-evaluation."
        )
    data = load_replay_input(data)
    events = _events(data)
    recorded = _recorded_snapshots(data, events)
    buckets = _archive_buckets(events, data["mode"])
    models, parameter_versions = _history_versions(events, recorded)
    digest = hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    base = {
        "schema": "trajectory-v1",
        "source_schema": "export-v1",
        "mode": data["mode"],
        "name": "exported-history",
        "seed": seed,
        "source_sha256": digest,
        "historical_model_versions": sorted(models),
        "historical_parameter_versions": sorted(parameter_versions),
        "feedback_used_as_input": False,
        "notifications_delivered": False,
        "source_mutated": False,
        "claims": "Local exported observations; no effectiveness or physiological claim.",
    }
    if not reevaluation and not recompute_history:
        if not recorded:
            raise ArchiveReplayError(
                "NO_RECORDED_SNAPSHOTS",
                "This export contains no saved snapshots. Re-export the desired interval or use --reevaluate if activity buckets remain.",
            )
        total = sum(float(row.get("duration_s", max(0, row["end"] - row["start"]))) for row in recorded)
        observed: dict[str, float] = defaultdict(float)
        for row in recorded:
            state = row.get("observation", "Missing")
            observed[state] += float(row.get("duration_s", max(0, row["end"] - row["start"])))
        return {
            **base,
            "replay_kind": "recorded historical playback",
            "computation": "none; original snapshots preserved",
            "duration_s": total,
            "duration_basis": "disjoint recorded snapshot durations",
            "observation_seconds": dict(observed),
            "snapshots": recorded,
            "missing_seconds": sum(observed.get(k, 0) for k in ("Missing", "Paused", "Locked")),
            "limitations": [
                "This mode displays saved results; it does not re-run the historical classifier.",
                "State labels on snapshot intervals are the saved estimates; deleted fine-grained data cannot be reconstructed.",
            ],
        }
    if not buckets:
        raise ArchiveReplayError(
            "MISSING_ACTIVITY_BUCKETS",
            "Raw aggregate buckets have expired or were omitted. Recorded playback remains available; re-export a retained interval for core recomputation.",
        )
    if any(feature.get("schema", "features-v1") != "features-v1" for feature in data.get("features", [])):
        raise ArchiveReplayError(
            "HISTORICAL_SCHEMA_MISMATCH",
            "This feature schema is not supported by the installed core. Use --recorded to inspect saved values.",
        )
    has_order = all("sequence" in event for event in events)
    if not has_order and any(b.start < a.end - 1e-6 for a, b in zip(buckets, buckets[1:])):
        raise ArchiveReplayError(
            "AMBIGUOUS_EVENT_ORDER",
            "This older export lacks ingestion order for overlapping or rolled-back timestamps. Re-export with the current version, or use --recorded playback.",
        )
    if recompute_history and not profile:
        raise ArchiveReplayError(
            "HISTORICAL_PROFILE_REQUIRED",
            "Exports omit personal settings. Supply the profile used in this history with --profile; the resulting reproduction will be checked against saved values.",
        )
    chosen_profile = profile or "Mixed"
    parameter_root = parameter_dir or (Path(model_dir) / "parameters" if model_dir else None)
    historical_models = (
        _load_historical_models(models, model_dir, data["mode"], chosen_profile) if recompute_history else {}
    )
    historical_parameters = (
        _load_historical_parameters(parameter_versions, parameter_root, data["mode"], parameters)
        if recompute_history
        else {}
    )
    steps = {
        (p.get("session_id", e.get("session_id", "default")), p["start"], p["end"]): p
        for e in events
        if e["kind"] == "WorkloadStep"
        for p in [e["payload"]]
        if p.get("duration_s", 0) > 0
    }
    first = buckets[0]
    initial = float(steps.get((first.session_id, first.start, first.end), {}).get("value_before", 0))
    if recompute_history and not steps:
        raise ArchiveReplayError(
            "HISTORICAL_CONTEXT_UNAVAILABLE",
            "Historical workload steps and their starting load are absent. Use --recorded or explicitly --reevaluate from zero.",
        )
    clock = FakeClock(first.start)
    engine = Engine(
        profile=chosen_profile,
        clock=clock,
        predictor=predictor,
        mode=data["mode"],
        workload=Workload(value=initial, parameters=parameters or WorkloadParameters()),
    )
    policy = ReminderManager(seed=seed)
    bucket_map = {bucket.id: bucket for bucket in buckets}
    # The next emitted PredictionEvent records which model governed that emission.
    # Version transitions are checked against saved components and workload below;
    # an unrecorded mid-window transition cannot silently pass as exact reproduction.
    prediction_events = [(i, e["payload"]) for i, e in enumerate(events) if e["kind"] == "PredictionEvent"]
    snapshots = []
    observed = defaultdict(float)
    elapsed = 0.0
    previous_bucket = None
    used_buckets = set()
    for index, event in enumerate(events):
        p = event["payload"]
        kind = event["kind"]
        if kind == "RestSession":
            start, end = float(p.get("start", event["start"])), float(p.get("end", event["end"]))
            if end > start and 0 < (end - start) / 60 <= 240:
                engine.start_rest((end - start) / 60, now=start)
        elif kind == "RestSessionEnd":
            engine.end_rest(now=float(p.get("at", event["end"])))
        elif kind == "CollectionGap" and "paused" in p:
            engine.pause(bool(p["paused"]))
        elif kind == "WorkloadReset":
            engine.reset_workload(now=float(p.get("at", event["start"])))
        elif kind == "ActivityBucket":
            bucket = bucket_map.get(p.get("id"))
            if bucket is None or bucket.id in used_buckets:
                continue
            used_buckets.add(bucket.id)
            step = steps.get((bucket.session_id, bucket.start, bucket.end))
            if recompute_history:
                if step is None:
                    raise ArchiveReplayError(
                        "HISTORICAL_CONTEXT_UNAVAILABLE",
                        "A retained bucket lacks its historical workload step. Re-export the complete interval or use --recorded.",
                    )
                parameter_version = step.get("parameter_version", "defaults-v1")
                engine.workload.parameters = historical_parameters[parameter_version]
                next_prediction = next(
                    (q for i, q in prediction_events if i > index and q.get("feature_id") is not None), None
                )
                model_version = (
                    next_prediction.get("model_version", "generic-prior-v1")
                    if next_prediction
                    else "generic-prior-v1"
                )
                engine.predictor = historical_models[model_version]
            clock.wall = bucket.start
            snapshot = engine.process(bucket)
            elapsed += bucket.duration_s
            clock.advance(bucket.duration_s, wall_seconds=bucket.end - bucket.start)
            observation = bucket.observation
            if engine.paused or (
                previous_bucket is not None
                and bucket.session_id == previous_bucket.session_id
                and bucket.start < previous_bucket.end - 1e-6
            ):
                observation = "Paused" if engine.paused else "Missing"
            observed[observation] += bucket.duration_s
            previous_bucket = bucket
            if recompute_history:
                assert step is not None
                if not math.isclose(engine.workload.value, float(step["value"]), abs_tol=1e-7):
                    raise ArchiveReplayError(
                        "HISTORY_REPRODUCTION_MISMATCH",
                        "Historical workload could not be reproduced with the supplied artifacts/profile and retained context. Saved values remain intact; use --recorded or --reevaluate.",
                    )
            if snapshot:
                row = snapshot.to_dict()
                row["elapsed_s"] = elapsed
                notification = policy.consider(snapshot, now=bucket.end, replay=True)
                row["notification"] = notification.to_dict() if notification else None
                snapshots.append(row)
    if recompute_history:
        saved = {(row.get("session_id", "default"), row["end"]): row for row in recorded}
        if not saved or len(saved) != len(snapshots):
            raise ArchiveReplayError(
                "HISTORICAL_CONTEXT_UNAVAILABLE",
                "The retained snapshots and activity buckets do not cover the same complete emissions. Use --recorded or --reevaluate.",
            )
        for row in snapshots:
            old = saved.get((row["session_id"], row["end"]))
            if (
                old is None
                or row["state"] != old["state"]
                or row["parameter_version"] != old["parameter_version"]
            ):
                raise ArchiveReplayError(
                    "HISTORY_REPRODUCTION_MISMATCH",
                    "State or parameter history differs under the supplied historical context. Use --recorded or explicitly --reevaluate.",
                )
            a, b = row.get("focus"), old.get("focus")
            if (a is None) != (b is None) or (
                a is not None and b is not None and not math.isclose(a, b, abs_tol=1e-7)
            ):
                raise ArchiveReplayError(
                    "HISTORY_REPRODUCTION_MISMATCH",
                    "Focus smoothing differs because the original initial context or profile is unavailable. Use --recorded or --reevaluate.",
                )
            if row["prediction"]["model_version"] != old["prediction"]["model_version"] or any(
                not math.isclose(
                    row["prediction"]["components"][c], old["prediction"]["components"][c], abs_tol=1e-7
                )
                for c in ("Focused", "Normal", "Distracted")
            ):
                raise ArchiveReplayError(
                    "HISTORY_REPRODUCTION_MISMATCH",
                    "The classifier output does not match the recorded historical version. No current model was substituted.",
                )
    return {
        **base,
        "replay_kind": "verified historical recomputation" if recompute_history else "re-evaluation",
        "computation": "shared production Engine, FeatureBuilder, classifier and Workload",
        "duration_s": elapsed,
        "duration_basis": "disjoint aggregate bucket durations",
        "observation_seconds": dict(observed),
        "snapshots": snapshots,
        "missing_seconds": sum(observed.get(k, 0) for k in ("Missing", "Paused", "Locked")),
        "initial_workload": initial,
        "selected_profile": chosen_profile,
        "parameter_version": engine.workload.parameters.version,
        "limitations": []
        if recompute_history
        else [
            "New analysis starts with retained buckets and the recorded starting load; earlier feature and smoothing context may be absent.",
            "Later state/load feedback is displayed only in the source archive and never fed into feature extraction or historical labels.",
            "Replayed policy decisions are suppressed and do not measure reminder effectiveness.",
        ],
    }
