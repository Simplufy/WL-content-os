"""Editing styles learned from the analyzed library, merged with the configured brand.

1. fingerprint(video): measured editing metrics (cuts/min, speech rate, pauses, colour) + Claude's read of
   the frames (captions, graphics, zoom habits, energy).
2. synthesize(): Claude clusters the best performers' fingerprints into a few named styles and
   translates each into editor parameters. Brand fonts/colours never change; styles control
   rhythm, layout, density and grade.
3. Each style gets a 7s GIF preview rendered on real footage (studio.editor.engine.render_preview).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np

from . import config, db, jobs, llm

JOB_KINDS = ("style_fingerprint", "style_build", "style_preview")
STYLE_DIR = config.MEDIA_DIR / "styles"

# The editor knobs a style can set (all brand-safe). Values map onto engine options in to_options().
KNOBS: dict[str, dict[str, Any]] = {
    "caption_position": {"enum": ["lower", "middle", "high"], "desc": "lower = lower third; middle = chest; high = just under the chin"},
    "caption_words": {"enum": [1, 2, 3, 4], "desc": "max words on screen at once"},
    "caption_size": {"enum": ["S", "M", "L"], "desc": "caption size"},
    "caption_case": {"enum": ["upper", "sentence"], "desc": "ALL CAPS or Sentence case"},
    "caption_font": {"enum": ["brand", "impact", "bold"], "desc": "brand = Space Grotesk; impact = Anton condensed; bold = Barlow Black"},
    "pace": {"enum": ["relaxed", "normal", "fast", "rapid"], "desc": "how hard pauses are tightened"},
    "zoom_mode": {"enum": ["none", "emphasis", "cuts", "both"], "desc": "punch-ins on key lines, alternate framing on every cut, or both"},
    "zoom_strength": {"enum": ["subtle", "medium", "strong"], "desc": "how far punch-ins go"},
    "graphics_density": {"enum": ["none", "low", "medium", "high"], "desc": "animated graphics: none, ~1/10s, ~1/6s, ~1/4s"},
    "hook_title": {"enum": [True, False], "desc": "branded hook card over the first 3s"},
    "color_grade": {"enum": ["natural", "punchy", "warm", "cool", "moody"], "desc": "colour treatment"},
    "sfx": {"enum": ["off", "subtle", "punchy"], "desc": "UI sound design under graphics"},
}

SIGNATURE = {
    "name": "House Style",
    "description": "The house style: clean brand captions in the lower third, measured pace, punch-ins on key lines, "
                   "branded graphics every few beats.",
    "params": {"caption_position": "lower", "caption_words": 3, "caption_size": "M", "caption_case": "sentence",
               "caption_font": "brand", "pace": "normal", "zoom_mode": "both", "zoom_strength": "medium",
               "graphics_density": "medium", "hook_title": True, "color_grade": "natural", "sfx": "subtle"},
}

PACE = {"relaxed": 0.45, "normal": 0.26, "fast": 0.18, "rapid": 0.12}
ZOOM = {"subtle": (1.04, 1.10), "medium": (1.07, 1.18), "strong": (1.12, 1.30)}
GRAPHICS_EVERY = {"none": 0, "low": 10, "medium": 6, "high": 4}
SFX_DB = {"off": None, "subtle": -24.0, "punchy": -17.0}
FONT_STYLE = {"brand": "brand", "impact": "impact", "bold": "bold"}
GRADES = {
    "natural": "",
    "punchy": "eq=contrast=1.08:saturation=1.20",
    "warm": "colorbalance=rs=0.05:gs=0.01:bs=-0.05:rm=0.03:bm=-0.03,eq=saturation=1.08",
    "cool": "colorbalance=rs=-0.04:bs=0.05:bm=0.03,eq=contrast=1.05:saturation=0.96",
    "moody": "eq=contrast=1.14:saturation=0.82:brightness=-0.025,vignette=angle=PI/5",
}


def to_options(params: dict[str, Any]) -> dict[str, Any]:
    """Style params → engine options (merged over DEFAULT_OPTIONS by the engine)."""
    p = {**SIGNATURE["params"], **(params or {})}
    alt, punch = ZOOM.get(p["zoom_strength"], ZOOM["medium"])
    return {
        "caption_style": FONT_STYLE.get(p["caption_font"], "brand"),
        "caption_position": p["caption_position"],
        "caption_words": int(p["caption_words"]),
        "caption_size": p["caption_size"],
        "caption_case": p["caption_case"],
        "max_pause": PACE.get(p["pace"], 0.26),
        "zooms": p["zoom_mode"] != "none",
        "zoom_mode": p["zoom_mode"],
        "zoom_alt": alt,
        "zoom_punch": punch,
        "graphics": GRAPHICS_EVERY.get(p["graphics_density"], 6) > 0,
        "graphics_every": GRAPHICS_EVERY.get(p["graphics_density"], 6),
        "hook_title": bool(p["hook_title"]),
        "color_grade": p["color_grade"] if p["color_grade"] in GRADES else "natural",
        "sfx": p["sfx"] != "off",
        "sfx_db": SFX_DB.get(p["sfx"]) or -24.0,
    }


# --------------------------------------------------------------------------- measured metrics

def _scene_cuts(path: Path, threshold: float = 0.32) -> int:
    res = subprocess.run([config.FFMPEG, "-hide_banner", "-i", str(path), "-an", "-vf",
                          f"scale=240:-2,select='gt(scene\\,{threshold})',metadata=print:file=-", "-f", "null", "-"],
                         capture_output=True, text=True, timeout=600)
    return len(re.findall(r"pts_time:", res.stdout))


def _colour(frames: list[dict[str, Any]]) -> dict[str, float]:
    import cv2
    sats, vals, cons = [], [], []
    for f in frames:
        img = cv2.imread(f["path"])
        if img is None:
            continue
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        sats.append(float(hsv[..., 1].mean()) / 255)
        vals.append(float(hsv[..., 2].mean()) / 255)
        cons.append(float(hsv[..., 2].std()) / 255)
    if not sats:
        return {}
    return {"saturation": round(mean(sats), 3), "brightness": round(mean(vals), 3), "contrast": round(mean(cons), 3)}


def measure(v: dict[str, Any]) -> dict[str, Any]:
    dur = v.get("duration") or 0
    words = json.loads(v.get("words_json") or "[]")
    frames = json.loads(v.get("frames_json") or "[]")
    out: dict[str, Any] = {"duration_s": round(dur, 1)}
    if v.get("media_path") and Path(v["media_path"]).exists() and dur:
        cuts = _scene_cuts(Path(v["media_path"]))
        out.update(cuts=cuts, cuts_per_min=round(cuts / dur * 60, 1), avg_shot_s=round(dur / (cuts + 1), 2))
    if words:
        speech = words[-1]["end"] - words[0]["start"]
        gaps = [b["start"] - a["end"] for a, b in zip(words, words[1:])]
        out.update(words_per_min=round(len(words) / max(speech, 1) * 60),
                   pause_ratio=round(sum(g for g in gaps if g > 0.3) / max(speech, 1), 3),
                   first_word_at=round(words[0]["start"], 2))
    out.update(_colour(frames))
    return out


# --------------------------------------------------------------------------- Claude fingerprint

FP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "captions": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "present": {"type": "boolean"},
                "position": {"type": "string", "enum": ["lower", "middle", "high", "top", "none"]},
                "words_per_line": {"type": "integer"},
                "case": {"type": "string", "enum": ["upper", "sentence", "mixed", "none"]},
                "size": {"type": "string", "enum": ["S", "M", "L", "none"]},
                "highlight": {"type": "string", "description": "how the active word is emphasised, '' if not"},
                "font_feel": {"type": "string", "description": "e.g. condensed bold, rounded, serif, handwritten"},
            },
            "required": ["present", "position", "words_per_line", "case", "size", "highlight", "font_feel"],
        },
        "graphics": {"type": "string", "enum": ["none", "low", "medium", "high"]},
        "graphic_kinds": {"type": "array", "items": {"type": "string"}},
        "zoom_cuts": {"type": "string", "enum": ["none", "some", "frequent"]},
        "framing": {"type": "string", "enum": ["tight", "medium", "wide", "mixed"]},
        "broll": {"type": "string", "enum": ["none", "some", "heavy"]},
        "energy": {"type": "string", "enum": ["calm", "medium", "high"]},
        "color_look": {"type": "string", "enum": ["natural", "punchy", "warm", "cool", "moody", "bw"]},
        "hook_text_overlay": {"type": "boolean"},
        "signature": {"type": "string", "description": "One sentence: what makes this edit recognisable"},
    },
    "required": ["captions", "graphics", "graphic_kinds", "zoom_cuts", "framing", "broll", "energy", "color_look",
                 "hook_text_overlay", "signature"],
}


def fingerprint(video_id: int, **_: Any) -> dict[str, Any]:
    v = db.row("SELECT v.*, c.handle FROM videos v JOIN creators c ON c.id = v.creator_id WHERE v.id = ?", (video_id,))
    if not v:
        raise LookupError(f"video {video_id} not found")
    metrics = measure(v)
    frames = json.loads(v.get("frames_json") or "[]")
    listing = "\n".join(f"- {f['path']} (t={f['t']}s)" for f in frames)
    prompt = f"""Read the EDITING style of this short-form video by @{v['handle']} (not its content).
