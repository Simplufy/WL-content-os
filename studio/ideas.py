"""Phase 2: turn the scored library into concepts, hooks and recordable scripts."""
from __future__ import annotations

import json
import re
from typing import Any

from . import analyze, brand, db, jobs, llm

JOB_KINDS = ("generate_concepts", "write_script", "more_hooks")
STAGES = ("idea", "scripted", "filmed", "edited", "scheduled", "posted")
SECTIONS = ["hook", "setup", "value", "proof", "payoff", "cta"]

MECHANISMS = ["reframe", "diagnostic_question", "pov", "contrarian_claim", "before_after", "list", "curiosity_gap", "story"]
FUNNEL = ["TOFU", "MOFU", "BOFU"]

GUARDRAILS = """Hard rules:
- Never copy a competitor's sentences. Borrow the mechanism (structure, hook pattern, format), never the wording.
- Facts stated in the brand brief are true and should be used for credibility (they're the proof).
  Never invent anything beyond them: no made-up revenue, client names, stats, dates or results. Where a
  real specific would make it stronger, write a fill-in placeholder in square brackets, e.g.
  "[real number from your business]" or "[client first name]".
- First-person anecdotes not covered by the brief are claims about the founder's real life ("the day I...",
  "my first hire..."). Never invent one. Frame it as an explicit hypothetical ("Picture an owner who...")
  or leave a placeholder the founder fills with a true story, e.g. "[founder: the moment you realised
  you were the bottleneck]". Numbers inside an explicit hypothetical are fine; numbers presented as real results are not.
- Follow every hard rule in the brand brief.
- Write for the ear: short sentences, plain words, how the audience actually talks. No guru clichés
  ("game-changer", "unlock", "level up", "in today's video")."""


def default_pillars() -> list[str]:
    return list(brand.get()["pillars"])


def pillars() -> list[str]:
    return db.get_setting("content_pillars", default_pillars())


# --------------------------------------------------------------------------- context

def library(limit: int = 25, video_ids: list[int] | None = None) -> list[dict[str, Any]]:
    """Best analyzed videos, compact enough to fit in a prompt."""
    if video_ids:
        marks = ",".join("?" for _ in video_ids)
        rows = db.rows(
            f"SELECT v.id, v.views, v.outlier, v.hook_score, v.score, v.analysis_json, c.handle, c.platform "
            f"FROM videos v JOIN creators c ON c.id = v.creator_id WHERE v.id IN ({marks}) AND v.analysis_json IS NOT NULL",
            video_ids,
        )
    else:
        rows = db.rows(
            "SELECT v.id, v.views, v.outlier, v.hook_score, v.score, v.analysis_json, c.handle, c.platform "
            "FROM videos v JOIN creators c ON c.id = v.creator_id WHERE v.status = 'done' AND v.analysis_json IS NOT NULL "
            "ORDER BY (COALESCE(v.score, 0) + 3 * COALESCE(json_extract(v.analysis_json, '$.relevance'), 5)) DESC LIMIT ?",
            (limit,),
        )
    out = []
    for r in rows:
        a = json.loads(r["analysis_json"])
        h = a.get("hook") or {}
        out.append({
            "video_id": r["id"],
            "creator": f"@{r['handle']} ({r['platform']})",
            "views": r["views"],
            "outlier_x": r["outlier"],
            "overall_score": r["score"],
            "hook": {"spoken": h.get("spoken"), "onscreen": h.get("onscreen_text"), "type": h.get("type"),
                     "score": h.get("score"), "template": h.get("template")},
            "format": a.get("format"),
            "topic": a.get("topic"),
            "angle": a.get("angle"),
            "beats": [b.get("label") for b in a.get("beats") or []],
            "relevance_to_us": a.get("relevance"),
            "our_angle": a.get("brand_angle"),
        })
    return out


def _existing_titles(limit: int = 60) -> list[str]:
    return [r["title"] for r in db.rows("SELECT title FROM ideas ORDER BY id DESC LIMIT ?", (limit,))]


# --------------------------------------------------------------------------- concepts

HOOK_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "text": {"type": "string", "description": "The spoken first line, under ~20 words"},
        "onscreen_text": {"type": "string", "description": "Short text overlay for the first 3s"},
        "type": {"type": "string", "enum": analyze.HOOK_TYPES},
        "source_video_id": {"type": ["integer", "null"], "description": "Library video whose hook pattern this borrows, if any"},
    },
    "required": ["text", "onscreen_text", "type", "source_video_id"],
}

