"""Optuna search over frozen state history, with independent self-report evidence."""
from __future__ import annotations

import hashlib
import json
import math
import threading
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from focuspet.learning.data import utc_day
from focuspet.learning.control import check_cancel
from focuspet.models.personal import atomic_json, canonical

DEFAULTS = {"a_user": 1.0, "tau_user": 12.0}
REGULARIZATION = .05
LINK_WIDTH = 20.0
SEARCH_BUDGET = 64


def clean_feedback(feedback: list[dict]) -> list[dict]:
    newest = {}
    for f in sorted(feedback, key=lambda x: x.get("labeled_at", x["at"])):
        # Reports within one minute in the same work segment are one observation.
        identity = (str(f["session_id"]), int(float(f["at"]) // 60))
        newest[identity] = dict(f)
    return [f for f in sorted(newest.values(), key=lambda x: x["at"])
            if f.get("answer") in ("Yes", "No") and not f.get("withdrawn")
            and f.get("source", "user") in {"user", "audit", "synthetic-oracle"}]


def calibration_status(feedback: list[dict]) -> dict:
    clean = clean_feedback(feedback)
    counts = {a: sum(f["answer"] == a for f in clean) for a in ("Yes", "No")}
    days = len({utc_day(f["at"]) for f in clean})
    rests: dict[str, set[str]] = defaultdict(set)
    for f in clean:
        if f.get("rest_id") and f.get("rest_phase") in {"before", "after"}:
            rests[str(f["rest_id"])].add(f["rest_phase"])
    pairs = sum(phases == {"before", "after"} for phases in rests.values())
    missing = []
    if len(clean) < 20:
        missing.append(f"{20-len(clean)} more independent explicit self-reports")
    if days < 3:
        missing.append(f"{3-days} more days")
    for answer, count in counts.items():
        if count < 5:
            missing.append(f"{5-count} more {answer} reports")
    return {"eligible": not missing, "reports": len(clean), "days": days, "answers": counts,
            "paired_rest_segments": pairs, "learn_tau": pairs >= 5, "missing": missing}


def replay_load(trajectory: list[dict], parameters: dict[str, float]) -> np.ndarray:
    """Use the same domain dynamics as live processing; history is immutable input."""
    from focuspet.domain import Workload, WorkloadParameters
    initial = float(trajectory[0].get("initial_value", 0)) if trajectory else 0
    load = Workload(value=initial, parameters=WorkloadParameters(a_user=parameters["a_user"],
                                                               tau_user=parameters["tau_user"]))
    values = []
    for step in trajectory:
        if step.get("reset"):
            load.value = 0
        load.advance(float(step["duration_s"]), components=step.get("components"),
                     rest=bool(step.get("rest", False)), valid=bool(step.get("valid", True)))
        values.append(load.value)
    return np.asarray(values)


def objective_loss(trajectory: list[dict], feedback: list[dict], parameters: dict[str, float],
                   regularization: float = REGULARIZATION) -> float:
    if not feedback:
        raise ValueError("No explicit calibration labels")
    loads = replay_load(trajectory, parameters)
    losses: dict[str, list[float]] = defaultdict(list)
    for f in feedback:
        index = int(f["step_index"])
        if index < 0 or index >= len(loads) or trajectory[index]["end"] > f["at"]:
            raise ValueError("Feedback must reference an already ended frozen trajectory step")
        z = (loads[index] - 100) / LINK_WIDTH
        y = 1.0 if f["answer"] == "Yes" else 0.0
        # Stable BCE on logits avoids overflow in long work cycles.
        loss = float(np.logaddexp(0, z) - y * z)
        losses[str(f["session_id"])].append(loss)
    equal_segment_loss = float(np.mean([np.mean(v) for v in losses.values()]))
    penalty = regularization * sum(math.log(parameters[k] / DEFAULTS[k]) ** 2 for k in DEFAULTS)
    return equal_segment_loss + penalty


def split_feedback(feedback: list[dict]) -> tuple[list[dict], list[dict]]:
    days = sorted({utc_day(f["at"]) for f in feedback})
    if len(days) < 2:
        return [], []
    boundary_day = days[max(1, min(len(days) - 1, int(len(days) * .75)))]
    from datetime import datetime, timezone
    boundary = datetime.fromisoformat(boundary_day).replace(tzinfo=timezone.utc).timestamp()
    sessions: dict[str, list[dict]] = defaultdict(list)
    for f in feedback:
        sessions[str(f["session_id"])].append(f)
    train, validation = [], []
    for group in sessions.values():
        if max(f["at"] for f in group) <= boundary - 300:
            train.extend(group)
        elif min(f["at"] for f in group) >= boundary + 300:
            validation.extend(group)
    return train, validation


def search(trajectory: list[dict], feedback: list[dict], sampler_name: str, seed: int = 7,
           budget: int = SEARCH_BUDGET, learn_tau: bool = True,
           cancel: threading.Event | None = None, trace_path: Path | None = None) -> dict:
    import optuna
    if not 1 <= budget <= SEARCH_BUDGET:
        raise ValueError("Search budget must be between 1 and 64")
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    sampler: optuna.samplers.BaseSampler
    if sampler_name == "random":
        sampler = optuna.samplers.RandomSampler(seed=seed)
    elif sampler_name == "tpe":
        sampler = optuna.samplers.TPESampler(seed=seed, n_startup_trials=10)
    else:
        raise ValueError("Unknown optimizer")
    study = optuna.create_study(direction="minimize", sampler=sampler)
    trials: list[dict[str, Any]] = []
    best = float("inf")
    start = time.perf_counter()
    # ask/tell keeps cancellation explicit before every bounded objective evaluation.
    for _ in range(budget):
        if trace_path is not None and cancel is not None and cancel.is_set():
            atomic_json(trace_path, {"status": "cancelled", "sampler": sampler_name, "seed": seed,
                                     "budget": budget, "trials": trials})
        check_cancel(cancel)
        trial = study.ask()
        params = {"a_user": trial.suggest_float("a_user", .7, 1.3),
                  "tau_user": trial.suggest_float("tau_user", 8, 25) if learn_tau else 12.0}
        trial_start = time.perf_counter()
        try:
            value = objective_loss(trajectory, feedback, params)
            if not math.isfinite(value):
                raise ValueError("Nonfinite objective")
            study.tell(trial, value)
            best = min(best, value)
            status = "complete"
        except (ValueError, OverflowError, FloatingPointError):
            study.tell(trial, state=optuna.trial.TrialState.FAIL)
            value, status = None, "failed-objective"
        trials.append({"number": trial.number, "parameters": params, "loss": value, "status": status,
                       "elapsed_s": time.perf_counter() - trial_start,
                       "best_so_far": best if math.isfinite(best) else None})
        if trace_path is not None:
            atomic_json(trace_path, {"status": "running" if len(trials) < budget else "complete",
                                     "sampler": sampler_name, "seed": seed, "budget": budget, "trials": trials})
    complete = [trial for trial in trials if trial["status"] == "complete"]
    if not complete:
        raise ValueError("All calibration objective evaluations failed")
    selected = min(complete, key=lambda t: t["loss"])
    return {"sampler": sampler_name, "seed": seed, "budget": budget, "trials": trials,
            "parameters": selected["parameters"], "fit_loss": selected["loss"],
            "elapsed_s": time.perf_counter() - start}


class ParameterRegistry:
    def __init__(self, root: Path, mode: str = "real"):
        self.root, self.mode = Path(root), mode
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "parameter-registry.json"
        try:
            self.state = json.loads(self.path.read_text())
            if not isinstance(self.state, dict) or self.state.get("format") != "parameters-v1" \
                    or not isinstance(self.state.get("versions"), dict) \
                    or not isinstance(self.state.get("previous"), list):
                raise ValueError("Parameter registry mismatch")
            recorded_checksum = self.state.pop("checksum", None)
            if recorded_checksum != hashlib.sha256(canonical(self.state)).hexdigest():
                raise ValueError("Parameter registry checksum mismatch")
        except (OSError, ValueError, KeyError, TypeError):
            self.state = {"format": "parameters-v1", "mode": mode, "active": None,
                          "previous": [], "candidate": None, "versions": {}}
        if self.state.get("mode") != mode:
            raise ValueError("Existing parameter directory belongs to a different data mode")

    def _save(self):
        state = dict(self.state)
        state["checksum"] = hashlib.sha256(canonical(self.state)).hexdigest()
        atomic_json(self.path, state)

    def current(self) -> dict:
        active = self.state.get("active")
        if not active:
            return {**DEFAULTS, "version": "defaults-v1"}
        record = self.state["versions"].get(active, {})
        params = record.get("parameters", {})
        if not (isinstance(params.get("a_user"), (int, float)) and
                isinstance(params.get("tau_user"), (int, float)) and
                .7 <= params["a_user"] <= 1.3 and 8 <= params["tau_user"] <= 25):
            self.rollback("invalid-parameters")
            return self.current()
        return {**params, "version": active}

    def active_parameters(self) -> dict:
        return self.current()

    def add_candidate(self, parameters: dict, report: dict, now: float):
        version = "parameters-" + uuid.uuid4().hex[:12]
        current = self.current()
        # Maximum absolute change per activation: 0.1 growth, 3 minutes recovery.
        limited = {"a_user": max(current["a_user"] - .1, min(current["a_user"] + .1, parameters["a_user"])),
                   "tau_user": max(current["tau_user"] - 3, min(current["tau_user"] + 3, parameters["tau_user"]))}
        self.state["versions"][version] = {"parameters": limited, "searched_parameters": parameters,
            "created_at": now, "status": "shadow", "report": report}
        old = self.state.get("candidate")
        if old:
            self.state["versions"][old]["status"] = "superseded"
        self.state["candidate"] = version
        self._save()
        return version

    def rollback(self, reason: str = "user-request") -> dict:
        old = self.state.get("active")
        previous = self.state["previous"]
        self.state["active"] = previous.pop() if previous else None
        if old:
            self.state["versions"].setdefault(old, {}).update(status="rolled-back", rollback_reason=reason)
        self._save()
        return {"status": "rolled-back", "active": self.state["active"]}

    def evaluate_shadow(self, trajectory: list[dict], feedback: list[dict], now: float | None = None) -> dict:
        now = time.time() if now is None else now
        version = self.state.get("candidate")
        if not version:
            return {"status": "no-shadow-candidate"}
        record = self.state["versions"][version]
        used = set(record["report"].get("feedback_ids", []))
        available = [f for f in feedback if f.get("labeled_at", f["at"]) <= now and f["at"] <= now]
        evidence = [f for f in clean_feedback(available) if f.get("mode", self.mode) == self.mode
                    and f["at"] > record["created_at"] + 300 and f["at"] <= now
                    and str(f.get("id")) not in used]
        # Fixed evidence gate chosen before seeing subsequent outcomes.
        if len(evidence) < 10 or len({utc_day(f["at"]) for f in evidence}) < 2 \
                or any(sum(f["answer"] == a for f in evidence) < 3 for a in ("Yes", "No")):
            return {"status": "collecting-shadow-evidence", "reports": len(evidence)}
        current = {k: self.current()[k] for k in DEFAULTS}
        proposed = objective_loss(trajectory, evidence, record["parameters"])
        baseline = objective_loss(trajectory, evidence, current)
        default = objective_loss(trajectory, evidence, DEFAULTS)
        passed = proposed < min(baseline, default) - .005
        record["shadow"] = {"candidate_loss": proposed, "current_loss": baseline,
                            "default_loss": default, "reports": len(evidence), "at": now}
        self.state["candidate"] = None
        if passed:
            if self.state.get("active"):
                self.state["previous"].append(self.state["active"])
            self.state["active"] = version
            record.update(status="active", active_from=now)
        else:
            record["status"] = "rejected-shadow"
        self._save()
        return {"status": "activated" if passed else "retained-current", **record["shadow"]}


def calibrate_records(trajectory: list[dict], feedback: list[dict], output: str | Path,
                      mode: str = "real", cancel: threading.Event | None = None,
                      now: float | None = None, seed: int = 7, budget: int = 64) -> dict:
    if any(f.get("mode", mode) != mode for f in feedback) or any(
            t.get("mode", mode) != mode for t in trajectory):
        raise ValueError("Calibration data modes must be isolated")
    if mode == "real" and any(f.get("source") == "synthetic-oracle" for f in feedback):
        raise ValueError("Synthetic oracle reports cannot calibrate real parameters")
    check_cancel(cancel)
    status = calibration_status(feedback)
    if not status["eligible"]:
        return {"status": "insufficient-evidence", **status}
    clean = clean_feedback(feedback)
    fitting, validation = split_feedback(clean)
    if not calibration_status(fitting)["eligible"] or len(validation) < 6 \
            or len({f["answer"] for f in validation}) < 2:
        return {"status": "insufficient-validation", "fitting": calibration_status(fitting),
                "validation_reports": len(validation)}
    # Tau identifiability comes only from the fitting partition, never future feedback.
    learn_tau = calibration_status(fitting)["learn_tau"]
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    attempt = uuid.uuid4().hex[:12]
    results = [search(trajectory, fitting, sampler, seed, budget, learn_tau, cancel,
                      output / f"search-{attempt}-{sampler}.json")
               for sampler in ("random", "tpe")]
    for result in results:
        result["validation_loss"] = objective_loss(trajectory, validation, result["parameters"])
    # Choose sampler on fit loss. Time-out validation is used once as a gate.
    selected = min(results, key=lambda r: r["fit_loss"])
    report = {"mode": mode, "data_source": "synthetic simulation" if mode == "synthetic-demo" else "explicit local self-report",
              "feedback_ids": [str(f.get("id")) for f in clean], "fitting": calibration_status(fitting),
              "validation_reports": len(validation), "learn_tau": learn_tau, "results": results,
              "default_fit_loss": objective_loss(trajectory, fitting, DEFAULTS),
              "default_validation_loss": objective_loss(trajectory, validation, DEFAULTS),
              "trajectory_sha256": hashlib.sha256(canonical(trajectory)).hexdigest(),
              "selected_sampler": selected["sampler"], "regularization": .05, "link_width": 20,
              "purge_s": 300, "history_rewritten": False,
              "evidence_limit": "Log replay search does not test an unobserved reminder action"}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(output / "calibration-report.json", report)
    if selected["validation_loss"] >= report["default_validation_loss"] - .005:
        return {"status": "retained-current", **report}
    registry = ParameterRegistry(output, mode)
    version = registry.add_candidate(selected["parameters"], report, time.time() if now is None else now)
    return {"status": "shadow", "version": version,
            "activation": "Requires at least 10 subsequent reports on 2 days before atomic activation", **report}


def calibrate_file(path: str | Path, output: str | Path, mode: str = "synthetic-demo", **kwargs: Any) -> dict:
    dataset = json.loads(Path(path).read_text())
    if dataset.get("mode") != mode or dataset.get("version") != 1:
        raise ValueError("Calibration dataset mode or version mismatch")
    return calibrate_records(dataset["trajectory"], dataset["feedback"], output, mode, **kwargs)


def dataset_from_events(events: list[dict], mode: str = "real") -> dict:
    """Adapt disjoint workload steps and explicit self-reports; summaries are excluded.

    The first retained step's recorded initial_value fixes the initial condition.
    Rest pairing is inferred from adjacent reports; answers are never inferred.
    """
    # Wall time can move backwards. Persisted insertion sequence is the causal order.
    if any("sequence" in event for event in events):
        sequence = [event.get("sequence") for event in events]
        if not all(isinstance(value, int) for value in sequence) or len(set(sequence)) != len(sequence):
            raise ValueError("Event ingestion sequence is incomplete or ambiguous")
        ordered = sorted(events, key=lambda e: e["sequence"])
    else:
        ordered = sorted(events, key=lambda e: (e.get("start", 0), e.get("end", 0)))
    steps, feedback, rests = [], [], []
    pending_reset = False
    for event in ordered:
        if event.get("mode", mode) != mode:
            raise ValueError("Event mode mismatch")
        p = event.get("payload", event)
        kind = event.get("kind", p.get("kind"))
        if kind in {"WorkloadReset", "NewWorkCycle"}:
            pending_reset = True
        elif kind == "WorkloadStep":
            steps.append({"start": p.get("start", event.get("start")),
                "end": p.get("end", event.get("end")),
                "duration_s": p.get("effective_seconds", p.get("duration_s", 0)),
                "components": p.get("components"), "rest": bool(p.get("rest")),
                "valid": p.get("valid", False), "reset": pending_reset or bool(p.get("reset")),
                "initial_value": p.get("initial_value", p.get("value_before", 0)),
                "session_id": p.get("session_id", event.get("session_id", "default")), "mode": mode})
            pending_reset = False
        elif kind == "RestSession":
            rests.append({"id": p.get("id", event.get("id")), "start": p.get("start", event.get("start")),
                          "end": p.get("end", event.get("end"))})
        elif kind == "RestSessionEnd" and rests:
            rests[-1]["end"] = min(rests[-1]["end"], p.get("at", event.get("end")))
        elif kind == "LoadFeedback":
            feedback.append({**p, "id": p.get("id", event.get("id")),
                "at": p.get("at", event.get("end")),
                "available_step_count": len(steps),
                "session_id": event.get("session_id", p.get("session_id", "default")), "mode": mode})
    mapped = []
    for f in feedback:
        count = f.pop("available_step_count")
        ended = [i for i, step in enumerate(steps[:count]) if step["end"] <= f["at"]]
        if not ended:
            continue
        index = ended[-1]
        if f["at"] - steps[index]["end"] > 300 or not steps[index]["valid"]:
            continue
        f["step_index"] = index
        for rest in rests:
            if 0 <= rest["start"] - f["at"] <= 300:
                f.update(rest_id=rest["id"], rest_phase="before")
            elif 0 <= f["at"] - rest["end"] <= 300:
                f.update(rest_id=rest["id"], rest_phase="after")
        mapped.append(f)
    return {"version": 1, "mode": mode, "trajectory": steps, "feedback": mapped,
            "source": "Persisted disjoint workload steps and explicit voluntary self-reports"}
