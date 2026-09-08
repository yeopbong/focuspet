import threading
import json
import subprocess
import sys

import pytest

from focuspet.learning.training import TrainingCancelled
from focuspet.learning.synthetic import load_dataset
from focuspet.optimization import (ParameterRegistry, calibrate_records, calibration_status,
                                    dataset_from_events, objective_loss, replay_load, search)
from focuspet.optimization.calibration import DEFAULTS, split_feedback


@pytest.fixture(scope="module")
def data():
    return load_dataset(seed=7)


def test_frozen_replay_uses_core_and_segment_weighted_bce():
    trajectory = [{"duration_s": 3600, "end": 3600, "components": {"Focused": 1}},
                  {"duration_s": 600, "end": 4200, "rest": True},
                  {"duration_s": 86400, "end": 90600, "valid": False}]
    values = replay_load(trajectory, DEFAULTS)
    assert values[0] == pytest.approx(100)
    assert -10 < values[1] < 100
    assert values[2] == values[1]
    feedback = [{"step_index": 0, "session_id": "a", "at": 3600, "answer": "Yes"},
                {"step_index": 1, "session_id": "b", "at": 4200, "answer": "No"}]
    assert objective_loss(trajectory, feedback, DEFAULTS) == pytest.approx(
        objective_loss(trajectory, feedback + [feedback[0]]*10, DEFAULTS))
    with pytest.raises(ValueError, match="ended"):
        objective_loss(trajectory, [{**feedback[0], "at": 3000}], DEFAULTS)


def test_self_report_gate_and_tau_identifiability(data):
    status = calibration_status(data["feedback"])
    assert status["eligible"] and status["learn_tau"]
    without_pairs = [{**f, "rest_id": None} for f in data["feedback"]]
    assert calibration_status(without_pairs)["eligible"]
    assert not calibration_status(without_pairs)["learn_tau"]
    assert calibration_status([{**f, "answer": "Not sure"} for f in data["feedback"]])["reports"] == 0
    fit, validation = split_feedback(data["feedback"])
    assert max(f["at"] for f in fit)+300 <= min(f["at"] for f in validation)
    assert {f["session_id"] for f in fit}.isdisjoint(f["session_id"] for f in validation)


def test_real_random_and_tpe_trials_reproducible_bounded_cancelled(data):
    fit, _ = split_feedback(data["feedback"])
    a = search(data["trajectory"], fit, "random", seed=5, budget=8, learn_tau=False)
    b = search(data["trajectory"], fit, "random", seed=5, budget=8, learn_tau=False)
    assert len(a["trials"]) == 8
    assert a["parameters"] == b["parameters"] and a["fit_loss"] == b["fit_loss"]
    assert all(t["parameters"]["tau_user"] == 12 for t in a["trials"])
    tpe = search(data["trajectory"], fit, "tpe", budget=12)
    assert len(tpe["trials"]) == 12 and all(t["status"] == "complete" for t in tpe["trials"])
    cancellation = threading.Event()
    cancellation.set()
    with pytest.raises(TrainingCancelled):
        search(data["trajectory"], fit, "tpe", cancel=cancellation)


def test_parameter_shadow_delta_limits_and_rollback(tmp_path, data):
    registry = ParameterRegistry(tmp_path, "synthetic-demo")
    now = max(f["at"] for f in data["feedback"])
    version = registry.add_candidate({"a_user": 1.3, "tau_user": 25}, {"feedback_ids": []}, now)
    candidate = registry.state["versions"][version]
    assert candidate["parameters"]["a_user"] == pytest.approx(1.1)
    assert candidate["parameters"]["tau_user"] == pytest.approx(15)
    assert registry.active_parameters()["version"] == "defaults-v1"
    assert registry.evaluate_shadow(data["trajectory"], data["feedback"], now+1)["status"] == "collecting-shadow-evidence"
    assert registry.rollback()["active"] is None
    assert ParameterRegistry(tmp_path, "synthetic-demo").active_parameters()["version"] == "defaults-v1"