Measured: {json.dumps(metrics)}
Frames (open each with Read):
{listing}

Describe captions, graphics, zoom habits, framing, b-roll, energy, colour and the one thing that makes the edit recognisable."""
    fp = llm.ask_json(prompt, FP_SCHEMA, system="You are a senior short-form video editor describing edit styles precisely.",
                      read_dirs=sorted({Path(f["path"]).parent for f in frames}) or None, model="sonnet", timeout=300)
    style = {"metrics": metrics, "look": fp, "at": db.now()}
    db.update("videos", video_id, {"style_json": json.dumps(style)})
    return style


def ensure_fingerprints(limit: int = 24) -> int:
    """Queue fingerprints for the best analyzed videos that don't have one yet."""
    n = 0
    for v in db.rows("SELECT id FROM videos WHERE status = 'done' AND style_json IS NULL AND media_path IS NOT NULL "
                     "ORDER BY score DESC NULLS LAST LIMIT ?", (limit,)):
        if jobs.enqueue("style_fingerprint", v["id"], priority=2, max_attempts=2):
            n += 1
    return n


# --------------------------------------------------------------------------- synthesis

STYLE_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "existing_id": {"type": ["integer", "null"], "description": "id of the current style this continues, null if new"},
        "name": {"type": "string", "description": "2-3 word style name (keep the existing name unless it's wrong)"},
        "description": {"type": "string", "description": "What it feels like and when to use it, 1-2 sentences"},
        "best_for": {"type": "string", "description": "Which kind of video for our brand suits it (pillar/funnel stage)"},
        "inspired_by": {"type": "array", "items": {"type": "integer"}, "description": "video ids from the evidence"},
        "params": {
            "type": "object", "additionalProperties": False,
            "properties": {k: {"enum": v["enum"], "description": v["desc"]} for k, v in KNOBS.items()},
            "required": list(KNOBS),
        },
    },
    "required": ["existing_id", "name", "description", "best_for", "inspired_by", "params"],
}

