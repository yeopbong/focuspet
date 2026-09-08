from focuspet.domain import FeatureWindow, Prediction, StateSnapshot
from focuspet.policy import ReminderManager, QueryCandidate, ActiveQuerySelector


def snapshot(t, load, state="Focused", stale=False):
    f = FeatureWindow(t - 60, t, {"active_ratio": 1}, id=f"f-{t}", mode="test")
    p = Prediction({"Focused": 1}, state=state)
    return StateSnapshot(t - 30, t, "Active", state, 80, load, stale, p, f, mode="test")


def test_threshold_crossing_cooldown_restart_and_idempotency():
    p = ReminderManager()
    assert p.consider(snapshot(0, 99)) is None
    note = p.consider(snapshot(30, 101))
    assert note and note.action == "A2"
    assert p.consider(snapshot(60, 110)) is None
    assert p.consider(snapshot(90, 121)) is None
    restored = ReminderManager(p.to_dict())
    assert restored.consider(snapshot(90, 121)) is None
    assert restored.consider(snapshot(120, 125), replay=True) is None
    note2 = restored.consider(snapshot(1830, 125))
    assert note2 and note2.action == "A3" and note2.id != note.id
    assert ReminderManager().consider(snapshot(1830, 125)) is None


def test_quiet_missing_and_snooze_are_explicit_and_shared():
    p = ReminderManager()
    p.consider(snapshot(0, 90))
    p.set_quiet(True)
    assert p.consider(snapshot(30, 101)) is None
    p.set_quiet(False)
    assert p.consider(snapshot(60, 101, state="Unknown", stale=True)) is None
    p.snooze(5, now=60)
    assert p.consider(snapshot(90, 102)) is None
    note = p.consider(snapshot(360, 102))
    assert note and note.mechanism == "user-snooze"
    assert p.consider(snapshot(390, 103)) is None
    p.privacy_paused = True
    p.snooze(5, now=390)
    assert p.consider(snapshot(690, 110)) is None


def test_query_completed_expiry_daily_budget_and_90_minute_spacing():
    p = ReminderManager()

    def candidate(i, t):
        return QueryCandidate(str(i), t - 300, t - 30, t, "audit", "random-audit", f"f-{i}")

    assert p.consider_query(candidate(1, 1000), now=1000)
    assert p.consider_query(candidate(2, 1300), now=1300) is None
    assert p.consider_query(candidate(2, 6400), now=6400)
    assert p.consider_query(candidate(3, 12000), now=12000) is None
    assert ReminderManager().consider_query(candidate(1, 1000), now=4000) is None
    assert ReminderManager().consider_query(candidate(1, 1000), now=900) is None


def test_automatic_overlays_share_hourly_budget_audit_has_no_guess():
    p = ReminderManager()
    p.consider(snapshot(0, 99))
    assert p.consider(snapshot(30, 100))
    q = QueryCandidate("audit", 0, 30, 40, "Random audit", "random-audit", "f")
    note = p.consider_query(q, now=40)
    assert note and "Focused" not in note.text
    p.consider(snapshot(1800, 90))
    assert p.consider(snapshot(1830, 101)) is None
    selector = ActiveQuerySelector(seed=7, audit_fraction=1)
    f = FeatureWindow(0, 60, {}, id="f")
    candidate = selector.select([(f, Prediction({"Normal": 1}, state="Normal"))], now=61)
    assert candidate and candidate.mechanism == "random-audit" and candidate.end < 61
    assert (
        selector.select([(f, Prediction({"Normal": 1}, state="Normal"))], now=61, labeled_feature_ids={"f"})
        is None
    )


def test_queries_can_request_uncertain_but_observed_episodes():
    selector = ActiveQuerySelector(seed=7, audit_fraction=0)
    feature = FeatureWindow(0, 60, {}, id="uncertain", coverage=1)
    candidate = selector.select([(feature, Prediction({"Normal": 1}, state="Unknown"))], now=61)
    assert candidate and candidate.mechanism == "uncertainty-diversity"
    feature.missing_reason = "permission-revoked"
    assert selector.select([(feature, Prediction({"Normal": 1}, state="Unknown"))], now=61) is None


def test_daily_six_budget_and_overnight_quiet_window():
    p = ReminderManager(timezone="UTC")
    p.quiet_hours = [("22:00", "08:00")]
    assert p._quiet_now(0) and not p._quiet_now(12 * 3600)
    p.quiet_hours = []
    for i in range(6):
        start = i * 4000
        p.consider(snapshot(start, 90))
        assert p.consider(snapshot(start + 30, 101))
    p.consider(snapshot(25000, 90))
    assert p.consider(snapshot(25030, 101)) is None
