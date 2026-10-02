"""Tiny durable job queue on SQLite."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db


def _at(delay_s: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delay_s)).replace(microsecond=0).isoformat()


def enqueue(kind: str, ref_id: int | None = None, payload: dict | None = None, *, delay_s: float = 0,
            priority: int = 0, max_attempts: int = 3, dedupe: bool = True) -> int | None:
    if dedupe:
        existing = db.row(
            "SELECT id FROM jobs WHERE kind = ? AND ref_id IS ? AND status IN ('pending', 'running')",
            (kind, ref_id),
        )
        if existing:
            return None
    return db.execute(
        "INSERT INTO jobs(kind, ref_id, payload, status, priority, max_attempts, run_after, created_at) "
        "VALUES(?, ?, ?, 'pending', ?, ?, ?, ?)",
        (kind, ref_id, json.dumps(payload or {}), priority, max_attempts, _at(delay_s), db.now()),
    )


def claim(kinds: list[str]) -> dict[str, Any] | None:
    marks = ",".join("?" for _ in kinds)
    with db.tx() as c:
        r = c.execute(
            f"UPDATE jobs SET status = 'running', started_at = ?, attempts = attempts + 1 "
            f"WHERE id = (SELECT id FROM jobs WHERE status = 'pending' AND run_after <= ? AND kind IN ({marks}) "
            f"ORDER BY priority DESC, id LIMIT 1) RETURNING *",
            (db.now(), db.now(), *kinds),
        ).fetchone()
    if not r:
        return None
    job = dict(r)
    job["payload"] = json.loads(job.get("payload") or "{}")
    return job


def finish(job_id: int) -> None:
    db.execute("UPDATE jobs SET status = 'done', finished_at = ?, error = NULL WHERE id = ?", (db.now(), job_id))


def fail(job: dict[str, Any], error: str, retry_delay_s: float = 120) -> bool:
    """Returns True if the job will be retried."""
    if job["attempts"] < job["max_attempts"]:
        db.execute(
            "UPDATE jobs SET status = 'pending', run_after = ?, error = ? WHERE id = ?",
            (_at(retry_delay_s * job["attempts"]), error[:2000], job["id"]),
        )
        return True
    db.execute("UPDATE jobs SET status = 'failed', finished_at = ?, error = ? WHERE id = ?",
               (db.now(), error[:2000], job["id"]))
    return False


def postpone(job: dict[str, Any], delay_s: float, note: str) -> None:
    """Retry later without burning an attempt (e.g. Claude login missing)."""
    db.execute(
        "UPDATE jobs SET status = 'pending', attempts = attempts - 1, run_after = ?, error = ? WHERE id = ?",
        (_at(delay_s), note[:2000], job["id"]),
    )


def recover_stale() -> int:
    """Jobs left 'running' by a crashed/restarted worker go back to pending."""
    with db.tx() as c:
        return c.execute("UPDATE jobs SET status = 'pending', attempts = MAX(attempts - 1, 0) WHERE status = 'running'").rowcount


def prune(days: int = 14) -> None:
    db.execute("DELETE FROM jobs WHERE status = 'done' AND finished_at < ?", (_at(-days * 86400),))