SYNTH_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "styles": {"type": "array", "items": STYLE_ITEM, "description": "Every style that should be active after this pass"},
        "retire": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                               "properties": {"id": {"type": "integer"}, "reason": {"type": "string"}},
                                               "required": ["id", "reason"]},
                   "description": "Current styles the evidence no longer supports"},
        "changelog": {"type": "string", "description": "One or two sentences on what changed and why"},
    },
    "required": ["styles", "retire", "changelog"],
}
MAX_ACTIVE = 6


def synthesize(_ref: Any = None, **_: Any) -> list[int]:
    """Create or EVOLVE the library styles. Existing styles keep their id/name (edits that use them keep
    working) and are refined only when the evidence says so; new ones appear only for a genuinely new
    pattern; styles the owner removed are never brought back."""
    rows = db.rows("SELECT v.id, v.score, v.outlier, v.views, v.style_json, v.analysis_json, c.handle FROM videos v "
                   "JOIN creators c ON c.id = v.creator_id WHERE v.style_json IS NOT NULL ORDER BY v.score DESC NULLS LAST LIMIT 40")
    if len(rows) < 4:
        raise RuntimeError("Need at least 4 fingerprinted videos — run fingerprints first")
    evidence = []
    for r in rows:
        st = json.loads(r["style_json"])
        a = json.loads(r["analysis_json"] or "{}")
        evidence.append({"video_id": r["id"], "creator": r["handle"], "score": r["score"], "outlier_x": r["outlier"],
                         "format": a.get("format"), "metrics": st["metrics"], "look": st["look"]})
    current = db.rows("SELECT id, name, description, best_for, params_json, inspired_by FROM edit_styles "
                      "WHERE source = 'library' AND archived = 0")
    removed = [r["name"] for r in db.rows("SELECT name FROM edit_styles WHERE source = 'library' AND archived = 1 AND retired_by = 'owner'")]
    cur_view = [{"id": c["id"], "name": c["name"], "description": c["description"], "best_for": c["best_for"],
                 "params": json.loads(c["params_json"]), "inspired_by": json.loads(c["inspired_by"] or "[]")} for c in current]
    knobs = "\n".join(f"- {k}: {v['enum']} — {v['desc']}" for k, v in KNOBS.items())
    prompt = f"""Evidence: editing fingerprints of the best-performing videos in our library (higher score/outlier = performs better):
{json.dumps(evidence, indent=1)[:70000]}

Editor controls available (choose a value for every one):
{knobs}

Our brand is fixed and applied automatically (fonts, accent colour, card style).
Styles control rhythm, layout, density and grade — not brand colours or fonts (except choosing the caption font option).
Our editor works from talking-head speech: no music beds, no b-roll library, no silent skits.

Current library styles (people are already using these — keep them stable):
{json.dumps(cur_view, indent=1) if cur_view else "(none yet — this is the first build)"}

Styles the owner removed (never recreate these or close variants): {removed or "none"}

The house style is separate and stays as is: {json.dumps(SIGNATURE['params'])}

Return the full set of library styles that should be active after this pass (at most {MAX_ACTIVE}):
- Keep each current style that the evidence still supports: same existing_id and name. Only change its
  controls if the newer evidence clearly shows the pattern is different; update inspired_by with the best examples.
- Add a new style (existing_id null) only for a pattern that is genuinely distinct from every current one,
  performs well, and our editor can reproduce.
- Put current styles the evidence no longer supports in 'retire' with a reason.
If there are no current styles, find 3-5 distinct styles."""
    res = llm.ask_json(prompt, SYNTH_SCHEMA, system="You are a creative director who turns what's working on short-form into repeatable edit recipes.",
                       model="opus", timeout=600)
    valid_ids = {r["id"] for r in rows}
    cur_ids = {c["id"]: c for c in current}
    touched: list[int] = []
    added = updated = 0
    for s_ in (res.get("styles") or [])[:MAX_ACTIVE]:
        params = {k: s_["params"].get(k, SIGNATURE["params"][k]) for k in KNOBS}
        inspired = json.dumps([i for i in s_.get("inspired_by") or [] if i in valid_ids])
        eid = s_.get("existing_id")
        if eid in cur_ids:
            old = cur_ids[eid]
            changed = json.loads(old["params_json"]) != params
            db.update("edit_styles", eid, {"name": s_["name"], "description": s_["description"], "best_for": s_.get("best_for"),
                                           "params_json": json.dumps(params), "inspired_by": inspired, "updated_at": db.now(),
                                           **({"preview_status": "pending"} if changed else {})})
            if changed:
                jobs.enqueue("style_preview", eid, priority=3, max_attempts=2)
                updated += 1
            touched.append(eid)
        else:
            sid = db.execute(
                "INSERT INTO edit_styles(name, description, best_for, params_json, inspired_by, source, created_at, updated_at) "
                "VALUES(?,?,?,?,?, 'library', ?, ?)",
                (s_["name"], s_["description"], s_.get("best_for"), json.dumps(params), inspired, db.now(), db.now()))
            jobs.enqueue("style_preview", sid, priority=3, max_attempts=2)
            touched.append(sid)
            added += 1
    retired = 0
    for r in res.get("retire") or []:
        if r.get("id") in cur_ids and r["id"] not in touched:
            db.update("edit_styles", r["id"], {"archived": 1, "retired_by": "evolve", "retire_reason": r.get("reason", "")[:300]})
            retired += 1
    fp_count = db.scalar("SELECT COUNT(*) FROM videos WHERE style_json IS NOT NULL")
    db.set_setting("styles_last_build", {"at": db.now(), "fingerprints": fp_count, "added": added, "updated": updated,
                                         "retired": retired, "changelog": res.get("changelog", "")})
    db.log_event(f"Styles evolved: {added} new, {updated} refined, {retired} retired"
                 + (f" — {res.get('changelog')}" if res.get("changelog") else ""))
    return touched


