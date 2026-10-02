"""Claude teardown of one competitor video: hook, structure, why it worked."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import brand, db, llm, media

def _default_brief() -> str:
    return brand.get()["brief"]

HOOK_TYPES = [
    "question", "bold_claim", "contrarian", "result_first", "story", "mistake_warning",
    "curiosity_gap", "list", "demonstration", "callout", "stat", "other",
]

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "hook": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "spoken": {"type": "string", "description": "Exact words spoken in roughly the first 3 seconds"},
                "onscreen_text": {"type": "string", "description": "Text overlay visible in the first 3s, '' if none"},
                "visual": {"type": "string", "description": "What the viewer sees in the first 3s"},
                "type": {"type": "string", "enum": HOOK_TYPES},
                "score": {"type": "integer", "minimum": 0, "maximum": 100},
                "subscores": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {k: {"type": "integer", "minimum": 0, "maximum": 10} for k in
                                   ("curiosity", "clarity", "specificity", "pattern_interrupt", "audience_callout")},
                    "required": ["curiosity", "clarity", "specificity", "pattern_interrupt", "audience_callout"],
                },
                "why": {"type": "string", "description": "One or two sentences on why this hook does or doesn't stop the scroll"},
                "template": {"type": "string", "description": "The hook abstracted into a reusable fill-in-the-blank pattern, e.g. 'Stop doing [common habit] if you want [desired result]'"},
            },
            "required": ["spoken", "onscreen_text", "visual", "type", "score", "subscores", "why", "template"],
        },
        "format": {"type": "string", "description": "e.g. talking head, green screen, voiceover b-roll, skit, interview clip"},
        "topic": {"type": "string"},
        "angle": {"type": "string", "description": "The specific take or promise of the video"},
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"start": {"type": "number"}, "label": {"type": "string"}, "summary": {"type": "string"}},
                "required": ["start", "label", "summary"],
            },
        },
        "cta": {"type": "string", "description": "Call to action, '' if none"},
        "retention_tactics": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string", "description": "2-3 sentence summary of the video"},
        "brand_angle": {
            "type": "string",
            "description": "How our brand could make an ORIGINAL video using the same mechanism (not copying lines)",
        },
        "relevance": {"type": "integer", "minimum": 0, "maximum": 10,
                      "description": "How relevant this topic/format is to our audience"},
    },
    "required": ["hook", "format", "topic", "angle", "beats", "cta", "retention_tactics", "summary",
                 "brand_angle", "relevance"],
}

SYSTEM = (
    "You are a short-form content strategist who reverse-engineers why videos perform. "
    "You are precise, you quote exactly, and you never invent metrics. Hook score rubric: "
    "90+ = elite scroll-stopper, 70-89 = strong, 50-69 = average, <50 = weak. Score the hook itself, "
    "not the creator's fame."
)


def brand_brief() -> str:
    return db.get_setting("brand_brief", _default_brief())


def build_prompt(video: dict[str, Any], creator: dict[str, Any], words: list[dict], segments: list[dict],
                 frames: list[dict]) -> str:
    hook_words = media.text_in_window(words, 0, 3.2)
    timed = "\n".join(f"[{s['start']:6.2f}] {s['text']}" for s in segments) or "(no speech detected)"
    metrics = {k: video.get(k) for k in ("views", "likes", "comments", "shares", "saves", "duration", "published_at")}
    frame_lines = "\n".join(
        f"- {f['path']}  (t={f['t']}s{' , HOOK' if f.get('hook') else ''})" for f in frames
    ) or "- (no frames)"
    return f"""Tear down this short-form video.

Creator: @{creator['handle']} on {creator['platform']} ({creator.get('display_name') or ''})
Caption/title: {video.get('title') or ''}
Description: {(video.get('description') or '')[:800]}
Metrics: {json.dumps(metrics)}
Words spoken in first ~3s (auto transcript): "{hook_words}"

Timed transcript:
{timed[:12000]}

Frames (use the Read tool to look at them; the HOOK frames are essential for on-screen text and the visual hook):
{frame_lines}

Our brand, for the brand_angle and relevance fields:
{brand_brief()}

Return the JSON teardown."""


def analyze(video: dict[str, Any], creator: dict[str, Any], words: list[dict], segments: list[dict],
            frames: list[dict]) -> dict[str, Any]:
    prompt = build_prompt(video, creator, words, segments, frames)
    dirs = sorted({str(Path(f["path"]).parent) for f in frames})
    return llm.ask_json(prompt, SCHEMA, system=SYSTEM, read_dirs=[Path(d) for d in dirs] or None)
