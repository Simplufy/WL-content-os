"""Self-check: listen to the cut, find words that shouldn't be there, cut them out.

Whisper hides some stutters even in verbatim mode, so a kept range can still contain
"that that that" or a half restart. We transcribe the assembled audio, align it with the
words we meant to keep, and turn every extra stretch into a source-time exclusion.
"""
from __future__ import annotations

import difflib
import re
from typing import Any

import numpy as np

from .plan import FPS, Segment, frame


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", t.lower().replace("'", ""))


def to_16k(pcm48: np.ndarray) -> np.ndarray:
    n = len(pcm48) // 3 * 3
    return pcm48[:n].reshape(-1, 3).mean(axis=1).astype(np.float32)  # boxcar low-pass + decimate


def _is_fragment(tok: str, nxt: str | None, cut: bool) -> bool:
    """A cut-off or half word: 'head' before 'headlights', 'hi' before 'high', 'ther' before 'the…', or anything
    whisper marked as cut off with a trailing dash."""
    if cut:
        return True
    if nxt and tok and tok != nxt and len(tok) >= 1 and nxt.startswith(tok) and len(nxt) - len(tok) >= 1:
        return True
    return False


def find_leftovers(intended: list[str], got: list[dict[str, Any]], min_words: int = 2) -> list[tuple[float, float]]:
    return [(a, b) for a, b, _ in find_leftovers_detailed(intended, got, min_words)]


def find_leftovers_detailed(intended: list[str], got: list[dict[str, Any]], min_words: int = 2) -> list[tuple[float, float, bool]]:
    """Output-time ranges of extra speech (present in `got`, absent from `intended`).

    Catches whole retakes ("blue or — blue or green"), exact stutters ("but but") and
    half-words ("head— headlights", "hi— high", "ther— the thermometer").
    """
    a = [_norm(w) for w in intended]
    keep_b: list[int] = []
    cut: dict[int, bool] = {}
    for k, w in enumerate(got):
        t = _norm(w["word"])
        if t:
            keep_b.append(k)
            cut[len(keep_b) - 1] = w["word"].rstrip().endswith(("-", "—", "–"))
        elif keep_b and w["word"].strip() in ("-", "—", "–", "..."):
            cut[len(keep_b) - 1] = True        # whisper's stand-alone dash marks the previous word as cut off
    bb = [_norm(got[k]["word"]) for k in keep_b]
    sm = difflib.SequenceMatcher(None, a, bb, autojunk=False)
    spans: list[tuple[int, int]] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "insert":
            lo, hi = j1, j2
        elif op == "replace" and (j2 - j1) > (i2 - i1):
            # retakes: the intended words are usually the LAST attempt, the junk comes first
            lo, hi = j1, j1 + (j2 - j1) - (i2 - i1)
        else:
            continue
        extra = hi - lo
        # a repeated phrase: drop the EARLIER copy — the last attempt is normally the keeper
        if lo - extra >= 0 and bb[lo - extra:lo] == bb[lo:hi]:
            lo, hi = lo - extra, lo
        strong = any(cut.get(k, False) for k in range(lo, hi)) or bb[lo:hi] == bb[hi:hi + extra]
        if lo - extra >= 0 and bb[lo - extra:lo] == bb[lo:hi]:
            strong = True
        if extra >= min_words:
            spans.append((lo, hi - 1, strong))
            continue
        # single extra word: only cut it if it's clearly a stutter or a fragment, never a real word
        prev_tok = bb[lo - 1] if lo > 0 else None
        nxt_tok = bb[hi] if hi < len(bb) else None
        if bb[lo] in (prev_tok, nxt_tok) or _is_fragment(bb[lo], nxt_tok, cut.get(lo, False)):
            spans.append((lo, hi - 1, True))
    ranges = []
    for lo, hi, strong in spans:
        glo, ghi = keep_b[lo], keep_b[hi]
        prev_end = got[glo - 1]["end"] if glo > 0 else 0.0
        nxt = keep_b[hi + 1] if hi + 1 < len(keep_b) else None
        next_start = got[nxt]["start"] if nxt is not None else got[ghi]["end"] + 0.2
        start = max(prev_end, got[glo]["start"] - 0.04)
        end = min(next_start - 0.01, max(got[ghi]["end"], got[min(ghi + 1, len(got) - 1)]["end"] if nxt is not None and ghi + 1 < nxt else got[ghi]["end"]) + 0.04)
        if end - start > 0.06:
            ranges.append((round(start, 3), round(end, 3), strong))
    return ranges


