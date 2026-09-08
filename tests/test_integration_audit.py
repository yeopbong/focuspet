import copy
from collections import deque
from types import SimpleNamespace
import threading

import pytest

from focuspet.domain import ActivityBucket, Engine, FeatureWindow, FakeClock, WorkloadParameters
from focuspet.service import AppService
from focuspet.storage import Store


@pytest.fixture
def service(tmp_path, monkeypatch):
    import focuspet.service as module

    monkeypatch.setattr(module, "DEFAULT_SETTINGS", copy.deepcopy(module.DEFAULT_SETTINGS))
    with Store(tmp_path, mode="test") as store:
        store.save_settings({"last_workload": 73.0, "last_seen": 1000.0})
    item = AppService.__new__(AppService)
    item.mode = "test"
    item.root = tmp_path
    item.scenario_name = "workday"
    item._history = deque(maxlen=3000)
    item._feedback = []
    item._lock = threading.Lock()
    item._ready = threading.Event()
    item._stop = threading.Event()
    item._cache = {"state": "Unknown", "workload": 0.0, "workload_stale": True, "evidence": []}
    item._initialize()
    yield item
    item.collector.stop()
    item.store.close()


def test_restart_cache_shows_preserved_stale_workload(service):
    assert service.engine.workload.value == 73
    assert service.snapshot()["workload"] == 73
    assert service.snapshot()["workload_stale"]


def test_explicit_reset_is_durable_before_another_activity_bucket(service):
    service._handle("reset_cycle", {})
    assert service.snapshot()["workload"] == 0
    assert service.store.settings()["last_workload"] == 0


def test_missing_parameter_directory_resets_in_memory_parameters(service):
    service.engine.workload.parameters = WorkloadParameters(1.2, 15, "parameters-obsolete")
    service._parameter_registry = object()
    service._reload_versions()
    assert service._parameter_registry is None
    assert service.engine.workload.parameters == WorkloadParameters()


def test_delete_all_discards_mutable_settings_and_query_buffers(service):
    service.collector.categories = {}
    service._handle("classify_app", {"app_id": "private.example", "category": "Reader"})
    service._recent.append((FeatureWindow(0, 60, {}), None))
    service._asked_features.add("deleted-feature")
    service._handle("delete_data", {"confirm": True})
    assert service.settings["categories"] == {}
    assert not service._recent and not service._asked_features


def test_range_delete_keeps_undeleted_history_visible(service):
    engine = Engine(mode="test")
    snapshot = None
    for t in range(0, 30, 10):
        snapshot = engine.process(
            ActivityBucket(t, t + 10, keyboard=10, idle_s=0, app_dwell={"IDE": 10}, mode="test")
        )
    service.store.save_snapshot(snapshot)
    service._handle("delete_range", {"confirm": True, "start": 1000, "end": 1100})
    assert len(service.store.get_events("StateSnapshot")) == 1
    assert len(service.snapshot()["history"]) == 1


def test_explicit_rest_advances_without_a_collector_read(service, monkeypatch):
    import focuspet.service as module

    clock = FakeClock(utc=2000, monotonic=100)
    monkeypatch.setattr(module, "time", SimpleNamespace(time=clock.utc, monotonic=clock.monotonic))
    service.engine.clock = clock
    service._handle("rest", {"minutes": 1})
    before = service.engine.workload.value
    clock.advance(30)
    service._tick()
    assert service.engine.workload.value < before
    assert service.snapshot()["source"] == "User declaration"


def test_rest_checkpoint_survives_new_monotonic_epoch_and_stops_at_declared_end(service):
    import math

    clock = FakeClock(utc=2_000_000_000, monotonic=100)
    service.engine.clock = clock
    service._handle("rest", {"minutes": 1})
    clock.advance(20)
    service._tick()
    assert service.engine.workload.value == pytest.approx(-10 + 83 * math.exp(-20 / 720))
    service.collector.stop()
    service.store.close()
    service._history.clear()
    restarted = FakeClock(utc=2_000_000_040, monotonic=90_000)
    service.clock = restarted
    service._initialize()
    assert service.engine.workload.value == pytest.approx(-10 + 83 * math.exp(-40 / 720))
    assert service._timed_rest and service.snapshot()["state"] == "Rest"
    restarted.advance(60)
    service._tick()
    assert service.engine.workload.value == pytest.approx(-10 + 83 * math.exp(-60 / 720))
    assert service._timed_rest is None and not service.snapshot()["rest_active"]
    assert service.store.settings()["declared_rest"] is None
    recovered = service.engine.workload.value
    service.collector.stop()
    service.store.close()
    service._history.clear()
    service.clock = FakeClock(utc=2_000_086_400, monotonic=10)
    service._initialize()
    assert service.engine.workload.value == recovered and service.engine.workload.stale


