"""Monitor an existing desktop process for real elapsed time; never starts collection.

Example: python scripts/soak.py --pid 123 --minutes 120 --output .runtime/soak.json
  --diagnostics .runtime/heartbeat.json --database /local/mode/activity.sqlite3
The app writes diagnostics when FOCUSPET_DIAGNOSTICS_PATH is set before launch.
UI polling is a liveness signal, not proof that a click worked. Record observed checks with:
  python scripts/soak.py --markers .runtime/checks.jsonl --mark pause --outcome pass
Reports are local validation artifacts. A restart always requires a new run.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import traceback
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

DIAGNOSTIC_FIELDS = {
    "schema",
    "pid",
    "mode",
    "wall_utc",
    "heartbeat_monotonic",
    "elapsed_s",
    "stopped",
    "coordinator_alive",
    "loop_count",
    "bucket_count",
    "snapshot_count",
    "command_queue",
    "command_capacity",
    "event_queue",
    "event_capacity",
    "consent",
    "privacy_paused",
    "declared_rest",
    "training_active",
    "training_generation",
    "ui_cache_poll_count",
    "ui_cache_poll_age_s",
    "ui_cache_poll_max_interval_s",
    "ui_interaction_validation",
    "inference",
    "bucket_processing",
    "actions",
    "action_semantics",
    "errors",
    "diagnostic_write_errors",
    "collector",
    "storage",
}
CHECKS = (
    "pause",
    "resume",
    "rest",
    "end_rest",
    "feedback",
    "settings",
    "drag",
    "tray",
    "query",
    "delete",
    "permission",
    "state_update",
    "ui_responsiveness",
)


def atomic_json(destination, value):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # A unique sibling avoids collisions with another inspector using the same name.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=destination.name + ".",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2, allow_nan=False)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def error_details(error, operation):
    """Keep actionable API/errno/stack locations without private paths or exception payloads."""
    return {
        "operation": operation,
        "type": type(error).__name__,
        "errno": getattr(error, "errno", None),
        "stack": [
            {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
            for frame in traceback.extract_tb(error.__traceback__)
        ],
    }


def missing_process(pid, error):
    return {
        "main": {
            "pid": pid,
            "cpu_percent_one_core": None,
            "cpu_seconds": None,
            "rss_bytes": None,
            "threads": None,
            "io": None,
        },
        "children": [],
        "children_complete": False,
        "unavailable_children": None,
        "total_rss_bytes": None,
        "total_threads": None,
        "identity_verified": False,
        "status": "unavailable",
        "errors": [error],
    }


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def read_diagnostics(path, pid, now):
    if path is None:
        return {"status": "not configured"}
    try:
        if Path(path).stat().st_size > 1_048_576:
            return {"status": "oversized"}
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if raw.get("schema") != "local-diagnostics-v1" or raw.get("pid") != pid:
            return {"status": "identity mismatch"}
        heartbeat = raw.get("heartbeat_monotonic")
        if not finite(heartbeat) or heartbeat > now + 1:
            return {"status": "invalid clock"}
        age = max(0.0, now - heartbeat)
        return {
            "status": "fresh" if age <= 15 and not raw.get("stopped") else "stale or stopped",
            "age_s": age,
            "snapshot": {key: raw[key] for key in DIAGNOSTIC_FIELDS if key in raw},
        }
    except (OSError, ValueError, AttributeError, TypeError):
        return {"status": "unavailable"}


def database_sizes(path):
    if path is None:
        return None
    sizes: dict[str, int | None] = {}
    for key, suffix in (("database_bytes", ""), ("wal_bytes", "-wal"), ("shm_bytes", "-shm")):
        try:
            sizes[key] = Path(str(path) + suffix).stat().st_size
        except FileNotFoundError:
            sizes[key] = 0
        except OSError:
            sizes[key] = None
    available = [size for size in sizes.values() if size is not None]
    sizes["total_bytes"] = sum(available) if len(available) == 3 else None
    return sizes


class ProcessSampler:
    """One process identity and descendants; CPU uses nonblocking deltas."""

    def __init__(self, pid, process_factory=psutil.Process):
        self.main = process_factory(pid)
        self.identity = (pid, self.main.create_time())
        self.previous = {}

    @staticmethod
    def _read(process, name, errors, call=None):
        try:
            return (call or getattr(process, name))()
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            raise
        except (OSError, psutil.Error) as error:
            errors.append(error_details(error, "process." + name))
            return None

    def _one(self, process, now, errors):
        created = self._read(process, "create_time", errors)
        identity = (process.pid, created)
        cpu = self._read(process, "cpu_times", errors)
        seconds = cpu.user + cpu.system if cpu is not None else None
        previous = self.previous.get(identity)
        percent = None
        if seconds is not None and created is not None:
            if previous is not None and now > previous[0]:
                percent = max(0, seconds - previous[1]) / (now - previous[0]) * 100
            self.previous[identity] = (now, seconds)
        try:
            io = process.io_counters()
            io_counts = {
                key: getattr(io, key, None)
                for key in ("read_count", "write_count", "read_bytes", "write_bytes")
            }
        except (AttributeError, NotImplementedError):
            io_counts = None  # macOS does not expose this psutil capability.
        except (OSError, psutil.Error) as error:
            errors.append(error_details(error, "process.io_counters"))
            io_counts = None
        memory = self._read(process, "memory_info", errors)
        return {
            "pid": process.pid,
            "cpu_percent_one_core": percent,
            "cpu_seconds": seconds,
            "rss_bytes": memory.rss if memory is not None else None,
            "threads": self._read(process, "num_threads", errors),
            "io": io_counts,
        }

    def sample(self, now):
        errors: list[dict] = []
        running = self._read(self.main, "is_running", errors)
        created = self._read(self.main, "create_time", errors)
        status = self._read(self.main, "status", errors)
        if (
            running is False
            or (created is not None and created != self.identity[1])
            or status == psutil.STATUS_ZOMBIE
        ):
            raise psutil.NoSuchProcess(self.identity[0])
        verified = running is True and created == self.identity[1] and status is not None
        main = self._one(self.main, now, errors)
        children = []
        unavailable_children = 0
        descendants = self._read(self.main, "children", errors, lambda: self.main.children(recursive=True))
        children_complete = descendants is not None
        live_ids = {self.identity}
        for child in descendants or []:
            try:
                children.append(self._one(child, now, errors))
                live_ids.add((child.pid, child.create_time()))
            except (OSError, psutil.Error) as error:
                errors.append(error_details(error, "child.sample"))
                unavailable_children += 1
                children_complete = False
        self.previous = {key: value for key, value in self.previous.items() if key in live_ids}
        group = [main] + children
        return {
            "main": main,
            "children": children,
            "children_complete": children_complete,
            "unavailable_children": unavailable_children if descendants is not None else None,
            "total_rss_bytes": sum(p["rss_bytes"] for p in group)
            if children_complete and all(p["rss_bytes"] is not None for p in group)
            else None,
            "total_threads": sum(p["threads"] for p in group)
            if children_complete and all(p["threads"] is not None for p in group)
            else None,
            "identity_verified": verified,
            "status": "partial" if errors else "available",
            "errors": errors,
        }


def _stats(values):
    values = sorted(value for value in values if finite(value))
    if not values:
        return {"count": 0, "median": None, "p95": None, "max": None}
    return {
        "count": len(values),
        "median": values[len(values) // 2],
        "p95": values[min(len(values) - 1, int(len(values) * 0.95))],
        "max": values[-1],
    }


def summarize(samples):
    phases = {}
    for name in ("companion", "training", "unknown"):
        phase = [sample for sample in samples if sample["phase"] == name]
        phases[name] = {
            "samples": len(phase),
            "main_cpu_percent_one_core": _stats(s["process"]["main"]["cpu_percent_one_core"] for s in phase),
            "main_rss_bytes": _stats(s["process"]["main"]["rss_bytes"] for s in phase),
            "total_rss_bytes": _stats(s["process"]["total_rss_bytes"] for s in phase),
            "total_threads": _stats(s["process"]["total_threads"] for s in phase),
        }
    snapshots = [s["diagnostics"].get("snapshot", {}) for s in samples]
    return {
        "phases": phases,
        "resource_missing_samples": sum(
            s["process"].get("status", "available") != "available" for s in samples
        ),
        "stale_or_missing_diagnostic_samples": sum(s["diagnostics"]["status"] != "fresh" for s in samples),
        "slow_ui_poll_samples": sum((s.get("ui_cache_poll_age_s") or 0) > 5 for s in snapshots),
        "max_command_queue": max((s.get("command_queue", 0) for s in snapshots), default=0),
        "max_storage_queue": max((s.get("storage", {}).get("queue_depth", 0) for s in snapshots), default=0),
        "healthy_native_samples": sum(bool(s.get("collector", {}).get("tap_healthy")) for s in snapshots),
        "consented_samples": sum(bool(s.get("consent")) for s in snapshots),
        "native_validation": "Requires real mode, consent, healthy listener and observed interaction checks.",
        "first_database_sizes": samples[0]["database"] if samples else None,
        "last_database_sizes": samples[-1]["database"] if samples else None,
    }


def read_markers(path, started, ended):
    if path is None:
        return []
    try:
        result = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
                if (
                    item.get("action") in CHECKS
                    and item.get("outcome") in ("pass", "fail", "not-tested")
                    and finite(item.get("monotonic"))
                    and started <= item["monotonic"] <= ended
                ):
                    result.append(
                        {key: item.get(key) for key in ("action", "outcome", "observer", "utc", "monotonic")}
                    )
            except (ValueError, AttributeError, TypeError):
                continue
        return result
    except OSError:
        return []


def monitor(
    *,
    pid,
    minutes,
    output,
    interval=5,
    diagnostics=None,
    database=None,
    markers=None,
    monotonic=time.monotonic,
    sleep=time.sleep,
    sampler_factory=ProcessSampler,
    writer=None,
    error_stream=None,
):
    if not finite(minutes) or minutes <= 0 or not finite(interval) or not 0.1 <= interval <= 60:
        raise ValueError("Duration must be positive and sample interval must be 0.1 to 60 seconds")
    writer = writer or atomic_json
    error_stream = error_stream or sys.stderr
    sampler = sampler_factory(pid)
    started = monotonic()
    duration = minutes * 60
    report = {
        "schema": "wall-clock-soak-v3",
        "started_utc": utc_now(),
        "pid": pid,
        "process_created_utc_seconds": sampler.identity[1],
        "requested_seconds": duration,
        "interval_seconds": interval,
        "samples": [],
        "completed": False,
        "duration_completed": False,
        "continuous_observation": True,
        "max_sample_gap_s": 0.0,
        "scope": "Process resources, cached service health and separately recorded UI checks.",
        "restart_policy": "One identity per run; restarts are never stitched into continuous duration.",
        "phase_limits": "Transition intervals are unclassified; children shorter than one sample may be missed.",
        "error_count": 0,
        "write_failures": 0,
        "errors": [],
        "report_persisted": False,
        "stop_reason": "monitoring",
        "monitor_status": "running",
    }
    previous = last_verified = started
    previous_phase = previous_training = None
    consecutive_writes_failed = 0

    def record_error(detail):
        entry = {"elapsed_s": monotonic() - started, **detail}
        report["error_count"] += 1
        report["errors"].append(entry)
        report["errors"] = report["errors"][-100:]  # Samples retain each per-field missing observation.
        try:
            print(json.dumps({"event": "monitor-error", **entry}), file=error_stream, flush=True)
        except OSError:
            pass  # A closed console must not stop file-backed monitoring.

    def persist():
        nonlocal consecutive_writes_failed
        report["report_persisted"] = True
        try:
            writer(output, report)
            consecutive_writes_failed = 0
            return True
        except (OSError, ValueError, TypeError) as error:
            report["report_persisted"] = False
            report["write_failures"] += 1
            consecutive_writes_failed += 1
            record_error(error_details(error, "report.write"))
            return False

    try:
        while True:
            now = monotonic()
            gap = now - previous
            report["max_sample_gap_s"] = max(report["max_sample_gap_s"], gap)
            if gap > max(30, interval * 3):
                report["continuous_observation"] = False
            try:
                process = sampler.sample(now)
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                raise
            except (OSError, psutil.Error) as error:
                process = missing_process(pid, error_details(error, "process.sample"))
            for detail in process.get("errors", []):
                record_error(detail)
            if process.get("identity_verified", True):
                last_verified = now
            elif now - last_verified > max(30, interval * 3):
                report["continuous_observation"] = False
            heartbeat = read_diagnostics(diagnostics, pid, now)
            snapshot = heartbeat.get("snapshot", {})
            phase = "unknown"
            if heartbeat["status"] == "fresh":
                phase = "training" if snapshot.get("training_active") else "companion"
                if snapshot.get("mode") == "synthetic-demo":
                    phase = "unknown"
            raw_phase = phase
            training = snapshot.get("training_generation")
            if phase != previous_phase or training != previous_training:
                phase = "unknown"
            previous_phase, previous_training = raw_phase, training
            report["samples"].append(
                {
                    "elapsed_s": now - started,
                    "utc": utc_now(),
                    "phase": phase,
                    "process": process,
                    "diagnostics": heartbeat,
                    "database": database_sizes(database),
                }
            )
            report["actual_elapsed_s"] = now - started
            report["summary"] = summarize(report["samples"])
            report["manual_checks"] = read_markers(markers, started, now)
            if now - started >= duration:
                report["duration_completed"] = True
                report["completed"] = report["continuous_observation"]
                report["stop_reason"] = "requested duration observed"
                break
            if not persist() and consecutive_writes_failed >= 3:
                report["stop_reason"] = "report persistence unavailable"
                break
            previous = now
            sleep(min(interval, max(0.0, duration - (monotonic() - started))))
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        report["stop_reason"] = "process exited or identity changed"
    except KeyboardInterrupt:
        report["stop_reason"] = "interrupted"
    except Exception as error:
        record_error(error_details(error, "monitor.loop"))
        report["stop_reason"] = "unexpected monitor failure"
    finally:
        report["actual_elapsed_s"] = monotonic() - started
        report["ended_utc"] = utc_now()
        report["summary"] = summarize(report["samples"])
        report["manual_checks"] = read_markers(markers, started, monotonic())
        report["monitor_status"] = "completed" if report["completed"] else "incomplete"
        if not persist():
            report["completed"] = False
            report["monitor_status"] = "incomplete"
            report["stop_reason"] = "final report could not be persisted"
            try:
                print(
                    json.dumps(
                        {
                            "event": "monitor-final",
                            **{
                                key: report[key]
                                for key in (
                                    "completed",
                                    "ended_utc",
                                    "actual_elapsed_s",
                                    "stop_reason",
                                    "error_count",
                                    "write_failures",
                                )
                            },
                        }
                    ),
                    file=error_stream,
                    flush=True,
                )
            except OSError:
                pass
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--pid", type=int)
    parser.add_argument("--minutes", type=float, default=120)
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--diagnostics", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--markers", type=Path)
    parser.add_argument("--mark", choices=CHECKS)
    parser.add_argument("--outcome", choices=("pass", "fail", "not-tested"))
    parser.add_argument("--observer", choices=("manual", "automation"), default="manual")
    args = parser.parse_args(argv)
    if args.mark:
        if not args.markers or not args.outcome:
            parser.error("--mark requires --markers and --outcome")
        args.markers.parent.mkdir(parents=True, exist_ok=True)
        with args.markers.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "action": args.mark,
                        "outcome": args.outcome,
                        "observer": args.observer,
                        "utc": utc_now(),
                        "monotonic": time.monotonic(),
                    }
                )
                + "\n"
            )
        return 0
    if not args.pid or not args.output:
        parser.error("Monitoring requires --pid and --output")
    try:
        report = monitor(
            pid=args.pid,
            minutes=args.minutes,
            interval=args.interval,
            output=args.output,
            diagnostics=args.diagnostics,
            database=args.database,
            markers=args.markers,
        )
    except (ValueError, OSError, psutil.Error) as error:
        parser.exit(2, json.dumps(error_details(error, "monitor.initialization")) + "\n")
    print(json.dumps({key: report[key] for key in ("completed", "actual_elapsed_s", "stop_reason")}))
    return 0 if report["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
