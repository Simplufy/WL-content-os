"""HTTP API + static dashboard.

Run: uvicorn studio.api:app --host 127.0.0.1 --port 8794
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from urllib.parse import urlparse
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import analyze, auth, brand, config, db, editing, ideas, jobs, postiz, publishing, styles, llm, pipeline, platforms, scoring


@asynccontextmanager
async def lifespan(_app: FastAPI):
    config.ensure_dirs()
    db.conn()
    auth.ensure_schema()
    yield


app = FastAPI(title=brand.get()["product_name"], docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)

PUBLIC_API = {"/api/health", "/api/auth/login", "/api/auth/me", "/api/brand"}


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    """Everything under /api needs a signed-in user unless the request is made directly on this machine."""
    local = auth.is_local(request.headers.get("host"), dict(request.headers))
    user = auth.user_for(request.cookies.get(auth.SESSION_COOKIE))
    request.state.local, request.state.user = local, user
    path = request.url.path
    if not local:
        if path.startswith("/api/") and path not in PUBLIC_API and not user:
            return JSONResponse({"detail": "Sign in required"}, status_code=401)
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)
    response = await call_next(request)
    if not local:
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


def _require_admin(request: Request) -> None:
    u = request.state.user
    if not request.state.local and not (u and u.get("is_admin")):
        raise HTTPException(403, "Admins only")


class LoginIn(BaseModel):
    email: str
    password: str


@app.post("/api/auth/login")
def auth_login(body: LoginIn, request: Request, response: Response) -> dict[str, Any]:
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
    if not auth.allow_attempt(f"{ip}|{body.email.lower()}") or not auth.allow_attempt(ip):
        raise HTTPException(429, "Too many attempts. Wait 15 minutes and try again.")
    res = auth.login(body.email, body.password, request.headers.get("user-agent", ""))
    if not res:
        raise HTTPException(401, "Wrong email or password")
    token, user = res
    response.set_cookie(auth.SESSION_COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True,
                        secure=not request.state.local, samesite="lax", path="/")
    return {"user": user}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response) -> dict[str, Any]:
    auth.logout(request.cookies.get(auth.SESSION_COOKIE))
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict[str, Any]:
    return {"user": request.state.user, "local": request.state.local,
            "has_users": bool(auth.list_users())}


class UserIn(BaseModel):
    email: str
    password: str
    name: str | None = None
    is_admin: bool = False


class PasswordIn(BaseModel):
    password: str


@app.get("/api/auth/users")
def auth_users(request: Request) -> list[dict[str, Any]]:
    _require_admin(request)
    return auth.list_users()


@app.post("/api/auth/users", status_code=201)
def auth_add_user(body: UserIn, request: Request) -> dict[str, Any]:
    _require_admin(request)
    first = not auth.list_users()
    try:
        uid = auth.create_user(body.email, body.password, name=body.name, is_admin=body.is_admin or first)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": uid}


@app.delete("/api/auth/users/{uid}")
def auth_delete_user(uid: int, request: Request) -> dict[str, Any]:
    _require_admin(request)
    me = request.state.user
    if me and me["id"] == uid:
        raise HTTPException(400, "You can't remove yourself")
    auth.delete_user(uid)
    return {"ok": True}


@app.post("/api/auth/password")
def auth_change_password(body: PasswordIn, request: Request) -> dict[str, Any]:
    me = request.state.user
    if not me:
        raise HTTPException(400, "Sign in first")
    try:
        auth.set_password(me["id"], body.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}

VIDEO_LIST_COLS = (
    "v.id, v.creator_id, v.platform, v.platform_id, v.url, v.kind, v.title, v.published_at, v.duration, v.thumbnail_url, "
    "v.views, v.likes, v.comments, v.shares, v.saves, v.discovered_at, v.is_new, v.status, v.error, "
    "v.outlier, v.engagement_rate, v.engagement_rel, v.hook_score, v.score, v.processed_at, "
    "v.analysis_json, v.frames_json, v.media_path, c.handle, c.display_name, c.avatar_url"
)


# --------------------------------------------------------------------------- helpers

def _frame_urls(video_id: int, frames_json: str | None) -> list[dict[str, Any]]:
    out = []
    for f in json.loads(frames_json or "[]"):
        out.append({"t": f["t"], "hook": f.get("hook", False), "url": f"/api/media/frame/{video_id}/{Path(f['path']).name}"})
    return out


def _shape_video(r: dict[str, Any], full: bool = False) -> dict[str, Any]:
    analysis = json.loads(r.pop("analysis_json") or "null")
    frames = _frame_urls(r["id"], r.pop("frames_json", None))
    r["has_media"] = bool(r.pop("media_path", None))
    r["age_hours"] = scoring.age_hours(r.get("published_at"))
    r["early"] = r["age_hours"] is not None and r["age_hours"] < 48
    hook = (analysis or {}).get("hook") or {}
    r["hook"] = {k: hook.get(k) for k in ("spoken", "onscreen_text", "type", "template")} if hook else None
    r["topic"] = (analysis or {}).get("topic")
    r["format"] = (analysis or {}).get("format")
    r["poster"] = next((f["url"] for f in frames if f["t"] >= 1.0), frames[0]["url"] if frames else r.get("thumbnail_url"))
    if full:
        r["analysis"] = analysis
        r["frames"] = frames
    return r


def _creator_or_404(cid: int) -> dict[str, Any]:
    c = db.row("SELECT * FROM creators WHERE id = ?", (cid,))
    if not c:
        raise HTTPException(404, "Creator not found")
    return c


# --------------------------------------------------------------------------- dashboard

@app.get("/api/brand")
def brand_info() -> dict[str, Any]:
    return brand.public_info()


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True}


@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    week = "datetime('now', '-7 days')"
    kpis = {
        "creators": db.scalar("SELECT COUNT(*) FROM creators WHERE active = 1"),
        "videos_tracked": db.scalar("SELECT COUNT(*) FROM videos"),
        "videos_analyzed": db.scalar("SELECT COUNT(*) FROM videos WHERE status = 'done'"),
        "new_this_week": db.scalar(f"SELECT COUNT(*) FROM videos WHERE is_new = 1 AND discovered_at >= {week}"),
        "outliers_this_month": db.scalar(
            "SELECT COUNT(*) FROM videos WHERE outlier >= 3 AND COALESCE(published_at, discovered_at) >= datetime('now','-30 days')"),
        "in_queue": db.scalar("SELECT COUNT(*) FROM jobs WHERE status = 'running' OR (status = 'pending' "
                              "AND COALESCE(error, '') NOT LIKE 'Claude unavailable%')"),
        "waiting_llm": db.scalar("SELECT COUNT(*) FROM videos WHERE status = 'waiting_llm'"),
        "failed": db.scalar("SELECT COUNT(*) FROM videos WHERE status = 'failed'"),
        "ideas_open": db.scalar("SELECT COUNT(*) FROM ideas WHERE archived = 0 AND stage = 'idea'"),
        "scripts_ready": db.scalar("SELECT COUNT(*) FROM ideas WHERE archived = 0 AND stage = 'scripted'"),
        "scheduled": db.scalar("SELECT COUNT(*) FROM publications WHERE status IN ('scheduled','publishing')"),
        "published_30d": db.scalar("SELECT COUNT(*) FROM publications WHERE status = 'published' AND published_at >= datetime('now','-30 days')"),
    }
    base = f"SELECT {VIDEO_LIST_COLS} FROM videos v JOIN creators c ON c.id = v.creator_id"
    newest = db.rows(base + " WHERE v.status != 'listed' ORDER BY COALESCE(v.published_at, v.discovered_at) DESC LIMIT 8")
    outliers = db.rows(base + " WHERE v.outlier IS NOT NULL AND COALESCE(v.published_at, v.discovered_at) >= datetime('now','-60 days') "
                       "ORDER BY v.outlier DESC LIMIT 8")
    hooks = db.rows(base + " WHERE v.hook_score IS NOT NULL ORDER BY v.score DESC, v.hook_score DESC LIMIT 8")
    events = db.rows("SELECT * FROM events ORDER BY id DESC LIMIT 15")
    return {
        "kpis": kpis,
        "newest": [_shape_video(r) for r in newest],
        "outliers": [_shape_video(r) for r in outliers],
        "top_hooks": [_shape_video(r) for r in hooks],
        "events": events,
        "llm": llm.status(),
        "processing": db.rows("SELECT v.id, v.status, v.title, c.handle FROM videos v JOIN creators c ON c.id=v.creator_id "
                              "WHERE v.status IN ('queued','downloading','transcribing','analyzing') ORDER BY v.id LIMIT 20"),
    }


# --------------------------------------------------------------------------- creators

class CreatorIn(BaseModel):
    url: str
    notes: str | None = None


class CreatorPatch(BaseModel):
    active: bool | None = None
    check_interval_min: int | None = None
    notes: str | None = None


@app.get("/api/creators")
def list_creators() -> list[dict[str, Any]]:
    creators = db.rows("SELECT * FROM creators ORDER BY added_at DESC")
    for c in creators:
        stats = db.row(
            "SELECT COUNT(*) AS posts, SUM(status='done') AS analyzed, AVG(score) AS avg_score, MAX(outlier) AS best_outlier, "
            "MAX(COALESCE(published_at, discovered_at)) AS last_post, SUM(status IN ('queued','downloading','transcribing','analyzing')) AS processing "
            "FROM videos WHERE creator_id = ?", (c["id"],))
        c.update(stats or {})
        c["baseline"] = scoring.creator_baseline(c["id"])
        c["check_pending"] = bool(db.row("SELECT 1 FROM jobs WHERE kind='check_creator' AND ref_id=? AND status IN ('pending','running')", (c["id"],)))
    return creators


@app.post("/api/creators", status_code=201)
def add_creator(body: CreatorIn) -> dict[str, Any]:
    try:
        p = platforms.parse_profile_url(body.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if db.row("SELECT id FROM creators WHERE platform = ? AND lower(handle) = lower(?)", (p.platform, p.handle)):
        raise HTTPException(409, f"@{p.handle} on {p.platform} is already on the list")
    cid = db.execute(
        "INSERT INTO creators(platform, handle, profile_url, notes, added_at) VALUES(?,?,?,?,?)",
        (p.platform, p.handle, p.profile_url, body.notes, db.now()),
    )
    jobs.enqueue("check_creator", cid, priority=8)
    warning = None
    if p.platform == "instagram" and not platforms.has_cookies("instagram"):
        warning = "Instagram needs cookies before it can be checked. Add them in Settings."
    return {**_creator_or_404(cid), "warning": warning}


@app.get("/api/creators/{cid}")
def get_creator(cid: int) -> dict[str, Any]:
    c = _creator_or_404(cid)
    c["baseline"] = scoring.creator_baseline(cid)
    vids = db.rows(f"SELECT {VIDEO_LIST_COLS} FROM videos v JOIN creators c ON c.id = v.creator_id WHERE v.creator_id = ? "
                   "ORDER BY COALESCE(v.published_at, v.discovered_at) DESC, v.id", (cid,))
    c["videos"] = [_shape_video(v) for v in vids]
    return c


@app.patch("/api/creators/{cid}")
def patch_creator(cid: int, body: CreatorPatch) -> dict[str, Any]:
    _creator_or_404(cid)
    fields = body.model_dump(exclude_none=True)
    if "active" in fields:
        fields["active"] = int(fields["active"])
    db.update("creators", cid, fields)
    return _creator_or_404(cid)


@app.delete("/api/creators/{cid}")
def delete_creator(cid: int) -> dict[str, Any]:
    _creator_or_404(cid)
    for v in db.rows("SELECT id, media_path FROM videos WHERE creator_id = ?", (cid,)):
        _delete_media(v)
    db.execute("DELETE FROM jobs WHERE ref_id IN (SELECT id FROM videos WHERE creator_id = ?) AND kind IN ('process_video','analyze_video','refresh_metrics')", (cid,))
    db.execute("DELETE FROM jobs WHERE kind = 'check_creator' AND ref_id = ?", (cid,))
    db.execute("DELETE FROM creators WHERE id = ?", (cid,))
    return {"ok": True}


@app.post("/api/creators/{cid}/check")
def check_now(cid: int) -> dict[str, Any]:
    _creator_or_404(cid)
    jobs.enqueue("check_creator", cid, priority=9)
    return {"queued": True}


def _delete_media(v: dict[str, Any]) -> None:
    if v.get("media_path"):
        Path(v["media_path"]).unlink(missing_ok=True)
    shutil.rmtree(config.MEDIA_DIR / "frames" / str(v["id"]), ignore_errors=True)


# --------------------------------------------------------------------------- videos

SORTS = {
    "score": "v.score DESC NULLS LAST",
    "outlier": "v.outlier DESC NULLS LAST",
    "hook": "v.hook_score DESC NULLS LAST",
    "views": "v.views DESC NULLS LAST",
    "recent": "COALESCE(v.published_at, v.discovered_at) DESC",
}


@app.get("/api/videos")
def list_videos(
    creator_id: int | None = None,
    status: str | None = None,
    analyzed: bool | None = None,
    sort: str = "recent",
    min_outlier: float | None = None,
    days: int | None = None,
    hook_type: str | None = None,
    q: str | None = None,
    limit: int = Query(60, le=500),
    offset: int = 0,
) -> dict[str, Any]:
    where, params = ["1=1"], []
    if creator_id:
        where.append("v.creator_id = ?"); params.append(creator_id)
    if status:
        where.append("v.status = ?"); params.append(status)
    if analyzed is True:
        where.append("v.status = 'done'")
    elif analyzed is False:
        where.append("v.status != 'done'")
    if min_outlier is not None:
        where.append("v.outlier >= ?"); params.append(min_outlier)
    if days:
        where.append("COALESCE(v.published_at, v.discovered_at) >= datetime('now', ?)"); params.append(f"-{int(days)} days")
    if hook_type:
        where.append("json_extract(v.analysis_json, '$.hook.type') = ?"); params.append(hook_type)
    if q:
        where.append("(v.title LIKE ? OR v.transcript_text LIKE ? OR c.handle LIKE ?)"); params += [f"%{q}%"] * 3
    order = SORTS.get(sort, SORTS["recent"])
    w = " AND ".join(where)
    total = db.scalar(f"SELECT COUNT(*) FROM videos v JOIN creators c ON c.id = v.creator_id WHERE {w}", params)
    items = db.rows(f"SELECT {VIDEO_LIST_COLS} FROM videos v JOIN creators c ON c.id = v.creator_id WHERE {w} "
                    f"ORDER BY {order}, v.id DESC LIMIT ? OFFSET ?", [*params, limit, offset])
    return {"total": total, "items": [_shape_video(r) for r in items]}


@app.get("/api/videos/{vid}")
def get_video(vid: int) -> dict[str, Any]:
    r = db.row(f"SELECT {VIDEO_LIST_COLS}, v.description, v.transcript_json, v.words_json, v.transcript_text "
               "FROM videos v JOIN creators c ON c.id = v.creator_id WHERE v.id = ?", (vid,))
    if not r:
        raise HTTPException(404, "Video not found")
    r["transcript"] = json.loads(r.pop("transcript_json") or "[]")
    r.pop("words_json", None)
    v = _shape_video(r, full=True)
    v["snapshots"] = db.rows("SELECT at, views, likes, comments, shares, saves FROM metric_snapshots WHERE video_id = ? ORDER BY at", (vid,))
    v["baseline"] = scoring.creator_baseline(v["creator_id"], exclude_id=vid, kind=v.get("kind") or "short")
    return v


@app.post("/api/videos/{vid}/process")
def process_now(vid: int) -> dict[str, Any]:
    if not db.row("SELECT id FROM videos WHERE id = ?", (vid,)):
        raise HTTPException(404, "Video not found")
    pipeline.enqueue_process(vid, priority=9)
    return {"queued": True}


@app.post("/api/videos/{vid}/analyze")
def analyze_now(vid: int) -> dict[str, Any]:
    v = db.row("SELECT id, words_json FROM videos WHERE id = ?", (vid,))
    if not v:
        raise HTTPException(404, "Video not found")
    if not v["words_json"]:
        pipeline.enqueue_process(vid, priority=9)
    else:
        db.update("videos", vid, {"status": "analyzing"})
        jobs.enqueue("analyze_video", vid, priority=9, max_attempts=2)
    return {"queued": True}


@app.get("/api/hooks")
def hooks(sort: str = "score", hook_type: str | None = None, limit: int = Query(100, le=500)) -> list[dict[str, Any]]:
    where = "v.status = 'done' AND v.analysis_json IS NOT NULL"
    params: list[Any] = []
    if hook_type:
        where += " AND json_extract(v.analysis_json, '$.hook.type') = ?"
        params.append(hook_type)
    order = {"hook": "v.hook_score DESC", "outlier": "v.outlier DESC NULLS LAST", "score": "v.score DESC NULLS LAST"}.get(sort, "v.score DESC NULLS LAST")
    out = []
    for r in db.rows(f"SELECT v.id, v.url, v.views, v.outlier, v.hook_score, v.score, v.published_at, v.analysis_json, "
                     f"c.handle, c.platform FROM videos v JOIN creators c ON c.id = v.creator_id WHERE {where} "
                     f"ORDER BY {order} LIMIT ?", [*params, limit]):
        a = json.loads(r.pop("analysis_json"))
        r["hook"] = a.get("hook")
        r["topic"] = a.get("topic")
        r["format"] = a.get("format")
        out.append(r)
    return out



# --------------------------------------------------------------------------- ideas & scripts

def _shape_idea(r: dict[str, Any], full: bool = False) -> dict[str, Any]:
    r["hooks"] = json.loads(r.pop("hooks_json") or "[]")
    r["inspired_by"] = json.loads(r.get("inspired_by") or "[]")
    script = json.loads(r.pop("script_json") or "null")
    r["originality"] = json.loads(r.pop("originality_json") or "null")
    r["has_script"] = script is not None
    r["script_summary"] = {"duration_s": script.get("duration_s"), "word_count": script.get("word_count"),
                           "lines": len(script.get("lines") or [])} if script else None
    if full:
        r["script"] = script
        r["inspired_videos"] = [
            _shape_video(v) for v in db.rows(
                f"SELECT {VIDEO_LIST_COLS} FROM videos v JOIN creators c ON c.id = v.creator_id WHERE v.id IN "
                f"({','.join('?' for _ in r['inspired_by']) or 'NULL'})", r["inspired_by"])
        ] if r["inspired_by"] else []
    return r


class GenerateIn(BaseModel):
    count: int = 5
    focus: str | None = None
    video_ids: list[int] | None = None


class IdeaIn(BaseModel):
    title: str
    angle: str | None = None
    pillar: str | None = None


class IdeaPatch(BaseModel):
    title: str | None = None
    angle: str | None = None
    pillar: str | None = None
    format: str | None = None
    stage: str | None = None
    notes: str | None = None
    starred: bool | None = None
    archived: bool | None = None
    chosen_hook: int | None = None


class InstructionIn(BaseModel):
    instruction: str | None = None


class ScriptIn(BaseModel):
    script: dict[str, Any]


@app.get("/api/ideas")
def list_ideas(archived: bool = False, stage: str | None = None) -> dict[str, Any]:
    where, params = ["archived = ?"], [int(archived)]
    if stage:
        where.append("stage = ?"); params.append(stage)
    items = db.rows(f"SELECT * FROM ideas WHERE {' AND '.join(where)} ORDER BY starred DESC, updated_at DESC", params)
    batches = db.rows("SELECT * FROM idea_batches ORDER BY id DESC LIMIT 10")
    for b in batches:
        b["source_video_ids"] = json.loads(b["source_video_ids"] or "[]")
        b["ideas"] = db.scalar("SELECT COUNT(*) FROM ideas WHERE batch_id = ?", (b["id"],))
    return {"items": [_shape_idea(i) for i in items], "batches": batches, "stages": list(ideas.STAGES),
            "pillars": ideas.pillars(), "library_size": db.scalar("SELECT COUNT(*) FROM videos WHERE status = 'done'")}


@app.post("/api/ideas/generate", status_code=202)
def generate_ideas(body: GenerateIn) -> dict[str, Any]:
    if not db.scalar("SELECT COUNT(*) FROM videos WHERE status = 'done'"):
        raise HTTPException(409, "No analyzed videos yet. Add creators first — ideas are built from their teardowns.")
    bid = ideas.create_batch(body.count, body.focus, body.video_ids)
    return {"batch_id": bid}


@app.post("/api/ideas", status_code=201)
def create_idea(body: IdeaIn) -> dict[str, Any]:
    if not body.title.strip():
        raise HTTPException(400, "Give the idea a title")
    iid = db.execute("INSERT INTO ideas(title, angle, pillar, hooks_json, created_at, updated_at) VALUES(?,?,?,'[]',?,?)",
                     (body.title.strip(), body.angle, body.pillar, db.now(), db.now()))
    return get_idea(iid)


@app.get("/api/ideas/{iid}")
def get_idea(iid: int) -> dict[str, Any]:
    r = db.row("SELECT * FROM ideas WHERE id = ?", (iid,))
    if not r:
        raise HTTPException(404, "Idea not found")
    return _shape_idea(r, full=True)


@app.patch("/api/ideas/{iid}")
def patch_idea(iid: int, body: IdeaPatch) -> dict[str, Any]:
    get_idea(iid)
    fields = body.model_dump(exclude_none=True)
    if "stage" in fields and fields["stage"] not in ideas.STAGES:
        raise HTTPException(400, "Unknown stage")
    for k in ("starred", "archived"):
        if k in fields:
            fields[k] = int(fields[k])
    fields["updated_at"] = db.now()
    db.update("ideas", iid, fields)
    return get_idea(iid)


@app.delete("/api/ideas/{iid}")
def delete_idea(iid: int) -> dict[str, Any]:
    db.execute("DELETE FROM jobs WHERE kind IN ('write_script','more_hooks') AND ref_id = ? AND status = 'pending'", (iid,))
    db.execute("DELETE FROM ideas WHERE id = ?", (iid,))
    return {"ok": True}


@app.post("/api/ideas/{iid}/script", status_code=202)
def script_idea(iid: int, body: InstructionIn) -> dict[str, Any]:
    get_idea(iid)
    ideas.request_script(iid, body.instruction)
    return {"queued": True}


@app.put("/api/ideas/{iid}/script")
def save_script(iid: int, body: ScriptIn) -> dict[str, Any]:
    get_idea(iid)
    sc = body.script
    if not isinstance(sc.get("hook"), dict) or not isinstance(sc.get("lines"), list):
        raise HTTPException(400, "Script needs a hook and lines")
    ideas.save_script_edit(iid, sc)
    return get_idea(iid)


@app.post("/api/ideas/{iid}/hooks", status_code=202)
def hooks_idea(iid: int, body: InstructionIn) -> dict[str, Any]:
    get_idea(iid)
    ideas.request_hooks(iid, body.instruction)
    return {"queued": True}



# --------------------------------------------------------------------------- editor

VIDEO_EXT = {".mov", ".mp4", ".m4v", ".mkv", ".webm", ".avi"}


def _edit_or_404(pid: int) -> dict[str, Any]:
    p = db.row("SELECT * FROM edit_projects WHERE id = ?", (pid,))
    if not p:
        raise HTTPException(404, "Edit not found")
    return p


def _shape_edit(p: dict[str, Any], full: bool = False) -> dict[str, Any]:
    probe = json.loads(p.get("probe_json") or "null")
    result = json.loads(p.get("result_json") or "null")
    decision = json.loads(p.get("decision_json") or "null")
    work = Path(p["work_dir"]) if p.get("work_dir") else None
    out = {
        "id": p["id"], "title": p["title"], "idea_id": p["idea_id"], "source_name": p["source_name"],
        "status": p["status"], "stage": p["stage"], "progress": p["progress"], "error": p["error"],
        "instruction": p["instruction"], "target_s": p["target_s"], "render_count": p["render_count"],
        "created_at": p["created_at"], "updated_at": p["updated_at"],
        "raw_duration": probe["duration"] if probe else None,
        "duration": result["duration"] if result else None,
        "has_output": bool(result and Path(result["output"]).exists()),
        "thumb": f"/api/edits/{p['id']}/thumb" if work and (work / "thumb.jpg").exists() else None,
        "options": {**editing.engine.DEFAULT_OPTIONS, **json.loads(p.get("options_json") or "{}")},
        "hook_text": decision.get("hook_text") if decision else None,
    }
    if full:
        out["probe"] = probe
        out["notes"] = (decision.get("notes") or "").replace('\\"', '"') if decision else None
        out["callouts"] = (result or {}).get("callouts") or []
        out["graphics"] = (result or {}).get("graphics") or []
        out["segments"] = len((result or {}).get("segments") or [])
        out["overrides"] = json.loads(p.get("overrides_json") or "null") or {"restore": [], "remove": []}
        words_path = work / "words.json" if work else None
        if decision and words_path and words_path.exists():
            words = json.loads(words_path.read_text())
            ranges = editing.engine.keep_ranges(decision, out["overrides"], len(words))
            kept = {i for a, b in ranges for i in range(a, b + 1)}
            reasons: dict[int, str] = {}
            for c in decision.get("cuts") or []:
                for i in range(c["from"], c["to"] + 1):
                    reasons[i] = c["reason"]
            out["words"] = [{"i": i, "w": w["word"], "s": w["start"], "e": w["end"], "k": i in kept,
                             "r": reasons.get(i)} for i, w in enumerate(words)]
            order = [i for a, b in ranges for i in range(a, b + 1)]
            out["reordered"] = order != sorted(order)
        else:
            out["words"] = []
    return out


class ImportIn(BaseModel):
    style_id: int | None = None
    path: str
    title: str | None = None
    idea_id: int | None = None
    instruction: str | None = None
    target_s: int | None = None


class EditPatch(BaseModel):
    title: str | None = None
    idea_id: int | None = None
    options: dict[str, Any] | None = None


class OverridesIn(BaseModel):
    restore: list[int] = []
    remove: list[int] = []


class RedecideIn(BaseModel):
    instruction: str | None = None
    target_s: int | None = None


@app.get("/api/edits")
def list_edits() -> list[dict[str, Any]]:
    return [_shape_edit(p) for p in db.rows("SELECT * FROM edit_projects ORDER BY id DESC")]


@app.post("/api/edits", status_code=201)
async def upload_edit(file: UploadFile, title: str | None = None, idea_id: int | None = None,
                      instruction: str | None = None, target_s: int | None = None) -> dict[str, Any]:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in VIDEO_EXT:
        raise HTTPException(400, f"Unsupported file type {ext or '(none)'}")
    tmp_dir = config.MEDIA_DIR / "uploads"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"upload-{db.now().replace(':', '')}{ext}"
    with tmp.open("wb") as fh:
        while chunk := await file.read(8 * 1024 * 1024):
            fh.write(chunk)
    pid = editing.create(tmp, title=title or Path(file.filename or "clip").stem, idea_id=idea_id,
                         instruction=instruction, target_s=target_s, move=True)
    return _shape_edit(_edit_or_404(pid))


# Small pieces: each must cross Cloudflare well inside its timeouts even on a slow home/cell uplink,
# and a dropped piece only costs a few MB to resend.
CHUNK = 8 * 1024 * 1024
UPLOADS = config.MEDIA_DIR / "uploads"


class UploadInit(BaseModel):
    filename: str
    size: int


class UploadDone(BaseModel):
    style_id: int | None = None
    title: str | None = None
    idea_id: int | None = None
    instruction: str | None = None


def _upload_paths(uid: str) -> tuple[Path, Path]:
    if not uid.isalnum():
        raise HTTPException(400, "Bad upload id")
    return UPLOADS / f"{uid}.part", UPLOADS / f"{uid}.json"


@app.post("/api/uploads", status_code=201)
def upload_init(body: UploadInit) -> dict[str, Any]:
    ext = Path(body.filename).suffix.lower()
    if ext not in VIDEO_EXT:
        raise HTTPException(400, f"Unsupported file type {ext or '(none)'}")
    if body.size <= 0 or body.size > 20 * 1024 ** 3:
        raise HTTPException(400, "File size must be between 1 byte and 20 GB")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    uid = uuid.uuid4().hex
    part, meta = _upload_paths(uid)
    part.write_bytes(b"")
    meta.write_text(json.dumps({"filename": body.filename, "size": body.size, "ext": ext, "created_at": db.now()}))
    return {"id": uid, "chunk_size": CHUNK}


@app.get("/api/uploads/{uid}")
def upload_status(uid: str) -> dict[str, Any]:
    """How much of an upload already arrived, so a dropped or reloaded upload resumes instead of restarting."""
    part, meta = _upload_paths(uid)
    if not meta.exists():
        raise HTTPException(404, "Upload not found")
    return {"received": part.stat().st_size, "size": json.loads(meta.read_text())["size"], "chunk_size": CHUNK}


@app.put("/api/uploads/{uid}")
async def upload_chunk(uid: str, request: Request, offset: int = 0) -> dict[str, Any]:
    part, meta = _upload_paths(uid)
    if not meta.exists():
        raise HTTPException(404, "Upload not found")
    info = json.loads(meta.read_text())
    have = part.stat().st_size
    if offset > have:
        raise HTTPException(409, f"Expected offset {have}")
    body = await request.body()
    if len(body) > CHUNK + 1024:
        raise HTTPException(413, "Chunk too large")
    if offset + len(body) > info["size"]:
        raise HTTPException(400, "More data than the declared size")
    if offset + len(body) > have:  # retries of chunks we already have are no-ops
        with part.open("r+b") as fh:
            fh.seek(offset)
            fh.write(body)
    return {"received": part.stat().st_size, "size": info["size"]}


@app.post("/api/uploads/{uid}/complete", status_code=201)
def upload_complete(uid: str, body: UploadDone) -> dict[str, Any]:
    part, meta = _upload_paths(uid)
    if not meta.exists():
        raise HTTPException(404, "Upload not found")
    info = json.loads(meta.read_text())
    if part.stat().st_size != info["size"]:
        raise HTTPException(409, f"Upload incomplete: {part.stat().st_size} of {info['size']} bytes")
    final = UPLOADS / f"{uid}{info['ext']}"
    part.rename(final)
    meta.unlink()
    pid = editing.create(final, title=body.title or Path(info["filename"]).stem, idea_id=body.idea_id,
                         instruction=body.instruction, move=True, style_id=body.style_id)
    return _shape_edit(_edit_or_404(pid))


@app.post("/api/edits/import", status_code=201)
def import_edit(body: ImportIn, request: Request) -> dict[str, Any]:
    if not request.state.local:
        raise HTTPException(403, "Importing files from this computer only works on the computer itself")
    src = Path(body.path).expanduser().resolve()
    if not src.is_file() or src.suffix.lower() not in VIDEO_EXT:
        raise HTTPException(400, "Not a video file")
    if config.HOME.resolve() not in src.parents:
        raise HTTPException(400, "Only files in your home folder can be imported")
    pid = editing.create(src, title=body.title, idea_id=body.idea_id, instruction=body.instruction, target_s=body.target_s,
                         style_id=body.style_id)
    return _shape_edit(_edit_or_404(pid))


@app.get("/api/edits/{pid}")
def get_edit(pid: int) -> dict[str, Any]:
    return _shape_edit(_edit_or_404(pid), full=True)


@app.patch("/api/edits/{pid}")
def patch_edit(pid: int, body: EditPatch) -> dict[str, Any]:
    p = _edit_or_404(pid)
    fields: dict[str, Any] = {"updated_at": db.now()}
    if body.title is not None:
        fields["title"] = body.title.strip() or p["title"]
    if body.idea_id is not None:
        fields["idea_id"] = body.idea_id or None
    if body.options is not None:
        allowed = set(editing.engine.DEFAULT_OPTIONS)
        merged = {**json.loads(p["options_json"] or "{}"), **{k: v for k, v in body.options.items() if k in allowed}}
        fields["options_json"] = json.dumps(merged)
    db.update("edit_projects", pid, fields)
    return _shape_edit(_edit_or_404(pid), full=True)


def _queue_render(pid: int) -> None:
    db.update("edit_projects", pid, {"status": "rendering", "stage": "Queued", "progress": 0, "error": None, "updated_at": db.now()})
    jobs.enqueue("edit_render", pid, priority=9, max_attempts=2)


@app.post("/api/edits/{pid}/overrides")
def set_overrides(pid: int, body: OverridesIn) -> dict[str, Any]:
    p = _edit_or_404(pid)
    if not p["decision_json"]:
        raise HTTPException(409, "The edit decision isn't ready yet")
    restore = sorted(set(body.restore) - set(body.remove))
    db.update("edit_projects", pid, {"overrides_json": json.dumps({"restore": restore, "remove": sorted(set(body.remove))}),
                                     "updated_at": db.now()})
    return _shape_edit(_edit_or_404(pid), full=True)


@app.post("/api/edits/{pid}/render", status_code=202)
def rerender(pid: int) -> dict[str, Any]:
    p = _edit_or_404(pid)
    if not p["decision_json"]:
        raise HTTPException(409, "The edit decision isn't ready yet")
    _queue_render(pid)
    return {"queued": True}


@app.post("/api/edits/{pid}/redecide", status_code=202)
def redecide(pid: int, body: RedecideIn) -> dict[str, Any]:
    p = _edit_or_404(pid)
    if not p["probe_json"]:
        raise HTTPException(409, "Still preparing the footage")
    db.update("edit_projects", pid, {"instruction": (body.instruction or "").strip() or None, "target_s": body.target_s,
                                     "status": "deciding", "stage": "Queued", "error": None, "updated_at": db.now()})
    jobs.enqueue("edit_decide", pid, priority=9, max_attempts=2)
    return {"queued": True}


@app.delete("/api/edits/{pid}")
def delete_edit(pid: int) -> dict[str, Any]:
    p = _edit_or_404(pid)
    db.execute("DELETE FROM jobs WHERE kind IN ('edit_prepare','edit_decide','edit_render') AND ref_id = ? AND status = 'pending'", (pid,))
    db.execute("DELETE FROM edit_projects WHERE id = ?", (pid,))
    if p["work_dir"] and Path(p["work_dir"]).is_relative_to(editing.EDITS_DIR):
        shutil.rmtree(p["work_dir"], ignore_errors=True)
    return {"ok": True}


def _edit_file(pid: int, name: str) -> Path:
    p = _edit_or_404(pid)
    f = Path(p["work_dir"]) / name
    if not f.exists():
        raise HTTPException(404)
    return f


@app.get("/api/edits/{pid}/video")
def edit_video(pid: int) -> FileResponse:
    return FileResponse(_edit_file(pid, "final.mp4"), media_type="video/mp4")


@app.get("/api/edits/{pid}/thumb")
def edit_thumb(pid: int) -> FileResponse:
    return FileResponse(_edit_file(pid, "thumb.jpg"), media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@app.get("/api/edits/{pid}/download")
def edit_download(pid: int) -> FileResponse:
    p = _edit_or_404(pid)
    safe = "".join(ch if ch.isalnum() or ch in " -_" else "" for ch in p["title"]).strip() or f"edit-{pid}"
    return FileResponse(_edit_file(pid, "final.mp4"), media_type="video/mp4", filename=f"{safe}.mp4")



# --------------------------------------------------------------------------- publishing (Postiz)

_cal_cache: dict[str, Any] = {"at": None, "key": None, "data": []}


def _integration(i: dict[str, Any]) -> dict[str, Any]:
    prov = i.get("identifier") or i.get("providerIdentifier") or i.get("provider") or ""
    return {"id": i.get("id"), "name": i.get("name") or i.get("profile") or prov, "provider": prov,
            "label": postiz.PROVIDERS.get(prov, prov.title()), "picture": i.get("picture"),
            "disabled": bool(i.get("disabled")), "profile": i.get("profile")}


@app.get("/api/publish/status")
def publish_status() -> dict[str, Any]:
    base, key = postiz.settings()
    public_url = os.environ.get("POSTIZ_PUBLIC_URL", "").rstrip("/")
    ui = public_url or base.split("/api/")[0]
    out: dict[str, Any] = {"configured": bool(key), "base_url": base, "ui_url": ui,
                           "public": ui.startswith("https://") and not any(h in ui for h in ("localhost", "127.0.0.1")),
                           "connected": False, "integrations": [], "error": None}
    if not key:
        return out
    try:
        c = postiz.Client()
        out["connected"] = c.connected()
        out["integrations"] = [_integration(i) for i in c.integrations()]
    except postiz.PostizError as e:
        out["error"] = str(e)
    return out


class CaptionsIn(BaseModel):
    edit_id: int
    providers: list[str]
    instruction: str | None = None


@app.post("/api/publish/captions")
def publish_captions(body: CaptionsIn) -> dict[str, Any]:
    if not body.providers:
        raise HTTPException(400, "Pick at least one channel")
    try:
        caps = publishing.write_captions(body.edit_id, body.providers, body.instruction)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except llm.LLMUnavailable as e:
        raise HTTPException(503, f"Claude isn't available right now: {e}")
    return {"captions": [{**c, "flags": publishing.rule_flags(c.get("text", ""), c.get("title", ""))} for c in caps]}


class CheckIn(BaseModel):
    text: str
    title: str | None = None


@app.post("/api/publish/check")
def publish_check(body: CheckIn) -> dict[str, Any]:
    return {"flags": publishing.rule_flags(body.text, body.title or "")}


class PublicationIn(BaseModel):
    edit_id: int
    channels: list[dict[str, Any]]
    when: str
    date: str | None = None
    override_rules: bool = False


def _shape_pub(p: dict[str, Any]) -> dict[str, Any]:
    e = db.row("SELECT id, title, work_dir, render_count FROM edit_projects WHERE id = ?", (p["edit_id"],)) or {}
    chans = json.loads(p.get("channels_json") or "[]")
    return {
        "id": p["id"], "edit_id": p["edit_id"], "idea_id": p["idea_id"], "kind": p["kind"], "status": p["status"],
        "scheduled_at": p["scheduled_at"], "published_at": p["published_at"], "error": p["error"],
        "created_at": p["created_at"], "title": e.get("title"),
        "thumb": f"/api/edits/{p['edit_id']}/thumb" if e and (Path(e["work_dir"]) / "thumb.jpg").exists() else None,
        "channels": [{k: c.get(k) for k in ("integration_id", "provider", "name", "picture", "title", "text")} for c in chans],
        "results": json.loads(p.get("results_json") or "[]"),
        "stale_render": bool(e and p.get("render_count") and e.get("render_count") != p.get("render_count")),
    }


@app.get("/api/publications")
def list_publications(edit_id: int | None = None) -> list[dict[str, Any]]:
    where, params = "1=1", []
    if edit_id:
        where, params = "edit_id = ?", [edit_id]
    return [_shape_pub(p) for p in db.rows(
        f"SELECT * FROM publications WHERE {where} ORDER BY COALESCE(scheduled_at, created_at) DESC", params)]


@app.post("/api/publications", status_code=201)
def create_publication(body: PublicationIn) -> dict[str, Any]:
    for c in body.channels:
        if not c.get("integration_id") or not c.get("provider"):
            raise HTTPException(400, "Each channel needs integration_id and provider")
    try:
        pid = publishing.create(body.edit_id, body.channels, when=body.when, date=body.date,
                                override_rules=body.override_rules)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _shape_pub(db.row("SELECT * FROM publications WHERE id = ?", (pid,)))


@app.post("/api/publications/{pid}/cancel")
def cancel_publication(pid: int) -> dict[str, Any]:
    try:
        publishing.cancel(pid)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except (ValueError, postiz.PostizError) as e:
        raise HTTPException(400, str(e))
    return _shape_pub(db.row("SELECT * FROM publications WHERE id = ?", (pid,)))


@app.post("/api/publications/{pid}/retry", status_code=202)
def retry_publication(pid: int) -> dict[str, Any]:
    p = db.row("SELECT * FROM publications WHERE id = ?", (pid,))
    if not p:
        raise HTTPException(404)
    if p["status"] != "error" or p.get("postiz_ids_json"):
        raise HTTPException(400, "Only failed submissions that never reached Postiz can be retried here")
    db.update("publications", pid, {"status": "submitting", "error": None, "updated_at": db.now()})
    jobs.enqueue("publish_submit", pid, priority=9, max_attempts=2)
    return {"queued": True}


@app.get("/api/publish/slot/{integration_id}")
def publish_slot(integration_id: str) -> dict[str, Any]:
    try:
        return {"date": postiz.Client().find_slot(integration_id)}
    except postiz.PostizError as e:
        raise HTTPException(502, str(e))


@app.post("/api/publish/sync", status_code=202)
def publish_sync_now() -> dict[str, Any]:
    jobs.enqueue("publish_sync", None, priority=9, max_attempts=1)
    return {"queued": True}


@app.get("/api/publish/calendar")
def publish_calendar(start: str, end: str) -> dict[str, Any]:
    """Studio publications plus anything else queued in Postiz (cached 5 min; Postiz allows 30 calls/h)."""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    studio_pubs = [_shape_pub(p) for p in db.rows(
        "SELECT * FROM publications WHERE COALESCE(scheduled_at, created_at) BETWEEN ? AND ? AND status != 'cancelled'",
        (start, end))]
    key = f"{start}|{end}"
    remote, err = [], None
    fresh = _cal_cache["at"] and _dt.now(_tz.utc) - _cal_cache["at"] < _td(minutes=5) and _cal_cache["key"] == key
    if fresh:
        remote = _cal_cache["data"]
    else:
        try:
            remote = postiz.Client().posts(start, end)
            _cal_cache.update(at=_dt.now(_tz.utc), key=key, data=remote)
        except postiz.PostizError as e:
            err = str(e)
    ours = {i for p in db.rows("SELECT postiz_ids_json FROM publications") for i in json.loads(p["postiz_ids_json"] or "[]")}
    others = [{"id": r.get("id"), "publishDate": r.get("publishDate"), "state": r.get("state"),
               "content": (r.get("content") or "")[:160], "url": r.get("releaseURL"),
               "channel": (r.get("integration") or {}).get("name"),
               "provider": (r.get("integration") or {}).get("providerIdentifier")} for r in remote if r.get("id") not in ours]
    return {"studio": studio_pubs, "postiz_other": others, "error": err}



# --------------------------------------------------------------------------- editing styles

def _shape_style(st: dict[str, Any]) -> dict[str, Any]:
    ids = json.loads(st.get("inspired_by") or "[]")
    vids = db.rows(f"SELECT {VIDEO_LIST_COLS} FROM videos v JOIN creators c ON c.id = v.creator_id WHERE v.id IN "
                   f"({','.join('?' for _ in ids) or 'NULL'})", ids) if ids else []
    has = bool(st.get("preview_path")) and Path(st["preview_path"]).exists()
    v = int(Path(st["preview_path"]).stat().st_mtime) if has else 0
    return {"id": st["id"], "name": st["name"], "description": st["description"], "best_for": st["best_for"],
            "source": st["source"], "params": json.loads(st["params_json"]),
            "preview_status": st["preview_status"], "preview_error": st["preview_error"],
            "gif": f"/api/styles/{st['id']}/preview.gif?v={v}" if has else None,
            "mp4": f"/api/styles/{st['id']}/preview.mp4?v={v}" if has else None,
            "inspired": [_shape_video(x) for x in vids], "created_at": st["created_at"]}


@app.get("/api/styles")
def list_styles() -> dict[str, Any]:
    styles.ensure_signature()
    rows = db.rows("SELECT * FROM edit_styles WHERE archived = 0 ORDER BY source = 'builtin' DESC, id")
    return {"items": [_shape_style(r) for r in rows],
            "status": db.get_setting("styles_status", {"state": "idle"}),
            "fingerprinted": db.scalar("SELECT COUNT(*) FROM videos WHERE style_json IS NOT NULL"),
            "last_build": db.get_setting("styles_last_build"),
            "auto": db.get_setting("auto_styles", True),
            "retired": db.rows("SELECT id, name, retire_reason, updated_at FROM edit_styles WHERE archived = 1 AND retired_by = 'evolve' ORDER BY id DESC LIMIT 5"),
            "analyzed": db.scalar("SELECT COUNT(*) FROM videos WHERE status = 'done'"),
            "knobs": {k: v["enum"] for k, v in styles.KNOBS.items()}}


@app.post("/api/styles/build", status_code=202)
def build_styles() -> dict[str, Any]:
    if db.scalar("SELECT COUNT(*) FROM videos WHERE status = 'done'") < 4:
        raise HTTPException(409, "Analyze at least 4 videos first")
    jobs.enqueue("style_build", None, priority=6, max_attempts=50)
    db.set_setting("styles_status", {"state": "queued", "at": db.now()})
    return {"queued": True}


class StylePatch(BaseModel):
    name: str | None = None
    params: dict[str, Any] | None = None
    archived: bool | None = None


@app.patch("/api/styles/{sid}")
def patch_style(sid: int, body: StylePatch) -> dict[str, Any]:
    st = db.row("SELECT * FROM edit_styles WHERE id = ?", (sid,))
    if not st:
        raise HTTPException(404)
    f: dict[str, Any] = {"updated_at": db.now()}
    if body.name:
        f["name"] = body.name.strip()
    if body.archived is not None:
        f["archived"] = int(body.archived)
        f["retired_by"] = "owner" if body.archived else None
    if body.params:
        cur = json.loads(st["params_json"])
        for k, v in body.params.items():
            if k in styles.KNOBS and v in styles.KNOBS[k]["enum"]:
                cur[k] = v
        f["params_json"] = json.dumps(cur)
        f["preview_status"] = "pending"
        jobs.enqueue("style_preview", sid, priority=6, max_attempts=2)
    db.update("edit_styles", sid, f)
    return _shape_style(db.row("SELECT * FROM edit_styles WHERE id = ?", (sid,)))


@app.post("/api/styles/{sid}/preview", status_code=202)
def rerender_style_preview(sid: int) -> dict[str, Any]:
    db.update("edit_styles", sid, {"preview_status": "pending", "preview_error": None})
    jobs.enqueue("style_preview", sid, priority=6, max_attempts=2)
    return {"queued": True}


@app.get("/api/styles/{sid}/preview.{ext}")
def style_preview_file(sid: int, ext: str) -> FileResponse:
    st = db.row("SELECT preview_path FROM edit_styles WHERE id = ?", (sid,))
    if not st or not st["preview_path"] or ext not in ("gif", "mp4"):
        raise HTTPException(404)
    p = Path(st["preview_path"]).with_suffix("." + ext)
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/gif" if ext == "gif" else "video/mp4",
                        headers={"Cache-Control": "max-age=86400"})


class EditStyleIn(BaseModel):
    style_id: int


@app.post("/api/edits/{pid}/style")
def set_edit_style(pid: int, body: EditStyleIn) -> dict[str, Any]:
    p = _edit_or_404(pid)
    opts = {**json.loads(p["options_json"] or "{}"), **editing.style_options(body.style_id)}
    if "style_id" not in opts or opts["style_id"] != body.style_id:
        raise HTTPException(404, "Style not found")
    db.update("edit_projects", pid, {"options_json": json.dumps(opts), "updated_at": db.now()})
    return _shape_edit(_edit_or_404(pid), full=True)


# --------------------------------------------------------------------------- media

@app.get("/api/media/video/{vid}")
def media_video(vid: int) -> FileResponse:
    v = db.row("SELECT media_path FROM videos WHERE id = ?", (vid,))
    if not v or not v["media_path"] or not Path(v["media_path"]).exists():
        raise HTTPException(404, "No local copy")
    return FileResponse(v["media_path"], media_type="video/mp4")


@app.get("/api/media/frame/{vid}/{name}")
def media_frame(vid: int, name: str) -> FileResponse:
    p = (config.MEDIA_DIR / "frames" / str(vid) / name).resolve()
    if p.parent != (config.MEDIA_DIR / "frames" / str(vid)).resolve() or not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


# --------------------------------------------------------------------------- settings / system

class SettingsIn(BaseModel):
    auto_styles: bool | None = None
    new_posts_only: bool | None = None
    backfill_top: int | None = None
    backfill_newest: int | None = None
    brand_brief: str | None = None
    content_pillars: list[str] | None = None
    auto_ideas: bool | None = None


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    model = config.whisper_model()
    return {
        "brand_brief": analyze.brand_brief(),
        "brand_brief_is_default": db.get_setting("brand_brief") is None,
        "cookies": {p: bool(platforms.cookies_file(p)) for p in platforms.PLATFORMS},
        "browser_cookies": {p: {"mode": platforms.browser_mode(p),
                                "found": (((db.get_setting("browser_cookies_found", {}) or {}).get(p) or {}).get("browser") or "").split(":")[0] or None}
                            for p in platforms.PLATFORMS},
        "browsers": list(platforms.BROWSERS),
        "llm": llm.status(),
        "llm_model": config.LLM_MODEL,
        "whisper_model": model.name if model else None,
        "check_interval_min": config.CHECK_INTERVAL_MIN,
        "content_pillars": ideas.pillars(),
        "new_posts_only": pipeline.new_posts_only(),
        "auto_styles": db.get_setting("auto_styles", True),
        "backfill_top": pipeline.backfill_counts()[0],
        "backfill_newest": pipeline.backfill_counts()[1],
        "auto_ideas": db.get_setting("auto_ideas", True),
    }


@app.put("/api/settings")
def put_settings(body: SettingsIn) -> dict[str, Any]:
    if body.brand_brief is not None:
        db.set_setting("brand_brief", body.brand_brief.strip())
    if body.content_pillars is not None:
        db.set_setting("content_pillars", [p.strip() for p in body.content_pillars if p.strip()] or ideas.default_pillars())
    if body.auto_ideas is not None:
        db.set_setting("auto_ideas", body.auto_ideas)
    if body.auto_styles is not None:
        db.set_setting("auto_styles", body.auto_styles)
    if body.new_posts_only is not None:
        db.set_setting("new_posts_only", body.new_posts_only)
    if body.backfill_top is not None:
        db.set_setting("backfill_top", max(0, min(30, body.backfill_top)))
    if body.backfill_newest is not None:
        db.set_setting("backfill_newest", max(0, min(30, body.backfill_newest)))
    return get_settings()


@app.post("/api/settings/cookies/{platform}")
async def upload_cookies(platform: str, file: UploadFile) -> dict[str, Any]:
    if platform not in platforms.PLATFORMS:
        raise HTTPException(400, "Unknown platform")
    raw = (await file.read()).decode("utf-8", errors="replace")
    lines = [l for l in raw.splitlines() if l.strip() and not l.startswith("#")]
    if not lines or not all(len(l.split("\t")) >= 7 for l in lines[:20]):
        raise HTTPException(400, "That doesn't look like a Netscape cookies.txt export")
    dest = config.COOKIES_DIR / f"{platform}.txt"
    dest.write_text(raw if raw.startswith("# Netscape") else "# Netscape HTTP Cookie File\n" + raw)
    dest.chmod(0o600)
    for c in db.rows("SELECT id FROM creators WHERE platform = ? AND active = 1", (platform,)):
        jobs.enqueue("check_creator", c["id"], priority=8)
    return get_settings()


class BrowserCookiesIn(BaseModel):
    browser: str | None = None


@app.put("/api/settings/cookies/{platform}/browser")
def set_browser_cookies(platform: str, body: BrowserCookiesIn) -> dict[str, Any]:
    if platform not in platforms.PLATFORMS:
        raise HTTPException(400, "Unknown platform")
    if body.browser and body.browser not in ("auto", "off") and body.browser.split(":")[0] not in platforms.BROWSERS:
        raise HTTPException(400, "Unknown browser")
    cur = db.get_setting("browser_cookies", {}) or {}
    cur[platform] = body.browser or "auto"
    db.set_setting("browser_cookies", cur)
    platforms.forget_browser(platform)
    if cur[platform] != "off":
        for c in db.rows("SELECT id FROM creators WHERE platform = ? AND active = 1", (platform,)):
            jobs.enqueue("check_creator", c["id"], priority=8)
    return get_settings()


@app.post("/api/settings/cookies/{platform}/test")
def test_platform_login(platform: str) -> dict[str, Any]:
    if platform not in platforms.PLATFORMS:
        raise HTTPException(400, "Unknown platform")
    if platforms.browser_mode(platform) == "off" and not platforms.cookies_file(platform):
        return {"ok": False, "detail": "No login set up for this platform yet"}
    return platforms.test_login(platform)


@app.delete("/api/settings/cookies/{platform}")
def delete_cookies(platform: str) -> dict[str, Any]:
    (config.COOKIES_DIR / f"{platform}.txt").unlink(missing_ok=True)
    return get_settings()


@app.post("/api/llm/ping")
def llm_ping() -> dict[str, Any]:
    status = llm.ping()
    if status.get("ok"):
        # release anything that was parked waiting for Claude
        db.execute("UPDATE jobs SET run_after = ? WHERE kind IN ('analyze_video','generate_concepts','write_script','more_hooks','edit_decide') AND status = 'pending'", (db.now(),))
    return status


@app.get("/api/jobs")
def list_jobs(status: str | None = None, limit: int = Query(100, le=500)) -> dict[str, Any]:
    where, params = "1=1", []
    if status:
        where, params = "status = ?", [status]
    counts = {r["status"]: r["n"] for r in db.rows("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")}
    items = db.rows(f"SELECT id, kind, ref_id, status, priority, attempts, max_attempts, run_after, created_at, started_at, "
                    f"finished_at, error FROM jobs WHERE {where} ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'pending' THEN 1 "
                    f"WHEN 'failed' THEN 2 ELSE 3 END, id DESC LIMIT ?", [*params, limit])
    return {"counts": counts, "items": items}


@app.post("/api/jobs/{jid}/retry")
def retry_job(jid: int) -> dict[str, Any]:
    j = db.row("SELECT * FROM jobs WHERE id = ?", (jid,))
    if not j:
        raise HTTPException(404)
    db.execute("UPDATE jobs SET status='pending', attempts=0, run_after=?, error=NULL WHERE id = ?", (db.now(), jid))
    return {"ok": True}


@app.get("/api/events")
def events(limit: int = Query(50, le=500)) -> list[dict[str, Any]]:
    return db.rows("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))


# --------------------------------------------------------------------------- static SPA

if config.WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=config.WEB_DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    index = config.WEB_DIST / "index.html"
    if not index.exists():
        return JSONResponse({"detail": "Dashboard not built. Run: cd web && npm run build"}, status_code=503)
    candidate = (config.WEB_DIST / path).resolve()
    if path and candidate.is_file() and config.WEB_DIST.resolve() in candidate.parents:
        return FileResponse(candidate)
    return FileResponse(index)
