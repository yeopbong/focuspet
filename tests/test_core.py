import math
import pytest
from focuspet.domain import (
    ActivityBucket,
    Engine,
    Prediction,
    Workload,
    WorkloadParameters,
    LoadBand,
    FakeClock,
)
from focuspet.domain.engine import StateSmoother
from focuspet.features import FeatureBuilder, FEATURE_NAMES
from focuspet.models.prior import GenericPrior


def bucket(t, **kw):
    return ActivityBucket(
        t,
        t + 10,
        keyboard=10,
        clicks=2,
        scroll=1,
        pointer=2,
        idle_s=2,
        app_dwell={"IDE": 10},
        mode="test",
        **kw,
    )


class FocusedPredictor:
    def predict(self, feature):
        return Prediction({"Focused": 1}, state="Focused", coverage=feature.coverage)


def test_workload_reference_recovery_and_step_consistency():
    a, b = Workload(), Workload()
    for _ in range(360):
        a.advance(10, {"Focused": 1})
    b.advance(3600, {"Focused": 1})
    assert a.value == pytest.approx(100)
    assert a.value == pytest.approx(b.value)
    a.advance(900, {"Focused": 1})
    assert a.value > 120 and a.display == "120+"
    before = a.value
    a.advance(60, rest=True)
    assert -10 < a.value < before
    assert a.value == pytest.approx(-10 + (before + 10) * math.exp(-1 / 12))
    a.advance(300, valid=False)
    assert a.stale and a.value > 100


def test_unknown_not_red_zero_and_permissions_missing():
    e = Engine(mode="test")
    for t in range(0, 90, 10):
        snap = e.process(
            ActivityBucket(
                t,
                t + 10,
                keyboard=None,
                clicks=None,
                scroll=None,
                pointer=None,
                idle_s=None,
                mode="test",
                observation="Missing",
            )
        )
    assert snap.state == "Unknown" and snap.focus is None
    assert snap.workload_stale and snap.workload == 0
    assert snap.coverage == 0
    assert snap.feature.values["keyboard_missing"] == 1


def test_reading_and_ambiguous_browser_are_not_forced_distracted():
    builder = FeatureBuilder()
    for t in range(0, 60, 10):
        feature = builder.add(
            ActivityBucket(
                t, t + 10, idle_s=10, app_dwell={"Browser": 10}, observation="No-input", mode="test"
            )
        )
    result = GenericPrior("Research / Reading").predict(feature)
    assert result.state in ("Unknown", "Normal", "Focused")
    assert "Low input" in " ".join(result.reason)


def test_causal_features_no_future_and_schema_stable():
    f = FeatureBuilder()
    for t in range(0, 90, 10):
        before = f.add(bucket(t))
    saved = before.to_dict()
    after = f.add(bucket(90))
    assert before.to_dict() == saved
    assert before.end == 90 and after.end == 100
    assert len(before.vector()) == len(FEATURE_NAMES)
    assert before.start == 30
    assert before.values["continuity_minutes"] == 1.5
    assert before.coverage == 1


def test_duplicate_windows_do_not_double_integrate_and_timeline_is_disjoint():
    engine = Engine(predictor=FocusedPredictor(), mode="test")
    snapshots = []
    for t in range(0, 300, 10):
        b = bucket(t)
        result = engine.process(b)
        value = engine.workload.value
        assert engine.process(b) is None
        assert engine.workload.value == value
        if result:
            snapshots.append(result)
    assert len(snapshots) == 10
    assert sum(s.end - s.start for s in snapshots) == 300
    assert engine.workload.value == pytest.approx(290 / 60 * 5 / 3)
    assert len(engine.workload_steps) == 30
    assert sum(x["effective_seconds"] for x in engine.workload_steps) == 290