CONCEPTS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "concepts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string", "description": "Working title, 4-10 words"},
                    "pillar": {"type": "string"},
                    "format": {"type": "string", "description": "How it's shot, e.g. talking head at the desk, green screen over a screenshot, walk-and-talk"},
                    "angle": {"type": "string", "description": "The specific promise/take in one or two sentences"},
                    "mechanism": {"type": "string", "enum": MECHANISMS, "description": "The borrowed mechanism"},
                    "funnel": {"type": "string", "enum": FUNNEL, "description": "TOFU relatable/no pitch, MOFU teaches a piece of the framework, BOFU proof/results/invite to apply"},
                    "why": {"type": "string", "description": "Why this should perform, citing the library evidence (video ids, outlier numbers)"},
                    "inspired_by": {"type": "array", "items": {"type": "integer"}, "description": "Library video ids this draws on"},
                    "hooks": {"type": "array", "items": HOOK_ITEM, "description": "4 hook variations"},
                },
                "required": ["title", "pillar", "format", "angle", "mechanism", "funnel", "why", "inspired_by", "hooks"],
            },
        }
    },
    "required": ["concepts"],
}

SYSTEM = (
    "You are the head of content for the brand described in the brief. You turn evidence about what is "
    "working on short-form video into original concepts the team can film "
    "this week. You think in mechanisms (why a hook stops the scroll, why a structure holds "
    "attention) and you are allergic to generic advice.\n\n" + GUARDRAILS
)


def create_batch(count: int = 5, focus: str | None = None, video_ids: list[int] | None = None, auto: bool = False) -> int:
    count = max(1, min(int(count), 12))
    bid = db.execute(
        "INSERT INTO idea_batches(focus, count, source_video_ids, auto, status, created_at) VALUES(?,?,?,?, 'pending', ?)",
        ((focus or "").strip() or None, count, json.dumps(video_ids or []), int(auto), db.now()),
    )
    jobs.enqueue("generate_concepts", bid, priority=4 if auto else 9, max_attempts=2)
    return bid


def run_batch(batch_id: int, **_: Any) -> list[int]:
    b = db.row("SELECT * FROM idea_batches WHERE id = ?", (batch_id,))
    if not b:
        raise LookupError(f"batch {batch_id} not found")
    vids = json.loads(b["source_video_ids"] or "[]")
    lib = library(video_ids=vids) if vids else library()
    if not lib:
        raise RuntimeError("No analyzed videos yet. Add creators and wait for teardowns first.")
    db.update("idea_batches", batch_id, {"status": "pending", "error": None})

    focus_line = f"\nFocus for this batch (from the owner): {b['focus']}\n" if b["focus"] else ""
    source_line = ("\nThese specific videos were picked as inspiration. Every concept must draw on at least one of them.\n"
                   if vids else "")
    prompt = f"""Our brand:
{analyze.brand_brief()}

Content pillars: {", ".join(pillars())}
{focus_line}{source_line}
Evidence: competitor/peer videos we've torn down, best first (outlier_x = views vs that creator's median):
{json.dumps(lib, indent=1)}

Ideas we already have (don't repeat them):
{json.dumps(_existing_titles())}

Create {b['count']} distinct, original short-form video concepts for us. For each: identify the mechanism
in the evidence, find our audience's version of that tension, map it to a pillar, write it in the brand's voice
with specifics from our world, and tag the funnel stage. Spread across pillars, mechanisms and funnel stages
(mostly TOFU, some MOFU, the odd BOFU) unless a focus was given. Use "Growth & acquisition" sparingly.
Each needs 4 hook variations in different hook types; at least one should adapt the template of a
high-outlier hook from the evidence (set source_video_id)."""
    result = llm.ask_json(prompt, CONCEPTS_SCHEMA, system=SYSTEM, timeout=420)

    valid_ids = {v["video_id"] for v in lib}
    created = []
    for c in result.get("concepts", [])[: b["count"]]:
        hooks = c.get("hooks") or []
        for h in hooks:
            if h.get("source_video_id") not in valid_ids:
                h["source_video_id"] = None
        created.append(db.execute(
            "INSERT INTO ideas(batch_id, title, pillar, format, angle, mechanism, funnel, why, hooks_json, chosen_hook, "
            "inspired_by, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (batch_id, c["title"], c.get("pillar"), c.get("format"), c.get("angle"), c.get("mechanism"),
             c.get("funnel"), c.get("why"),
             json.dumps(hooks), 0 if hooks else None,
             json.dumps([i for i in c.get("inspired_by") or [] if i in valid_ids]), db.now(), db.now()),
        ))
    db.update("idea_batches", batch_id, {"status": "ready", "finished_at": db.now()})
    db.log_event(f"{len(created)} new idea(s) generated" + (f" — focus: {b['focus']}" if b["focus"] else ""))
    return created


