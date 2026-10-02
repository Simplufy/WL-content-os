"""Phase 4: send finished edits to Postiz (schedule / draft / now) and track what happened."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import analyze, brand, db, ideas, jobs, llm, postiz

JOB_KINDS = ("publish_submit", "publish_sync")
OPEN_STATES = ("scheduled", "publishing")
SYNC_EVERY_MIN = 10  # Postiz allows 30 list calls/hour

PLATFORM_GUIDE = {
    "tiktok": "1-2 punchy lines that extend the hook, then 3-5 relevant hashtags. Max ~300 chars.",
    "instagram": "Reel caption: hook line, 2-3 short lines of value, the CTA, blank line, 5-8 hashtags.",
    "instagram-standalone": "Reel caption: hook line, 2-3 short lines of value, the CTA, blank line, 5-8 hashtags.",
    "youtube": "Shorts: 'title' is a curiosity-driven title under 70 chars; 'text' is a 2-3 line description ending with #shorts.",
    "facebook": "2-4 conversational lines, CTA, at most 3 hashtags.",
    "linkedin": "Peer-to-peer post: 4-8 short lines with line breaks, one insight, the CTA, at most 3 hashtags.",
    "linkedin-page": "Peer-to-peer post: 4-8 short lines with line breaks, one insight, the CTA, at most 3 hashtags.",
    "x": "One tweet under 260 characters, no hashtags or at most one.",
    "threads": "Under 450 characters, conversational, no hashtag spam.",
}

CAPTIONS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "captions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "provider": {"type": "string"},
                    "title": {"type": "string", "description": "Title where the platform uses one (YouTube, TikTok), else ''"},
                    "text": {"type": "string"},
                },
                "required": ["provider", "title", "text"],
            },
        }
    },
    "required": ["captions"],
}


def _edit(edit_id: int) -> dict[str, Any]:
    e = db.row("SELECT * FROM edit_projects WHERE id = ?", (edit_id,))
    if not e:
        raise LookupError(f"edit {edit_id} not found")
    return e


def edit_transcript(e: dict[str, Any]) -> str:
    res = json.loads(e.get("result_json") or "null")
    wf = Path(e["work_dir"]) / "words.json"
    if not res or not wf.exists():
        return ""
    words = json.loads(wf.read_text())
    return " ".join(words[i]["word"] for i in res.get("kept_words") or [] if i < len(words))


def write_captions(edit_id: int, providers: list[str], instruction: str | None = None) -> list[dict[str, str]]:
    e = _edit(edit_id)
    idea = db.row("SELECT * FROM ideas WHERE id = ?", (e["idea_id"],)) if e.get("idea_id") else None
    funnel = (idea or {}).get("funnel") or "TOFU"
    script_caption = ""
    if idea and idea.get("script_json"):
        script_caption = (json.loads(idea["script_json"]) or {}).get("caption", "")
    uniq = sorted(set(providers))
    cta = brand.get()["cta"]
    prompt = f"""Brand brief:
{analyze.brand_brief()}

Video title: {e['title']}
Funnel stage: {funnel} (TOFU = {cta["TOFU"]}; MOFU = {cta["MOFU"]}; BOFU = {cta["BOFU"]})
{f"Caption from the script: {script_caption}" if script_caption else ""}
Owner's instruction: {instruction or "none"}

What is said in the finished video:
{edit_transcript(e)[:5000]}

Write the post copy for each platform: {", ".join(uniq)}
Platform guidance:
{chr(10).join(f"- {p}: {PLATFORM_GUIDE.get(p, 'Short caption with the CTA.')}" for p in uniq)}

Follow every hard rule in the brief. Don't invent facts."""
    res = llm.ask_json(prompt, CAPTIONS_SCHEMA, system=ideas.SYSTEM, model="sonnet", timeout=300)
    out = {c["provider"]: c for c in res.get("captions") or [] if c.get("provider") in uniq}
    return [out.get(p, {"provider": p, "title": e["title"], "text": ""}) for p in uniq]


