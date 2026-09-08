import copy
import json
import subprocess
import sys

import pytest

from focuspet.domain import ActivityBucket, Engine, FakeClock, Workload, WorkloadParameters
from focuspet.replay import ArchiveReplayError, load_replay_input, replay_export, replay_scenario
from focuspet.storage import Store


def export_cycle(tmp_path, *, predictor=None, parameters=None, rest=True):
    start = 1735722000.0
    clock = FakeClock(start)
    engine = Engine(
        clock=clock, mode="synthetic-demo", predictor=predictor, workload=Workload(parameters=parameters)
    )
    path = tmp_path / "history.json"
    rows = []
    with Store(tmp_path / "app-data", mode="synthetic-demo") as store:
        for i in range(24):
            now = start + i * 10
            if rest and i == 12:
                engine.start_rest(1, now=now)
                for event in engine.events:
                    store.save_event(event["kind"], event, start=event["start"], end=event["end"])
                engine.events.clear()
            bucket = ActivityBucket(
                start=now,
                end=now + 10,
                keyboard=15,
                idle_s=2,
                app_dwell={"IDE": 10},
                mode="synthetic-demo",
                session_id="example",
            )
            store.save_bucket(bucket)
            snapshot = engine.process(bucket)
            for step in engine.workload_steps:
                store.save_event(
                    "WorkloadStep", step, start=step["start"], end=step["end"], session_id=step["session_id"]
                )
            engine.workload_steps.clear()
            if snapshot:
                store.save_snapshot(snapshot)
                rows.append(snapshot.to_dict())
            clock.advance(10)
        store.add_state_feedback(start, start + 90, "Distracted", labeled_at=start + 500)
        store.export(path)
    return json.loads(path.read_text()), rows, path


def test_export_roundtrip_recorded_and_verified_core_history(tmp_path):
    archive, original, path = export_cycle(tmp_path)
    before = copy.deepcopy(archive)
    recorded = replay_export(load_replay_input(path))
    assert recorded["replay_kind"] == "recorded historical playback"
    assert recorded["duration_s"] == 240
    assert recorded["computation"].startswith("none")
    assert [r["prediction"] for r in recorded["snapshots"]] == [r["prediction"] for r in original]
    assert [r["workload"] for r in recorded["snapshots"]] == [r["workload"] for r in original]
    strict = replay_export(archive, profile="Mixed", recompute_history=True)
    assert strict["replay_kind"] == "verified historical recomputation"
    assert strict["duration_s"] == 240
    assert strict["notifications_delivered"] is False
    assert [r["focus"] for r in strict["snapshots"]] == pytest.approx(
        [r["focus"] for r in original], nan_ok=True
    )
    assert archive == before


def test_reevaluation_uses_core_new_parameters_without_future_feedback(tmp_path):
    archive, original, _ = export_cycle(tmp_path, rest=False)
    parameters = WorkloadParameters(a_user=1.2, version="reanalysis-test")
    result = replay_export(archive, reevaluation=True, parameters=parameters)
    assert result["replay_kind"] == "re-evaluation"
    assert result["snapshots"][-1]["workload"] > original[-1]["workload"]
    assert result["parameter_version"] == "reanalysis-test"
    assert result["feedback_used_as_input"] is False
    changed = copy.deepcopy(archive)
    changed["feedback"] = [{"label": "Focused", "start": 0, "end": 99999999999, "labeled_at": 99999999999}]
    rerun = replay_export(changed, reevaluation=True, parameters=parameters)
    assert result["snapshots"] == rerun["snapshots"]
    assert archive["feedback"] != changed["feedback"]


