"""Claude as the editor: pick the keepers from a word-numbered raw transcript."""
from __future__ import annotations

import difflib
import json
import re
from typing import Any

from .. import analyze, llm

CALLOUT_COLORS = ["red", "amber", "green", "blue", "white"]

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "keep": {
            "type": "array",
            "description": "Word ranges to keep, in playback order. Inclusive word numbers.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"from": {"type": "integer"}, "to": {"type": "integer"}},
                "required": ["from", "to"],
            },
        },
        "cuts": {
            "type": "array",
            "description": "Notable removed stretches and why (false start, retake, rambling, off-topic, dead air)",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"from": {"type": "integer"}, "to": {"type": "integer"}, "reason": {"type": "string"}},
                "required": ["from", "to", "reason"],
            },
        },
        "final_text": {"type": "string", "description": "The words of the kept ranges joined, as a check that it reads cleanly"},
        "hook_text": {"type": "string", "description": "Short on-screen title for the first ~3s (max 7 words)"},
        "emphasis": {
            "type": "array",
            "description": "4-10 moments for a punch-in zoom (key claims, numbers, turns). Each is an exact 2-5 word quote from final_text where the zoom lands.",
            "items": {"type": "string"},
        },
        "callouts": {
            "type": "array",
            "description": "On-screen text graphics that reinforce what's said (labels, numbers, lists). Max one every ~4s.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "anchor": {"type": "string", "description": "Exact 2-6 word quote from final_text; the graphic appears as these words are spoken"},
                    "text": {"type": "string", "description": "1-4 words, punchy"},
                    "color": {"type": "string", "enum": CALLOUT_COLORS},
                },
                "required": ["anchor", "text", "color"],
            },
        },
        "title": {"type": "string", "description": "Working title for this video"},
        "notes": {"type": "string", "description": "Anything the owner should know (e.g. a point that was never delivered cleanly)"},
    },
    "required": ["keep", "cuts", "final_text", "hook_text", "emphasis", "callouts", "title", "notes"],
}

SYSTEM = """You are a senior short-form video editor. You cut raw talking-head footage into tight,
fluent, high-retention vertical videos. You work from a word-numbered transcript with timing.

How you edit:
- Speakers restart sentences constantly, often with no pause ("the color tells you how much to be the
  The color tells you..."). Keep ONE clean, complete delivery of each thought, usually the last full
  attempt, and drop every false start, stutter, repeated phrase and abandoned clause.
- Cuts must land between words that form a grammatical, natural sentence when joined. Read your
  final_text out loud in your head; it must sound like one fluent take.
- Drop dead openings (setup, "okay", looking for the button), filler, and tangents that don't serve
  the payoff. Keep the speaker's voice and phrasing; never create meaning they didn't say.
- Open on the strongest hook the footage contains. You may move one sentence earlier to make a cold
  open if it clearly improves retention; otherwise keep the original order.
- Aim for the shortest version that still fully delivers the point (usually 35-75s for 3-5 minutes
  of raw), unless told otherwise.
- Callouts are short on-screen labels that reinforce the exact words being said (a color, a number,
  a step). Colors carry meaning: red = danger/stop, amber = caution, green/blue = OK/info, white = neutral.
- Keep ranges use word numbers. Emphasis and callout anchors are exact quotes from your final_text."""


def format_words(words: list[dict[str, Any]], pause: float = 0.45) -> str:
    lines, cur, prev_end = [], [], 0.0
    for i, w in enumerate(words):
        gap = w["start"] - prev_end
        if cur and gap > pause:
            lines.append(cur)
            cur = []
        if not cur:
            cur.append(f"[{w['start']:.1f}s{f' after {gap:.1f}s pause' if gap > pause else ''}]")
        cur.append(f"{i}:{w['word']}")
        prev_end = w["end"]
    if cur:
        lines.append(cur)
    return "\n".join(" ".join(l) for l in lines)


