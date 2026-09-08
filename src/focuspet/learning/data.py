"""Latest explicit feedback, episode weights, and purged chronological partitions."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

import numpy as np

CLASSES = ("Focused", "Normal", "Distracted")
PURGE_SECONDS = 300


def utc_day(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()


def latest_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Revision is episode-wide; withdrawn or ambiguous newest labels remove all children.

    Overlapping separately submitted targets are connected into one independent episode.
    A feature cannot inherit two conflicting labels: newest annotation wins.
    """
    revisions: dict[str, tuple[float, int]] = {}
    for r in records:
        ep = str(r["episode_id"])
        key = (float(r.get("labeled_at", r["end"])), int(r.get("revision", 0)))
        revisions[ep] = max(revisions.get(ep, key), key)
    newest = [dict(r) for r in records if (
        float(r.get("labeled_at", r["end"])), int(r.get("revision", 0))
    ) == revisions[str(r["episode_id"])]]
    newest = [r for r in newest if r.get("label") in CLASSES and not r.get("withdrawn")
              and r.get("features_available", True) and float(r.get("coverage", 1)) >= .6
              and r.get("source", "user") in {"user", "audit", "active-query", "synthetic-oracle"}]
    by_feature: dict[str, dict[str, Any]] = {}
    for r in sorted(newest, key=lambda x: float(x.get("labeled_at", x["end"]))):
        if float(r["end"]) <= float(r["start"]):
            continue
        feature_key = str(r.get("feature_id", f"{r['session_id']}:{r['start']}:{r['end']}"))
        by_feature[feature_key] = r
    result = sorted(by_feature.values(), key=lambda r: (float(r["start"]), float(r["end"])))
    # Merge overlapping *feedback target* intervals, not overlapping causal lookbacks.
    episode_ranges: dict[str, tuple[float, float]] = {}
    for r in result:
        ep = str(r["episode_id"])
        start = float(r.get("target_start", r["start"]))
        end = float(r.get("target_end", r["end"]))
        old = episode_ranges.get(ep, (start, end))
        episode_ranges[ep] = (min(old[0], start), max(old[1], end))
    groups: dict[str, str] = {}
    prior_end = float("-inf")
    group = ""
    for ep, (start, end) in sorted(episode_ranges.items(), key=lambda p: p[1][0]):
        if start >= prior_end:
            group = ep
        groups[ep] = group
        prior_end = max(prior_end, end)
    for r in result:
        r["original_episode_id"] = r["episode_id"]
        r["episode_id"] = groups[str(r["episode_id"])]
    return result


def support(records: list[dict[str, Any]]) -> dict[str, Any]:
    labels: dict[str, set[str]] = defaultdict(set)
    for r in records:
        labels[str(r["episode_id"])].add(str(r["label"]))
    counts = {c: sum(c in values and len(values) == 1 for values in labels.values()) for c in CLASSES}
    return {"episodes": len(labels), "days": len({utc_day(float(r["end"])) for r in records}),
            "sessions": len({str(r["session_id"]) for r in records}), "class_support": counts,
            "windows": len(records)}


def status_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    s = support(latest_records(records))
    missing = []
    for key, minimum in (("episodes", 30), ("days", 2), ("sessions", 3)):
        if s[key] < minimum:
            missing.append(f"{minimum - s[key]} more independent {key}")
    for c, count in s["class_support"].items():
        if count < 5:
            missing.append(f"{5 - count} more {c} episodes")
    return {**s, "eligible": not missing, "missing": missing}


def chronological_split(records: list[dict[str, Any]], fraction: float = .75,
                        purge_s: float = PURGE_SECONDS) -> tuple[list[dict], list[dict]]:
    """Whole UTC days and episodes, train first; purge longest causal lookback.

    start/end identify the entire feature support. A whole episode crossing either
    side of the split boundary is excluded, including any child windows.
    """
    if purge_s < PURGE_SECONDS:
        raise ValueError("Purge must be at least the 300 second feature lookback")
    days = sorted({utc_day(float(r["end"])) for r in records})
    if len(days) < 2:
        return [], []
    n = max(1, min(len(days) - 1, int(len(days) * fraction)))
    boundary = datetime.fromisoformat(days[n]).replace(tzinfo=timezone.utc).timestamp()
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        grouped[str(r["episode_id"])].append(r)
    train, validation = [], []
    for group in grouped.values():
        earliest = min(float(r["start"]) for r in group)
        latest = max(float(r["end"]) for r in group)
        if latest <= boundary - purge_s:
            train.extend(group)
        elif earliest >= boundary + purge_s:
            validation.extend(group)
    return sorted(train, key=lambda r: r["end"]), sorted(validation, key=lambda r: r["end"])


def matrix(records: list[dict[str, Any]], names: list[str] | tuple[str, ...]) -> np.ndarray:
    rows = []
    for r in records:
        raw = r.get("values", r.get("features"))
        row = [raw[n] for n in names] if isinstance(raw, dict) else raw
        if row is None or len(row) != len(names):
            raise ValueError("Feature schema mismatch")
        rows.append(row)
    x = np.asarray(rows, dtype=float)
    if x.ndim != 2 or not np.isfinite(x).all():
        raise ValueError("Feature matrix must be finite and rectangular")
    return x


def targets(records: list[dict[str, Any]]) -> np.ndarray:
    return np.asarray([CLASSES.index(r["label"]) for r in records], dtype=int)


def episode_weights(records: list[dict[str, Any]]) -> np.ndarray:
    count = Counter(r["episode_id"] for r in records)
    return np.asarray([1.0 / count[r["episode_id"]] for r in records])
