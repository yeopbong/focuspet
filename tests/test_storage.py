import json
import pytest
from focuspet.domain import ActivityBucket, Engine
from focuspet.storage import Store


def populate(store):
    e = Engine(mode=store.mode)
    for t in range(0, 180, 10):
        b = ActivityBucket(
            t, t + 10, keyboard=10, idle_s=0, app_dwell={"IDE": 10}, mode=store.mode, session_id="work-1"
        )
        store.save_bucket(b)
        if s := e.process(b):
            store.save_snapshot(s)


def test_single_actor_persistence_revisions_withdrawal_and_original_predictions(tmp_path):
    with Store(tmp_path, mode="test") as store:
        populate(store)
        first = store.add_state_feedback(0, 180, "Focused", labeled_at=180)
        predictions = store.get_events("PredictionEvent")
        rows = store.training_records()
        assert rows and {r["label"] for r in rows} == {"Focused"}
        assert len({r["episode_id"] for r in rows}) == 1
        assert sum(r["weight"] for r in rows) == pytest.approx(1)
        second = store.add_state_feedback(0, 180, "Normal", revises=first, labeled_at=200)
        assert {r["label"] for r in store.training_records()} == {"Normal"}
        assert store.get_events("PredictionEvent") == predictions
        store.retract_feedback(second)
        assert store.training_records() == []
        assert len(store.feedback()) == 2
        store.save_settings({"profile": "Creative"})
    with Store(tmp_path, mode="test") as store:
        assert store.settings()["profile"] == "Creative"
        assert store.training_records() == []
        assert len(store.get_events("ActivityBucket")) == 18


def test_demo_isolation_delete_derivatives_models_and_all(tmp_path):
    with Store(tmp_path, mode="real") as real, Store(tmp_path, mode="synthetic-demo") as demo:
        populate(demo)
        assert real.get_events() == []
        demo.add_state_feedback(0, 180, "Focused", labeled_at=180)
        (demo.model_dir / "personal.model").write_text("example owned artifact")
        result = demo.delete_range(80, 90)
        assert result["features_deleted"] > 0 and result["feedback_deleted"] == 1
        assert not list(demo.model_dir.iterdir())
        assert demo.training_records() == []
        assert demo.get_events("ModelInvalidation")
        demo.save_settings({"profile": "Mixed"})
        demo.delete_all()
        assert demo.get_events() == [] and demo.settings() == {}
        assert demo.training_records() == []
        assert real.get_events() == []


def test_retention_does_not_leave_feature_copies_export_preview_and_privacy(tmp_path):
    with Store(tmp_path, mode="test") as store:
        populate(store)
        store.add_state_feedback(0, 180, "Focused", labeled_at=180)
        path = store.export(tmp_path / "export.json")
        exported = json.loads(path.read_text())
        assert exported["mode"] == "test" and exported["features"]
        assert "user settings" in exported["preview"]["excluded"]
        with pytest.raises(ValueError, match="forbidden"):
            store.save_event("Unsafe", {"window_title": "should never persist"})
        with pytest.raises(ValueError, match="mode"):
            store.save_bucket(ActivityBucket(300, 310, mode="real"))
        result = store.retention(now=91 * 86400)
        assert result["features_deleted"] == 6 and result["buckets_deleted"] == 18
        assert store.training_records() == []
        assert store.feedback()[0]["trainable"] == 0
        assert store.get_events("StateSnapshot") == []
        assert store.get_events("PredictionEvent") == []


def test_feedback_separation_and_policy_restart(tmp_path):
    with Store(tmp_path, mode="test") as store:
        load = store.add_load_feedback("Yes", at=100)
        assert load and store.training_records() == []
        with pytest.raises(ValueError):
            store.add_load_feedback("Do not interrupt")
        store.save_policy_state({"seen": ["notice-1"], "quiet": True})
        assert store.load_policy_state()["quiet"]
    with Store(tmp_path, mode="test") as store:
        assert store.load_policy_state()["seen"] == ["notice-1"]


def test_frozen_step_atomically_checkpoints_load_before_snapshot(tmp_path):
    with Store(tmp_path, mode="test") as store:
        store.save_event(
            "WorkloadStep",
            {"id": "step-1", "start": 0, "end": 10, "value": 52.0, "duration_s": 10, "valid": True},
            start=0,
            end=10,
        )
        assert store.settings()["last_workload"] == 52
        with pytest.raises(ValueError, match="forbidden"):
            store.save_event("ModelVersion", {"path": "/private/example/model.json"})
    with Store(tmp_path, mode="test") as store:
        assert store.settings()["last_workload"] == 52
        assert store.settings()["last_seen"] == 10


def test_range_controls_include_clock_rollback_events(tmp_path):
    with Store(tmp_path, mode="test") as store:
        rollback = ActivityBucket(300, 200, duration_s=10, observation="Missing", mode="test")
        store.save_bucket(rollback)
        store.save_bucket(ActivityBucket(500, 510, mode="test"))
        rows = store.get_events("ActivityBucket", since=225, until=250)
        assert [row["id"] for row in rows] == [rollback.id]
        archive = json.loads(store.export(tmp_path / "range.json", since=225, until=250).read_text())
        assert [row["id"] for row in archive["events"]] == [rollback.id]
        assert store.delete_range(225, 250)["events_deleted"] == 1
        remaining = store.get_events("ActivityBucket")
        assert len(remaining) == 1 and remaining[0]["start"] == 500