def decide(words: list[dict[str, Any]], *, script: dict[str, Any] | None = None, instruction: str | None = None,
           target_s: int | None = None, model: str | None = None) -> dict[str, Any]:
    script_part = ""
    if script:
        lines = [script.get("hook", {}).get("spoken", "")] + [l.get("text", "") for l in script.get("lines") or []]
        script_part = ("\nThe speaker was reading this script. Keep the best take of each line, in script order, "
                       "and drop anything not in it unless it's clearly better:\n" + "\n".join(f"- {l}" for l in lines if l) + "\n")
    prompt = f"""Brand context (for judging what matters):
{analyze.brand_brief()}
{script_part}
Owner's instruction: {instruction or "none"}
Target length: {f"about {target_s}s" if target_s else "as short as it can be while fully landing the point"}

Raw transcript, word-numbered ("n:word"), with timestamps and pauses:
{format_words(words)}

Return the edit decision."""
    result = llm.ask_json(prompt, SCHEMA, system=SYSTEM, model=model or "opus", timeout=600)
    return sanitize(result, len(words), words)


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9']", "", t.lower())


def find_anchor(words: list[dict[str, Any]], kept_order: list[int], phrase: str) -> tuple[int, int] | None:
    """Locate a quoted phrase in the kept word sequence → (first, last) word index. Fuzzy-tolerant."""
    target = [_norm(t) for t in phrase.split() if _norm(t)]
    if not target:
        return None
    seq = [_norm(words[i]["word"]) for i in kept_order]
    n = len(target)
    best, best_k = 0.0, -1
    for k in range(0, max(1, len(seq) - n + 1)):
        window = seq[k:k + n]
        score = difflib.SequenceMatcher(None, " ".join(window), " ".join(target)).ratio()
        if score > best:
            best, best_k = score, k
            if score == 1.0:
                break
    if best < 0.75 or best_k < 0:
        return None
    return kept_order[best_k], kept_order[min(best_k + n - 1, len(kept_order) - 1)]