def test_pause_clock_rollback_and_offline_do_not_restore():
    e = Engine(predictor=FocusedPredictor(), mode="test")
    for t in range(0, 60, 10):
        e.process(bucket(t))
    value = e.workload.value
    e.pause()
    e.process(bucket(60))
    assert e.latest.focus is None and e.latest.observation == "Paused"
    assert e.workload.value == value
    e.pause(False)
    e.process(bucket(20))
    assert e.workload.value == value and e.workload.stale
    e.process(
        ActivityBucket(
            10000,
            10010,
            mode="test",
            observation="Missing",
            keyboard=None,
            clicks=None,
            scroll=None,
            pointer=None,
            idle_s=None,
        )
    )
    assert e.workload.value == value and e.workload.stale


def test_declared_rest_immediate_and_bounded_with_missing_activity():
    e = Engine(predictor=FocusedPredictor(), mode="test", workload=Workload(100))
    for t in range(0, 30, 10):
        e.process(bucket(t))
    e.start_rest(minutes=1, now=30)
    before = e.workload.value
    assert e.latest.state == "Rest" and e.latest.focus is None
    for t in range(30, 150, 10):
        e.process(
            ActivityBucket(
                t,
                t + 10,
                mode="test",
                observation="Missing",
                keyboard=None,
                clicks=None,
                scroll=None,
                pointer=None,
                idle_s=None,
            )
        )
    assert e.workload.value == pytest.approx(-10 + (before + 10) * math.exp(-1 / 12))
    assert e.latest.state == "Unknown" and e.workload.stale
    e.reset_workload(now=150)
    assert e.workload.value == 0


def test_deterministic_replay_and_mode_guard():
    def run():
        e = Engine(mode="test")
        return [s.to_dict() for t in range(0, 300, 10) if (s := e.process(bucket(t)))]

    assert run() == run()
    with pytest.raises(ValueError, match="mode"):
        Engine(mode="real").process(bucket(0))


def test_state_and_load_hysteresis_focus_is_separate_from_uncertainty():
    smoother = StateSmoother()
    state, focus = smoother.update(
        Prediction({"Focused": 0.7, "Normal": 0.2, "Distracted": 0.1}, state="Focused", uncertainty=0.9), 30
    )
    assert state == "Focused" and focus == 80
    for expected in ("Focused", "Normal"):
        state, focus = smoother.update(Prediction({"Normal": 1}, state="Normal"), 30)
        assert state == expected and 50 < focus < 80
    bands = LoadBand()
    assert bands.update(80) == 80
    assert bands.update(77) == 80
    assert bands.update(74) == 40
    clock = FakeClock(utc=100)
    clock.advance(10, wall_seconds=-100)
    assert clock.monotonic() == 10 and clock.utc() == 0
    with pytest.raises(ValueError):
        WorkloadParameters(a_user=2)


def test_pet_interaction_is_not_work_and_personal_failures_fall_back():
    class Broken:
        def predict(self, feature):
            raise ValueError("broken model")

    e = Engine(predictor=Broken(), mode="test")
    for t in range(0, 90, 10):
        e.process(bucket(t))
    assert "Personal model unavailable" in " ".join(e.latest.prediction.reason)
    before = e.workload.value
    for t in range(90, 120, 10):
        e.process(bucket(t, interaction_s=1))
    assert e.latest.focus is None and e.latest.state == "Unknown"
    assert e.workload.value == before
    assert e.workload_steps[-1]["value_before"] == before


def test_gap_resets_context_and_snapshot_does_not_claim_offline_work():
    e = Engine(predictor=FocusedPredictor(), mode="test")
    for t in range(0, 60, 10):
        e.process(bucket(t))
    before = e.workload.value
    for t in range(10000, 10030, 10):
        snap = e.process(bucket(t))
    assert snap.start == 10000 and snap.end == 10030
    assert snap.feature.start == 10000
    assert snap.feature.values["continuity_minutes"] == 0.5
    assert snap.workload - before == pytest.approx(20 / 60 * 5 / 3)
