from __future__ import annotations

import json
import random
from dataclasses import asdict
from pathlib import Path

from focuspet.domain import ActivityBucket

SCENARIOS = {
    "workday": "Coding, reading, document work, declared breaks, ambiguity and a permission gap.",
    "coding": "High input programming; input volume alone is not proof of intent.",
    "reading": "Low input reading with stable application dwell; never force distraction.",
    "office": "Ordinary document editing with reasonable switches.",
    "creative": "Design work dominated by pointer and click activity.",
    "browser-ambiguity": "Identical browser statistics for reading and entertainment: intent is unobservable.",
    "reasonable-switching": "IDE, documents and terminal workflow; switches are weak evidence.",
    "task-drift": "A latent task deviation under noisy overlapping activity distributions.",
    "automation-burst": "Short extreme input bursts, deliberately outside normal training support.",
    "breaks": "Short then long explicitly declared breaks; continuous exponential recovery.",
    "no-input": "Prolonged silence without a rest declaration; cannot imply recovery.",
    "sleep-wake": "Lock and missing intervals, followed by a new observed interval.",
    "permission-loss": "Independent input and application capability loss represented as missing.",
    "clock-rollback": "UTC clock goes backwards while each monotonic bucket duration remains ten seconds.",
    "habit-change": "A user switches from typing-heavy to pointer-heavy work.",
    "contradictory-feedback": "Same observations with incompatible labels supplied separately as feedback revisions.",
}

_SEGMENTS = {
    "coding": [("code", 80)],
    "reading": [("read", 35)],
    "office": [("office", 35)],
    "creative": [("design", 35)],
    "browser-ambiguity": [("browser", 20), ("browser", 20)],
    "reasonable-switching": [("switch", 40)],
    "task-drift": [("code", 15), ("drift", 20)],
    "automation-burst": [("code", 5), ("burst", 5), ("office", 10)],
    "breaks": [("code", 65), ("rest", 5), ("code", 10), ("rest", 25), ("office", 10)],
    "no-input": [("code", 10), ("silent", 35)],
    "sleep-wake": [("code", 10), ("locked", 5), ("sleep", 30), ("read", 10)],
    "permission-loss": [("office", 10), ("denied", 10), ("no-app", 10), ("read", 10)],
    "clock-rollback": [("code", 10), ("rollback", 10), ("office", 10)],
    "habit-change": [("code", 20), ("design", 30)],
    "contradictory-feedback": [("browser", 30)],
    "workday": [
        ("code", 65),
        ("rest", 8),
        ("read", 15),
        ("office", 15),
        ("browser", 10),
        ("denied", 5),
        ("rest", 15),
        ("design", 15),
    ],
}


