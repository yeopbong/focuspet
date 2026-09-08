import json
import math
import os
import sys
from types import SimpleNamespace

import pytest

from focuspet.collectors.macos import NativeCollector, AggregateCounter
from focuspet.collectors.replay import ReplayCollector, make_scenario
from focuspet.collectors.windows import WindowsCollector
from focuspet.domain import FakeClock, Engine


class Event:
    def __repr__(self):
        raise AssertionError("Raw event representation must never be requested")

    def __getattr__(self, name):
        raise AssertionError("Raw event contents must never be requested")


class Center:
    def __init__(self):
        self.observers = []

    def addObserver_selector_name_object_(self, observer, selector, name, obj):
        self.observers.append(observer)

    def removeObserver_(self, observer):
        self.observers = [item for item in self.observers if item is not observer]


class NSObject:
    @classmethod
    def alloc(cls):
        return cls()

    def init(self):
        return self


class Workspace:
    def __init__(self):
        self.identifier = "com.apple.Terminal"
        self.pid = -1
        self.reads = 0
        self.center = Center()
        self.fail = False

    def frontmostApplication(self):
        self.reads += 1
        if self.fail:
            raise ValueError("Unavailable")
        return SimpleNamespace(processIdentifier=lambda: self.pid, bundleIdentifier=lambda: self.identifier)

    def notificationCenter(self):
        return self.center


class Quartz:
    kCGEventKeyDown = 1
    kCGEventLeftMouseDown = 2
    kCGEventRightMouseDown = 3
    kCGEventOtherMouseDown = 4
    kCGEventScrollWheel = 5
    kCGEventMouseMoved = 6
    kCGEventLeftMouseDragged = 7
    kCGEventRightMouseDragged = 8
    kCGEventTapDisabledByTimeout = -1
    kCGEventTapDisabledByUserInput = -2
    kCGSessionEventTap = 0
    kCGHeadInsertEventTap = 0
    kCGEventTapOptionListenOnly = 0
    kCFRunLoopCommonModes = "common"
    kCFRunLoopDefaultMode = "default"
    kCGMouseEventDeltaX = "dx"
    kCGMouseEventDeltaY = "dy"
    kCGEventSourceStateCombinedSessionState = 0
    kCGAnyInputEventType = -1

    def __init__(self, granted=True):
        self.granted = granted
        self.request_count = 0
        self.reads = 0
        self.fields = []
        self.enabled = False
        self.tap_count = 0
        self.idle = 0
        self.idle_failure = False

    def CGPreflightListenEventAccess(self):
        self.reads += 1
        return self.granted

    def CGRequestListenEventAccess(self):
        self.request_count += 1
        return self.granted

    def CGEventTapCreate(self, *args):
        self.tap_count += 1
        self.callback = args[-2]
        return "fake-tap"

    def CFMachPortCreateRunLoopSource(self, *args):
        return "fake-source"

    def CFRunLoopGetCurrent(self):
        return "fake-loop"

    def CFRunLoopAddSource(self, *args):
        pass

    def CFRunLoopRemoveSource(self, *args):
        pass

    def CFMachPortInvalidate(self, *args):
        pass

    def CFRunLoopRunInMode(self, *args):
        self.reads += 1

    def CGEventTapEnable(self, tap, enabled):
        self.enabled = enabled

    def CGEventTapIsEnabled(self, tap):
        self.reads += 1
        return self.enabled

    def CGEventGetIntegerValueField(self, event, field):
        assert field in ("dx", "dy")
        self.fields.append(field)
        return 3 if field == "dx" else 4

    def CGEventSourceSecondsSinceLastEventType(self, *args):
        self.reads += 1
        if self.idle_failure:
            raise ValueError("Idle unavailable")
        return self.idle


@pytest.fixture
def native(monkeypatch):
    import focuspet.collectors.macos as module

    workspace = Workspace()
    distributed = Center()
    monkeypatch.setattr(module, "_OBSERVER_CLASS", None)
    monkeypatch.setitem(
        sys.modules,
        "AppKit",
        SimpleNamespace(
            NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: workspace),
            NSWorkspaceWillSleepNotification="sleep",
            NSWorkspaceDidWakeNotification="wake",
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "Foundation",
        SimpleNamespace(
            NSObject=NSObject,
            NSDistributedNotificationCenter=SimpleNamespace(defaultCenter=lambda: distributed),
        ),
    )
    clock = FakeClock(utc=1000, monotonic=0)
    quartz = Quartz()
    collector = NativeCollector(clock=clock, quartz=quartz, workspace=workspace)
    yield collector, quartz, workspace, clock, distributed
    collector.stop()


