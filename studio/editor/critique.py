"""Make Claude watch its own frames: contact sheet → scores → fixes (graphics only)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .. import config, llm

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "graphics": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "n": {"type": "integer", "description": "Graphic number as labelled on the sheet"},
                    "readability": {"type": "integer", "minimum": 1, "maximum": 10},
                    "relevance": {"type": "integer", "minimum": 1, "maximum": 10},
                    "brand": {"type": "integer", "minimum": 1, "maximum": 10},
                    "composition": {"type": "integer", "minimum": 1, "maximum": 10,
                                    "description": "Low if it covers the face/eyes, collides with captions, or feels cramped"},
                    "problem": {"type": "string", "description": "The main problem, '' if none"},
                    "fix": {"type": "string", "description": "Concrete change (swap type, shorten title, drop it, move timing), '' if none"},
                },
                "required": ["n", "readability", "relevance", "brand", "composition", "problem", "fix"],
            },
        },
        "overall": {"type": "string", "description": "One line on the whole set: pacing, variety, anything missing"},
    },
    "required": ["graphics", "overall"],
}

SYSTEM = """You are a harsh motion director reviewing graphics for a vertical talking-head video, viewed on a phone.
Brand look: {brand_look}.
Deliberate parts of the system (do NOT mark these down): dashboard lights and traffic lamps sit in a dark
instrument tile/housing and glow in their real colors (red/amber/green) because that's what a real dash looks like;
the word-slam is a single big white card.
Judge each numbered tile honestly (8+ means you'd ship it). Look for: text too small at phone size, graphic
covering the speaker's face or eyes, collisions with the captions, titles that just repeat the caption,
off-brand colours, cramped or clipped cards, a graphic that doesn't match what is being said."""


def contact_sheet(video: Path, graphics: list[dict[str, Any]], out: Path, captions_ass: Path | None = None) -> Path:
    """One phone-sized tile per graphic, at the moment it's fully on screen."""
    tiles = []
    work = out.parent / "review"
    work.mkdir(parents=True, exist_ok=True)
    for k, g in enumerate(graphics):
        dur = g["end"] - g["start"]
        # judge the settled state: multi-phase graphics finish building near the end
        if g.get("type") in ("checklist", "steps", "traffic_light"):
            t_rel = max(0.4, dur - 0.55)
        elif g.get("type") == "bulb_check":
            t_rel = min(0.9, dur * 0.3)
        else:
            t_rel = min(1.5, dur * 0.6)
        t = g["start"] + t_rel
        tile = work / f"t{k:02d}.png"
        bg, fg = work / f"bg{k:02d}.png", work / f"fg{k:02d}.png"
        # grab each still on its own (seeking two inputs at once mis-times the overlay)
        cap = f",ass={captions_ass}:fontsdir={config.ROOT / 'assets' / 'fonts'}" if captions_ass else ""
        subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", str(video), "-vf", f"select='gte(t\\,{t:.3f})'{cap}",
                        "-frames:v", "1", str(bg)], check=True, capture_output=True)
        subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", g["file"], "-vf", f"select='gte(t\\,{t_rel:.3f})'",
                        "-frames:v", "1", str(fg)], check=True, capture_output=True)
        vf = (f"[0:v][1:v]overlay=0:{g['band']['top']}:format=auto,scale=360:-2,"
              f"drawtext=text='{k + 1}':x=14:y=12:fontsize=34:fontcolor=white:box=1:boxcolor=black@0.7:boxborderw=8")
        subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", str(bg), "-i", str(fg), "-filter_complex", vf,
                        "-frames:v", "1", str(tile)], check=True, capture_output=True)
        tiles.append(tile)
    cols = min(4, len(tiles))
    rows = (len(tiles) + cols - 1) // cols
    inputs = sum((["-i", str(p)] for p in tiles), [])
    pad = "".join(f"[{i}:v]" for i in range(len(tiles)))
    blank = cols * rows - len(tiles)
    fc = pad + "".join(f"color=c=black:s=360x640:d=1[b{i}];" for i in range(blank)).rstrip(";")
    if blank:
        fc = "".join(f"color=c=black:s=360x640[b{i}];" for i in range(blank)) + pad + "".join(f"[b{i}]" for i in range(blank))
    fc += f"xstack=inputs={cols * rows}:layout=" + "|".join(f"{(i % cols) * 360}_{(i // cols) * 640}" for i in range(cols * rows))
    subprocess.run([config.FFMPEG, "-y", "-v", "error", *inputs, "-filter_complex", fc, "-frames:v", "1", str(out)],
                   check=True, capture_output=True)
    return out


def review(sheet: Path, graphics: list[dict[str, Any]], transcript: str) -> dict[str, Any]:
    listing = "\n".join(
        f"{k + 1}. {g['type']} at {g['start']:.1f}s — " + json.dumps({x: g.get(x) for x in ("eyebrow", "label", "text", "value", "items") if g.get(x)})
        for k, g in enumerate(graphics))
    prompt = f"""Open and study the contact sheet: {sheet}
Each tile is the finished frame (phone size) while that graphic is on screen.

Graphics:
{listing}

Transcript of the edit (for relevance):
{transcript[:6000]}

Score every graphic and give fixes."""
    from .. import brand
    return llm.ask_json(prompt, REVIEW_SCHEMA, system=SYSTEM.replace("{brand_look}", brand.get()["graphics_style"]), read_dirs=[sheet.parent], model="sonnet", timeout=420)


def failing(rev: dict[str, Any], floor: int = 7, mean: float = 8.0) -> list[dict[str, Any]]:
    """Fails if any score is below `floor` or the average is below `mean`."""
    out = []
    for r in rev.get("graphics") or []:
        sc = [r["readability"], r["relevance"], r["brand"], r["composition"]]
        if min(sc) < floor or sum(sc) / len(sc) < mean:
            out.append(r)
    return out
