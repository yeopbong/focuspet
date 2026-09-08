import copy
import json
import threading
import subprocess
import sys

import numpy as np
import pytest

from focuspet.features import FEATURE_NAMES
from focuspet.learning.data import (CLASSES, chronological_split, episode_weights, latest_records,
                                    matrix, status_records)
from focuspet.learning.registry import ModelRegistry
from focuspet.learning.training import TrainingCancelled, fit_models, train_records
from focuspet.models.personal import NumericModel, estimator_payload, load_artifact, save_artifact

EPOCH = 1735722000.0


def records_for_days(days=6, offset=0, source="user"):
    records = []
    for day in range(offset, offset + days):
        for episode in range(6):
            label = CLASSES[episode % 3]
            start = EPOCH + day * 86400 + episode * 1000
            values = dict.fromkeys(FEATURE_NAMES, 0.)
            values[FEATURE_NAMES[episode % 3]] = 10.
            records.append({"episode_id": f"d{day}e{episode}", "feature_id": f"d{day}f{episode}",
                "session_id": f"d{day}s{episode//2}", "start": start, "end": start+300,
                "labeled_at": start+330, "label": label, "values": values,
                "coverage": 1., "mode": "test", "source": source,
                "prior": {"Focused": .1, "Normal": .8, "Distracted": .1}})
    return records


def test_latest_revision_withdrawal_and_independent_episode_weights():
    original = records_for_days(1)[:1]
    repeated = [{**original[0], "feature_id": str(i), "start": original[0]["start"]+i,
                 "end": original[0]["end"]+i} for i in range(20)]
    clean = latest_records(repeated)
    assert len(clean) == 20
    assert episode_weights(clean).sum() == pytest.approx(1)
    assert status_records(repeated)["episodes"] == 1
    withdrawn = {**original[0], "labeled_at": original[0]["labeled_at"]+1000, "withdrawn": True}
    assert latest_records(repeated + [withdrawn]) == []
    revised = {**withdrawn, "withdrawn": False, "label": "Distracted"}
    assert [r["label"] for r in latest_records(repeated + [revised])] == ["Distracted"]


def test_overlapping_separate_feedback_is_not_independent():
    a, b = records_for_days(1)[:2]
    b.update(start=a["start"]+100, end=a["end"]+100)
    assert status_records([a, b])["episodes"] == 1


def test_purged_split_is_chronological_day_and_episode_disjoint():
    records = records_for_days()
    train, val = chronological_split(records)
    assert max(r["end"] for r in train) + 300 <= min(r["start"] for r in val)
    assert {r["episode_id"] for r in train}.isdisjoint(r["episode_id"] for r in val)
    crossing = dict(records[0], episode_id="crossing", feature_id="crossing", end=val[0]["end"])
    a, b = chronological_split(records + [crossing])
    assert "crossing" not in {r["episode_id"] for r in a+b}
    with pytest.raises(ValueError, match="300"):
        chronological_split(records, purge_s=30)


def test_scaler_training_only_and_safe_numeric_roundtrip(tmp_path):
    train, validation = chronological_split(records_for_days())
    names = list(FEATURE_NAMES)
    scaler, models = fit_models(train, names)
    np.testing.assert_allclose(scaler.mean_, np.average(matrix(train, names), axis=0,
                                                       weights=episode_weights(train)))
    for model in models.values():
        assert list(model.classes_) == [0, 1, 2]
    x = matrix(validation, names)
    for kind, model in models.items():
        payload = estimator_payload(model, scaler, kind=kind, names=names, mode="test",
                                    version="personal-test", alpha=.75, metadata={})
        path = tmp_path / f"{kind}.json"
        save_artifact(path, payload)
        loaded = NumericModel(load_artifact(path, names, "test"))
        np.testing.assert_allclose(loaded.predict_proba(x), model.predict_proba(scaler.transform(x)), atol=1e-12)
        with pytest.raises(ValueError, match="mode"):
            load_artifact(path, names, "real")
        with pytest.raises(ValueError, match="order"):
            load_artifact(path, names[::-1], "test")
        envelope = json.loads(path.read_text())
        envelope["payload"]["alpha"] = .25
        path.write_text(json.dumps(envelope))
        with pytest.raises(ValueError, match="checksum"):
            load_artifact(path)


def test_thresholds_cancellation_and_no_automatic_early_activation(tmp_path):
    records = records_for_days()
    assert status_records(records)["eligible"]
    assert train_records(records[:10], tmp_path, mode="test")["status"] == "insufficient-evidence"
    cancellation = threading.Event()
    cancellation.set()
    with pytest.raises(TrainingCancelled):
        train_records(records, tmp_path, mode="test", cancel=cancellation)
    now = EPOCH + 6*86400
    result = train_records(records, tmp_path, mode="test", now=now, automatic=True)
    assert result["status"] == "shadow"
    registry = ModelRegistry(tmp_path, "test")
    assert registry.active_predictor() is None
    assert registry.evaluate_shadow(records, now=now)["status"] == "collecting-shadow-evidence"
    assert train_records(records, tmp_path, mode="test", now=now+90000, automatic=True)["status"] == "not-due"
    new_records = records + records_for_days(1, offset=7)
    assert train_records(new_records, tmp_path, mode="test", now=now+172800,
                         automatic=True)["status"] == "not-due"  # Do not starve shadow evidence.
    with pytest.raises(ValueError, match="different data mode"):
        ModelRegistry(tmp_path, "real")


