"""SQLite storage. One file, WAL mode, shared by the API and the worker."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS creators (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,              -- tiktok | youtube | instagram
    handle TEXT NOT NULL,
    profile_url TEXT NOT NULL,
    display_name TEXT,
    avatar_url TEXT,
    followers INTEGER,
    notes TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    check_interval_min INTEGER,
    added_at TEXT NOT NULL,
    last_checked_at TEXT,
    last_check_status TEXT,              -- ok | error
    last_check_error TEXT,
    baseline_done INTEGER NOT NULL DEFAULT 0,
    baseline_at TEXT,
    UNIQUE (platform, handle)
);

CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY,
    creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    platform_id TEXT NOT NULL,
    url TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'short',  -- short | long (YouTube long-form)
    list_pos INTEGER,                    -- position in the profile listing at last check (0 = newest)
    title TEXT,
    description TEXT,
    published_at TEXT,                   -- ISO-8601 UTC
    duration REAL,
    thumbnail_url TEXT,
    views INTEGER,
    likes INTEGER,
    comments INTEGER,
    shares INTEGER,
    saves INTEGER,
    metrics_at TEXT,
    discovered_at TEXT NOT NULL,
    is_new INTEGER NOT NULL DEFAULT 0,   -- 1 = posted after we started watching
    -- processing
    status TEXT NOT NULL DEFAULT 'listed', -- listed | queued | downloading | transcribing | analyzing | waiting_llm | done | failed
    error TEXT,
    media_path TEXT,
    frames_json TEXT,
    transcript_json TEXT,                -- [{start,end,text}] segments
    words_json TEXT,                     -- [{start,end,word}]
    transcript_text TEXT,
    analysis_json TEXT,
    -- scores
    outlier REAL,
    engagement_rate REAL,
    engagement_rel REAL,
    hook_score REAL,
    score REAL,
    processed_at TEXT,
    UNIQUE (platform, platform_id)
);
CREATE INDEX IF NOT EXISTS idx_videos_creator ON videos(creator_id);
CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);

CREATE TABLE IF NOT EXISTS metric_snapshots (
    id INTEGER PRIMARY KEY,
    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    at TEXT NOT NULL,
    views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER, saves INTEGER
);
CREATE INDEX IF NOT EXISTS idx_snap_video ON metric_snapshots(video_id);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    ref_id INTEGER,
    payload TEXT,
    status TEXT NOT NULL DEFAULT 'pending', -- pending | running | done | failed
    priority INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    run_after TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, run_after);

CREATE TABLE IF NOT EXISTS idea_batches (
    id INTEGER PRIMARY KEY,
    focus TEXT,
    count INTEGER NOT NULL,
    source_video_ids TEXT,               -- JSON list, empty = use the top of the library
    auto INTEGER NOT NULL DEFAULT 0,     -- 1 = made by the daily scheduler
    status TEXT NOT NULL DEFAULT 'pending', -- pending | ready | failed | waiting_llm
    error TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS ideas (
    id INTEGER PRIMARY KEY,
    batch_id INTEGER REFERENCES idea_batches(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    pillar TEXT,
    format TEXT,
    angle TEXT,
    mechanism TEXT,
    funnel TEXT,
    why TEXT,
    hooks_json TEXT,                     -- [{text, onscreen_text, type, source_video_id}]
    chosen_hook INTEGER,                 -- index into hooks_json
    inspired_by TEXT,                    -- JSON list of video ids
    stage TEXT NOT NULL DEFAULT 'idea',  -- idea | scripted | filmed | edited | scheduled | posted
    script_json TEXT,
    script_status TEXT NOT NULL DEFAULT 'none', -- none | pending | ready | failed | waiting_llm
    script_error TEXT,
    script_version INTEGER NOT NULL DEFAULT 0,
    originality_json TEXT,
    hooks_status TEXT NOT NULL DEFAULT 'ready',  -- ready | pending | failed | waiting_llm
    notes TEXT,
    starred INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ideas_stage ON ideas(stage, archived);

CREATE TABLE IF NOT EXISTS edit_projects (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    idea_id INTEGER REFERENCES ideas(id) ON DELETE SET NULL,
    source_path TEXT NOT NULL,
    source_name TEXT,
    work_dir TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued', -- queued | preparing | deciding | rendering | ready | failed | waiting_llm
    stage TEXT,
    progress REAL,
    error TEXT,
    probe_json TEXT,
    decision_json TEXT,
    instruction TEXT,
    target_s INTEGER,
    options_json TEXT,
    overrides_json TEXT,
    result_json TEXT,
    render_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS publications (
    id INTEGER PRIMARY KEY,
    edit_id INTEGER NOT NULL REFERENCES edit_projects(id) ON DELETE CASCADE,
    idea_id INTEGER REFERENCES ideas(id) ON DELETE SET NULL,
    kind TEXT NOT NULL,                  -- schedule | draft | now
    scheduled_at TEXT,
    channels_json TEXT NOT NULL,         -- [{integration_id, provider, name, picture, text, title, settings}]
    status TEXT NOT NULL,                -- submitting | scheduled | publishing | draft | published | error | cancelled
    error TEXT,
    media_json TEXT,
    postiz_ids_json TEXT,
    postiz_response_json TEXT,
    results_json TEXT,
    render_count INTEGER,
    last_sync_at TEXT,
    published_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS edit_styles (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    best_for TEXT,
    params_json TEXT NOT NULL,
    inspired_by TEXT,
    source TEXT NOT NULL,                -- builtin | library | custom
    preview_path TEXT,
    preview_status TEXT NOT NULL DEFAULT 'pending',
    preview_error TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    retired_by TEXT,                     -- owner | evolve
    retire_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    level TEXT NOT NULL,                 -- info | warn | error
    message TEXT NOT NULL,
    creator_id INTEGER,
    video_id INTEGER
);
"""

