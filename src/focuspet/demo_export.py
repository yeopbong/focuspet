from __future__ import annotations
import json
from importlib.resources import files
from pathlib import Path
from focuspet.replay import replay_scenario


def export_demo(scenario, output, seed=7):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    template = files("focuspet").joinpath("assets/web-demo")
    for name in ("index.html", "style.css", "app.js"):
        (output / name).write_text(template.joinpath(name).read_text())
    scenario_names = list(
        dict.fromkeys([scenario, "reading", "browser-ambiguity", "breaks", "permission-loss"])
    )
    payload: dict = {"schema": "static-demo-v1", "mode": "synthetic-demo", "characters": {}, "scenarios": {}}
    for name in scenario_names:
        original = replay_scenario(name, seed)
        corrected = replay_scenario(name, seed, correction=True, reevaluation=True)
        payload["scenarios"][name] = {
            "title": name.replace("-", " ").title(),
            "original": original,
            "corrected": corrected,
        }
    for name in ("mira", "jun", "ada", "sol"):
        folder = files("focuspet").joinpath("assets/characters", name)
        manifest = json.loads(folder.joinpath("manifest.json").read_text())
        payload["characters"][name] = manifest
        target = output / "characters" / name
        target.mkdir(parents=True, exist_ok=True)
        for image in ("sprites.png", "thumbnail.png"):
            target.joinpath(image).write_bytes(folder.joinpath(image).read_bytes())
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), allow_nan=False).replace(
        "</", "<\\/"
    )
    (output / "trajectory.js").write_text("window.FOCUS_PET_DEMO = " + encoded + ";\n")
    (output / "README.txt").write_text(
        "Focus Pet — Demo / synthetic data\nOpen index.html in a browser. No server, installation, external fonts or network required.\nAll state and load values were exported by the Python core.\n"
    )
    return {
        "output": str(output),
        "entry": "index.html",
        "scenarios": scenario_names,
        "mode": "synthetic-demo",
        "network_requests": 0,
    }
