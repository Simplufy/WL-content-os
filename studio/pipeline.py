"""Job handlers: check a creator, process a video, analyze it, refresh metrics."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
import threading
from typing import Any

from . import analyze, config, db, editing, ideas, jobs, publishing, styles, llm, media, platforms, scoring

_whisper_lock = threading.Lock()  # one transcription at a time on the GPU

METRIC_FIELDS = ("views", "likes", "comments", "shares", "saves")


def _creator(creator_id: int) -> dict[str, Any]:
    c = db.row("SELECT * FROM creators WHERE id = ?", (creator_id,))
    if not c:
        raise LookupError(f"creator {creator_id} not found")
    return c


def _video(video_id: int) -> dict[str, Any]:
    v = db.row("SELECT * FROM videos WHERE id = ?", (video_id,))
    if not v:
        raise LookupError(f"video {video_id} not found")
    return v


def _snapshot(video_id: int, m: dict[str, Any]) -> None:
    if all(m.get(k) is None for k in METRIC_FIELDS):
        return
    last = db.row("SELECT * FROM metric_snapshots WHERE video_id = ? ORDER BY id DESC LIMIT 1", (video_id,))
    if last and all(last.get(k) == m.get(k) for k in METRIC_FIELDS):
        return
    db.execute(
        "INSERT INTO metric_snapshots(video_id, at, views, likes, comments, shares, saves) VALUES(?,?,?,?,?,?,?)",
        (video_id, db.now(), *(m.get(k) for k in METRIC_FIELDS)),
    )


def _apply_metadata(video_id: int, meta: dict[str, Any]) -> None:
    fields = {k: v for k, v in meta.items() if not k.startswith("_") and k != "platform_id" and v is not None}
    if any(meta.get(k) is not None for k in METRIC_FIELDS):
        fields["metrics_at"] = db.now()
    db.update("videos", video_id, fields)
    _snapshot(video_id, meta)


def enqueue_process(video_id: int, priority: int = 0) -> None:
    db.update("videos", video_id, {"status": "queued", "error": None})
    jobs.enqueue("process_video", video_id, priority=priority)


# --------------------------------------------------------------------------- check_creator

def check_creator(creator_id: int, **_: Any) -> dict[str, Any]:
    c = _creator(creator_id)
    try:
        listing = platforms.list_profile(c["platform"], c["handle"], c["profile_url"])
    except platforms.PlatformError as e:
        db.update("creators", creator_id, {"last_checked_at": db.now(), "last_check_status": "error",
                                           "last_check_error": str(e)})
        db.log_event(f"Check failed for @{c['handle']}: {e}", "error", creator_id=creator_id)
        raise

    info = {k: v for k, v in listing["info"].items() if v}
    new_ids: list[int] = []
    known = skipped_old = 0
    existing_ids = {item["platform_id"]: db.row("SELECT id FROM videos WHERE platform = ? AND platform_id = ?",
                                                (c["platform"], item["platform_id"])) for item in listing["videos"]}
    # where the already-known posts sit in this listing, per tab (Shorts vs long-form)
    first_known_pos: dict[str, int] = {}
    for item in listing["videos"]:
        if existing_ids[item["platform_id"]] and item.get("list_pos") is not None:
            k = item.get("kind", "short")
            first_known_pos[k] = min(first_known_pos.get(k, 10 ** 6), item["list_pos"])
    new_only = bool(c["baseline_done"]) and new_posts_only()
    for item in listing["videos"]:
        existing = existing_ids[item["platform_id"]]
        if existing:
            known += 1
            _apply_metadata(existing["id"], {k: item[k] for k in METRIC_FIELDS})
            db.update("videos", existing["id"], {"list_pos": item.get("list_pos")})
            continue
        if new_only and not is_newly_posted(item, c, first_known_pos):
            skipped_old += 1
            continue
        vid = db.execute(
            "INSERT INTO videos(creator_id, platform, platform_id, url, kind, list_pos, title, description, published_at, duration, "
            "thumbnail_url, views, likes, comments, shares, saves, metrics_at, discovered_at, is_new, status) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'listed')",
            (creator_id, c["platform"], item["platform_id"], item["url"], item.get("kind", "short"),
             item.get("list_pos"), item["title"], item["description"],
             item["published_at"], item["duration"], item["thumbnail_url"],
             *(item[k] for k in METRIC_FIELDS), db.now(), db.now(), 1 if c["baseline_done"] else 0),
        )
        _snapshot(vid, item)
        new_ids.append(vid)

    db.update("creators", creator_id, {**info, "last_checked_at": db.now(), "last_check_status": "ok",
                                       "last_check_error": None})

    if c["baseline_done"]:
        for vid in new_ids:
            enqueue_process(vid, priority=10)
        if new_ids:
            db.log_event(f"{len(new_ids)} new post(s) from @{c['handle']}", creator_id=creator_id)
    else:
        # First check: analyze their best posts + newest posts so we learn what works for them.
        listed = db.rows("SELECT id, views, kind FROM videos WHERE creator_id = ? ORDER BY id", (creator_id,))
        # Short-form is the priority; only fall back to long-form for long-form-only channels.
        pool = [v for v in listed if v["kind"] == "short"] or listed
        top = sorted((v for v in pool if v["views"] is not None), key=lambda v: v["views"], reverse=True)
        n_top, n_new = backfill_counts()
        pick = [v["id"] for v in top[:n_top]]
        pick += [v["id"] for v in pool[:n_new] if v["id"] not in pick]
        for vid in pick:
            enqueue_process(vid, priority=0)
        db.update("creators", creator_id, {"baseline_done": 1, "baseline_at": db.now()})
        db.log_event(f"Added @{c['handle']}: {len(listed)} posts found, analyzing {len(pick)}", creator_id=creator_id)

    scoring.rescore_creator(creator_id)
    return {"found": len(listing["videos"]), "new": len(new_ids), "known": known, "skipped_old": skipped_old}


def new_posts_only() -> bool:
    return bool(db.get_setting("new_posts_only", True))


def backfill_counts() -> tuple[int, int]:
    return (int(db.get_setting("backfill_top", config.BACKFILL_TOP)),
            int(db.get_setting("backfill_newest", config.BACKFILL_NEWEST)))


def is_newly_posted(item: dict[str, Any], creator: dict[str, Any], first_known_pos: dict[str, int]) -> bool:
    """Posted after we finished this creator's backfill (not an old post that just showed up in the list)."""
    since = creator.get("baseline_at") or creator.get("added_at")
    if item.get("published_at") and since:
        cutoff = (datetime.fromisoformat(since) - timedelta(hours=12)).isoformat()
        return item["published_at"] >= cutoff
    # date hidden (YouTube listings): new uploads appear above everything we already know
    pos = item.get("list_pos")
    top_known = first_known_pos.get(item.get("kind", "short"))
    return pos is not None and (top_known is None or pos < top_known)