def sanitize(d: dict[str, Any], n_words: int, words: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    keep = []
    for r in d.get("keep") or []:
        a, b = int(r["from"]), int(r["to"])
        a, b = max(0, min(a, b)), min(n_words - 1, max(a, b))
        if a <= b:
            keep.append({"from": a, "to": b})
    d["keep"] = keep
    kept_order = [i for r in keep for i in range(r["from"], r["to"] + 1)]
    if words is None:
        d["emphasis_words"], d["callout_words"] = [], []
        return d
    emph = []
    for ph in d.get("emphasis") or []:
        hit = find_anchor(words, kept_order, ph) if isinstance(ph, str) else None
        if hit:
            emph.append(hit[0])
    d["emphasis_words"] = emph
    callouts = []
    for c in d.get("callouts") or []:
        hit = find_anchor(words, kept_order, c.get("anchor", ""))
        if hit and c.get("text"):
            callouts.append({**c, "at_word": hit[0], "until_word": hit[1]})
    d["callout_words"] = callouts
    return d


def json_dump(d: dict[str, Any]) -> str:
    return json.dumps(d, indent=1)


# --------------------------------------------------------------------------- motion graphics plan

GRAPHICS_TYPES = {
    "dash_light": "A dashboard warning light lighting up in its real colour inside a dark tile, with a title (props: icon, color, eyebrow, label, sub)",
    "bulb_check": "A dark instrument cluster of 4 lights that all come on, then go out one by one (props: items=exactly 4 dash icon names, eyebrow, label)",
    "traffic_light": "Traffic light with an eyebrow; lights the 'active' lamp or runs red→amber→green ('sequence') with one title per lamp. Anchor it on the line where the colours are explained, not the intro (props: active, eyebrow, items=exactly 3 titles)",
    "stat": "One big number that counts up (props: value e.g. '$4M' or '15', eyebrow, sub)",
    "before_after": "Before (struck through) → after number (props: before, value, eyebrow, sub)",
    "icon": "Icon in a mint circle + title for a concrete thing being mentioned (props: icon, eyebrow, label, sub)",
    "checklist": "2-4 short items ticking in with mint check badges (props: eyebrow, items)",
    "steps": "2-4 numbered steps with teal numbered circles, for a sequence/process (props: eyebrow, items)",
    "word_slam": "One huge 1-3 word statement, no card; the single biggest moment only, at most once (props: text)",
}
REQUIRED = {
    "dash_light": ("label",), "bulb_check": (), "traffic_light": (), "stat": ("value",),
    "before_after": ("before", "value"), "icon": ("label",), "checklist": ("items",), "steps": ("items",),
    "word_slam": ("text",),
}

GRAPHICS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "hook": {
            "type": "object",
            "additionalProperties": False,
            "description": "The title card shown over the first ~3 seconds",
            "properties": {
                "eyebrow": {"type": "string", "description": "2-5 word audience callout or context, e.g. 'For first-time founders'"},
                "text": {"type": "string", "description": "3-8 word hook; wrap the 1-2 key words in *asterisks* for the teal highlight"},
            },
            "required": ["eyebrow", "text"],
        },
        "graphics": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "anchor": {"type": "string", "description": "Exact 2-6 word quote from the transcript; the graphic lands as these words are said"},
                    "type": {"type": "string", "enum": list(GRAPHICS_TYPES)},
                    "duration_s": {"type": "number", "description": "How long it stays, usually 2-4s"},
                    "icon": {"type": "string", "description": "Dash icon name or icon library name, '' if unused"},
                    "color": {"type": "string", "enum": CALLOUT_COLORS, "description": "Only meaningful for dash lights (the light's real colour)"},
                    "eyebrow": {"type": "string", "description": "Tiny uppercase context line, 2-5 words, '' if none"},
                    "label": {"type": "string", "description": "Title, 1-5 words; wrap the key word in *asterisks*; '' if unused"},
                    "sub": {"type": "string", "description": "Optional short line under the title, '' if none"},
                    "value": {"type": "string", "description": "For stat/before_after, '' otherwise"},
                    "before": {"type": "string", "description": "For before_after, '' otherwise"},
                    "active": {"type": "string", "description": "For traffic_light: red|amber|green|sequence, '' otherwise"},
                    "items": {"type": "array", "items": {"type": "string"}, "description": "checklist/steps items (may use *highlight*), bulb_check icon names, traffic_light titles"},
                    "text": {"type": "string", "description": "For word_slam, '' otherwise; may use *highlight*"},
                },
                "required": ["anchor", "type", "duration_s", "icon", "color", "eyebrow", "label", "sub", "value", "before",
                             "active", "items", "text"],
            },
        },
    },
    "required": ["hook", "graphics"],
}

GRAPHICS_SYSTEM = """You are the motion designer for this brand's short-form videos. You add animated
graphics that SHOW what the speaker is talking about: the actual object, number, symbol or step. Captions
already exist, so a graphic must add a visual, never repeat the sentence.

Brand look (already built into the components, you only pick and fill them): {brand_look}. One accent colour. Calm, confident.

Rules:
- About one graphic every 5-8 seconds, never overlapping, each 2-4s. Leave breathing room.
- Variety: don't use the same type twice in a row unless it's a deliberate series (e.g. red lights).
- Titles are 1-5 words. Highlight exactly one key word or short phrase per title with *asterisks*.
- Eyebrows add context the title doesn't (who it's for, which step, which colour), 2-5 words.
- Never invent facts or numbers that aren't said. Never show prices for the program.
- The first ~3.3s are the hook card; place graphics after that."""


def graphics_system() -> str:
    from .. import brand
    return GRAPHICS_SYSTEM.replace("{brand_look}", brand.get()["graphics_style"])