def sample_seconds(collector, clock, n=10):
    result = None
    for _ in range(n):
        clock.advance(1)
        result = collector.sample()
    return result


def test_callback_only_counts_whitelisted_scalars_and_uses_injected_clock(native):
    c, q, w, clock, _ = native
    c.start()
    event = Event()
    clock.advance(0.5)
    for event_type in (
        q.kCGEventKeyDown,
        q.kCGEventLeftMouseDown,
        q.kCGEventScrollWheel,
        q.kCGEventMouseMoved,
    ):
        assert q.callback(None, event_type, event, None) is event
    assert c.counter.last_input == 0.5
    assert c.counter.values == {"keyboard": 1, "clicks": 1, "scroll": 1, "pointer": 5}
    assert q.fields == ["dx", "dy"]
    b = sample_seconds(c, clock)
    assert b.keyboard == 1 and b.pointer == 5
    assert b.duration_s == 10.5
    serialized = json.dumps(b.to_dict())
    assert "com.apple.Terminal" not in serialized
    assert "dx" not in serialized and "dy" not in serialized
    assert q.request_count == 0


def test_permission_denied_is_missing_and_revoke_stops_reading_input(native):
    c, q, w, clock, _ = native
    q.granted = False
    assert c.capabilities()["keyboard"] == "denied"
    c.start()
    assert q.request_count == q.tap_count == 0
    denied = sample_seconds(c, clock)
    assert denied.keyboard is None and denied.idle_s is None
    assert denied.coverage["keyboard"] == 0 and denied.observation == "Missing"
    q.granted = True
    c.start()
    q.callback(None, q.kCGEventKeyDown, Event(), None)
    q.granted = False
    clock.advance(1)
    c.sample()
    assert not q.enabled and c.capabilities()["keyboard"] == "denied"
    q.callback(None, q.kCGEventKeyDown, Event(), None)
    assert c.counter.values["keyboard"] == 0
    assert q.request_count == 0


def test_pause_does_no_reads_and_discards_pending_counts(native):
    c, q, w, clock, _ = native
    c.start()
    q.callback(None, q.kCGEventKeyDown, Event(), None)
    c.pause()
    before = q.reads, w.reads
    clock.advance(1000)
    assert c.sample() is None
    assert (q.reads, w.reads) == before
    assert c.counter.values["keyboard"] == 0
    assert c.counter.last_input is None
    c.pause(False)
    b = sample_seconds(c, clock)
    assert b.duration_s == 10 and b.keyboard == 0


def test_boundary_application_switches_and_observer_class_reuse(native):
    c, q, w, clock, distributed = native
    c.start()
    first_class = type(c._observer)
    first = sample_seconds(c, clock)
    w.identifier = "com.apple.Preview"
    second = sample_seconds(c, clock)
    assert first.app_switches == 0 and second.app_switches == 1
    assert second.app_dwell == {"Reader": 10}
    c.start()
    assert type(c._observer) is first_class
    assert len(w.center.observers) == len(distributed.observers) == 2
    c._observer.didLock_(Event())
    previous_reads = w.reads
    locked = sample_seconds(c, clock)
    assert locked.observation == "Locked" and w.reads == previous_reads
    assert locked.coverage["application"] == 0


def test_idle_failure_is_independent_and_own_app_inputs_are_excluded(native):
    c, q, w, clock, _ = native
    c.start()
    q.idle_failure = True
    first = sample_seconds(c, clock)
    assert first.keyboard == 0 and first.idle_s is None
    assert first.coverage["keyboard"] == 1 and first.coverage["idle"] == 0
    assert c.capabilities()["idle"] == "error"
    w.pid = os.getpid()
    clock.advance(1)
    c.sample()
    q.callback(None, q.kCGEventKeyDown, Event(), None)
    assert c.counter.values["keyboard"] == 0
    second = sample_seconds(c, clock, 9)
    assert second.interaction_s == 10
    assert second.coverage["keyboard"] == 0


def test_sleep_rollback_and_tap_timeout_are_gaps(native):
    c, q, w, clock, _ = native
    c.start()
    clock.advance(7200)
    sleep = c.sample()
    assert sleep.observation == "Missing"
    assert sleep.duration_s == 120 and sleep.end - sleep.start == 7200
    clock.advance(10, wall_seconds=-120)
    rollback = c.sample()
    assert rollback.observation == "Missing" and rollback.duration_s == 10
    assert rollback.end < rollback.start
    q.callback(None, q.kCGEventTapDisabledByTimeout, Event(), None)
    q.callback(None, q.kCGEventKeyDown, Event(), None)
    assert c.counter.values["keyboard"] == 0
    assert c.capabilities()["keyboard"] == "error"


