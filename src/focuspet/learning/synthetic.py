from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from focuspet.domain import ActivityBucket
from focuspet.features import FEATURE_NAMES, FeatureBuilder
from focuspet.learning.data import CLASSES
from focuspet.models import GenericPrior
from focuspet.models.personal import atomic_json

EPOCH = 1735722000.0


def work_dataset(seed: int = 7, days: int = 60, episodes_per_day: int = 6) -> dict:
    rng = np.random.default_rng(seed)
    speed = rng.lognormal(0, .22)
    switch_style = rng.uniform(.5, 1.7)
    tasks = ["coding", "reading", "writing", "design", "browser", "video"]
    emissions = {"coding": (42, 2, 1, 12, .7, "IDE"), "reading": (.4, .5, 7, 2, .16, "Reader"),
                 "writing": (32, 3, 2, 7, .58, "Office"), "design": (5, 12, 4, 55, .75, "Creative"),
                 "browser": (4, 3, 8, 10, .3, "Browser"), "video": (.2, .2, .6, 1, .1, "Browser")}
    records = []
    descriptions = {}
    prior = GenericPrior("Mixed")
    for day in range(days):
        for number in range(episodes_per_day):
            episode = f"s{seed}-d{day}-e{number}"
            task = tasks[int(rng.integers(len(tasks)))]
            latent = int(rng.choice(3, p=[.38, .37, .25]))
            intentionality = rng.beta([8, 4, 2][latent], [2, 4, 8][latent])
            label = CLASSES[latent]
            start = EPOCH + day * 86400 + (number // 3) * 14400 + (number % 3) * 420
            builder = FeatureBuilder()
            key, click, scroll, pointer, activity, category = emissions[task]
            out_of_distribution = day >= days - 8 and rng.random() < .3
            extra_switch = (1 - intentionality) * rng.uniform(0, 2.5) * switch_style
            rhythm = rng.lognormal(0, .28)
            if day >= days // 2:
                rhythm *= 1.15
            descriptions[episode] = {"latent_task": task, "latent_engagement": intentionality,
                                     "ood": out_of_distribution, "expected": "Signals can be ambiguous"}
            for i in range(36):
                bump = 1 + .6 * math.sin(i * .45 + number)
                if latent == 2:
                    bump *= rng.lognormal(0, .65)
                if out_of_distribution and task == "coding":
                    bump *= 6 if i % 6 == 0 else .4
                app = category
                if rng.random() < min(.65, extra_switch / 5):
                    app = str(rng.choice(["Browser", "IDE", "Reader", "Communication", "Other"]))
                active = min(.98, max(.01, activity * rhythm + rng.normal(0, .12)))
                bucket = ActivityBucket(start=start + i * 10, end=start + (i + 1) * 10,
                    duration_s=10, keyboard=int(rng.poisson(max(0, key * speed * bump / 6))),
                    clicks=int(rng.poisson(max(0, click * bump / 6))),
                    scroll=float(rng.poisson(scroll * bump / 6)), pointer=max(0., pointer * bump / 6),
                    idle_s=10 * (1 - active), app_dwell={app: 10},
                    app_switches=int(rng.poisson((.1 + extra_switch) / 6)),
                    session_id=f"s{seed}-d{day}-session{number//2}", mode="synthetic-demo")
                window = builder.add(bucket)
                if i in (32, 35) and window is not None:
                    prediction = prior.predict(window)
                    records.append({"id": f"{episode}-{i}", "feature_id": f"{episode}-{i}",
                        "episode_id": episode, "session_id": bucket.session_id,
                        "start": start, "end": window.end, "target_start": start, "target_end": start + 360,
                        "labeled_at": start + 390, "label": label, "values": window.values,
                        "coverage": window.coverage, "mode": "synthetic-demo", "source": "synthetic-oracle",
                        "prior": prediction.components, "revision": 0, "withdrawn": False})
    return {"version": 1, "mode": "synthetic-demo", "seed": seed, "feature_names": list(FEATURE_NAMES),
            "records": records, "scenario_metadata": descriptions,
            "provenance": "Fictional latent task/engagement sampled before activity emissions; no prior-generated labels",
            "limitations": "Single simulated style per seed; no real-user effectiveness or calibrated probabilities"}


def load_dataset(seed: int = 7, days: int = 12) -> dict:
    from focuspet.domain import Workload, WorkloadParameters
    rng = np.random.default_rng(seed)
    hidden = {"a_user": float(rng.uniform(1.12, 1.28)), "tau_user": float(rng.uniform(15, 22))}
    trajectory, feedback = [], []
    for day in range(days):
        for segment in range(2):
            session = f"load-s{seed}-d{day}-session{segment}"
            start = EPOCH + day * 86400 + segment * 15000
            rest_id = session + "-rest"
            actual = Workload(parameters=WorkloadParameters(a_user=hidden["a_user"], tau_user=hidden["tau_user"]))
            components = dict(zip(CLASSES, map(float, rng.dirichlet([6, 2, 1]))))
            for minute in range(190):
                rest = 125 <= minute < 145
                step: dict[str, Any] = {"end": start + (minute + 1) * 60, "duration_s": 60,
                        "components": components, "rest": rest, "valid": True,
                        "reset": minute == 0, "session_id": session, "mode": "synthetic-demo"}
                actual.advance(60, components=components, rest=rest)
                trajectory.append(step)
                if minute in (24, 69, 124, 144, 189):
                    link = 1 / (1 + math.exp(-(actual.value - 100 + (8 if day >= days-2 else 0)) / 20))
                    answer = "Yes" if rng.random() < .94 * link + .03 else "No"
                    phase = "before" if minute == 124 else "after" if minute == 144 else None
                    feedback.append({"id": f"{session}-{minute}", "session_id": session,
                        "at": step["end"], "labeled_at": step["end"] + 5,
                        "step_index": len(trajectory) - 1, "answer": answer,
                        "rest_id": rest_id if phase else None, "rest_phase": phase,
                        "mode": "synthetic-demo", "source": "synthetic-oracle"})
    return {"version": 1, "mode": "synthetic-demo", "seed": seed, "trajectory": trajectory,
            "feedback": feedback, "simulation_only_hidden_parameters": hidden,
            "provenance": "Stochastic voluntary self-report simulation; not medical or reminder-effectiveness evidence"}


def write_examples(output: str | Path, seed: int = 7) -> dict:
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(output / "learning.json", work_dataset(seed))
    atomic_json(output / "calibration.json", load_dataset(seed))
    return {"learning": str(output / "learning.json"), "calibration": str(output / "calibration.json")}