def rule_flags(text: str, title: str = "") -> list[dict[str, str]]:
    return ideas.check_brand_rules({"hook": {"spoken": title}, "lines": [], "caption": text})


# --------------------------------------------------------------------------- create / submit

def create(edit_id: int, channels: list[dict[str, Any]], *, when: str, date: str | None,
           override_rules: bool = False) -> int:
    """channels: [{integration_id, provider, name, picture, text, title, settings}]"""
    e = _edit(edit_id)
    if e["status"] != "ready" or not e.get("result_json"):
        raise ValueError("This edit hasn't finished rendering yet")
    if when not in ("schedule", "draft", "now"):
        raise ValueError("when must be schedule, draft or now")
    if when == "schedule":
        if not date:
            raise ValueError("Pick a date and time")
        dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("Date needs a timezone")
        if dt < datetime.now(timezone.utc) + timedelta(minutes=2):
            raise ValueError("That time is in the past")
        date = dt.astimezone(timezone.utc).isoformat()
    if not channels:
        raise ValueError("Pick at least one channel")
    flags = []
    for c in channels:
        if not (c.get("text") or "").strip():
            raise ValueError(f"Caption for {c.get('name') or c['provider']} is empty")
        for f in rule_flags(c["text"], c.get("title", "")):
            flags.append({**f, "where": f"{c.get('name') or c['provider']}"})
        if c["provider"] == "youtube" and not (2 <= len((c.get("settings") or {}).get("title") or c.get("title") or "") <= 100):
            raise ValueError("YouTube needs a title between 2 and 100 characters")
    if flags and not override_rules:
        raise ValueError("Brand rule check failed: " + "; ".join(f"{f['where']}: “{f['term']}” — {f['why']}" for f in flags))

    pid = db.execute(
        "INSERT INTO publications(edit_id, idea_id, kind, scheduled_at, channels_json, status, render_count, created_at, updated_at) "
        "VALUES(?,?,?,?,?, 'submitting', ?, ?, ?)",
        (edit_id, e.get("idea_id"), when, date, json.dumps(channels), e.get("render_count"), db.now(), db.now()),
    )
    jobs.enqueue("publish_submit", pid, priority=9, max_attempts=2)
    db.log_event(f"Publishing “{e['title']}” to {len(channels)} channel(s) ({when})")
    return pid


def _set(pid: int, **f: Any) -> None:
    f["updated_at"] = db.now()
    db.update("publications", pid, f)


def build_body(kind: str, date: str | None, channels: list[dict[str, Any]], media: dict[str, Any]) -> dict[str, Any]:
    img = [{"id": media["id"], "path": media["path"]}]
    posts = []
    for c in channels:
        st = {**postiz.default_settings(c["provider"], title=c.get("title", "")), **(c.get("settings") or {})}
        if c["provider"] in ("youtube", "tiktok") and c.get("title"):
            st["title"] = c["title"][:100 if c["provider"] == "youtube" else 90]
        posts.append({"integration": {"id": c["integration_id"]}, "value": [{"content": c["text"], "image": img}],
                      "settings": st})
    return {"type": kind, "date": date or datetime.now(timezone.utc).isoformat(), "shortLink": False, "tags": [],
            "posts": posts}


def submit(pid: int, **_: Any) -> None:
    p = db.row("SELECT * FROM publications WHERE id = ?", (pid,))
    if not p:
        raise LookupError(f"publication {pid} not found")
    e = _edit(p["edit_id"])
    video = Path(e["work_dir"]) / "final.mp4"
    if not video.exists():
        raise FileNotFoundError("Rendered video is missing")
    client = postiz.Client()
    media = json.loads(p["media_json"]) if p.get("media_json") else None
    if not media:
        media = client.upload(video)
        _set(pid, media_json=json.dumps(media))
    channels = json.loads(p["channels_json"])
    resp = client.create_post(build_body(p["kind"], p["scheduled_at"], channels, media))
    ids = postiz.extract_post_ids(resp)
    status = {"schedule": "scheduled", "now": "publishing", "draft": "draft"}[p["kind"]]
    _set(pid, status=status, postiz_ids_json=json.dumps(ids), postiz_response_json=json.dumps(resp)[:20000], error=None)
    if e.get("idea_id") and p["kind"] != "draft":
        db.execute("UPDATE ideas SET stage = 'scheduled', updated_at = ? WHERE id = ? AND stage != 'posted'",
                   (db.now(), e["idea_id"]))
    db.log_event(f"{'Scheduled' if p['kind'] == 'schedule' else 'Sent' if p['kind'] == 'now' else 'Saved draft'}: "
                 f"{e['title']} → {', '.join(c.get('name') or c['provider'] for c in channels)}")


