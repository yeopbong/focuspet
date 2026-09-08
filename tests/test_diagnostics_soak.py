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


def test_transient_process_oserror_preserves_other_fields_and_recovers():
    import errno

    process = Process()
    sampler = soak.ProcessSampler(7, process_factory=lambda _: process)
    original = process.memory_info

    def unavailable():
        raise OSError(errno.EIO, "temporary failure", "/private/not-to-record")

    process.memory_info = unavailable
    partial = sampler.sample(100)
    assert partial["status"] == "partial" and partial["identity_verified"]
    assert partial["main"]["rss_bytes"] is None and partial["total_rss_bytes"] is None
    assert partial["main"]["threads"] == 2 and partial["main"]["cpu_seconds"] == 1
    assert partial["errors"][0]["operation"] == "process.memory_info"
    assert partial["errors"][0]["errno"] == errno.EIO
    assert "/private/" not in json.dumps(partial)
    process.memory_info = original
    assert sampler.sample(105)["status"] == "available"


def test_child_enumeration_oserror_is_unknown_not_zero():
    import errno

    process = Process()

    def unavailable(recursive=True):
        raise OSError(errno.EINTR, "interrupted")

    process.children = unavailable
    result = soak.ProcessSampler(7, process_factory=lambda _: process).sample(100)
    assert result["children_complete"] is False and result["unavailable_children"] is None
    assert result["total_threads"] is None and result["total_rss_bytes"] is None
    assert result["errors"][0]["operation"] == "process.children"


def test_monitor_transient_write_failure_recovers_with_evidence(tmp_path):
    import errno
    import io

    clock, process = Clock(), Process()
    calls = []

    def writer(path, value):
        calls.append(1)
        if len(calls) == 1:
            raise OSError(errno.EIO, "write unavailable")
        soak.atomic_json(path, value)

    errors = io.StringIO()
    report = soak.monitor(
        pid=7,
        minutes=0.1,
        interval=2,
        output=tmp_path / "recovered.json",
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        sampler_factory=lambda pid: soak.ProcessSampler(pid, process_factory=lambda _: process),
        writer=writer,
        error_stream=errors,
    )
    saved = json.loads((tmp_path / "recovered.json").read_text())
    assert report["completed"] and saved["report_persisted"]
    assert saved["write_failures"] == 1 and saved["ended_utc"]
    assert saved["errors"][0]["operation"] == "report.write"
    assert '"errno": 5' in errors.getvalue()


def test_persistent_write_failure_ends_incomplete_and_preserves_final_error(tmp_path):
    import errno
    import io

    clock, process = Clock(), Process()

    def writer(path, value):
        raise OSError(errno.ENOSPC, "no space")

    errors = io.StringIO()
    report = soak.monitor(
        pid=7,
        minutes=2,
        interval=2,
        output=tmp_path / "unwritten.json",
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        sampler_factory=lambda pid: soak.ProcessSampler(pid, process_factory=lambda _: process),
        writer=writer,
        error_stream=errors,
    )
    assert not report["completed"] and not report["report_persisted"]
    assert report["ended_utc"] and report["actual_elapsed_s"] == 4
    assert report["write_failures"] == 4 and len(report["samples"]) == 3
    assert report["stop_reason"] == "final report could not be persisted"
    assert report["errors"][0]["errno"] == errno.ENOSPC
    assert '"event": "monitor-final"' in errors.getvalue()


def test_unexpected_sample_oserror_marks_missing_sample_without_losing_run(tmp_path):
    import errno
    import io

    clock, process = Clock(), Process()
    sampler = soak.ProcessSampler(7, process_factory=lambda _: process)
    original = sampler.sample
    calls = []

    def sometimes(now):
        calls.append(now)
        if len(calls) == 2:
            raise OSError(errno.EIO, "temporary process read")
        return original(now)

    sampler.sample = sometimes
    report = soak.monitor(
        pid=7,
        minutes=0.1,
        interval=2,
        output=tmp_path / "partial.json",
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        sampler_factory=lambda _: sampler,
        error_stream=io.StringIO(),
    )
    assert report["completed"] and report["error_count"] == 1
    assert report["summary"]["resource_missing_samples"] == 1
    assert report["samples"][1]["process"]["main"]["rss_bytes"] is None
    assert report["samples"][2]["process"]["main"]["rss_bytes"] == 1024
