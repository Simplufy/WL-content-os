"""ASS overlay: word-by-word captions, colour-coded callouts, hook title."""
from __future__ import annotations

import re
from typing import Any

W, H = 1080, 1920

# Override tags take &HBBGGRR& (no alpha byte — libass misreads 8 digits there)
def _c(hex_rgb: str) -> str:
    r, g, b = hex_rgb[0:2], hex_rgb[2:4], hex_rgb[4:6]
    return f"&H{b}{g}{r}&".upper()


PALETTE = {
    "red": ("E53935", "FFFFFF"),
    "amber": ("FFB300", "111111"),
    "green": ("22C55E", "0B1F12"),
    "blue": ("2F80ED", "FFFFFF"),
    "white": ("FFFFFF", "111111"),
}

STYLES = {
    "brand": {"font": "Space Grotesk", "size": 86, "highlight": "56C7C1", "upper": False, "bold": True, "spacing": -2},
    "bold": {"font": "Barlow Black", "size": 92, "highlight": "56C7C1", "upper": True},
    "clean": {"font": "Barlow SemiBold", "size": 78, "highlight": "56C7C1", "upper": False},
    "impact": {"font": "Anton", "size": 104, "highlight": "56C7C1", "upper": True},
}


def _ts(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _clean(word: str, upper: bool) -> str:
    w = re.sub(r"[^\w'%$?!&.-]", "", word).strip(".,")
    w = w.replace("{", "").replace("}", "")
    return w.upper() if upper else w


def group_words(words: list[dict[str, Any]], max_words: int = 3, max_chars: int = 18, max_gap: float = 0.35) -> list[list[dict]]:
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        text_len = sum(len(x["word"]) + 1 for x in cur) + len(w["word"])
        if cur and (len(cur) >= max_words or text_len > max_chars or w["start"] - cur[-1]["end"] > max_gap):
            groups.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.?!,]$", w["word"]):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


CAPTION_MARGIN = {"lower": 560, "middle": 690, "high": 860}
CAPTION_SCALE = {"S": 0.82, "M": 1.0, "L": 1.2}
MAX_CHARS = {1: 14, 2: 16, 3: 18, 4: 24}


def _brand_glow() -> str | None:
    from .. import brand
    c = (brand.get().get("colors") or {}).get("accent_glow")
    return c.lstrip("#").upper() if c else None


def caption_top(position: str = "lower", size: str = "M", style: str = "brand", chin_y: int | None = None) -> int:
    """Approximate top edge of the caption block (graphics placed below the chin must end above it)."""
    st = STYLES.get(style, STYLES["brand"])
    fs = st["size"] * CAPTION_SCALE.get(size, 1.0)
    mv = CAPTION_MARGIN.get(position, 560)
    if chin_y:
        mv = max(300, min(mv, int(1920 - (chin_y + 40) - fs * 1.3)))
    return int(1920 - mv - fs * 1.35)


