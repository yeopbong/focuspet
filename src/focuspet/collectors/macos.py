from __future__ import annotations

import math
import os
import threading
import time
import uuid
import weakref

from focuspet.domain import ActivityBucket, SystemClock
from focuspet.collectors.base import validate_categories

DEFAULT_CATEGORIES = {
    "com.apple.Safari": "Browser",
    "org.mozilla.firefox": "Browser",
    "com.google.Chrome": "Browser",
    "com.microsoft.edgemac": "Browser",
    "com.microsoft.VSCode": "IDE",
    "com.apple.Terminal": "IDE",
    "com.googlecode.iterm2": "IDE",
    "com.jetbrains.pycharm": "IDE",
    "com.apple.Preview": "Reader",
    "com.adobe.Reader": "Reader",
    "com.microsoft.Word": "Office",
    "com.microsoft.Excel": "Office",
    "com.apple.iWork.Pages": "Office",
    "com.apple.iWork.Numbers": "Office",
    "com.adobe.Photoshop": "Creative",
    "com.figma.Desktop": "Creative",
    "com.tinyspeck.slackmacgap": "Communication",
    "us.zoom.xos": "Communication",
    "com.apple.MobileSMS": "Communication",
    "com.spotify.client": "Entertainment",
}
SIGNALS = ("keyboard", "clicks", "scroll", "pointer", "idle", "application", "lock", "sleep")
_OBSERVER_CLASS = None


def _get_observer_class(base):
    global _OBSERVER_CLASS
    if _OBSERVER_CLASS is None:

        class FocusPetRhythmObserver(base):  # type: ignore[valid-type,misc]
            def willSleep_(self, notification):
                owner = self.owner_ref()
                if owner is not None:
                    owner._sleeping = True
                    owner.counter.clear()
                    owner._gap = True

            def didWake_(self, notification):
                owner = self.owner_ref()
                if owner is not None:
                    owner._sleeping = False
                    owner.counter.clear()
                    owner._gap = True

            def didLock_(self, notification):
                owner = self.owner_ref()
                if owner is not None:
                    owner._locked = True
                    owner.counter.clear()

            def didUnlock_(self, notification):
                owner = self.owner_ref()
                if owner is not None:
                    owner._locked = False
                    owner.counter.clear()
                    owner._gap = True

        _OBSERVER_CLASS = FocusPetRhythmObserver
    return _OBSERVER_CLASS


class AggregateCounter:

    def __init__(self):
        self.lock = threading.Lock()
        self.clear()

    def clear(self):
        with self.lock:
            self.values = dict(keyboard=0, clicks=0, scroll=0.0, pointer=0.0)
            self.last_input = None

    def count(self, kind: str, amount: float = 1, now: float | None = None):
        if kind not in ("keyboard", "clicks", "scroll", "pointer") or not math.isfinite(amount):
            return
        with self.lock:
            self.values[kind] += max(0, min(float(amount), 1e6))
            self.last_input = time.monotonic() if now is None else now

    def drain(self):
        with self.lock:
            values = self.values.copy()
            self.values = dict(keyboard=0, clicks=0, scroll=0.0, pointer=0.0)
            return values