def maybe_auto_batch(min_new: int = 3, every_hours: int = 24) -> int | None:
    """Once a day, if at least `min_new` teardowns finished since the last auto batch, make 5 ideas."""
    if not db.get_setting("auto_ideas", True):
        return None
    last = db.row("SELECT created_at FROM idea_batches WHERE auto = 1 ORDER BY id DESC LIMIT 1")
    since = last["created_at"] if last else "1970-01-01"
    if last and db.scalar("SELECT ? > datetime('now', ?)", (since.replace("T", " ")[:19], f"-{every_hours} hours")):
        return None
    if db.row("SELECT 1 FROM idea_batches WHERE status IN ('pending','waiting_llm') AND auto = 1"):
        return None
    fresh = db.scalar("SELECT COUNT(*) FROM videos WHERE status = 'done' AND processed_at > ?", (since,))
    if fresh < min_new:
        return None
    return create_batch(5, auto=True)


# --------------------------------------------------------------------------- scripts

SCRIPT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "hook": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "spoken": {"type": "string"},
                "onscreen_text": {"type": "string"},
                "visual": {"type": "string", "description": "What the camera sees in the first 2 seconds"},
            },
            "required": ["spoken", "onscreen_text", "visual"],
        },
        "lines": {
            "type": "array",
            "description": "The full spoken script after the hook, one sentence or beat per line. Each line is recorded as its own take.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "section": {"type": "string", "enum": SECTIONS[1:]},
                    "text": {"type": "string", "description": "Exactly what is said. One breath, ideally under 20 words."},
                    "onscreen_text": {"type": "string", "description": "Optional emphasis overlay, '' if none"},
                    "broll": {"type": "string", "description": "Optional cutaway/b-roll to film, '' if none"},
                },
                "required": ["section", "text", "onscreen_text", "broll"],
            },
        },
        "caption": {"type": "string", "description": "Post caption, 1-3 short lines, ends with the CTA"},
        "hashtags": {"type": "array", "items": {"type": "string"}, "description": "Up to 6, no # sign"},
        "duration_s": {"type": "integer", "description": "Estimated spoken length in seconds"},
        "filming_notes": {"type": "string", "description": "Setup, location, props, energy"},
    },
    "required": ["hook", "lines", "caption", "hashtags", "duration_s", "filming_notes"],
}


def _idea(idea_id: int) -> dict[str, Any]:
    i = db.row("SELECT * FROM ideas WHERE id = ?", (idea_id,))
    if not i:
        raise LookupError(f"idea {idea_id} not found")
    return i


def request_script(idea_id: int, instruction: str | None = None) -> None:
    db.update("ideas", idea_id, {"script_status": "pending", "script_error": None, "updated_at": db.now()})
    jobs.enqueue("write_script", idea_id, {"instruction": (instruction or "").strip() or None}, priority=9, max_attempts=2)


