"""Focus Pet command line. No HTTP server, network client, or content collection."""

from __future__ import annotations
import argparse
import json
import multiprocessing
import sys
from pathlib import Path


def parser():
    result = argparse.ArgumentParser(
        prog="focuspet", description="A quiet local desktop companion for work rhythms."
    )
    result.add_argument("--version", action="version", version="Focus Pet 0.1.0")
    sub = result.add_subparsers(dest="command")
    for name in ("run", "demo"):
        p = sub.add_parser(name, help="Start the native desktop companion")
        p.add_argument("--data-dir", type=Path)
        p.add_argument("--scenario", default="workday")
        p.add_argument("--quit-after", type=float, help=argparse.SUPPRESS)
        p.add_argument("--screenshot", type=Path, help=argparse.SUPPRESS)
    sub.add_parser("doctor", help="Read-only platform, dependency and permission checks")
    replay = sub.add_parser("replay", help="Replay a deterministic scenario or a local exported history")
    replay.add_argument("scenario", help="Built-in scenario name, scenario-v1 JSON, or export-v1 JSON")
    replay.add_argument("--seed", type=int, default=7)
    replay.add_argument("--output", type=Path)
    replay.add_argument(
        "--profile", help="Initial profile; required for strict exported-history recomputation"
    )
    replay_mode = replay.add_mutually_exclusive_group()
    replay_mode.add_argument(
        "--reevaluate", action="store_true", help="Recompute with explicitly selected/current versions"
    )
    replay_mode.add_argument(
        "--recompute-history",
        action="store_true",
        help="Require historical artifacts and verify exact saved results",
    )
    replay_mode.add_argument(
        "--recorded", action="store_true", help="Play immutable saved snapshots (default for exports)"
    )
    replay.add_argument("--parameters", type=Path)
    replay.add_argument("--model-dir", type=Path)
    replay.add_argument("--model-version")
    replay.add_argument(
        "--parameter-dir", type=Path, help="Directory holding the historical parameter registry"
    )
    for name in ("train", "calibrate"):
        p = sub.add_parser(name, help="Create a candidate from explicit feedback; no automatic promotion")
        p.add_argument("--data", required=True, type=Path)
        p.add_argument("--output", type=Path)
        p.add_argument("--mode", choices=("real", "synthetic-demo", "test"))
    evaluate = sub.add_parser("evaluate", help="Run the reproducible synthetic comparison protocol")
    evaluate.add_argument("--config", required=True, type=Path)
    export = sub.add_parser(
        "export-demo", help="Write an offline static playback of precomputed Python trajectories"
    )
    export.add_argument("--scenario", default="workday")
    export.add_argument("--output", required=True, type=Path)
    export.add_argument("--seed", type=int, default=7)
    sub.add_parser("scenarios", help="List built-in explicitly synthetic scenarios")
    return result


