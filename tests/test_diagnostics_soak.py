"""Deterministic harness regressions. Simulated time is never a longevity result."""

import importlib.util
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from focuspet.diagnostics import Diagnostics
from focuspet.service import AppService

spec = importlib.util.spec_from_file_location("soak", Path(__file__).parents[1] / "scripts" / "soak.py")
soak = importlib.util.module_from_spec(spec)
spec.loader.exec_module(soak)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Process:
    pid = 7
    created = 100
    seconds = 1
    alive = True

    def create_time(self):
        return self.created

    def cpu_times(self):
        return SimpleNamespace(user=self.seconds, system=0)

    def is_running(self):
        return self.alive

    def status(self):
        return "running"

    def memory_info(self):
        return SimpleNamespace(rss=1024)

    def num_threads(self):
        return 2

    def children(self, recursive=True):
        return []


def test_process_sampler_cpu_identity_and_unavailable_io():
    process = Process()
    sampler = soak.ProcessSampler(7, process_factory=lambda pid: process)
    first = sampler.sample(100)
    assert first["main"]["cpu_percent_one_core"] is None
    assert first["main"]["io"] is None
    process.seconds = 2
    assert sampler.sample(105)["main"]["cpu_percent_one_core"] == 20
    process.created = 101
    with pytest.raises(psutil.NoSuchProcess):
        sampler.sample(110)


def test_monitor_uses_elapsed_time_and_never_stitches_restarts(tmp_path):
    clock, process = Clock(), Process()

    def factory(pid):
        return soak.ProcessSampler(pid, process_factory=lambda _: process)

    report = soak.monitor(
        pid=7,
        minutes=0.1,
        interval=2,
        output=tmp_path / "run.json",
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        sampler_factory=factory,
    )
    assert report["completed"] and report["actual_elapsed_s"] == 6
    assert [s["elapsed_s"] for s in report["samples"]] == [0, 2, 4, 6]
    assert report["summary"]["phases"]["unknown"]["samples"] == 4
    assert json.loads((tmp_path / "run.json").read_text())["duration_completed"]

    def restart(seconds):
        clock.sleep(seconds)
        process.created += 1

    interrupted = soak.monitor(
        pid=7,
        minutes=2,
        output=tmp_path / "restart.json",
        monotonic=clock.monotonic,
        sleep=restart,
        sampler_factory=factory,
    )
    assert not interrupted["completed"] and not interrupted["duration_completed"]
    assert interrupted["stop_reason"] == "process exited or identity changed"


def test_long_scheduler_gap_is_not_continuous_validation(tmp_path):
    clock, process = Clock(), Process()
    report = soak.monitor(
        pid=7,
        minutes=1,
        output=tmp_path / "gap.json",
        monotonic=clock.monotonic,
        sleep=lambda _: clock.sleep(120),
        sampler_factory=lambda pid: soak.ProcessSampler(pid, process_factory=lambda _: process),
    )
    assert report["duration_completed"] and not report["continuous_observation"]
    assert not report["completed"] and report["max_sample_gap_s"] == 120


def test_diagnostic_reader_requires_identity_freshness_and_excludes_extra_fields(tmp_path):
    path = tmp_path / "heartbeat.json"
    path.write_text(
        json.dumps(
            {
                "schema": "local-diagnostics-v1",
                "pid": 7,
                "heartbeat_monotonic": 100,
                "path": "/private/location",
            }
        )
    )
    assert soak.read_diagnostics(path, 8, 100)["status"] == "identity mismatch"
    result = soak.read_diagnostics(path, 7, 100)
    assert result["status"] == "fresh"
    assert "path" not in result["snapshot"]
    assert soak.read_diagnostics(path, 7, 120)["status"] == "stale or stopped"


def test_opt_in_service_diagnostics_are_scalar_and_never_collect(tmp_path):
    destination = tmp_path / "heartbeat.json"
    service = AppService(mode="test", data_dir=tmp_path / "data", diagnostics_path=destination)
    try:
        assert service.wait_ready()
        service.snapshot()
        service._diagnostics.write(service, force=True)
        result = json.loads(destination.read_text())
        assert result["mode"] == "test" and not result["consent"]
        assert result["ui_cache_poll_count"] == 1
        assert result["collector"] == {"adapter": "unavailable"}
        assert result["storage"]["actor_alive"]
        assert result["storage"]["operations"] >= 1
        before = service.store.diagnostics()["write_transactions"]
        service.store.save_settings({"quiet": True})
        assert service.store.diagnostics()["write_transactions"] == before + 1
        assert not {"settings", "history", "feedback", "root", "path", "evidence"}.intersection(result)
        assert str(tmp_path) not in destination.read_text()
        blocked = tmp_path / "blocked"
        blocked.write_text("occupied")
        service._diagnostics = Diagnostics(blocked / "heartbeat.json")
        service._diagnostics.write(service, force=True)
        assert service._diagnostics.write_errors == 1
        assert service.snapshot()["status"] == "Ready"
    finally:
        service.close()
    assert not service._thread.is_alive()


def test_close_stops_service_even_when_action_queue_is_saturated():
    service = AppService.__new__(AppService)
    service._stop = threading.Event()
    service.command = lambda name: False
    service._thread = SimpleNamespace(join=lambda timeout: None)
    service.close()
    assert service._stop.is_set()