# Columns added after first release: (table, column, ddl)
MIGRATIONS = [
    ("videos", "kind", "ALTER TABLE videos ADD COLUMN kind TEXT NOT NULL DEFAULT 'short'"),
    ("videos", "list_pos", "ALTER TABLE videos ADD COLUMN list_pos INTEGER"),
    ("ideas", "mechanism", "ALTER TABLE ideas ADD COLUMN mechanism TEXT"),
    ("creators", "baseline_at", "ALTER TABLE creators ADD COLUMN baseline_at TEXT"),
    ("videos", "style_json", "ALTER TABLE videos ADD COLUMN style_json TEXT"),
    ("edit_styles", "retired_by", "ALTER TABLE edit_styles ADD COLUMN retired_by TEXT"),
    ("edit_styles", "retire_reason", "ALTER TABLE edit_styles ADD COLUMN retire_reason TEXT"),
    ("ideas", "funnel", "ALTER TABLE ideas ADD COLUMN funnel TEXT"),
]

_local = threading.local()
_init_lock = threading.Lock()
_initialized = False


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def conn() -> sqlite3.Connection:
    global _initialized
    c = getattr(_local, "conn", None)
    if c is None:
        c = _connect()
        _local.conn = c
    if not _initialized:
        with _init_lock:
            if not _initialized:
                c.executescript(SCHEMA)
                for table, column, ddl in MIGRATIONS:
                    cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
                    if column not in cols:
                        c.execute(ddl)
                c.commit()
                _initialized = True
    return c


def reset_for_tests() -> None:
    global _initialized
    c = getattr(_local, "conn", None)
    if c is not None:
        c.close()
        _local.conn = None
    _initialized = False


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    c = conn()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise


def rows(sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn().execute(sql, params).fetchall()]


def row(sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
    r = conn().execute(sql, params).fetchone()
    return dict(r) if r else None


def scalar(sql: str, params: tuple | list = ()) -> Any:
    r = conn().execute(sql, params).fetchone()
    return r[0] if r else None


def execute(sql: str, params: tuple | list = ()) -> int:
    with tx() as c:
        cur = c.execute(sql, params)
        return cur.lastrowid or cur.rowcount


def update(table: str, id_: int, fields: dict[str, Any]) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    execute(f"UPDATE {table} SET {cols} WHERE id = ?", [*fields.values(), id_])


def get_setting(key: str, default: Any = None) -> Any:
    r = row("SELECT value FROM settings WHERE key = ?", (key,))
    return json.loads(r["value"]) if r else default


def set_setting(key: str, value: Any) -> None:
    execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


def log_event(message: str, level: str = "info", creator_id: int | None = None, video_id: int | None = None) -> None:
    execute(
        "INSERT INTO events(at, level, message, creator_id, video_id) VALUES(?, ?, ?, ?, ?)",
        (now(), level, message, creator_id, video_id),
    )
