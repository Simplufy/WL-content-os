"""Scores.

outlier          views / median views of the creator's other recent posts (1.0 = typical, 3.0 = 3x)
engagement_rate  (likes + comments + shares + saves) / views, in percent
engagement_rel   engagement_rate / creator's median engagement_rate
hook_score       0-100 from the Claude teardown
score            0-100 composite: 50% performance, 20% engagement, 30% hook
                 (only for analyzed posts; performance/engagement renormalized if missing)

Baselines are per creator AND per kind (short vs YouTube long-form), and skip posts
younger than 3 days (or, when the platform hides the date, the 3 newest in the listing)
because fresh posts haven't collected their views yet.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from statistics import median
from typing import Any

from . import db

W_PERF, W_ENG, W_HOOK = 0.5, 0.2, 0.3
MIN_BASELINE = 3


def engagement_rate(v: dict[str, Any]) -> float | None:
    views = v.get("views")
    if not views or all(v.get(k) is None for k in ("likes", "comments", "shares", "saves")):
        return None
    inter = sum(v.get(k) or 0 for k in ("likes", "comments", "shares", "saves"))
    return round(inter / views * 100, 3)


def _log_scale(ratio: float | None) -> float | None:
    """1x -> 50, 2x -> 75, 4x -> 100, 0.5x -> 25."""
    if ratio is None or ratio <= 0:
        return None
    return max(0.0, min(100.0, 50 + 25 * math.log2(ratio)))


def composite(outlier: float | None, eng_rel: float | None, hook: float | None) -> float | None:
    if hook is None:
        return None
    parts = [(W_PERF, _log_scale(outlier)), (W_ENG, _log_scale(eng_rel)), (W_HOOK, hook)]
    parts = [(w, s) for w, s in parts if s is not None]
    if not parts:
        return None
    total_w = sum(w for w, _ in parts)
    return round(sum(w * s for w, s in parts) / total_w, 1)


def age_hours(published_at: str | None) -> float | None:
    if not published_at:
        return None
    try:
        dt = datetime.fromisoformat(published_at)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600


def _settled(v: dict[str, Any]) -> bool:
    age = age_hours(v["published_at"])
    if age is not None:
        return age > 72
    return v.get("list_pos") is None or v["list_pos"] >= 3


def creator_baseline(creator_id: int, exclude_id: int | None = None, kind: str = "short") -> dict[str, float | None]:
    vids = db.rows(
        "SELECT id, views, likes, comments, shares, saves, published_at, list_pos FROM videos "
        "WHERE creator_id = ? AND kind = ? AND views IS NOT NULL "
        "ORDER BY COALESCE(published_at, discovered_at) DESC, list_pos LIMIT 60",
        (creator_id, kind),
    )
    others = [v for v in vids if v["id"] != exclude_id]
    # Prefer settled posts when there are enough of them.
    settled = [v for v in others if _settled(v)]
    pool = settled if len(settled) >= MIN_BASELINE else others
    views = [v["views"] for v in pool if v["views"]]
    ers = [e for e in (engagement_rate(v) for v in pool) if e is not None]
    return {
        "median_views": float(median(views)) if len(views) >= MIN_BASELINE else None,
        "median_er": float(median(ers)) if len(ers) >= MIN_BASELINE else None,
        "n": len(pool),
    }


def rescore_video(video_id: int) -> dict[str, Any]:
    v = db.row("SELECT * FROM videos WHERE id = ?", (video_id,))
    if not v:
        return {}
    base = creator_baseline(v["creator_id"], exclude_id=video_id, kind=v.get("kind") or "short")
    er = engagement_rate(v)
    outlier = round(v["views"] / base["median_views"], 2) if v.get("views") is not None and base["median_views"] else None
    eng_rel = round(er / base["median_er"], 2) if er is not None and base["median_er"] else None
    fields = {
        "engagement_rate": er,
        "outlier": outlier,
        "engagement_rel": eng_rel,
        "score": composite(outlier, eng_rel, v.get("hook_score")),
    }
    db.update("videos", video_id, fields)
    return fields


def rescore_creator(creator_id: int) -> None:
    for r in db.rows("SELECT id FROM videos WHERE creator_id = ?", (creator_id,)):
        rescore_video(r["id"])
