"""Run actual CLI subprocesses with isolated synthetic fixtures and bounded timeouts.

Source: python .github/scripts/ci_smoke.py --output .runtime/cli-source
Frozen: python .github/scripts/ci_smoke.py --executable 'dist/Focus Pet.app/Contents/MacOS/Focus Pet'
        --fixtures .runtime/cli-source/run-.../inputs --output .runtime/cli-frozen
Add --native in an ordinary desktop session to launch demo and unconsented run.
The one-seed/eight-trial evaluate smoke is not the five-seed experiment protocol.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType


CLASSES = ["Focused", "Normal", "Distracted"]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def prepare_inputs(folder, fixtures=None):
    folder.mkdir(parents=True)
    if fixtures:
        for name in ("learning.json", "calibration.json"):
            data = read_json(fixtures / name)
            if data.get("mode") != "synthetic-demo" or data.get("version") != 1:
                raise ValueError("Only version-1 synthetic-demo fixtures are allowed")
            write_json(folder / name, data)
    else:
        from focuspet.features import FEATURE_NAMES
        from focuspet.learning.synthetic import load_dataset

        records = []
        for day in range(6):
            for number in range(6):
                start = 1735722000.0 + day * 86400 + number * 1000
                values = dict.fromkeys(FEATURE_NAMES, 0.0)
                values[FEATURE_NAMES[number % 3]] = 10.0
                identity = f"cli-d{day}-e{number}"
                records.append({"id": identity, "episode_id": identity, "feature_id": identity,
                    "session_id": f"cli-d{day}-s{number//2}", "start": start, "end": start + 300,
                    "labeled_at": start + 330, "label": CLASSES[number % 3], "values": values,
                    "coverage": 1.0, "mode": "synthetic-demo", "source": "synthetic-oracle",
                    "prior": {"Focused": .1, "Normal": .8, "Distracted": .1}})
        write_json(folder / "learning.json", {"version": 1, "mode": "synthetic-demo",
            "feature_names": list(FEATURE_NAMES), "records": records,
            "purpose": "Separable synthetic CLI integration fixture; not accuracy evidence"})
        write_json(folder / "calibration.json", load_dataset(seed=7, days=6))
    write_json(folder / "parameters.json", {"a_user": 1.1, "tau_user": 12.0, "version": "verify-growth"})


def verify(prefix, root, native=False, native_seconds=8, fixtures=None, timeout=180):
    inputs = root / "inputs"
    prepare_inputs(inputs, fixtures)
    evaluation = {"seeds": [7], "label_budgets": [30], "search_budget": 8,
                  "output": str(root / "evaluation")}
    write_json(inputs / "evaluate.json", evaluation)
    env = os.environ.copy()
    env["FOCUSPET_DATA_HOME"] = str(root / "managed")
    env.pop("FOCUSPET_DIAGNOSTICS_PATH", None)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    report = {"schema": "cli-integration-v1", "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "mode": "synthetic-demo", "native_requested": native, "passed": False,
              "evaluation_scope": "one-seed 30-label eight-trial smoke; not five-seed results",
              "commands": [], "checks": {}, "root": str(root)}
    commands = []

    def run(name, args, expected=0, json_output=True, extra_env=None):
        command = [*prefix, *map(str, args)]
        commands.append(shlex.join(command))
        started = time.perf_counter()
        result = subprocess.run(command, cwd=root, env=env | (extra_env or {}),
                                capture_output=True, text=True, timeout=timeout)
        (root / f"{name}.stdout.txt").write_text(result.stdout)
        (root / f"{name}.stderr.txt").write_text(result.stderr)
        report["commands"].append({"name": name, "argv": command, "returncode": result.returncode,
                                   "expected_returncode": expected, "elapsed_s": time.perf_counter()-started})
        write_json(root / "report.json", report)
        if result.returncode != expected:
            raise RuntimeError(f"{name}: exit {result.returncode}; see its private stderr log")
        return json.loads(result.stdout) if json_output and expected == 0 else None

    try:
        doctor = run("doctor", ["doctor"])
        assert doctor["network_required_for_core"] is False and doctor["telemetry"] is False
        assert all(value["status"] == "available" for value in doctor["dependency_imports"].values())
        scenarios = run("scenarios", ["scenarios"])
        assert "reading" in scenarios
        baseline_path = root / "baseline.json"
        run("replay", ["replay", "reading", "--output", baseline_path])
        run("replay-repeat", ["replay", "reading", "--output", root / "repeat.json"])
        baseline = read_json(baseline_path)
        assert baseline == read_json(root / "repeat.json")
        assert baseline["mode"] == "synthetic-demo" and baseline["snapshots"]
        assert all(math.isfinite(row["workload"]) for row in baseline["snapshots"])
        run("replay-parameters", ["replay", "reading", "--reevaluate", "--parameters",
                                  inputs / "parameters.json", "--output", root / "parameters-replay.json"])
        changed = read_json(root / "parameters-replay.json")
        assert changed["parameter_version"] == "verify-growth"
        assert math.isclose(changed["snapshots"][-1]["workload"],
                            baseline["snapshots"][-1]["workload"] * 1.1, rel_tol=1e-10)
        training = run("train", ["train", "--data", inputs / "learning.json", "--mode", "synthetic-demo",
                                 "--output", root / "models"])
        assert training["status"] == "shadow"
        model_version = training["version"]
        artifact = read_json(root / "models" / f"{model_version}.json")
        payload = artifact["payload"]
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        assert artifact["sha256"] == hashlib.sha256(canonical).hexdigest()
        assert payload["format"] == "numeric-model-v1" and payload["classes"] == CLASSES
        assert payload["mode"] == "synthetic-demo" and read_json(root / "models/model-registry.json")["active"] is None
        model_args = ["replay", "reading", "--reevaluate", "--model-dir", root / "models",
                      "--model-version", model_version, "--output", root / "personal-replay.json"]
        run("model-reload", model_args)
        predicted = read_json(root / "personal-replay.json")
        assert any(row["prediction"]["model_version"] == model_version for row in predicted["snapshots"])
        damaged = root / "damaged-models"
        shutil.copytree(root / "models", damaged)
        artifact["payload"]["alpha"] = .25 if payload["alpha"] != .25 else .5
        write_json(damaged / f"{model_version}.json", artifact)
        run("checksum-rejection", ["replay", "reading", "--reevaluate", "--model-dir", damaged,
                                   "--model-version", model_version], expected=1, json_output=False)
        run("mode-rejection", ["train", "--data", inputs / "learning.json", "--mode", "real",
                               "--output", root / "rejected-real"], expected=1, json_output=False)
        assert not (root / "rejected-real").exists()
        calibration = run("calibrate", ["calibrate", "--data", inputs / "calibration.json", "--mode",
                                        "synthetic-demo", "--output", root / "parameters"])
        assert calibration["status"] in {"shadow", "retained-current"}
        assert {item["sampler"] for item in calibration["results"]} == {"random", "tpe"}
        assert all(len(item["trials"]) == 64 and all(t["status"] == "complete" for t in item["trials"])
                   for item in calibration["results"])
        if (root / "parameters/parameter-registry.json").exists():
            assert read_json(root / "parameters/parameter-registry.json")["active"] is None
        evaluated = run("evaluate", ["evaluate", "--config", inputs / "evaluate.json"])
        assert evaluated["seeds"] == [7] and evaluated["label_budgets"] == [30]
        seed = read_json(root / "evaluation/seed-7/results.json")
        assert all(len(seed["calibration"][method]["trials"]) == 8 for method in ("random", "tpe"))
        assert math.isfinite(evaluated["classification"]["30"]["logistic"]["macro_f1_mean"])
        exported = run("export-demo", ["export-demo", "--scenario", "reading", "--output", root / "demo"])
        assert exported["mode"] == "synthetic-demo" and exported["network_requests"] == 0
        assert all((root / "demo" / name).is_file() for name in ("index.html", "app.js", "style.css", "trajectory.js"))
        content = (root / "demo/trajectory.js").read_text()
        demo = json.loads(content.removeprefix("window.FOCUS_PET_DEMO = ").rstrip().removesuffix(";"))
        assert demo["scenarios"]["reading"]["original"]["snapshots"] == baseline["snapshots"]
        assert demo["scenarios"]["reading"]["corrected"]["correction"] is not None
        import struct
        image_module: ModuleType | None = None
        try:
            from PIL import Image
            image_module = Image
        except ImportError:
            pass
        for name in ("mira", "jun", "ada", "sol"):
            assert len(demo["characters"][name]["actions"]) == 8
            sprite = root / "demo/characters" / name / "sprites.png"
            header = sprite.read_bytes()[:29]
            assert header[:8] == b"\x89PNG\r\n\x1a\n"
            assert struct.unpack(">II", header[16:24]) == (256, 640)
            assert header[24:26] == bytes([8, 6])
            if image_module is not None:
                with image_module.open(sprite) as image:
                    assert image.mode == "RGBA" and image.getchannel("A").getextrema() == (0, 255)
        report["checks"] = {"deterministic_replay": True, "parameter_numeric_effect": True,
            "numeric_candidate_created_and_reloaded": True, "no_early_model_or_parameter_activation": True,
            "corrupt_artifact_rejected": True, "synthetic_to_real_rejected": True,
            "calibration_trials_each": 64, "evaluate_seeds": 1, "evaluate_trials_each": 8,
            "static_demo_matches_core": True, "bundled_characters": 4,
            "alpha_extrema": "passed" if image_module is not None else "not tested; driver Pillow unavailable",
            "browser_interaction": "not tested"}
        for command in ("demo", "run"):
            data_dir = root / f"native-{command}"
            native_args = [command, "--data-dir", data_dir, "--quit-after", native_seconds,
                           "--screenshot", root / f"native-{command}.png"]
            if native:
                diagnostics = root / f"native-{command}-diagnostics.json"
                run(command, native_args, json_output=False,
                    extra_env={"FOCUSPET_DIAGNOSTICS_PATH": str(diagnostics)})
                snapshot = read_json(diagnostics)
                assert snapshot["consent"] is False
                assert snapshot["mode"] == ("real" if command == "run" else "synthetic-demo")
                assert (root / f"native-{command}.png").is_file()
            else:
                commands.append("# Native not executed: " + shlex.join([*prefix, *map(str, native_args)]))
        report["checks"]["native_demo_and_unconsented_run"] = "passed" if native else "not tested"
        report["passed"] = True
    except (AssertionError, OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        report["failure"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(root / "report.json", report)
        (root / "commands.txt").write_text("\n".join(commands) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, help="Reuse only synthetic-demo JSON inputs; needed without source package")
    parser.add_argument("--executable", "--app", dest="app", type=Path, help="Frozen executable, not its .app directory")
    parser.add_argument("--native", action="store_true", help="Also launch desktop windows; never grants consent")
    parser.add_argument("--native-seconds", type=float, default=8)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    if not 2 <= args.native_seconds <= 60 or not 10 <= args.timeout <= 600:
        parser.error("Native seconds must be 2..60 and command timeout 10..600")
    if args.app and not args.fixtures:
        parser.error("--executable requires prepared --fixtures to keep the driver independent of application imports")
    args.output.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="run-", dir=args.output.resolve()))
    prefix = [str(args.app.resolve())] if args.app else [sys.executable, "-m", "focuspet.cli"]
    report = verify(prefix, root, args.native, args.native_seconds,
                    args.fixtures.resolve() if args.fixtures else None, args.timeout)
    write_json(args.output.resolve() / "summary.json", report)
    print(json.dumps({"passed": report["passed"], "root": str(root), "checks": report["checks"],
                      "failure": report.get("failure")}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