def write_script(idea_id: int, instruction: str | None = None, **_: Any) -> dict[str, Any]:
    i = _idea(idea_id)
    hooks = json.loads(i["hooks_json"] or "[]")
    chosen = hooks[i["chosen_hook"]] if hooks and i["chosen_hook"] is not None and i["chosen_hook"] < len(hooks) else None
    inspired = library(video_ids=json.loads(i["inspired_by"] or "[]"))
    cta = brand.get()["cta"]
    previous = json.loads(i["script_json"]) if i["script_json"] else None
    db.update("ideas", idea_id, {"script_status": "pending", "script_error": None})

    rewrite = ""
    if previous:
        rewrite = f"\nCurrent draft (revise it rather than starting over unless told otherwise):\n{json.dumps(previous, indent=1)}\n"
    prompt = f"""Our brand:
{analyze.brand_brief()}

Concept: {i['title']}
Pillar: {i['pillar']}
Mechanism: {i.get('mechanism') or "(pick the best fit)"}
Funnel stage: {i.get('funnel') or "TOFU"}
Format: {i['format']}
Angle: {i['angle']}
Why it should work: {i['why']}
Chosen hook: {json.dumps(chosen) if chosen else "(none chosen — write the strongest one)"}
Other hook options: {json.dumps(hooks)}

Structures of the videos that inspired it (borrow the shape, not the words):
{json.dumps(inspired, indent=1)}
{rewrite}
Owner's instruction for this draft: {instruction or "none — write the best version"}

Write a recordable short-form script. Unless the instruction says otherwise: 30-60 seconds spoken
(about 75-150 words after the hook), open with the chosen hook (tighten it if needed), deliver one clear
payoff, then a CTA that fits the funnel stage: TOFU = {cta["TOFU"]}; MOFU = {cta["MOFU"]}; BOFU = {cta["BOFU"]}.
Every line must be sayable in one breath; the editor will match each line to its best take."""
    result = llm.ask_json(prompt, SCRIPT_SCHEMA, system=SYSTEM, timeout=420)
    for n, line in enumerate(result.get("lines") or [], start=1):
        line["id"] = f"L{n}"
    result["word_count"] = len(" ".join([result["hook"]["spoken"], *(l["text"] for l in result["lines"])]).split())
    originality = check_originality(result)
    db.update("ideas", idea_id, {
        "script_json": json.dumps(result),
        "script_status": "ready",
        "script_error": None,
        "script_version": (i["script_version"] or 0) + 1,
        "originality_json": json.dumps(originality),
        "stage": "scripted" if i["stage"] == "idea" else i["stage"],
        "updated_at": db.now(),
    })
    return result


def save_script_edit(idea_id: int, script: dict[str, Any]) -> dict[str, Any]:
    """Owner edited the script by hand."""
    for n, line in enumerate(script.get("lines") or [], start=1):
        line["id"] = f"L{n}"
    script["word_count"] = len(" ".join([script["hook"]["spoken"], *(l["text"] for l in script["lines"])]).split())
    originality = check_originality(script)
    db.update("ideas", idea_id, {"script_json": json.dumps(script), "originality_json": json.dumps(originality),
                                 "updated_at": db.now()})
    return originality


# --------------------------------------------------------------------------- hooks

MORE_HOOKS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"hooks": {"type": "array", "items": HOOK_ITEM, "description": "8 hook options"}},
    "required": ["hooks"],
}


def request_hooks(idea_id: int, instruction: str | None = None) -> None:
    db.update("ideas", idea_id, {"hooks_status": "pending", "updated_at": db.now()})
    jobs.enqueue("more_hooks", idea_id, {"instruction": (instruction or "").strip() or None}, priority=9, max_attempts=2)


def more_hooks(idea_id: int, instruction: str | None = None, **_: Any) -> list[dict[str, Any]]:
    i = _idea(idea_id)
    existing = json.loads(i["hooks_json"] or "[]")
    lib = library(limit=30)
    templates = [{"video_id": v["video_id"], "type": v["hook"]["type"], "template": v["hook"]["template"],
                  "outlier_x": v["outlier_x"], "hook_score": v["hook"]["score"]} for v in lib if v["hook"]["template"]]
    prompt = f"""Our brand:
{analyze.brand_brief()}

Concept: {i['title']}
Angle: {i['angle']}

Hook templates proven in our niche (with performance):
{json.dumps(templates, indent=1)}

Hooks we already have for this concept (don't repeat):
{json.dumps([h['text'] for h in existing])}

Instruction: {instruction or "none"}

Write 8 new hook options for this concept. Mix hook types. At least half should adapt a high-performing
template above (set source_video_id). Each spoken hook must work in under 3 seconds."""
    result = llm.ask_json(prompt, MORE_HOOKS_SCHEMA, system=SYSTEM, timeout=300)
    valid = {t["video_id"] for t in templates}
    new = result.get("hooks") or []
    for h in new:
        if h.get("source_video_id") not in valid:
            h["source_video_id"] = None
    db.update("ideas", idea_id, {"hooks_json": json.dumps(existing + new), "hooks_status": "ready",
                                 "chosen_hook": i["chosen_hook"] if i["chosen_hook"] is not None else 0,
                                 "updated_at": db.now()})
    return new


