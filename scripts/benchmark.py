from __future__ import annotations
import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]


def directory_size(folder):
    return sum(p.stat().st_size for p in Path(folder).rglob("*") if p.is_file())


def measure_process(command, seconds=None):
    start = time.monotonic()
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=ROOT,
        env={**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"},
    )
    handle = psutil.Process(process.pid)
    cpu, rss, steady = [], [], []
    interval = 0.5 if seconds else 0.05
    handle.cpu_percent(None)
    while process.poll() is None:
        try:
            value = handle.cpu_percent(interval=interval)
            cpu.append(value)
            if time.monotonic() - start > 5:
                steady.append(value)
            rss.append(handle.memory_info().rss / 1024**2)
        except psutil.NoSuchProcess:
            break
        if seconds and time.monotonic() - start > seconds + 15:
            process.terminate()
            break
    code = process.wait()
    return {
        "elapsed_s": time.monotonic() - start,
        "exit_code": code,
        "cpu_percent_one_logical_core_mean": statistics.mean(cpu) if cpu else None,
        "cpu_percent_steady_after_5s_mean": statistics.mean(steady) if steady else None,
        "rss_mb_peak": max(rss) if rss else None,
        "rss_mb_first": rss[0] if rss else None,
        "rss_mb_last": rss[-1] if rss else None,
        "samples": len(rss),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--output", default="experiments/performance.json")
    parser.add_argument("--paced-child", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.paced_child:
        from focuspet.collectors.replay import ReplayCollector
        from focuspet.service import AppService
        from focuspet.ui import run_app

        class PacedCollector(ReplayCollector):
            next_tick = 0.0

            def sample(self):
                now = time.monotonic()
                if now < self.next_tick:
                    return None
                self.next_tick = now + 10
                return super().sample()

        class PacedService(AppService):
            def _make_collector(self):
                return PacedCollector("coding")

        service = PacedService(mode="synthetic-demo", data_dir=args.paced_child)
        return run_app(service=service, quit_after=args.seconds)
    root = Path(tempfile.mkdtemp(prefix="rhythm-benchmark-"))
    results = {
        "platform": "macOS arm64",
        "protocol": "Short native measured runs, not a two-hour soak",
        "duration_per_native_case_s": args.seconds,
        "native_input": "not collected; consent absent",
        "two_hour_soak": "not verified",
    }
    paced = root / "companion-normal-cadence-synthetic"
    results["companion-normal-cadence-synthetic"] = measure_process(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--paced-child",
            str(paced),
            "--seconds",
            str(args.seconds),
        ],
        args.seconds,
    )
    results["companion-normal-cadence-synthetic"]["managed_data_bytes"] = directory_size(paced)
    results["companion-normal-cadence-synthetic"]["scope"] = (
        "Production UI/service with synthetic collector paced at one bucket per 10 real seconds; "
        "does not include native OS input callback overhead."
    )
    for name, mode in [("demo-accelerated-40x", "demo"), ("idle-unconsented", "run")]:
        folder = root / name
        results[name] = measure_process(
            [
                sys.executable,
                "-m",
                "focuspet",
                mode,
                "--data-dir",
                str(folder),
                "--quit-after",
                str(args.seconds),
            ],
            args.seconds,
        )
        results[name]["managed_data_bytes"] = directory_size(folder)
    results["training_process"] = measure_process(
        [
            sys.executable,
            "-m",
            "focuspet",
            "train",
            "--data",
            "experiments/results/seed-7/work-data.json",
            "--mode",
            "synthetic-demo",
            "--output",
            str(root / "trained"),
        ]
    )
    results["training_process"]["managed_data_bytes"] = directory_size(root / "trained")
    results["replay_process"] = measure_process(
        [sys.executable, "-m", "focuspet", "replay", "workday", "--output", str(root / "trajectory.json")]
    )
    results["replay_process"]["output_bytes"] = (
        (root / "trajectory.json").stat().st_size if (root / "trajectory.json").exists() else 0
    )
    from focuspet.replay import replay_scenario
    from focuspet.domain import FeatureWindow
    from focuspet.models.prior import GenericPrior

    trajectory = replay_scenario("coding")
    window = FeatureWindow(**trajectory["snapshots"][-1]["feature"])
    model = GenericPrior()
    latency = []
    for _ in range(500):
        start = time.perf_counter()
        model.predict(window)
        latency.append((time.perf_counter() - start) * 1000)
    results["prior_inference_ms"] = {"p50": statistics.median(latency), "p95": sorted(latency)[474]}
    from focuspet.learning.registry import ModelRegistry
    from focuspet.models.personal import PersonalPredictor

    registry = ModelRegistry(root / "trained", "synthetic-demo")
    versions = list(registry.state["versions"])
    if versions:
        predictor = PersonalPredictor(registry._load(versions[-1]))
        latency = []
        for _ in range(500):
            start = time.perf_counter()
            predictor.predict(window)
            latency.append((time.perf_counter() - start) * 1000)
        results["personal_inference_ms"] = {"p50": statistics.median(latency), "p95": sorted(latency)[474]}
    Path(args.output).write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
