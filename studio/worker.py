"""Background worker: job lanes + the monitor schedule.

Run: python -m studio.worker
"""
from __future__ import annotations

import logging
import signal
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

from . import config, db, ideas, jobs, pipeline, platforms, publishing, scoring, styles
from .llm import LLMUnavailable

log = logging.getLogger("studio.worker")

# lane name -> (job kinds, thread count)
LANES = {
    "net": (["check_creator", "refresh_metrics", "publish_submit", "publish_sync"], 2),
    "media": (["process_video"], 2),
    "llm": (["generate_concepts", "write_script", "more_hooks", "edit_decide", "style_build", "analyze_video",
             "style_fingerprint"], 1),
    "render": (["edit_prepare", "edit_render", "style_preview"], 1),
}
LLM_RETRY_S = 15 * 60

_stop = threading.Event()


def run_job(job: dict) -> None:
    handler = pipeline.HANDLERS.get(job["kind"])
    if handler is None:
        jobs.fail({**job, "max_attempts": 0}, f"unknown job kind {job['kind']}")
        return
    try:
        handler(job["ref_id"], **(job.get("payload") or {}))
    except LLMUnavailable as e:
        log.warning("job %s waiting for Claude: %s", job["id"], e)
        pipeline.on_llm_unavailable(job, str(e))
        jobs.postpone(job, LLM_RETRY_S, f"Claude unavailable: {e}")
        return
    except Exception as e:  # noqa: BLE001 - every failure is recorded on the job
        err = f"{type(e).__name__}: {e}"
        log.error("job %s (%s #%s) failed: %s\n%s", job["id"], job["kind"], job["ref_id"], err, traceback.format_exc())
        if not jobs.fail(job, err):
            pipeline.on_final_failure(job, err)
        return
    jobs.finish(job["id"])


def lane_loop(name: str, kinds: list[str]) -> None:
    while not _stop.is_set():
        try:
            job = jobs.claim(kinds)
        except Exception:  # db locked etc.
            log.exception("claim failed in lane %s", name)
            job = None
        if job is None:
            _stop.wait(2)
            continue
        log.info("[%s] start job %s %s #%s", name, job["id"], job["kind"], job["ref_id"])
        run_job(job)


def _older_than(ts: str | None, minutes: float) -> bool:
    if not ts:
        return True
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - dt > timedelta(minutes=minutes)


def schedule_tick() -> None:
    # 1. creators due for a check
    for c in db.rows("SELECT id, last_checked_at, check_interval_min FROM creators WHERE active = 1"):
        if _older_than(c["last_checked_at"], c["check_interval_min"] or config.CHECK_INTERVAL_MIN):
            jobs.enqueue("check_creator", c["id"], priority=3)

    # 2. metric refresh at 24h / 72h / 7d for analyzed posts
    for v in db.rows("SELECT id, published_at, metrics_at FROM videos WHERE status IN ('done','waiting_llm') "
                     "AND published_at IS NOT NULL"):
        age = scoring.age_hours(v["published_at"]) or 0
        due = [h for h in config.METRIC_REFRESH_HOURS if age >= h]
        if not due or age > max(config.METRIC_REFRESH_HOURS) + 48:
            continue
        milestone_at = datetime.fromisoformat(v["published_at"]) + timedelta(hours=due[-1])
        if not v["metrics_at"] or datetime.fromisoformat(v["metrics_at"]) < milestone_at:
            jobs.enqueue("refresh_metrics", v["id"], max_attempts=2)

    jobs.prune()

    # 3. publishing status (rate-limited by Postiz; only when something is due)
    try:
        if publishing.due_for_sync():
            jobs.enqueue("publish_sync", None, max_attempts=1)
    except Exception:
        log.exception("publish sync scheduling failed")

    # 3a. evolve editing styles when enough new evidence has built up
    try:
        styles.maybe_auto_rebuild()
    except Exception:
        log.exception("style auto-rebuild failed")

    # 3b. daily auto-ideas when enough new teardowns have landed
    try:
        ideas.maybe_auto_batch()
    except Exception:
        log.exception("auto ideas failed")

    # 4. keep yt-dlp current (once a day)
    last = db.get_setting("ytdlp_updated_at")
    if _older_than(last, 24 * 60):
        db.set_setting("ytdlp_updated_at", db.now())
        try:
            log.info("yt-dlp update: %s", platforms.self_update())
        except Exception:
            log.exception("yt-dlp update failed")


def scheduler_loop() -> None:
    while not _stop.is_set():
        try:
            schedule_tick()
        except Exception:
            log.exception("schedule tick failed")
        _stop.wait(60)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config.ensure_dirs()
    db.conn()
    n = jobs.recover_stale()
    if n:
        log.info("recovered %s stale job(s)", n)
    # Videos stuck mid-pipeline from a crash go back to their job.
    for v in db.rows("SELECT id, status FROM videos WHERE status IN ('downloading','transcribing')"):
        if not db.row("SELECT 1 FROM jobs WHERE kind='process_video' AND ref_id=? AND status='pending'", (v["id"],)):
            pipeline.enqueue_process(v["id"])

    threads = [threading.Thread(target=scheduler_loop, name="scheduler", daemon=True)]
    for name, (kinds, count) in LANES.items():
        for i in range(count):
            threads.append(threading.Thread(target=lane_loop, args=(name, kinds), name=f"{name}-{i}", daemon=True))
    for t in threads:
        t.start()
    log.info("worker started: %s", ", ".join(t.name for t in threads))

    def _shutdown(*_):
        log.info("stopping…")
        _stop.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    while not _stop.is_set():
        time.sleep(1)
    for t in threads:
        t.join(timeout=5)


if __name__ == "__main__":
    main()
