"""White-label brand configuration.

Everything client-specific lives here (or in ROOT/brand.json, which overrides these defaults):
product name, logo initials, colours, the default brief, pillars, content rules and CTA language.
The brief and pillars can also be edited per install in Settings (stored in the database).
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from . import config

DEFAULTS: dict[str, Any] = {
    "product_name": "Content Studio",
    "org_name": "Your Brand",
    "logo_initials": "CS",
    "public_url": "",
    "colors": {
        "accent": "#147d78",       # the one highlight colour (key words, eyebrows, step circles)
        "accent_soft": "#31a5a0",
        "accent_glow": "#56c7c1",  # active caption word on footage
        "tint": "#e8f5f4",
        "ink": "#0f172a",
        "ink_deep": "#061616",
        "success": "#1fb573",
        "danger": "#e11d48",
    },
    "fonts": {"display": "Space Grotesk", "body": "Inter", "mono": "Kode Mono"},
    "brief": (
        "DRAFT BRIEF — replace in Settings. Describe: what you sell and to whom; the audience's pains and "
        "desires in their words; the founder/brand story and verified proof points (numbers you're happy to "
        "use publicly); voice and tone; words to avoid; and any hard rules (what can never be said)."
    ),
    "pillars": [
        "Pain points",
        "Myth-busting & hot takes",
        "How-to & frameworks",
        "Numbers & proof",
        "Behind the scenes",
        "Founder story",
    ],
    # (term, why) — flagged in scripts and captions
    "banned_terms": [
        ["limited time", "No fake-urgency framing"],
        ["limited-time", "No fake-urgency framing"],
        ["guaranteed results", "No guarantees"],
        ["% off", "No discounting language"],
        ["crush it", "Hype word — say it plainly"],
    ],
    # dollar amounts near these words are treated as offer pricing and flagged
    "offer_words": ["program", "course", "coaching", "membership", "cohort", "subscription"],
    "cta": {
        "TOFU": "no pitch — at most 'follow' or a question that makes them diagnose themselves",
        "MOFU": "a light pointer to the next step (newsletter, waitlist, free resource)",
        "BOFU": "a direct invitation to buy, book or apply",
    },
    "graphics_style": "white frosted-glass cards, display-font titles with ONE key word in the accent colour, "
                      "tiny uppercase mono eyebrows, check badges, numbered accent circles",
}


def _merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(a[k], v) if isinstance(v, dict) and isinstance(a.get(k), dict) else v
    return out


@lru_cache(maxsize=1)
def get() -> dict[str, Any]:
    path = config.ROOT / "brand.json"
    if path.exists():
        return _merge(DEFAULTS, json.loads(path.read_text()))
    return DEFAULTS


def name() -> str:
    return get()["org_name"]


def public_info() -> dict[str, Any]:
    """What the dashboard needs (no secrets in here)."""
    b = get()
    return {k: b[k] for k in ("product_name", "org_name", "logo_initials", "public_url", "colors", "fonts")}
