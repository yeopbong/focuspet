"""SQLite persistence through one bounded actor; callers use a service worker."""

from __future__ import annotations
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue, Full
from threading import Thread
from typing import Any, Callable
from uuid import uuid4
import json
import shutil
import sqlite3
import time

from focuspet.domain.types import ActivityBucket, StateSnapshot, MODES

SCHEMA_VERSION = 1
FORBIDDEN_KEYS = {
    "path",
    "filesystem_path",
    "artifact_path",
    "model_path",
    "data_path",
    "output_path",
    "window_title",
    "full_window_title",
    "clipboard",
    "keystrokes",
    "characters",
    "raw_event",
    "raw_events",
    "coordinates",
    "mouse_positions",
    "screenshot",
    "browser_history",
    "page_content",
}


class PersistenceError(RuntimeError):
    """A recoverable write failure; contains no private event contents."""


def _json(value: Any) -> str:
    def check(item):
        if isinstance(item, dict):
            if FORBIDDEN_KEYS.intersection(item):
                raise ValueError("Content-bearing fields are forbidden in activity storage")
            for val in item.values():
                check(val)
        elif isinstance(item, (tuple, list)):
            for val in item:
                check(val)

    check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise PersistenceError("Database schema is newer than this application")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS events(
      id TEXT PRIMARY KEY,kind TEXT NOT NULL,start REAL NOT NULL,end REAL NOT NULL,
      created_at REAL NOT NULL,session_id TEXT NOT NULL,episode_id TEXT,mode TEXT NOT NULL,
      schema TEXT NOT NULL,payload TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS events_kind_time ON events(kind,start,end);
    CREATE INDEX IF NOT EXISTS events_session ON events(session_id,start);
    CREATE TABLE IF NOT EXISTS features(
      id TEXT PRIMARY KEY,start REAL NOT NULL,end REAL NOT NULL,session_id TEXT NOT NULL,
      mode TEXT NOT NULL,coverage REAL NOT NULL,schema TEXT NOT NULL,payload TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS features_time ON features(start,end);
    CREATE TABLE IF NOT EXISTS state_feedback(
      id TEXT PRIMARY KEY,episode_id TEXT NOT NULL,start REAL NOT NULL,end REAL NOT NULL,
      labeled_at REAL NOT NULL,label TEXT NOT NULL,source TEXT NOT NULL,revises TEXT,
      withdrawn INTEGER NOT NULL DEFAULT 0,trainable INTEGER NOT NULL DEFAULT 1,
      mode TEXT NOT NULL,prediction_ids TEXT NOT NULL,model_versions TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS feedback_episode_time ON state_feedback(episode_id,labeled_at);
    CREATE INDEX IF NOT EXISTS feedback_interval ON state_feedback(start,end);
    CREATE TABLE IF NOT EXISTS policy_state(key TEXT PRIMARY KEY,payload TEXT NOT NULL);
    PRAGMA user_version=1;
    """)
    conn.commit()


class Store:
    def __init__(self, root: str | Path, mode: str = "real", queue_size: int = 256):
        if mode not in MODES:
            raise ValueError("Invalid persistence mode")
        self.mode = mode
        self.root = Path(root).expanduser().resolve() / mode
        self.root.mkdir(parents=True, exist_ok=True)
        self.model_dir = self.root / "models"
        self.model_dir.mkdir(exist_ok=True)
        self.db_path = self.root / "activity.sqlite3"
        self._queue: Queue = Queue(maxsize=queue_size)
        self._closed = False
        self._operations = self._errors = self._write_transactions = self._changed_rows = 0
        self._ready: Future = Future()
        self._thread = Thread(target=self._run, name="focuspet-storage", daemon=True)
        self._thread.start()
        self._ready.result(timeout=10)

    def _run(self) -> None:
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=3)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA foreign_keys=ON")
            _migrate(conn)
            self._ready.set_result(True)
            while True:
                item = self._queue.get()
                if item is None:
                    self._queue.task_done()
                    break
                fn, future = item
                previous_changes = conn.total_changes
                try:
                    with conn:
                        result = fn(conn)
                    changes = conn.total_changes - previous_changes
                    self._changed_rows += changes
                    self._write_transactions += int(changes > 0)
                    future.set_result(result)
                except sqlite3.Error as exc:
                    self._errors += 1
                    conn.rollback()
                    future.set_exception(
                        PersistenceError(
                            f"Local storage operation failed ({type(exc).__name__}); collection can be paused and retried."
                        )
                    )
                except Exception as exc:
                    self._errors += 1
                    conn.rollback()
                    future.set_exception(exc)
                finally:
                    self._operations += 1
                    self._queue.task_done()
        except Exception as exc:
            if not self._ready.done():
                self._ready.set_exception(
                    PersistenceError(f"Local storage could not open ({type(exc).__name__}).")
                )
        finally:
            if conn:
                conn.close()

    def _call(self, fn: Callable) -> Any:
        if self._closed:
            raise PersistenceError("Store is closed")
        future: Future = Future()
        try:
            self._queue.put((fn, future), timeout=2)
        except Full as exc:
            raise PersistenceError("Storage queue is full; pause collection before retrying") from exc
        return future.result(timeout=20)

    def diagnostics(self) -> dict:
        """Scalar actor counters; does not execute SQL or reveal stored contents."""
        return {
            "actor_alive": self._thread.is_alive(),
            "queue_depth": self._queue.qsize(),
            "queue_capacity": self._queue.maxsize,
            "operations": self._operations,
            "errors": self._errors,
            "write_transactions": self._write_transactions,
            "changed_rows": self._changed_rows,
        }

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._queue.put(None, timeout=5)
            self._thread.join(timeout=10)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _insert_event(
        self, conn, kind, payload, start, end, session_id="default", episode_id=None, event_id=None
    ):
        if payload.get("mode", self.mode) != self.mode:
            raise ValueError("Cannot mix data modes")
        record_id = event_id or payload.get("id") or uuid4().hex
        inserted = conn.execute(
            "INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                record_id,
                kind,
                start,
                end,
                time.time(),
                session_id,
                episode_id,
                self.mode,
                payload.get("schema", "events-v1"),
                _json(payload),
            ),
        )
        if inserted.rowcount and kind == "WorkloadStep" and "value" in payload:
            conn.executemany(
                "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload",
                [("last_workload", _json(payload["value"])), ("last_seen", _json(end))],
            )
        return record_id

    def save_event(
        self,
        kind: str,
        payload: dict,
        start: float | None = None,
        end: float | None = None,
        session_id: str = "default",
        episode_id: str | None = None,
    ) -> str:
        start = float(payload.get("start", payload.get("at", time.time())) if start is None else start)
        end = float(payload.get("end", start) if end is None else end)
        return self._call(lambda c: self._insert_event(c, kind, payload, start, end, session_id, episode_id))

    def save_bucket(self, bucket: ActivityBucket) -> str:
        return self.save_event(
            "ActivityBucket", bucket.to_dict(), bucket.start, bucket.end, bucket.session_id
        )

    def save_buckets(self, buckets: list[ActivityBucket]) -> None:
        def work(c):
            for b in buckets:
                self._insert_event(c, "ActivityBucket", b.to_dict(), b.start, b.end, b.session_id)

        self._call(work)

    def save_snapshot(self, snapshot: StateSnapshot) -> None:
        if snapshot.mode != self.mode:
            raise ValueError("Cannot mix snapshot modes")

        def work(c):
            f = snapshot.feature
            c.execute(
                "INSERT OR IGNORE INTO features VALUES(?,?,?,?,?,?,?,?)",
                (f.id, f.start, f.end, f.session_id, f.mode, f.coverage, f.schema, _json(f.to_dict())),
            )
            self._insert_event(
                c, "PredictionEvent", snapshot.prediction.to_dict(), f.start, f.end, snapshot.session_id
            )
            self._insert_event(
                c,
                "WorkloadEvent",
                {
                    "id": snapshot.id + "-load",
                    "value": snapshot.workload,
                    "stale": snapshot.workload_stale,
                    "parameter_version": snapshot.parameter_version,
                    "rest": snapshot.rest_active,
                    "prediction_id": snapshot.prediction.id,
                    "feature_id": f.id,
                },
                f.end,
                f.end,
                snapshot.session_id,
            )
            # Keep references instead of a hidden fine-grained feature copy after retention.
            payload = snapshot.to_dict()
            payload.pop("feature")
            payload["feature_id"] = f.id
            self._insert_event(c, "StateSnapshot", payload, snapshot.start, snapshot.end, snapshot.session_id)

        self._call(work)

    def get_events(
        self,
        kind: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        def work(c):
            where: list[str] = []
            params: list[Any] = []
            if kind:
                where.append("kind=?")
                params.append(kind)
            if since is not None:
                where.append("MAX(start,end)>=?")
                params.append(since)
            if until is not None:
                where.append("MIN(start,end)<=?")
                params.append(until)
            sql = (
                "SELECT rowid AS sequence,* FROM events"
                + (" WHERE " + " AND ".join(where) if where else "")
                + " ORDER BY start DESC,id LIMIT ?"
            )
            params.append(limit)
            return [dict(row) | {"payload": json.loads(row["payload"])} for row in c.execute(sql, params)]

        return self._call(work)

    def settings(self) -> dict:
        return self._call(
            lambda c: {r["key"]: json.loads(r["payload"]) for r in c.execute("SELECT * FROM settings")}
        )

    def save_settings(self, settings: dict) -> None:
        if "categories" in settings:
            from focuspet.collectors.base import validate_categories

            validate_categories(settings["categories"])

        def work(c):
            c.executemany(
                "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload",
                [(k, _json(v)) for k, v in settings.items()],
            )

        self._call(work)

    def save_policy_state(self, state: dict) -> None:
        self._call(
            lambda c: c.execute(
                "INSERT INTO policy_state VALUES(?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload",
                ("reminders", _json(state)),
            ).rowcount
        )

    def load_policy_state(self) -> dict | None:
        def work(c):
            row = c.execute("SELECT payload FROM policy_state WHERE key=?", ("reminders",)).fetchone()
            return json.loads(row[0]) if row else None

        return self._call(work)

    def add_state_feedback(
        self,
        start: float,
        end: float,
        label: str,
        source: str = "user",
        episode_id: str | None = None,
        revises: str | None = None,
        labeled_at: float | None = None,
    ) -> str:
        if label not in ("Focused", "Normal", "Distracted", "Rest", "Not sure"):
            raise ValueError("Unknown feedback label")
        if end <= start or end - start > 900.001:
            raise ValueError("Feedback must target a completed interval of at most 15 minutes")
        at = time.time() if labeled_at is None else labeled_at
        if end > at + 1:
            raise ValueError("Feedback cannot label future activity")
        if source not in ("user", "audit", "active-query"):
            raise ValueError("Only explicit user feedback is accepted")
        record_id = uuid4().hex

        def work(c):
            nonlocal episode_id
            if revises:
                previous = c.execute("SELECT * FROM state_feedback WHERE id=?", (revises,)).fetchone()
                if not previous:
                    raise ValueError("Feedback revision target does not exist")
                episode_id = previous["episode_id"]
            if episode_id is None:
                overlap = c.execute(
                    "SELECT episode_id FROM state_feedback WHERE start<? AND end>? ORDER BY labeled_at DESC LIMIT 1",
                    (end, start),
                ).fetchone()
                episode_id = overlap[0] if overlap else uuid4().hex
            replacing_episode = bool(
                c.execute("SELECT 1 FROM state_feedback WHERE episode_id=? LIMIT 1", (episode_id,)).fetchone()
            )
            predictions = [
                json.loads(r["payload"])
                for r in c.execute(
                    "SELECT payload FROM events WHERE kind='PredictionEvent' AND start>=? AND end<=?",
                    (start, end),
                )
            ]
            feature_count = c.execute(
                "SELECT COUNT(*) FROM features WHERE start>=? AND end<=? AND coverage>=.45", (start, end)
            ).fetchone()[0]
            c.execute(
                "INSERT INTO state_feedback VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record_id,
                    episode_id,
                    start,
                    end,
                    at,
                    label,
                    source,
                    revises,
                    0,
                    int(feature_count > 0),
                    self.mode,
                    _json([p["id"] for p in predictions]),
                    _json(sorted({p.get("model_version", "unknown") for p in predictions})),
                ),
            )
            if revises or replacing_episode:
                self._invalidate_models(c, "feedback-revised")
            return record_id

        return self._call(work)

    def retract_feedback(self, feedback_id: str) -> None:
        def work(c):
            row = c.execute("SELECT * FROM state_feedback WHERE id=?", (feedback_id,)).fetchone()
            if not row:
                raise ValueError("Feedback does not exist")
            # Withdraw the complete episode, so older revisions cannot silently become active.
            c.execute(
                "UPDATE state_feedback SET withdrawn=1,trainable=0 WHERE episode_id=?", (row["episode_id"],)
            )
            self._insert_event(
                c,
                "FeedbackWithdrawal",
                {"feedback_id": feedback_id, "episode_id": row["episode_id"]},
                time.time(),
                time.time(),
                episode_id=row["episode_id"],
            )
            self._invalidate_models(c, "feedback-withdrawn")

        self._call(work)

    def feedback(self) -> list[dict]:
        return self._call(
            lambda c: [
                dict(r)
                | {
                    "prediction_ids": json.loads(r["prediction_ids"]),
                    "model_versions": json.loads(r["model_versions"]),
                }
                for r in c.execute("SELECT * FROM state_feedback ORDER BY labeled_at DESC")
            ]
        )

    def training_records(self) -> list[dict]:
        def work(c):
            records = []
            rows = c.execute("SELECT * FROM state_feedback ORDER BY labeled_at DESC,rowid DESC").fetchall()
            seen = set()
            for row in rows:
                if row["episode_id"] in seen:
                    continue
                seen.add(row["episode_id"])
                if (
                    row["withdrawn"]
                    or not row["trainable"]
                    or row["label"] not in ("Focused", "Normal", "Distracted")
                ):
                    continue
                features = c.execute(
                    "SELECT * FROM features WHERE start>=? AND end<=? AND coverage>=.45 ORDER BY end",
                    (row["start"], row["end"]),
                ).fetchall()
                for f in features:
                    payload = json.loads(f["payload"])
                    prior = c.execute(
                        "SELECT payload FROM events WHERE kind='PredictionEvent' AND start=? AND end=? ORDER BY created_at LIMIT 1",
                        (f["start"], f["end"]),
                    ).fetchone()
                    original = json.loads(prior[0]) if prior else None
                    records.append(
                        {
                            "id": row["id"],
                            "episode_id": row["episode_id"],
                            "label": row["label"],
                            "source": row["source"],
                            "start": f["start"],
                            "end": f["end"],
                            "target_start": row["start"],
                            "target_end": row["end"],
                            "labeled_at": row["labeled_at"],
                            "feature_id": f["id"],
                            "values": payload["values"],
                            "coverage": f["coverage"],
                            "session_id": f["session_id"],
                            "mode": self.mode,
                            "schema": f["schema"],
                            "weight": 1 / max(len(features), 1),
                            "original_prediction": original,
                            "prior_components": original.get("components")
                            if original and original.get("model_version") == "generic-prior-v1"
                            else None,
                            "prediction_ids": json.loads(row["prediction_ids"]),
                            "model_versions": json.loads(row["model_versions"]),
                        }
                    )
            return sorted(records, key=lambda r: r["end"])

        return self._call(work)

    def add_load_feedback(
        self, answer: str, at: float | None = None, session_id: str = "default", rest_id: str | None = None
    ) -> str:
        if answer not in ("Yes", "No", "Not sure"):
            raise ValueError("Load feedback must be an explicit Yes, No, or Not sure")
        at = time.time() if at is None else at
        return self.save_event(
            "LoadFeedback",
            {"answer": answer, "at": at, "rest_id": rest_id, "source": "user"},
            at,
            at,
            session_id,
        )

    def _invalidate_models(self, conn, reason: str) -> None:
        # Deletion affects all locally generated model artifacts, including old versions and backups.
        for path in self.model_dir.iterdir():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        conn.execute("DELETE FROM events WHERE kind IN ('ModelVersion','ParameterVersion')")
        self._insert_event(conn, "ModelInvalidation", {"reason": reason}, time.time(), time.time())

    def retention(
        self,
        now: float | None = None,
        bucket_days: int = 7,
        feature_days: int = 90,
        feedback_days: int | None = None,
        model_days: int | None = None,
    ) -> dict:
        at = time.time() if now is None else now
        if (
            bucket_days < 0
            or feature_days < 0
            or (feedback_days is not None and feedback_days < 0)
            or (model_days is not None and model_days < 0)
        ):
            raise ValueError("Retention periods must be nonnegative")

        def work(c):
            buckets = c.execute(
                "DELETE FROM events WHERE kind='ActivityBucket' AND end<?", (at - bucket_days * 86400,)
            ).rowcount
            cutoff = at - feature_days * 86400
            features = c.execute("DELETE FROM features WHERE end<?", (cutoff,)).rowcount
            # Remove minute-granularity predictions/curves too, rather than retaining disguised copies.
            c.execute(
                "DELETE FROM events WHERE kind IN ('StateSnapshot','PredictionEvent','WorkloadEvent','WorkloadStep') AND end<?",
                (cutoff,),
            )
            c.execute(
                "UPDATE state_feedback SET trainable=0 WHERE NOT EXISTS(SELECT 1 FROM features f WHERE f.start>=state_feedback.start AND f.end<=state_feedback.end)"
            )
            feedback = 0
            if feedback_days is not None:
                feedback = c.execute(
                    "DELETE FROM state_feedback WHERE labeled_at<?", (at - feedback_days * 86400,)
                ).rowcount
                c.execute(
                    "DELETE FROM events WHERE kind='LoadFeedback' AND end<?", (at - feedback_days * 86400,)
                )
            expired_models = model_days is not None and any(
                p.stat().st_mtime < at - model_days * 86400 for p in self.model_dir.rglob("*") if p.is_file()
            )
            if features or feedback or expired_models:
                self._invalidate_models(c, "retention-expired")
            return {"buckets_deleted": buckets, "features_deleted": features, "feedback_deleted": feedback}

        result = self._call(work)
        self._compact()
        return result

    def delete_range(self, start: float, end: float) -> dict:
        if end < start:
            raise ValueError("Invalid deletion interval")

        def work(c):
            # UTC may move backwards within an observed monotonic bucket.
            events = c.execute(
                "DELETE FROM events WHERE MIN(start,end)<=? AND MAX(start,end)>=?", (end, start)
            ).rowcount
            features = c.execute(
                "DELETE FROM features WHERE MIN(start,end)<=? AND MAX(start,end)>=?", (end, start)
            ).rowcount
            feedback = c.execute(
                "DELETE FROM state_feedback WHERE start<=? AND end>=?", (end, start)
            ).rowcount
            self._invalidate_models(c, "source-data-deleted")
            c.execute("DELETE FROM policy_state")
            return {"events_deleted": events, "features_deleted": features, "feedback_deleted": feedback}

        result = self._call(work)
        self._compact()
        return result

    def _compact(self) -> None:
        # SQLite free pages and WAL must not retain an application-managed copy after deletion.
        def work(c):
            c.commit()
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.execute("VACUUM")

        self._call(work)

    def delete_all(self) -> None:
        def work(c):
            for table in ("events", "features", "state_feedback", "settings", "policy_state"):
                c.execute(f"DELETE FROM {table}")
            for path in self.root.iterdir():
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.name not in ("activity.sqlite3", "activity.sqlite3-wal", "activity.sqlite3-shm"):
                    path.unlink()
            self.model_dir.mkdir(exist_ok=True)

        self._call(work)
        self._compact()

    @staticmethod
    def export_preview() -> dict:
        return {
            "format": "JSON",
            "fields": [
                "time intervals",
                "aggregate activity counts",
                "feature values",
                "prediction components",
                "workload history",
                "explicit feedback",
                "model and parameter version references",
            ],
            "excluded": ["user settings", "application identifiers", "filesystem paths", "input content"],
            "scope": "Selected mode and time range only",
        }

    def export(self, path: str | Path, since: float | None = None, until: float | None = None) -> Path:
        start, end = (
            since if since is not None else float("-inf"),
            until if until is not None else float("inf"),
        )

        def work(c):
            events = [
                dict(r) | {"payload": json.loads(r["payload"])}
                for r in c.execute(
                    "SELECT rowid AS sequence,* FROM events WHERE MIN(start,end)<=? AND MAX(start,end)>=? ORDER BY rowid",
                    (end, start),
                )
            ]
            features = [
                json.loads(r[0])
                for r in c.execute(
                    "SELECT payload FROM features WHERE MIN(start,end)<=? AND MAX(start,end)>=? ORDER BY start",
                    (end, start),
                )
            ]
            feedback = [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM state_feedback WHERE start<=? AND end>=? ORDER BY labeled_at", (end, start)
                )
            ]
            return {
                "schema": "export-v1",
                "mode": self.mode,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "preview": self.export_preview(),
                "events": events,
                "features": features,
                "feedback": feedback,
            }

        payload = self._call(work)
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
        return destination