class NativeCollector:
    def __init__(self, clock=None, categories=None, mode="real", quartz=None, workspace=None):
        self.clock = clock or SystemClock()
        self.categories = {**DEFAULT_CATEGORIES, **validate_categories(categories or {})}
        self.mode = mode
        self.counter = AggregateCounter()
        self.session_id = uuid.uuid4().hex
        self._caps = {s: "supported" for s in SIGNALS}
        self._caps.update(lock="unavailable", sleep="unavailable")
        self._tap = self._source = None
        self._q, self._workspace = quartz, workspace
        self._paused = True
        self._started = False
        self._sleeping = self._locked = self._own_app = False
        self._suppressed_until = 0.0
        self._observer = None
        self._tap_healthy = False
        self._health_checks = 0
        self._health_failures = 0
        self._last_poll = 0.0
        self._reset_bucket()

    def _mono(self):
        return self.clock.monotonic()

    def _utc(self):
        return self.clock.utc()

    def _reset_bucket(self, preserve_context=False):
        if not preserve_context:
            self.counter.clear()
            self._previous_app = None
        self._begin_mono = self._mono()
        self._begin_utc = self._utc()
        self._previous_poll = self._last_poll = self._begin_mono
        self._dwell: dict[str, float] = {}
        self._switches = 0
        self._idle_s = self._idle_coverage = self._input_coverage = self._app_coverage = (
            self._interaction_s
        ) = 0.0
        self._gap = False

    def capabilities(self):
        try:
            if self._q is None:
                import Quartz as q
            else:
                q = self._q
            granted = bool(q.CGPreflightListenEventAccess())
            for key in ("keyboard", "clicks", "scroll", "pointer", "idle"):
                if not self._started or not granted:
                    self._caps[key] = "granted" if granted else "denied"
            if not self._started:
                self._caps["application"] = "supported"
        except (ImportError, AttributeError):
            self._caps = {s: "unavailable" for s in SIGNALS}
        except Exception:
            for key in ("keyboard", "clicks", "scroll", "pointer", "idle"):
                self._caps[key] = "error"
        return self._caps.copy()

    def diagnostics(self):
        return {
            "started": self._started,
            "paused": self._paused,
            "tap_healthy": self._tap_healthy and not self._paused,
            "health_checks": self._health_checks,
            "health_failures": self._health_failures,
            "poll_age_s": max(0.0, self._mono() - self._last_poll),
            "capabilities": self._caps.copy(),
        }

    def _check_tap_health(self):
        self._health_checks += 1
        try:
            healthy = self._tap is not None and bool(self._q.CGEventTapIsEnabled(self._tap))
        except Exception:
            healthy = False
        self._tap_healthy = healthy
        if not healthy:
            self._health_failures += 1
            for key in ("keyboard", "clicks", "scroll", "pointer", "idle"):
                if self._caps[key] == "granted":
                    self._caps[key] = "error"
            self.counter.clear()
            self._gap = True
        return healthy

    def _install_observers(self):
        try:
            import AppKit
            import Foundation

            observer_class = _get_observer_class(Foundation.NSObject)
            self._observer = observer_class.alloc().init()
            self._observer.owner_ref = weakref.ref(self)
            center = self._workspace.notificationCenter()
            center.addObserver_selector_name_object_(
                self._observer, b"willSleep:", AppKit.NSWorkspaceWillSleepNotification, None
            )
            center.addObserver_selector_name_object_(
                self._observer, b"didWake:", AppKit.NSWorkspaceDidWakeNotification, None
            )
            distributed = Foundation.NSDistributedNotificationCenter.defaultCenter()
            distributed.addObserver_selector_name_object_(
                self._observer, b"didLock:", "com.apple.screenIsLocked", None
            )
            distributed.addObserver_selector_name_object_(
                self._observer, b"didUnlock:", "com.apple.screenIsUnlocked", None
            )
            self._caps["sleep"] = "granted"
            self._caps["lock"] = "supported"
        except Exception:
            self._remove_observers()
            self._caps["sleep"] = "unavailable"
            self._caps["lock"] = "unavailable"

    def _remove_observers(self):
        if self._observer is None:
            return
        try:
            self._workspace.notificationCenter().removeObserver_(self._observer)
        except Exception:
            pass
        try:
            import Foundation

            Foundation.NSDistributedNotificationCenter.defaultCenter().removeObserver_(self._observer)
        except Exception:
            pass
        self._observer = None

    def start(self, request_permission=False):
        if self.mode != "real":
            raise ValueError("Native collector can only run in real mode")
        if self._started:
            self.stop()
        if self._q is None:
            import Quartz as q

            self._q = q
        else:
            q = self._q
        if self._workspace is None:
            from AppKit import NSWorkspace

            self._workspace = NSWorkspace.sharedWorkspace()
        self._paused = False
        self._started = True
        self._reset_bucket()
        self._install_observers()
        granted = bool(q.CGPreflightListenEventAccess())
        if request_permission and not granted:
            granted = bool(q.CGRequestListenEventAccess())
        for key in ("keyboard", "clicks", "scroll", "pointer", "idle"):
            self._caps[key] = "granted" if granted else "denied"
        self._caps["application"] = "granted"
        if not granted:
            return
        event_types = [
            q.kCGEventKeyDown,
            q.kCGEventLeftMouseDown,
            q.kCGEventRightMouseDown,
            q.kCGEventOtherMouseDown,
            q.kCGEventScrollWheel,
            q.kCGEventMouseMoved,
            q.kCGEventLeftMouseDragged,
            q.kCGEventRightMouseDragged,
        ]
        mask = sum(1 << int(t) for t in event_types)

        def callback(proxy, event_type, event, refcon):
            if event_type in (q.kCGEventTapDisabledByTimeout, q.kCGEventTapDisabledByUserInput):
                self._tap_healthy = False
                for key in ("keyboard", "clicks", "scroll", "pointer", "idle"):
                    self._caps[key] = "error"
                self.counter.clear()
                self._gap = True
                return event
            if (
                self._caps["keyboard"] != "granted"
                or self._paused
                or self._sleeping
                or self._locked
                or self._own_app
                or self._mono() < self._suppressed_until
            ):
                return event
            if event_type == q.kCGEventKeyDown:
                self.counter.count("keyboard", now=self._mono())
            elif event_type in (q.kCGEventLeftMouseDown, q.kCGEventRightMouseDown, q.kCGEventOtherMouseDown):
                self.counter.count("clicks", now=self._mono())
            elif event_type == q.kCGEventScrollWheel:
                self.counter.count("scroll", now=self._mono())
            else:
                try:
                    dx = q.CGEventGetIntegerValueField(event, q.kCGMouseEventDeltaX)
                    dy = q.CGEventGetIntegerValueField(event, q.kCGMouseEventDeltaY)
                    self.counter.count("pointer", math.hypot(dx, dy), now=self._mono())
                except Exception:
                    self._caps["pointer"] = "error"
                    self._gap = True
            return event

        self._callback = callback
        self._tap = q.CGEventTapCreate(
            q.kCGSessionEventTap, q.kCGHeadInsertEventTap, q.kCGEventTapOptionListenOnly, mask, callback, None
        )
        if self._tap is None:
            for key in ("keyboard", "clicks", "scroll", "pointer", "idle"):
                self._caps[key] = "error"
            return
        self._source = q.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        q.CFRunLoopAddSource(q.CFRunLoopGetCurrent(), self._source, q.kCFRunLoopCommonModes)
        q.CGEventTapEnable(self._tap, True)
        self._check_tap_health()

    def suppress_interaction(self, seconds=2.0):
        self._suppressed_until = self._mono() + seconds
        self.counter.clear()
        self._gap = True

    def pause(self, paused=True):
        self._paused = paused
        if self._tap is not None:
            self._q.CGEventTapEnable(self._tap, not paused and self._caps["keyboard"] == "granted")
        self._tap_healthy = False
        if not paused and self._caps["keyboard"] == "granted":
            self._check_tap_health()
        self._reset_bucket()

    def stop(self):
        self._paused = True
        self._started = False
        self._tap_healthy = False
        try:
            if self._tap is not None:
                try:
                    self._q.CGEventTapEnable(self._tap, False)
                    if self._source is not None:
                        self._q.CFRunLoopRemoveSource(
                            self._q.CFRunLoopGetCurrent(), self._source, self._q.kCFRunLoopCommonModes
                        )
                finally:
                    self._q.CFMachPortInvalidate(self._tap)
        except Exception:
            for key in ("keyboard", "clicks", "scroll", "pointer", "idle"):
                self._caps[key] = "error"
        finally:
            self._tap = self._source = None
            self._remove_observers()
            self.counter.clear()

    def sample(self):
        if not self._started or self._paused:
            return None
        q = self._q
        try:
            q.CFRunLoopRunInMode(q.kCFRunLoopDefaultMode, 0.001, False)
        except Exception:
            self._gap = True
            self._tap_healthy = False
            for key in ("keyboard", "clicks", "scroll", "pointer", "idle"):
                if self._caps[key] == "granted":
                    self._caps[key] = "error"
            self.counter.clear()
        now = self._mono()
        if now - self._last_poll >= 1:
            elapsed = min(1.2, max(0, now - self._previous_poll))
            if now - self._previous_poll > 3:
                self._gap = True
            self._previous_poll = self._last_poll = now
            try:
                granted = bool(q.CGPreflightListenEventAccess())
            except Exception:
                granted = False
            if not granted:
                self._tap_healthy = False
                if self._tap is not None:
                    q.CGEventTapEnable(self._tap, False)
                for key in ("keyboard", "clicks", "scroll", "pointer", "idle"):
                    self._caps[key] = "denied"
                self.counter.clear()
                self._gap = True
            elif self._caps["keyboard"] == "granted":
                self._check_tap_health()
            usable = (
                granted and self._caps["keyboard"] == "granted" and not self._sleeping and not self._locked
            )
            try:
                app = None if self._sleeping or self._locked else self._workspace.frontmostApplication()
                self._own_app = app is not None and int(app.processIdentifier()) == os.getpid()
                identifier = str(app.bundleIdentifier() or "") if app is not None else ""
                if identifier and not self._own_app:
                    category = self.categories.get(identifier, "Other")
                    self._dwell[category] = self._dwell.get(category, 0) + elapsed
                    self._app_coverage += elapsed
                    if self._previous_app is not None and self._previous_app != identifier:
                        self._switches += 1
                    self._previous_app = identifier
                    self._caps["application"] = "granted"
                else:
                    self._caps["application"] = "unavailable" if app is None else "granted"
            except Exception:
                self._caps["application"] = "error"
                self._own_app = True
                self.counter.clear()
                self._gap = True
            if self._own_app or now < self._suppressed_until:
                self._interaction_s += elapsed
            elif usable:
                self._input_coverage += elapsed
                try:
                    idle = q.CGEventSourceSecondsSinceLastEventType(
                        q.kCGEventSourceStateCombinedSessionState, q.kCGAnyInputEventType
                    )
                    if not math.isfinite(idle) or idle < 0:
                        raise ValueError("Invalid idle observation")
                    self._idle_coverage += elapsed
                    self._caps["idle"] = "granted"
                    if idle >= 5:
                        self._idle_s += elapsed
                except Exception:
                    self._caps["idle"] = "error"
        elapsed = now - self._begin_mono
        if elapsed < 10:
            return None
        values = self.counter.drain()
        valid_input = self._caps["keyboard"] == "granted"
        coverage = {
            key: min(1, self._input_coverage / elapsed) if self._caps[key] == "granted" else 0
            for key in ("keyboard", "clicks", "scroll", "pointer")
        }
        coverage["idle"] = min(1, self._idle_coverage / elapsed) if self._caps["idle"] == "granted" else 0
        coverage["application"] = min(1, self._app_coverage / elapsed)
        observation = "Active"
        reason = None
        wall_end = self._utc()
        if abs((wall_end - self._begin_utc) - elapsed) > 3:
            self._gap = True
        if self._gap or elapsed > 20 or self._sleeping:
            observation, reason = "Missing", "sleep, clock discontinuity or excluded app interaction"
        elif self._locked:
            observation, reason = "Locked", "lock notification"
        elif not valid_input:
            observation, reason = "Missing", "input permission unavailable"
        elif self._idle_s >= elapsed * 0.8:
            observation = "No-input"
        bucket = ActivityBucket(
            start=self._begin_utc,
            end=wall_end,
            duration_s=min(120, elapsed),
            keyboard=int(values["keyboard"]) if valid_input else None,
            clicks=int(values["clicks"]) if valid_input else None,
            scroll=values["scroll"] if self._caps["scroll"] == "granted" else None,
            pointer=values["pointer"] if self._caps["pointer"] == "granted" else None,
            idle_s=self._idle_s if self._caps["idle"] == "granted" else None,
            app_dwell=dict(self._dwell),
            app_switches=self._switches if self._app_coverage else None,
            coverage=coverage,
            observation=observation,
            session_id=self.session_id,
            mode=self.mode,
            missing_reason=reason,
            interaction_s=self._interaction_s,
        )
        self._reset_bucket(preserve_context=True)
        return bucket