def maybe_auto_rebuild(min_new: int = 10, every_hours: int = 24) -> bool:
    """Daily: evolve the styles once enough newly fingerprinted videos have built up."""
    if not db.get_setting("auto_styles", True):
        return False
    last = db.get_setting("styles_last_build")
    if not last:
        return False  # the first build is started by the owner from the Styles page
    from datetime import datetime, timedelta, timezone
    if datetime.now(timezone.utc) - datetime.fromisoformat(last["at"]) < timedelta(hours=every_hours):
        return False
    if db.row("SELECT 1 FROM jobs WHERE kind IN ('style_build','style_fingerprint') AND status IN ('pending','running')"):
        return False
    fresh = db.scalar("SELECT COUNT(*) FROM videos WHERE style_json IS NOT NULL") - int(last.get("fingerprints", 0))
    if fresh < min_new:
        return False
    jobs.enqueue("style_build", None, priority=1, max_attempts=50)
    db.set_setting("styles_status", {"state": "queued", "auto": True, "at": db.now()})
    return True


def auto_fingerprint(video_id: int) -> None:
    """Called when a teardown finishes: keep the style evidence current."""
    if db.get_setting("auto_styles", True) and db.get_setting("styles_last_build"):
        jobs.enqueue("style_fingerprint", video_id, priority=0, max_attempts=2)