def test_parameter_later_evidence_can_activate_then_revert(tmp_path, data):
    registry = ParameterRegistry(tmp_path, "synthetic-demo")
    first_at = min(f["at"] for f in data["feedback"])
    version = registry.add_candidate({"a_user": 1.3, "tau_user": 20}, {"feedback_ids": []}, first_at-600)
    result = registry.evaluate_shadow(data["trajectory"], data["feedback"],
                                      now=max(f["at"] for f in data["feedback"])+10)
    assert result["status"] == "activated"
    assert ParameterRegistry(tmp_path, "synthetic-demo").active_parameters()["version"] == version
    assert registry.rollback()["active"] is None
    assert registry.current()["a_user"] == 1
    with pytest.raises(ValueError, match="different data mode"):
        ParameterRegistry(tmp_path, "real")
    persisted = json.loads((tmp_path / "parameter-registry.json").read_text())
    persisted["active"] = version  # Tampering without updating the checksum cannot activate a candidate.
    (tmp_path / "parameter-registry.json").write_text(json.dumps(persisted))
    assert ParameterRegistry(tmp_path, "synthetic-demo").active_parameters()["version"] == "defaults-v1"


def test_calibration_insufficient_data_and_mode_rejection(tmp_path, data):
    assert calibrate_records(data["trajectory"], data["feedback"][:4], tmp_path,
                             "synthetic-demo")["status"] == "insufficient-evidence"
    with pytest.raises(ValueError, match="modes"):
        calibrate_records(data["trajectory"], data["feedback"], tmp_path, "real")


def test_event_adapter_excludes_overlapping_summary_uses_fresh_seconds():
    events = [{"kind": "WorkloadStep", "start": 0, "end": 30, "mode": "test", "session_id": "s",
               "payload": {"effective_seconds": 15, "duration_s": 30, "valid": True,
                           "components": {"Focused": 1}, "value_before": 25}},
              {"kind": "WorkloadEvent", "start": 0, "end": 30, "payload": {"value": 80}},
              {"kind": "LoadFeedback", "start": 31, "end": 31, "session_id": "s",
               "payload": {"answer": "Yes", "at": 31}}]
    adapted = dataset_from_events(events, "test")
    assert len(adapted["trajectory"]) == 1
    assert adapted["feedback"][0]["step_index"] == 0
    assert replay_load(adapted["trajectory"], DEFAULTS)[0] == pytest.approx(25 + 15/60*5/3)


def test_cli_calibration_truthfully_reports_missing_evidence(tmp_path, data):
    path = tmp_path / "load.json"
    path.write_text(json.dumps({**data, "feedback": data["feedback"][:3]}))
    run = subprocess.run([sys.executable, "-m", "focuspet.cli", "calibrate", "--data", str(path),
                          "--output", str(tmp_path / "parameters"), "--mode", "synthetic-demo"],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["status"] == "insufficient-evidence"


def test_shadow_excludes_reports_answered_after_review_time(tmp_path, data):
    registry = ParameterRegistry(tmp_path, "synthetic-demo")
    now = max(f["at"] for f in data["feedback"]) + 10
    registry.add_candidate({"a_user": 1.3, "tau_user": 20}, {"feedback_ids": []},
                           min(f["at"] for f in data["feedback"]) - 600)
    unavailable = [{**f, "labeled_at": now + 1000} for f in data["feedback"]]
    result = registry.evaluate_shadow(data["trajectory"], unavailable, now=now)
    assert result["status"] == "collecting-shadow-evidence"
    assert result["reports"] == 0
    assert registry.active_parameters()["version"] == "defaults-v1"


def test_event_adapter_uses_ingestion_order_across_clock_rollback():
    events = [
        {"sequence": 1, "kind": "WorkloadStep", "start": 100, "end": 110,
         "payload": {"effective_seconds": 10, "valid": True,
                     "components": {"Focused": 1}, "value_before": 25}},
        {"sequence": 2, "kind": "WorkloadStep", "start": 90, "end": 100,
         "payload": {"effective_seconds": 0, "valid": False, "value_before": 25 + 10/36}},
    ]
    adapted = dataset_from_events(list(reversed(events)), "test")
    assert [step["end"] for step in adapted["trajectory"]] == [110, 100]
    assert replay_load(adapted["trajectory"], DEFAULTS)[-1] == pytest.approx(25 + 10/36)


def test_event_adapter_cannot_link_feedback_to_later_ingested_step():
    events = [
        {"sequence": 1, "kind": "WorkloadStep", "start": 100, "end": 110,
         "payload": {"effective_seconds": 10, "valid": True, "components": {"Focused": 1}}},
        {"sequence": 2, "kind": "LoadFeedback", "start": 111, "end": 111,
         "payload": {"at": 111, "answer": "Yes"}},
        {"sequence": 3, "kind": "WorkloadStep", "start": 101, "end": 111,
         "payload": {"effective_seconds": 10, "valid": True, "components": {"Focused": 1}}},
    ]
    adapted = dataset_from_events(events, "test")
    assert adapted["feedback"][0]["step_index"] == 0