def test_counter_invalid_values_and_windows_honesty():
    counter = AggregateCounter()
    counter.count("keyboard", float("nan"))
    counter.count("raw-event", 1)
    assert counter.values["keyboard"] == 0
    counter.count("pointer", float("inf"))
    assert counter.values["pointer"] == 0
    collector = WindowsCollector()
    collector.start()
    assert collector.sample() is None
    assert set(collector.capabilities().values()) == {"unavailable"}


def test_replay_determinism_pause_mode_and_same_core():
    scenario = make_scenario("permission-loss", seed=12)
    assert scenario == make_scenario("permission-loss", seed=12)
    replay = ReplayCollector(scenario)
    assert replay.sample() is None
    replay.start(request_permission=True)
    first = replay.sample()
    assert first.mode == "synthetic-demo"
    replay.pause()
    assert replay.sample() is None
    replay.pause(False)
    assert replay.sample().start == first.end

    def trajectory():
        collector = ReplayCollector(scenario)
        collector.start()
        engine = Engine(mode="synthetic-demo")
        snapshots = []
        while (b := collector.sample()) is not None:
            if (snapshot := engine.process(b)) is not None:
                snapshots.append(snapshot.to_dict())
        return snapshots

    a, b = trajectory(), trajectory()
    assert a == b
    assert any(s["state"] == "Unknown" and s["workload_stale"] for s in a)
    assert all(math.isfinite(s["workload"]) for s in a)


def test_observer_instances_never_capture_the_first_collector(native):
    c, q, w, clock, _ = native
    c.start()
    second = NativeCollector(clock=clock, quartz=Quartz(), workspace=Workspace())
    second.start()
    try:
        assert type(second._observer) is type(c._observer)
        second._observer.willSleep_(Event())
        assert second._sleeping and not c._sleeping
        assert c.counter.last_input is None
    finally:
        second.stop()


def test_stop_still_discards_counters_if_native_teardown_fails(native):
    c, q, w, clock, distributed = native
    c.start()
    q.callback(None, q.kCGEventKeyDown, Event(), None)

    def fail(*args):
        raise RuntimeError("native source unavailable")

    q.CFRunLoopRemoveSource = fail
    c.stop()
    assert c.counter.values["keyboard"] == 0
    assert not w.center.observers and not distributed.observers
    assert c.sample() is None


def test_normal_sampling_frequency_does_not_advance_time_artificially(native):
    c, q, w, clock, _ = native
    c.start()
    for _ in range(500):
        assert c.sample() is None
    for _ in range(199):
        clock.advance(0.05)
        assert c.sample() is None
    clock.advance(0.05)
    bucket = c.sample()
    assert bucket is not None and bucket.duration_s == pytest.approx(10)
    assert bucket.end - bucket.start == pytest.approx(10)
    assert bucket.observation == "Active"
    with pytest.raises(ValueError):
        NativeCollector(clock=clock, categories={"/private/path/App.app": "Reader"}, quartz=q, workspace=w)


def test_silent_tap_failure_is_missing_until_explicit_restart(native):
    c, q, w, clock, _ = native
    c.start()
    assert sample_seconds(c, clock).keyboard == 0
    q.enabled = False
    failed = sample_seconds(c, clock)
    assert failed.observation == "Missing"
    assert failed.keyboard is None and failed.coverage["keyboard"] == 0
    assert not c.diagnostics()["tap_healthy"]
    assert c.diagnostics()["health_failures"] == 1
    q.enabled = True
    assert sample_seconds(c, clock).keyboard is None
    c.start()
    assert sample_seconds(c, clock).keyboard == 0
    c.pause()
    before = q.reads, w.reads
    assert not c.diagnostics()["tap_healthy"]
    assert (q.reads, w.reads) == before


def test_failed_app_identity_cannot_collect_pet_input(native):
    c, q, w, clock, _ = native
    c.start()
    w.fail = True
    clock.advance(1)
    c.sample()
    q.callback(None, q.kCGEventKeyDown, Event(), None)
    assert c.counter.values["keyboard"] == 0
    bucket = sample_seconds(c, clock, 9)
    assert bucket.observation == "Missing"
    assert bucket.coverage["keyboard"] == 0