# --------------------------------------------------------------------------- process_video

def process_video(video_id: int, **_: Any) -> None:
    v = _video(video_id)
    c = _creator(v["creator_id"])

    db.update("videos", video_id, {"status": "downloading", "error": None})
    try:
        meta = platforms.video_metadata(c["platform"], c["handle"], v["url"])
        _apply_metadata(video_id, meta)
    except platforms.PlatformError as e:
        db.log_event(f"Metadata fetch failed (continuing): {e}", "warn", video_id=video_id)
    stem = config.MEDIA_DIR / "videos" / f"{c['platform']}_{v['platform_id']}"
    try:
        path = platforms.download_video(c["platform"], v["url"], stem)
    except platforms.NotAVideo as e:
        db.update("videos", video_id, {"status": "skipped", "error": str(e)})
        return
    duration = media.probe_duration(path)
    db.update("videos", video_id, {"media_path": str(path), "duration": duration or v.get("duration"),
                                   "status": "transcribing"})

    with _whisper_lock:
        tr = media.transcribe(path)
    frames = media.extract_frames(path, config.MEDIA_DIR / "frames" / str(video_id), duration)
    db.update("videos", video_id, {
        "words_json": json.dumps(tr["words"]),
        "transcript_json": json.dumps(tr["segments"]),
        "transcript_text": tr["text"],
        "frames_json": json.dumps(frames),
        "status": "analyzing",
    })
    jobs.enqueue("analyze_video", video_id, priority=5, max_attempts=2)