def plan_graphics(kept_text: str, *, icon_names: list[str], dash_icons: list[str], context: str = "",
                  feedback: str | None = None, model: str | None = None, every_s: float = 6) -> dict[str, Any]:
    catalog = "\n".join(f"- {k}: {v}" for k, v in GRAPHICS_TYPES.items())
    fb = f"\nA reviewer looked at the last version and asked for these changes; apply them:\n{feedback}\n" if feedback else ""
    prompt = f"""Components available:
{catalog}

Dashboard icon names: {", ".join(dash_icons)}
Icon library (use the exact name): {", ".join(icon_names)}

{context}
{fb}
Final edited transcript with timestamps (seconds into the finished video):
{kept_text}

Pacing for this edit's style: about one graphic every {every_s:.0f} seconds (never overlapping).

Plan the hook card and the motion graphics."""
    res = llm.ask_json(prompt, GRAPHICS_SCHEMA, system=graphics_system(), model=model or "sonnet", timeout=420)
    return {"hook": res.get("hook") or {}, "graphics": [g for g in (validate_graphic(g, dash_icons, icon_names)
                                                                for g in res.get("graphics") or []) if g]}


def validate_graphic(g: dict[str, Any], dash_icons: list[str], icon_names: list[str]) -> dict[str, Any] | None:
    t = g.get("type")
    if t not in GRAPHICS_TYPES:
        return None
    for field in REQUIRED[t]:
        if not g.get(field):
            return None
    if t == "dash_light" and g.get("icon") not in dash_icons:
        g["icon"] = ""
    if t == "bulb_check":
        g["items"] = [i for i in g.get("items") or [] if i in dash_icons]
    if t == "icon" and g.get("icon") not in dash_icons and g.get("icon") not in icon_names:
        return None
    if t in ("checklist", "steps"):
        g["items"] = [i for i in g.get("items") or [] if i][:4]
        if len(g["items"]) < 2:
            return None
    g["duration_s"] = float(min(5.0, max(1.6, g.get("duration_s") or 2.8)))
    return g


FIX_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "fixes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "n": {"type": "integer"},
                    "drop": {"type": "boolean", "description": "true to remove this graphic entirely"},
                    "graphic": GRAPHICS_SCHEMA["properties"]["graphics"]["items"],
                },
                "required": ["n", "drop", "graphic"],
            },
        }
    },
    "required": ["fixes"],
}


def fix_graphics(failing: list[dict[str, Any]], transcript: str, *, icon_names: list[str], dash_icons: list[str]) -> list[dict[str, Any]]:
    """Revise only the graphics a reviewer failed. Each: {n, spec, problem, fix} -> {n, drop, graphic}."""
    catalog = "\n".join(f"- {k}: {v}" for k, v in GRAPHICS_TYPES.items())
    body = "\n\n".join(f"Graphic {f['n']} (currently at {f['spec'].get('start', 0):.1f}s):\n{json.dumps({k: v for k, v in f['spec'].items() if k not in ('file', 'band', 'start', 'end')})}\n"
                        f"Reviewer: {f['problem']} → {f['fix']}" for f in failing)
    prompt = f"""Components available:
{catalog}

Dashboard icon names: {", ".join(dash_icons)}
Icon library: {", ".join(icon_names)}

Transcript with timestamps:
{transcript[:6000]}

These graphics failed review. For each, either return a corrected graphic (keep the same anchor unless the
reviewer said the timing is wrong; you may change the type) or drop it if it doesn't earn its place.

{body}"""
    res = llm.ask_json(prompt, FIX_SCHEMA, system=graphics_system(), model="sonnet", timeout=420)
    out = []
    for f in res.get("fixes") or []:
        if not f.get("drop"):
            g = validate_graphic(f.get("graphic") or {}, dash_icons, icon_names)
            if g is None:
                f["drop"] = True
            else:
                f["graphic"] = g
        out.append(f)
    return out