def test_privacy_pause_stops_declared_rest_but_a_new_explicit_rest_is_allowed(service):
    clock = FakeClock(utc=2_000_000_000, monotonic=10)
    service.engine.clock = clock
    service._handle("rest", {"minutes": 1})
    clock.advance(20)
    service._tick()
    service._handle("pause", {})
    before = service.engine.workload.value
    assert service.store.settings()["declared_rest"] is None
    clock.advance(60)
    service._tick()
    assert service.engine.workload.value == before
    service._handle("rest", {"minutes": 1})
    clock.advance(30)
    service._tick()
    assert service.engine.workload.value < before
    assert service.policy.privacy_paused
    service._handle("end_rest", {})
    assert service.engine.paused and service.settings["privacy_paused"]


def test_interaction_suppression_does_not_refresh_or_write_sql(service):
    calls = []
    service.collector.suppress_interaction = lambda: calls.append("suppress")
    service._refresh = lambda: calls.append("refresh")
    service._persist_engine_events = lambda: calls.append("persist")
    service._handle("interaction", {})
    assert calls == ["suppress"]


def test_retention_setting_applies_immediately_and_resets_deleted_models(service):
    engine = Engine(mode="test")
    for t in range(0, 30, 10):
        snapshot = engine.process(
            ActivityBucket(t, t + 10, keyboard=10, idle_s=0, app_dwell={"IDE": 10}, mode="test")
        )
    service.store.save_snapshot(snapshot)
    (service.store.model_dir / "old.model").write_text("owned artifact")
    service.engine.workload.parameters = WorkloadParameters(1.2, 15, "parameters-old")
    service._handle("settings", {"values": {"feature_days": 0}})
    assert not service.store.get_events("StateSnapshot")
    assert not list(service.store.model_dir.iterdir())
    assert service.engine.workload.parameters == WorkloadParameters()
    assert service.snapshot()["history"] == []


def test_query_answer_cancels_old_training_before_revising_sources(service):
    now = service._now()
    service._query = {
        "id": "q",
        "target_start": now - 120,
        "target_end": now - 60,
        "mechanism": "random-audit",
    }
    calls = []
    service._cancel_job = lambda: calls.append("cancel")
    service._reload_versions = lambda: calls.append("reload")
    service._review_learning = lambda: calls.append("review")
    service._handle("query_answer", {"query_id": "q", "label": "Normal"})
    assert calls == ["cancel", "reload", "review"]
    assert service.store.feedback()[0]["source"] == "audit"


def test_learning_outcome_does_not_persist_or_display_private_paths(service):
    from focuspet.service import safe_job_result

    original = {
        "status": "candidate",
        "path": "/private/person/models/personal-123.json",
        "metrics": {"macro_f1": 0.7, "data_path": "/private/labels.json"},
        "source": "/private/person/input.json",
    }
    safe = safe_job_result(original)
    assert safe == {"status": "candidate", "metrics": {"macro_f1": 0.7}, "source": "[local artifact]"}
    service.store.save_event("ModelVersion", safe)
    assert "/private/" not in str(service.store.get_events("ModelVersion"))


def test_manual_labels_remove_automatic_candidates_and_pending_duplicate_query(service):
    from focuspet.domain import Prediction

    labeled = FeatureWindow(0, 60, {}, id="labeled")
    other = FeatureWindow(90, 150, {}, id="other")
    prediction = Prediction({"Normal": 1}, state="Normal")
    service._recent.extend([(labeled, prediction), (other, prediction)])
    service._query = {"id": "pending", "target_start": 0, "target_end": 60}
    service._handle("feedback", {"label": "Normal", "start": 0, "end": 60})
    assert [f.id for f, p in service._query_candidates()] == ["other"]
    assert service._query is None
    assert len(service.store.get_events("QueryDismissed")) == 1


def test_category_settings_reject_paths_before_mutation_and_persistence(service):
    bad_identifiers = [
        "/private/example/Application.app",
        "C:\\Users\\person\\App.exe",
        "file:///private/app",
        "~/Applications/App.app",
        "two words",
        "person@example.com",
    ]
    for identifier in bad_identifiers:
        with pytest.raises(ValueError):
            service._handle("settings", {"values": {"categories": {identifier: "Reader"}}})
        with pytest.raises(ValueError):
            service.store.save_settings({"categories": {identifier: "Reader"}})
    assert service.settings["categories"] == {}
    assert service.store.settings().get("categories", {}) == {}
    service._handle("settings", {"values": {"categories": {"com.example.Reader": "Reader"}}})
    assert service.store.settings()["categories"] == {"com.example.Reader": "Reader"}