def ensure_signature() -> int:
    r = db.row("SELECT id FROM edit_styles WHERE source = 'builtin' AND archived = 0")
    if r:
        return r["id"]
    sid = db.execute("INSERT INTO edit_styles(name, description, best_for, params_json, inspired_by, source, created_at, updated_at) "
                     "VALUES(?,?,?,?, '[]', 'builtin', ?, ?)",
                     (SIGNATURE["name"], SIGNATURE["description"], "Anything — the default", json.dumps(SIGNATURE["params"]),
                      db.now(), db.now()))
    jobs.enqueue("style_preview", sid, priority=3, max_attempts=2)
    return sid


def build_all(_ref: Any = None, **_: Any) -> dict[str, Any]:
    """Fingerprint what's missing, then synthesize once they're done (re-queues itself while waiting)."""
    ensure_signature()
    pending = ensure_fingerprints()
    waiting = db.scalar("SELECT COUNT(*) FROM jobs WHERE kind = 'style_fingerprint' AND status IN ('pending','running')")
    if pending or waiting:
        jobs.enqueue("style_build", None, delay_s=60, priority=1, max_attempts=50, dedupe=False)
        db.set_setting("styles_status", {"state": "fingerprinting", "remaining": waiting, "at": db.now()})
        return {"waiting": waiting}
    db.set_setting("styles_status", {"state": "synthesizing", "at": db.now()})
    ids = synthesize()
    db.set_setting("styles_status", {"state": "ready", "styles": len(ids), "at": db.now()})
    return {"styles": ids}


# --------------------------------------------------------------------------- preview

def preview(style_id: int, **_: Any) -> str:
    from .editor import engine

    st = db.row("SELECT * FROM edit_styles WHERE id = ?", (style_id,))
    if not st:
        raise LookupError("style not found")
    sample = db.row("SELECT * FROM edit_projects WHERE status = 'ready' AND decision_json IS NOT NULL ORDER BY id LIMIT 1")
    if not sample:
        raise RuntimeError("Previews need at least one finished edit to use as sample footage")
    STYLE_DIR.mkdir(parents=True, exist_ok=True)
    out = STYLE_DIR / f"style_{style_id}.gif"
    engine.render_preview(Path(sample["work_dir"]), json.loads(sample["probe_json"]), json.loads(sample["decision_json"]),
                          to_options(json.loads(st["params_json"])), out)
    db.update("edit_styles", style_id, {"preview_path": str(out), "preview_status": "ready", "updated_at": db.now(),
                                        "preview_error": None})
    return str(out)


def mark(kind: str, ref_id: int | None, status: str, error: str) -> None:
    if kind == "style_preview" and ref_id is not None:
        db.update("edit_styles", ref_id, {"preview_status": status, "preview_error": error[:500]})
    elif kind == "style_build":
        db.set_setting("styles_status", {"state": status, "error": error[:500], "at": db.now()})


__all__ = ["np"]