def analyze_video(video_id: int, **_: Any) -> None:
    v = _video(video_id)
    c = _creator(v["creator_id"])
    words = json.loads(v["words_json"] or "[]")
    segments = json.loads(v["transcript_json"] or "[]")
    frames = json.loads(v["frames_json"] or "[]")
    db.update("videos", video_id, {"status": "analyzing"})
    result = analyze.analyze(v, c, words, segments, frames)
    hook = result.get("hook") or {}
    db.update("videos", video_id, {
        "analysis_json": json.dumps(result),
        "hook_score": hook.get("score"),
        "status": "done",
        "processed_at": db.now(),
        "error": None,
    })
    scoring.rescore_video(video_id)
    styles.auto_fingerprint(video_id)


def refresh_metrics(video_id: int, **_: Any) -> None:
    v = _video(video_id)
    c = _creator(v["creator_id"])
    meta = platforms.video_metadata(c["platform"], c["handle"], v["url"])
    _apply_metadata(video_id, {k: meta.get(k) for k in METRIC_FIELDS})
    scoring.rescore_video(video_id)


HANDLERS = {
    "check_creator": check_creator,
    "process_video": process_video,
    "analyze_video": analyze_video,
    "refresh_metrics": refresh_metrics,
    "generate_concepts": ideas.run_batch,
    "write_script": ideas.write_script,
    "more_hooks": ideas.more_hooks,
    **editing.HANDLERS,
    "publish_submit": publishing.submit,
    "publish_sync": publishing.sync,
    "style_fingerprint": styles.fingerprint,
    "style_build": styles.build_all,
    "style_preview": styles.preview,
}


def on_final_failure(job: dict[str, Any], error: str) -> None:
    if job["kind"] in ("process_video", "analyze_video") and job.get("ref_id"):
        db.update("videos", job["ref_id"], {"status": "failed", "error": error[:1000]})
        db.log_event(f"Video processing failed: {error[:200]}", "error", video_id=job["ref_id"])
    elif job["kind"] in ideas.JOB_KINDS:
        ideas.mark(job["kind"], job["ref_id"], "failed", error)
    elif job["kind"] in editing.JOB_KINDS:
        editing.mark(job["kind"], job["ref_id"], "failed", error)
    elif job["kind"] in publishing.JOB_KINDS:
        publishing.mark(job["kind"], job["ref_id"], "failed", error)
    elif job["kind"] in styles.JOB_KINDS:
        styles.mark(job["kind"], job["ref_id"], "failed", error)


def on_llm_unavailable(job: dict[str, Any], error: str) -> None:
    if job["kind"] in ideas.JOB_KINDS:
        ideas.mark(job["kind"], job["ref_id"], "waiting_llm", "Waiting for Claude: " + error[:300])
    elif job["kind"] in editing.JOB_KINDS:
        editing.mark(job["kind"], job["ref_id"], "waiting_llm", "Waiting for Claude: " + error[:300])
    elif job.get("ref_id"):
        db.update("videos", job["ref_id"], {"status": "waiting_llm", "error": "Waiting for Claude: " + error[:300]})


__all__ = ["HANDLERS", "on_final_failure", "on_llm_unavailable", "enqueue_process", "llm"]