# --------------------------------------------------------------------------- status sync

def due_for_sync() -> bool:
    pubs = db.rows(f"SELECT id, scheduled_at, last_sync_at, kind FROM publications WHERE status IN {OPEN_STATES}")
    now = datetime.now(timezone.utc)
    for p in pubs:
        last = datetime.fromisoformat(p["last_sync_at"]) if p.get("last_sync_at") else None
        sched = datetime.fromisoformat(p["scheduled_at"]) if p.get("scheduled_at") else now
        # nothing to learn before the scheduled time
        if sched - now > timedelta(minutes=5):
            continue
        if not last or now - last > timedelta(minutes=SYNC_EVERY_MIN):
            return True
    return False


def sync(_ref: Any = None, **_: Any) -> dict[str, int]:
    pubs = db.rows(f"SELECT * FROM publications WHERE status IN {OPEN_STATES}")
    if not pubs:
        return {"checked": 0}
    times = [datetime.fromisoformat(p["scheduled_at"]) for p in pubs if p.get("scheduled_at")] or [datetime.now(timezone.utc)]
    start = (min(times) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    end = (max(max(times), datetime.now(timezone.utc)) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    remote = {r["id"]: r for r in postiz.Client().posts(start, end)}
    changed = 0
    for p in pubs:
        ids = json.loads(p.get("postiz_ids_json") or "[]")
        found = [remote[i] for i in ids if i in remote]
        fields: dict[str, Any] = {"last_sync_at": db.now()}
        if found:
            states = {r.get("state") for r in found}
            urls = [r.get("releaseURL") for r in found if r.get("releaseURL")]
            fields["results_json"] = json.dumps([{"id": r["id"], "state": r.get("state"), "url": r.get("releaseURL"),
                                                  "provider": (r.get("integration") or {}).get("providerIdentifier"),
                                                  "name": (r.get("integration") or {}).get("name")} for r in found])
            if "ERROR" in states:
                fields.update(status="error", error="Postiz reported an error on at least one channel — open Postiz for details")
            elif states == {"PUBLISHED"}:
                fields.update(status="published", published_at=db.now())
                if p.get("idea_id"):
                    db.execute("UPDATE ideas SET stage = 'posted', updated_at = ? WHERE id = ?", (db.now(), p["idea_id"]))
                db.log_event(f"Published: {len(urls)} link(s) live")
        if fields.get("status") or fields.get("results_json") != p.get("results_json"):
            changed += 1
        _set(p["id"], **fields)
    return {"checked": len(pubs), "changed": changed}


def cancel(pid: int) -> None:
    p = db.row("SELECT * FROM publications WHERE id = ?", (pid,))
    if not p:
        raise LookupError("publication not found")
    if p["status"] == "published":
        raise ValueError("Already published — delete it on the platform itself")
    client = postiz.Client()
    for i in json.loads(p.get("postiz_ids_json") or "[]")[:1]:  # deleting one removes the whole group
        client.delete_post(i)
    _set(pid, status="cancelled")
    db.log_event("Cancelled a scheduled post")


def mark(kind: str, ref_id: int | None, status: str, error: str) -> None:
    if kind == "publish_submit" and ref_id is not None:
        _set(ref_id, status="error" if status == "failed" else status, error=error[:1500])