def test_new_independent_shadow_activation_corrupt_fallback_and_rollback(tmp_path):
    now = EPOCH + 6*86400
    trained = train_records(records_for_days(), tmp_path, mode="test", now=now)
    registry = ModelRegistry(tmp_path, "test")
    audit = records_for_days(2, offset=7, source="audit")
    # Ordinary active-selection feedback cannot be silently reused as independent audit.
    selected = copy.deepcopy(audit)
    for row in selected:
        row["source"] = "active-query"
    assert registry.evaluate_shadow(selected, now=EPOCH+10*86400)["status"] == "collecting-shadow-evidence"
    activated = registry.evaluate_shadow(audit, now=EPOCH+10*86400)
    assert activated["status"] == "activated"
    assert ModelRegistry(tmp_path, "test").active_predictor() is not None
    # Restart loading recovers safely from a damaged active artifact.
    registry.artifact_path(trained["version"]).write_text("broken")
    assert ModelRegistry(tmp_path, "test").active_predictor() is None
    assert registry.rollback()["active"] is None


def test_post_activation_audits_trigger_regression_rollback(tmp_path):
    train_records(records_for_days(), tmp_path, mode="test", now=EPOCH+6*86400)
    registry = ModelRegistry(tmp_path, "test")
    registry.evaluate_shadow(records_for_days(2, offset=7, source="audit"), now=EPOCH+10*86400)
    assert registry.active_predictor() is not None
    changed = records_for_days(2, offset=11, source="audit")
    for row in changed:
        row["label"] = CLASSES[(CLASSES.index(row["label"])+1) % 3]
    result = registry.assess_active(changed, now=EPOCH+14*86400)
    assert result["status"] == "rolled-back"
    assert ModelRegistry(tmp_path, "test").active_predictor() is None


def test_synthetic_mode_and_incompatible_dependency_are_rejected(tmp_path):
    records = records_for_days()
    with pytest.raises(ValueError, match="Mixed"):
        train_records(records, tmp_path, mode="real")
    scaler, models = fit_models(records, list(FEATURE_NAMES))
    payload = estimator_payload(models["logistic"], scaler, kind="logistic", names=list(FEATURE_NAMES),
                                mode="test", version="personal-safe", alpha=1, metadata={})
    payload["dependencies"]["scikit-learn"] = "0.1.0"
    path = tmp_path / "personal-safe.json"
    save_artifact(path, payload)
    with pytest.raises(ValueError, match="incompatible"):
        load_artifact(path)


def test_cli_train_writes_reloadable_candidate_and_enforces_mode(tmp_path):
    dataset = tmp_path / "feedback.json"
    dataset.write_text(json.dumps({"version": 1, "mode": "test", "feature_names": list(FEATURE_NAMES),
                                   "records": records_for_days()}))
    output = tmp_path / "models"
    run = subprocess.run([sys.executable, "-m", "focuspet.cli", "train", "--data", str(dataset),
                          "--output", str(output), "--mode", "test"], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    report = json.loads(run.stdout)
    assert report["status"] == "shadow"
    assert load_artifact(output / f"{report['version']}.json", FEATURE_NAMES, "test")["classes"] == list(CLASSES)
    assert ModelRegistry(output, "test").active_predictor() is None
    rejected = subprocess.run([sys.executable, "-m", "focuspet.cli", "train", "--data", str(dataset),
                               "--output", str(output), "--mode", "real"], capture_output=True, text=True)
    assert rejected.returncode == 1


def test_active_monitor_excludes_labels_answered_after_review_time(tmp_path):
    train_records(records_for_days(), tmp_path, mode="test", now=EPOCH+6*86400)
    registry = ModelRegistry(tmp_path, "test")
    registry.evaluate_shadow(records_for_days(2, offset=7, source="audit"), now=EPOCH+10*86400)
    assert registry.active_predictor() is not None
    now = EPOCH + 14*86400
    unavailable = records_for_days(2, offset=11, source="audit")
    for row in unavailable:
        row["label"] = CLASSES[(CLASSES.index(row["label"])+1) % 3]
        row["labeled_at"] = now + 1000
    result = registry.assess_active(unavailable, now=now)
    assert result["status"] == "collecting-monitor-evidence"
    assert result["support"]["episodes"] == 0
    assert registry.active_predictor() is not None