def make_scenario(name="workday", seed=7) -> dict:
    if name not in SCENARIOS:
        raise ValueError("Unknown scenario. Choose: " + ", ".join(SCENARIOS))
    rng = random.Random(seed)
    start = 1788220800.0
    t = start
    buckets = []
    declarations = []
    latent_segments = []
    style = rng.uniform(0.7, 1.4)
    keys: int | None
    clicks: int | None
    scroll: float | None
    pointer: float | None
    idle: float | None
    for n, (task, minutes) in enumerate(_SEGMENTS[name]):
        segment_start = t
        if task == "rest":
            declarations.append({"at": t, "minutes": minutes, "kind": "rest"})
        for j in range(minutes * 6):
            keys, clicks, scroll, pointer, idle, switches = 0, 0, 0.0, 0.0, 0.0, 0
            category = "Other"
            if task in ("code", "rollback", "switch"):
                keys = int(max(0, rng.gauss(32 * style, 20)))
                clicks, pointer = rng.randint(0, 6), rng.uniform(0, 650)
                category = "IDE" if task != "switch" or j % 3 == 0 else ("Reader" if j % 3 == 1 else "IDE")
                switches = rng.randint(1, 3) if task == "switch" else int(rng.random() < 0.1)
                idle = rng.uniform(0, 4)
            elif task in ("read", "browser"):
                category = "Reader" if task == "read" else "Browser"
                keys, clicks, scroll = rng.randint(0, 2), rng.randint(0, 2), rng.randint(0, 4)
                pointer, idle = rng.uniform(0, 60), rng.uniform(6, 9.5)
            elif task in ("office", "no-app"):
                category = "Office"
                keys, clicks, scroll = (
                    int(max(0, rng.gauss(17 * style, 13))),
                    rng.randint(1, 4),
                    rng.randint(0, 3),
                )
                pointer, idle, switches = rng.uniform(50, 250), rng.uniform(1, 5), int(rng.random() < 0.2)
            elif task == "design":
                category = "Creative"
                keys, clicks = rng.randint(0, 9), rng.randint(3, 12)
                pointer, scroll, idle = rng.uniform(600, 2200) * style, rng.randint(0, 6), rng.uniform(0, 3)
            elif task == "drift":
                category = rng.choice(["Browser", "Communication", "Other"])
                keys, clicks, scroll = rng.randint(0, 20), rng.randint(0, 7), rng.randint(0, 8)
                pointer, switches, idle = rng.uniform(0, 900), rng.randint(0, 3), rng.uniform(0, 8)
            elif task == "burst":
                keys, category = rng.randint(1500, 8000), "IDE"
            else:
                idle = 10.0
            observation, reason = "Active", None
            cov = dict.fromkeys(["keyboard", "clicks", "scroll", "pointer", "idle", "application"], 1.0)
            if task in ("sleep", "denied"):
                keys = clicks = scroll = pointer = idle = None
                observation, reason = "Missing", "sleep" if task == "sleep" else "permission revoked"
                cov = dict.fromkeys(cov, 0.0)
            elif task == "locked":
                observation, reason = "Locked", "synthetic lock"
            elif task in ("silent", "rest"):
                observation = "No-input"
            if task == "no-app":
                cov["application"] = 0.0
            if task == "rollback" and j == 0:
                t -= 120
            bucket = ActivityBucket(
                start=t,
                end=t + 10,
                duration_s=10,
                keyboard=keys,
                clicks=clicks,
                scroll=scroll,
                pointer=pointer,
                idle_s=idle,
                app_dwell={category: 10} if cov["application"] else {},
                app_switches=switches,
                coverage=cov,
                observation=observation,
                session_id=f"synthetic-{seed}-{name}",
                mode="synthetic-demo",
                missing_reason=reason,
            )
            buckets.append(asdict(bucket))
            t += 10
        latent_segments.append({"start": segment_start, "end": t, "latent_task": task})
    return {
        "schema": "scenario-v1",
        "mode": "synthetic-demo",
        "name": name,
        "seed": seed,
        "description": SCENARIOS[name],
        "buckets": buckets,
        "declarations": declarations,
        "latent_segments": latent_segments,
        "invariants": [
            "No future features",
            "Missing is not zero",
            "No implicit rest",
            "Duration from non-overlapping buckets",
            "No real-user claims",
        ],
    }


def load_scenario(value: str | Path, seed=7) -> dict:
    path = Path(value)
    if path.is_file():
        data = json.loads(path.read_text())
        if data.get("schema") != "scenario-v1" or data.get("mode") not in ("synthetic-demo", "real", "test"):
            raise ValueError("Unsupported scenario schema or mode")
        return data
    return make_scenario(str(value), seed)


class ReplayCollector:
    def __init__(self, scenario="workday", seed=7):
        self.scenario = load_scenario(scenario, seed) if not isinstance(scenario, dict) else scenario
        self._index = 0
        self._paused = True

    def start(self, request_permission=False):
        self._paused = False

    def stop(self):
        self._paused = True

    def pause(self, paused=True):
        self._paused = paused

    def sample(self):
        if self._paused or self._index >= len(self.scenario["buckets"]):
            return None
        bucket = ActivityBucket(**self.scenario["buckets"][self._index])
        self._index += 1
        return bucket

    @property
    def finished(self):
        return self._index >= len(self.scenario["buckets"])

    def capabilities(self):
        return dict.fromkeys(
            ["keyboard", "clicks", "scroll", "pointer", "idle", "application", "lock", "sleep"], "supported"
        )