def combine_passes(a: list[tuple[float, float, bool]], b: list[tuple[float, float, bool]]) -> list[tuple[float, float]]:
    """Keep a leftover if both transcription passes found it, or if either found a high-confidence
    one (cut-off with a dash, half-word, exact repeat). One-pass, low-confidence hits are usually
    a mis-hearing at a window edge, and cutting them would remove real words."""
    def overlaps(x, ys):
        return [y for y in ys if y[0] < x[1] and x[0] < y[1]]
    keep: list[tuple[float, float]] = []
    for x in a:
        both = overlaps(x, b)
        if both:
            keep.append((min([x[0]] + [y[0] for y in both]), max([x[1]] + [y[1] for y in both])))
        elif x[2]:
            keep.append((x[0], x[1]))
    for y in b:
        if not overlaps(y, a) and y[2]:
            keep.append((y[0], y[1]))
    return merge_ranges(keep)


def unheard_islands(got: list[dict[str, Any]], env: np.ndarray, hop: float, *, rise_db: float = 14.0,
                    min_len: float = 0.12, max_len: float = 0.6, max_gap: float = 2.0, guard: float = 0.08,
                    also_heard: list[dict[str, Any]] | None = None) -> list[tuple[float, float]]:
    """Voiced audio with no transcribed word on it, cleanly separated from the words around it.

    Whisper often skips a false start ("A therm—") and starts the next word late; the skipped bit is
    still in the audio. Only islands with quiet on both sides and a clear gap to neighbouring words
    are returned, so real word onsets/tails are never clipped.
    """
    if not len(env) or len(got) < 2:
        return []
    floor = float(np.percentile(env, 15))
    voiced = env > floor + rise_db
    quiet = env < floor + 6
    out = []
    for w0, w1 in zip(got, got[1:]):
        g0, g1 = w0["end"] + guard, w1["start"] - guard
        if g1 - g0 < min_len + 0.06 or w1["start"] - w0["end"] > max_gap:
            continue  # a long gap means whisper dropped real words, not a false start
        a, b = int(g0 / hop), int(g1 / hop)
        k = a
        while k < b:
            if voiced[k]:
                j = k
                while j < b and not quiet[j]:
                    j += 1
                i = k
                while i > a and not quiet[i - 1]:
                    i -= 1
                # must be bounded by real quiet on both sides inside the gap
                t0, t1 = i * hop, j * hop
                heard = any(w["start"] < t1 and t0 < w["end"] for w in (also_heard or []))
                if i > a and j < b and min_len <= t1 - t0 <= max_len and not heard:
                    out.append((round(t0 - 0.03, 3), round(t1 + 0.03, 3)))
                k = j + 1
            else:
                k += 1
    return out


def merge_ranges(ranges: list[tuple[float, float]], gap: float = 0.05) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in out]


def out_to_src(ranges: list[tuple[float, float]], segs: list[Segment]) -> list[tuple[float, float]]:
    """Map output-time ranges back onto the source, split across segment boundaries."""
    out = []
    t0 = 0.0
    for s in segs:
        t1 = t0 + s.dur
        for a, b in ranges:
            lo, hi = max(a, t0), min(b, t1)
            if hi - lo > 0.02:
                out.append((s.src_in + lo - t0, s.src_in + hi - t0))
        t0 = t1
    return out


def apply_exclusions(segs: list[Segment], words: list[dict[str, Any]], excl: list[tuple[float, float]],
                     min_frames: int = 3) -> list[Segment]:
    result: list[Segment] = []
    for s in segs:
        pieces = [(s.src_in, s.src_out)]
        for a, b in excl:
            nxt = []
            for p0, p1 in pieces:
                if b <= p0 or a >= p1:
                    nxt.append((p0, p1))
                    continue
                if a > p0:
                    nxt.append((p0, a))
                if b < p1:
                    nxt.append((b, p1))
            pieces = nxt
        for p0, p1 in pieces:
            p0, p1 = frame(p0), frame(p1)
            if (p1 - p0) * FPS < min_frames:
                continue
            ws = [i for i in s.words if p0 <= (words[i]["start"] + words[i]["end"]) / 2 < p1]
            if not ws:
                continue
            result.append(Segment(src_in=p0, src_out=p1, zoom=s.zoom, words=ws, range_id=s.range_id))
    return result