def mark(kind: str, ref_id: int | None, status: str, error: str) -> None:
    if ref_id is None:
        return
    if kind == "generate_concepts":
        db.update("idea_batches", ref_id, {"status": status, "error": error[:1000]})
    elif kind == "write_script":
        db.update("ideas", ref_id, {"script_status": status, "script_error": error[:1000]})
    elif kind == "more_hooks":
        db.update("ideas", ref_id, {"hooks_status": "failed" if status == "failed" else status})


# --------------------------------------------------------------------------- brand rules



def banned_terms() -> list[tuple[str, str]]:
    return [tuple(x) for x in db.get_setting("banned_terms", brand.get()["banned_terms"])]


def _price_near_offer() -> re.Pattern:
    words = "|".join(re.escape(w) for w in brand.get()["offer_words"]) or "program"
    amount = r"\$\s?\d[\d,]*(?:\.\d+)?\s?(?:k|K)?(?!\s?(?:M|million|B|billion)\b|[\d.,])"
    return re.compile(rf"({amount}[^.?!]{{0,60}}\b({words}|month)\b)|(\b({words})\b[^.?!]{{0,60}}{amount})", re.I)


def check_brand_rules(script: dict[str, Any]) -> list[dict[str, str]]:
    parts = [("hook", script.get("hook", {}).get("spoken", "")), ("hook on-screen", script.get("hook", {}).get("onscreen_text", ""))]
    for l in script.get("lines") or []:
        parts.append((l.get("id", "?"), " ".join([l.get("text", ""), l.get("onscreen_text", "")])))
    parts.append(("caption", script.get("caption", "")))
    flags = []
    for where, text in parts:
        low = (text or "").lower()
        for term, why in banned_terms():
            if term.startswith("re:"):  # regex entry, e.g. "re:\\bVAs?\\b" (case-sensitive)
                m = re.search(term[3:], text or "")
                if m:
                    flags.append({"where": where, "term": m.group(0), "why": why})
            elif term.lower() in low:
                flags.append({"where": where, "term": term, "why": why})
        if _price_near_offer().search(text or ""):
            flags.append({"where": where, "term": "price", "why": "No offer pricing in content"})
    return flags


# --------------------------------------------------------------------------- originality

_WORD = re.compile(r"[a-z0-9']+")
SHINGLE = 6


def _words(text: str) -> list[str]:
    return _WORD.findall((text or "").lower().replace("’", "'"))


def _shingles(words: list[str], n: int = SHINGLE) -> set[tuple[str, ...]]:
    return {tuple(words[k:k + n]) for k in range(len(words) - n + 1)}


def check_originality(script: dict[str, Any]) -> dict[str, Any]:
    """Flag any 6+ word run that also appears in a competitor transcript."""
    lines = [("hook", script.get("hook", {}).get("spoken", ""))] + [(l.get("id", "?"), l.get("text", "")) for l in script.get("lines") or []]
    corpus = db.rows("SELECT v.id, v.transcript_text, c.handle FROM videos v JOIN creators c ON c.id = v.creator_id "
                     "WHERE v.transcript_text IS NOT NULL AND v.transcript_text != ''")
    index: dict[tuple[str, ...], tuple[int, str]] = {}
    for v in corpus:
        for sh in _shingles(_words(v["transcript_text"])):
            index.setdefault(sh, (v["id"], v["handle"]))
    matches = []
    for line_id, text in lines:
        words = _words(text)
        hits = [(k, index[tuple(words[k:k + SHINGLE])]) for k in range(len(words) - SHINGLE + 1) if tuple(words[k:k + SHINGLE]) in index]
        if hits:
            k, (vid, handle) = hits[0]
            matches.append({"line": line_id, "phrase": " ".join(words[k:k + SHINGLE + len(hits) - 1][:14]),
                            "video_id": vid, "handle": handle})
    rule_flags = check_brand_rules(script)
    return {"ok": not matches, "matches": matches, "checked_against": len(corpus),
            "rules_ok": not rule_flags, "rule_flags": rule_flags}