def build_ass(
    words: list[dict[str, Any]],
    *,
    callouts: list[dict[str, Any]] | None = None,
    hook_text: str | None = None,
    style: str = "brand",
    captions: bool = True,
    duration: float | None = None,
    position: str = "lower",
    max_words: int = 3,
    size: str = "M",
    case: str | None = None,
    chin_y: int | None = None,
) -> str:
    st = dict(STYLES.get(style, STYLES["brand"]))
    st["highlight"] = _brand_glow() or st["highlight"]
    st["size"] = int(round(st["size"] * CAPTION_SCALE.get(size, 1.0)))
    if case in ("upper", "sentence"):
        st["upper"] = case == "upper"
    margin_v = CAPTION_MARGIN.get(position, 560)
    if chin_y:  # never over the mouth: the caption block's top stays below the chin
        margin_v = min(margin_v, int(1920 - (chin_y + 40) - st["size"] * 1.3))
        margin_v = max(margin_v, 300)
    hl = _c(st["highlight"])
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{st['font']},{st['size']},&H00FFFFFF,&H00FFFFFF,&H00161606,&H70000000,{-1 if st.get('bold') else 0},0,0,0,100,100,{st.get('spacing', 1)},0,1,6,3,2,60,60,{margin_v},1
Style: Callout,Barlow Black,76,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,1,0,3,20,0,8,80,80,210,1
Style: Hook,Barlow Black,84,&H00111111,&H00111111,&H00FFFFFF,&H00FFFFFF,0,0,0,0,100,100,0,0,3,24,0,8,150,150,200,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev: list[str] = []
    if captions:
        mw = max(1, min(4, int(max_words)))
        groups = group_words(words, max_words=mw, max_chars=MAX_CHARS.get(mw, 18))
        for gi, g in enumerate(groups):
            g_end = g[-1]["end"]
            nxt = groups[gi + 1][0]["start"] if gi + 1 < len(groups) else g_end + 0.4
            g_end = min(max(g_end, g[0]["start"] + 0.25), nxt)
            for wi, w in enumerate(g):
                s = w["start"] if wi else g[0]["start"]
                e = g[wi + 1]["start"] if wi + 1 < len(g) else g_end
                if e <= s:
                    continue
                parts = []
                for k, x in enumerate(g):
                    txt = _clean(x["word"], st["upper"])
                    if not txt:
                        continue
                    if k == wi:
                        parts.append("{\\1c" + hl + "\\fscx100\\fscy100\\t(0,70,\\fscx112\\fscy112)\\t(70,150,\\fscx100\\fscy100)}"
                                     + txt + "{\\1c&HFFFFFF&}")
                    else:
                        parts.append(txt)
                if parts:
                    pop = "{\\fscx92\\fscy92\\t(0,80,\\fscx100\\fscy100)}" if wi == 0 else ""
                    ev.append(f"Dialogue: 1,{_ts(s)},{_ts(e)},Cap,,0,0,0,,{pop}{' '.join(parts)}")

    for c in callouts or []:
        bg, fg = PALETTE.get(c.get("color", "white"), PALETTE["white"])
        s, e = c["start"], max(c["end"], c["start"] + 1.2)
        text = c["text"].upper().replace("{", "").replace("}", "")
        tags = (f"{{\\fad(90,140)\\1c{_c(fg)}\\3c{_c(bg)}\\4c{_c(bg)}\\fscx30\\fscy30"
                f"\\t(0,130,\\fscx108\\fscy108)\\t(130,210,\\fscx100\\fscy100)}}")
        ev.append(f"Dialogue: 2,{_ts(s)},{_ts(e)},Callout,,0,0,0,,{tags}{text}")

    if hook_text:
        end = min(3.2, duration or 3.2)
        text = hook_text.upper().replace("{", "").replace("}", "")
        ev.append(f"Dialogue: 3,{_ts(0)},{_ts(end)},Hook,,0,0,0,,{{\\q0\\fad(0,220)\\fscx96\\fscy96\\t(0,160,\\fscx100\\fscy100)}}{text}")
    return head + "\n".join(ev) + "\n"


def place_callouts(callouts: list[dict[str, Any]], out_words: list[dict[str, Any]], min_gap: float = 0.4) -> list[dict[str, Any]]:
    """Map word-number callouts onto the edited timeline; drop overlaps."""
    by_i = {w["i"]: w for w in out_words}
    placed: list[dict[str, Any]] = []
    for c in sorted(callouts, key=lambda c: by_i.get(c["at_word"], {}).get("start", 1e9)):
        a = by_i.get(c["at_word"])
        b = by_i.get(c.get("until_word"), a)
        if not a:
            continue
        start = a["start"]
        end = max((b or a)["end"] + 0.35, start + 1.4)
        if placed and start < placed[-1]["end"] + min_gap:
            placed[-1]["end"] = max(placed[-1]["start"] + 1.0, start - min_gap)
            if start < placed[-1]["end"] + 0.05:
                continue
        placed.append({**c, "start": round(start, 3), "end": round(end, 3)})
    return placed