def test_history_requires_original_parameter_version_and_profile(tmp_path):
    original_parameters = WorkloadParameters(a_user=1.1, tau_user=15, version="parameters-original")
    archive, _, _ = export_cycle(tmp_path, parameters=original_parameters)
    with pytest.raises(ArchiveReplayError, match="HISTORICAL_PROFILE_REQUIRED"):
        replay_export(archive, recompute_history=True)
    with pytest.raises(ArchiveReplayError, match="MISSING_HISTORICAL_PARAMETERS"):
        replay_export(archive, recompute_history=True, profile="Mixed")
    verified = replay_export(archive, recompute_history=True, profile="Mixed", parameters=original_parameters)
    assert verified["historical_parameter_versions"] == ["parameters-original"]
    incorrect = WorkloadParameters(a_user=1, version="parameters-original")
    with pytest.raises(ArchiveReplayError, match="HISTORY_REPRODUCTION_MISMATCH"):
        replay_export(archive, recompute_history=True, profile="Mixed", parameters=incorrect)


def test_missing_model_never_substitutes_current_and_recorded_remains_available(tmp_path):
    archive, _, _ = export_cycle(tmp_path)
    for event in archive["events"]:
        if event["kind"] == "PredictionEvent":
            event["payload"]["model_version"] = "personal-missing"
        elif event["kind"] == "StateSnapshot":
            event["payload"]["prediction"]["model_version"] = "personal-missing"
    assert replay_export(archive)["historical_model_versions"] == ["personal-missing"]
    with pytest.raises(ArchiveReplayError, match="MISSING_HISTORICAL_MODEL"):
        replay_export(archive, recompute_history=True, profile="Mixed", model_dir=tmp_path / "absent")
    assert not (tmp_path / "absent").exists()


def test_expired_buckets_modes_schema_and_event_order_are_explicit(tmp_path):
    archive, _, _ = export_cycle(tmp_path)
    expired = copy.deepcopy(archive)
    expired["events"] = [e for e in expired["events"] if e["kind"] != "ActivityBucket"]
    assert replay_export(expired)["snapshots"]
    with pytest.raises(ArchiveReplayError, match="MISSING_ACTIVITY_BUCKETS"):
        replay_export(expired, reevaluation=True)
    mixed = copy.deepcopy(archive)
    mixed["events"][0]["mode"] = "real"
    with pytest.raises(ArchiveReplayError, match="MODE_MISMATCH"):
        replay_export(mixed)
    wrong_schema = copy.deepcopy(archive)
    wrong_schema["features"][0]["schema"] = "features-future"
    with pytest.raises(ArchiveReplayError, match="HISTORICAL_SCHEMA_MISMATCH"):
        replay_export(wrong_schema, recompute_history=True, profile="Mixed")
    reordered = copy.deepcopy(archive)
    reordered["events"].reverse()
    assert replay_export(reordered)["snapshots"] == replay_export(archive)["snapshots"]


def test_cli_export_replay_and_safe_actionable_missing_version_error(tmp_path):
    archive, _, path = export_cycle(tmp_path)
    output = tmp_path / "playback.json"
    run = subprocess.run(
        [sys.executable, "-m", "focuspet.cli", "replay", str(path), "--output", str(output)],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    assert json.loads(output.read_text())["replay_kind"] == "recorded historical playback"
    for event in archive["events"]:
        if event["kind"] == "WorkloadStep":
            event["payload"]["parameter_version"] = "parameters-lost"
    path.write_text(json.dumps(archive))
    failed = subprocess.run(
        [
            sys.executable,
            "-m",
            "focuspet.cli",
            "replay",
            str(path),
            "--recompute-history",
            "--profile",
            "Mixed",
        ],
        capture_output=True,
        text=True,
    )
    assert failed.returncode == 1
    assert "MISSING_HISTORICAL_PARAMETERS" in failed.stderr
    assert "--recorded" in failed.stderr
    assert str(tmp_path) not in failed.stderr


def test_short_scenario_correction_occurs_before_trajectory_ends():
    original = replay_scenario("reading")
    changed = replay_scenario("reading", correction=True)
    assert changed["correction"]["at_elapsed_s"] == original["duration_s"] * 0.5
    assert changed["correction"]["at_elapsed_s"] < original["duration_s"]
    assert changed["replay_kind"] == "re-evaluation"
    assert any(row["prediction"]["source"] == "User declaration" for row in changed["snapshots"])
    assert [r["workload"] for r in changed["snapshots"]] != [r["workload"] for r in original["snapshots"]]