def main(argv=None):
    multiprocessing.freeze_support()
    args = parser().parse_args(argv)
    command = args.command or "run"
    try:
        if command in ("run", "demo"):
            from focuspet.ui import run_app

            return run_app(
                mode="real" if command == "run" else "synthetic-demo",
                data_dir=getattr(args, "data_dir", None),
                scenario=getattr(args, "scenario", "workday"),
                quit_after=getattr(args, "quit_after", None),
                screenshot=getattr(args, "screenshot", None),
            )
        if command == "doctor":
            from focuspet.doctor import doctor

            output = doctor()
        elif command == "replay":
            from focuspet.replay import (
                ArchiveReplayError,
                load_replay_input,
                replay_export,
                replay_scenario,
                save_trajectory,
            )
            from focuspet.domain import WorkloadParameters

            params = None
            if args.parameters:
                import hashlib

                raw_parameters = json.loads(args.parameters.read_text())
                if "version" not in raw_parameters and (
                    raw_parameters.get("a_user", 1) != 1 or raw_parameters.get("tau_user", 12) != 12
                ):
                    raw_parameters["version"] = (
                        "ad-hoc-"
                        + hashlib.sha256(json.dumps(raw_parameters, sort_keys=True).encode()).hexdigest()[:12]
                    )
                params = WorkloadParameters(**raw_parameters)
            predictor = None
            data = load_replay_input(args.scenario, args.seed)
            is_archive = data["schema"] == "export-v1"
            if args.recompute_history and args.model_version:
                raise ArchiveReplayError(
                    "HISTORICAL_VERSION_OVERRIDE",
                    "Strict history loads each original recorded model automatically. Use --reevaluate to select a different --model-version.",
                )
            if (
                is_archive
                and not args.reevaluate
                and not args.recompute_history
                and (args.model_version or args.parameters)
            ):
                raise ArchiveReplayError(
                    "REPLAY_MODE_REQUIRED",
                    "Model or parameter overrides require --reevaluate. Use --recompute-history to verify original versions, or --recorded to view saved values.",
                )
            if args.model_version and not args.model_dir:
                raise ArchiveReplayError(
                    "MODEL_DIRECTORY_REQUIRED", "An explicit model version also requires its --model-dir."
                )
            if args.model_dir and not args.recompute_history and (not is_archive or args.reevaluate):
                from focuspet.learning.registry import ModelRegistry
                from focuspet.models.personal import PersonalPredictor

                registry = ModelRegistry(args.model_dir, data["mode"])
                predictor = (
                    PersonalPredictor(registry._load(args.model_version), args.profile or "Mixed")
                    if args.model_version
                    else registry.active_predictor(args.profile or "Mixed")
                )
            if args.reevaluate and params is None:
                parameter_root = args.parameter_dir or (
                    args.model_dir / "parameters" if args.model_dir else None
                )
                if parameter_root and (parameter_root / "parameter-registry.json").is_file():
                    from focuspet.optimization import ParameterRegistry

                    params = WorkloadParameters(**ParameterRegistry(parameter_root, data["mode"]).current())
            if is_archive:
                output = replay_export(
                    data,
                    seed=args.seed,
                    profile=args.profile,
                    parameters=params,
                    predictor=predictor,
                    reevaluation=args.reevaluate,
                    recompute_history=args.recompute_history,
                    model_dir=args.model_dir,
                    parameter_dir=args.parameter_dir,
                )
            else:
                if args.recompute_history or args.recorded:
                    raise ArchiveReplayError(
                        "EXPORT_REQUIRED",
                        "Recorded playback and strict historical verification apply to export-v1 archives. Named scenarios already run the shared core.",
                    )
                output = replay_scenario(
                    data,
                    args.seed,
                    args.profile or "Mixed",
                    params,
                    predictor,
                    args.reevaluate or params is not None or predictor is not None,
                )
            if args.output:
                save_trajectory(output, args.output)
                output = {k: v for k, v in output.items() if k != "snapshots"} | {
                    "snapshots": len(output["snapshots"]),
                    "output": str(args.output),
                }
            else:
                output = {k: v for k, v in output.items() if k != "snapshots"} | {
                    "snapshots": len(output["snapshots"]),
                    "last": output["snapshots"][-1] if output["snapshots"] else None,
                }
        elif command in ("train", "calibrate"):
            from focuspet.service import data_root

            mode = args.mode or json.loads(args.data.read_text()).get("mode", "real")
            destination = args.output or data_root() / mode / "models" / (
                "parameters" if command == "calibrate" else ""
            )
            if command == "train":
                from focuspet.learning import train_file

                output = train_file(args.data, destination, mode=mode)
            else:
                from focuspet.optimization import calibrate_file

                output = calibrate_file(args.data, destination, mode=mode)
        elif command == "evaluate":
            from focuspet.learning.evaluation import evaluate_config

            output = evaluate_config(args.config)
        elif command == "export-demo":
            from focuspet.demo_export import export_demo

            output = export_demo(args.scenario, args.output, args.seed)
        elif command == "scenarios":
            from focuspet.collectors.replay import SCENARIOS

            output = SCENARIOS
        else:
            return 2
        print(json.dumps(output, indent=2, allow_nan=False))
        return 0
    except KeyboardInterrupt:
        print("Cancelled; active versions retained.", file=sys.stderr)
        return 130
    except (OSError, ValueError, KeyError, ImportError, RuntimeError) as error:
        # No native events or private application identifiers in error output.
        from focuspet.replay import ArchiveReplayError

        if isinstance(error, ArchiveReplayError):
            print(f"Focus Pet: {error.code}: {error.safe_message}", file=sys.stderr)
        else:
            print(f"Focus Pet could not complete this command ({type(error).__name__}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
