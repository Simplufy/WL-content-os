"""Turn word-level edit decisions into a frame-accurate cut timeline.

Pure functions (numpy only) so they're easy to test without media.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

FPS = 30
ENV_HOP = 0.01  # energy envelope resolution (s)


@dataclass
class Segment:
    src_in: float            # seconds in source (frame aligned)
    src_out: float
    zoom: float = 1.0
    words: list[int] = field(default_factory=list)  # word indices covered
    range_id: int = 0        # which kept range this came from

    @property
    def dur(self) -> float:
        return self.src_out - self.src_in

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def frame(t: float) -> float:
    return round(t * FPS) / FPS


def energy_envelope(samples: np.ndarray, sr: int) -> np.ndarray:
    """RMS in dBFS per 10 ms hop."""
    hop = int(sr * ENV_HOP)
    n = len(samples) // hop
    if n == 0:
        return np.zeros(0)
    x = samples[: n * hop].reshape(n, hop).astype(np.float64)
    rms = np.sqrt((x ** 2).mean(axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-9)


def noise_floor(env: np.ndarray) -> float:
    return float(np.percentile(env, 15)) if len(env) else -60.0


def quietest(env: np.ndarray, lo: float, hi: float, prefer: float) -> float:
    """Time of the quietest 30 ms window in [lo, hi]; ties broken toward `prefer`."""
    a, b = max(0, int(lo / ENV_HOP)), min(len(env), int(hi / ENV_HOP))
    if b - a < 3:
        return prefer
    win = np.convolve(env[a:b], np.ones(3) / 3, mode="same")
    floor = win.min()
    cand = np.where(win <= floor + 1.5)[0]
    best = cand[np.argmin(np.abs((a + cand) * ENV_HOP - prefer))]
    return (a + best) * ENV_HOP


def speech_onset(env: np.ndarray, lo: float, hi: float, thresh: float) -> float | None:
    """First time in [lo, hi] where energy rises above `thresh`."""
    a, b = max(0, int(lo / ENV_HOP)), min(len(env), int(hi / ENV_HOP))
    above = np.where(env[a:b] > thresh)[0]
    return (a + above[0]) * ENV_HOP if len(above) else None


def speech_offset(env: np.ndarray, lo: float, hi: float, thresh: float) -> float | None:
    a, b = max(0, int(lo / ENV_HOP)), min(len(env), int(hi / ENV_HOP))
    above = np.where(env[a:b] > thresh)[0]
    return (a + above[-1] + 1) * ENV_HOP if len(above) else None


def build_timeline(
    words: list[dict[str, Any]],
    keep: list[tuple[int, int]],
    env: np.ndarray,
    *,
    max_pause: float = 0.32,
    lead: float = 0.07,
    tail: float = 0.12,
    duration: float | None = None,
) -> list[Segment]:
    """keep: ordered (first_word, last_word) inclusive ranges in playback order.

    - each range is split wherever the speaker paused longer than `max_pause`
      (the pause is shortened, not removed, so delivery still breathes)
    - edges are padded, pulled in to real speech onset/offset, then snapped to
      the quietest nearby moment so cuts don't clip syllables
    - never extends into a neighbouring word that was cut
    """
    if not words:
        return []
    floor = noise_floor(env)
    thresh = floor + 10
    end_limit = duration if duration else words[-1]["end"] + 1
    segs: list[Segment] = []
    for rid, (a, b) in enumerate(keep):
        a, b = max(0, a), min(len(words) - 1, b)
        if a > b:
            continue
        # split into runs on long pauses
        runs: list[list[int]] = [[a]]
        for i in range(a + 1, b + 1):
            if words[i]["start"] - words[i - 1]["end"] > max_pause:
                runs.append([i])
            else:
                runs[-1].append(i)
        for run in runs:
            i0, i1 = run[0], run[-1]
            prev_end = words[i0 - 1]["end"] if i0 > 0 else 0.0
            next_start = words[i1 + 1]["start"] if i1 + 1 < len(words) else end_limit
            w0, w1 = words[i0], words[i1]

            # start: real onset near the word start, padded, never before the previous word
            onset = speech_onset(env, max(prev_end, w0["start"] - 0.25), w0["end"], thresh)
            start = (onset if onset is not None else w0["start"]) - lead
            start = max(start, prev_end + 0.02, 0.0)
            start = quietest(env, max(prev_end + 0.02, start - 0.05), start + 0.02, prefer=start)

            # end: real offset near the word end, padded, never into the next word
            offset = speech_offset(env, w1["start"], min(next_start, w1["end"] + 0.35), thresh)
            end = max(offset if offset is not None else w1["end"], w1["end"] - 0.05) + tail
            end = min(end, next_start - 0.02, end_limit)
            end = quietest(env, end - 0.03, min(end + 0.06, next_start - 0.02, end_limit), prefer=end)

            s, e = frame(start), frame(end)
            if e - s < 2 / FPS:
                continue
            segs.append(Segment(src_in=s, src_out=e, words=list(range(i0, i1 + 1)), range_id=rid))
    return segs


def assign_zoom(segs: list[Segment], emphasis_words: set[int], base: float = 1.0, alt: float = 1.07,
                punch: float = 1.18, mode: str = "both") -> None:
    """Talking-head rhythm. mode: 'cuts' alternates framing on every cut, 'emphasis' punches in on key
    lines, 'both' does both, 'none' keeps one framing."""
    for k, s in enumerate(segs):
        hit = bool(emphasis_words.intersection(s.words))
        if mode == "none":
            s.zoom = base
        elif mode == "emphasis":
            s.zoom = punch if hit else base
        elif mode == "cuts":
            s.zoom = alt if k % 2 else base
        else:
            s.zoom = punch if hit else (alt if k % 2 else base)


def output_word_times(words: list[dict[str, Any]], segs: list[Segment]) -> list[dict[str, Any]]:
    """Map each kept word onto the edited timeline (for captions/graphics)."""
    out = []
    t = 0.0
    for s in segs:
        for i in s.words:
            w = words[i]
            ws = max(w["start"], s.src_in)
            we = min(w["end"], s.src_out)
            out.append({"i": i, "word": w["word"], "start": round(t + ws - s.src_in, 3),
                        "end": round(t + max(we, ws + 0.05) - s.src_in, 3)})
        t += s.dur
    return out


def total_duration(segs: list[Segment]) -> float:
    return round(sum(s.dur for s in segs), 3)


def compress_silences(segs: list[Segment], env: np.ndarray, words: list[dict[str, Any]], *,
                      min_gap: float = 0.25, keep: float = 0.08) -> list[Segment]:
    """Split segments at real silences (measured from audio, not word timings).

    Whisper often stretches a word's timestamp across the pause after it, so a
    'continuous' run can still hold half a second of nothing. Any quiet stretch
    longer than `min_gap` is cut down to `2 * keep`.
    """
    if not len(env):
        return segs
    thresh = noise_floor(env) + 10
    out: list[Segment] = []
    for s in segs:
        a, b = int(s.src_in / ENV_HOP), min(len(env), int(s.src_out / ENV_HOP))
        quiet = env[a:b] < thresh
        cuts: list[tuple[float, float]] = []
        k = 0
        while k < len(quiet):
            if quiet[k]:
                j = k
                while j < len(quiet) and quiet[j]:
                    j += 1
                t0, t1 = (a + k) * ENV_HOP, (a + j) * ENV_HOP
                # only interior silences; segment edges were already trimmed by build_timeline
                if t1 - t0 >= min_gap and t0 > s.src_in + 0.05 and t1 < s.src_out - 0.05:
                    cuts.append((t0 + keep, t1 - keep))
                k = j
            else:
                k += 1
        # edge silence: whisper word starts often run early into the pause before them
        lead_q = 0
        while lead_q < len(quiet) and quiet[lead_q]:
            lead_q += 1
        trail_q = 0
        while trail_q < len(quiet) and quiet[len(quiet) - 1 - trail_q]:
            trail_q += 1
        s_in, s_out = s.src_in, s.src_out
        if lead_q < len(quiet) and lead_q * ENV_HOP > keep + 0.04:
            s_in = (a + lead_q) * ENV_HOP - keep
        if trail_q < len(quiet) and trail_q * ENV_HOP > keep + 0.08:
            s_out = (a + len(quiet) - trail_q) * ENV_HOP + keep + 0.04
        if not cuts and (s_in, s_out) == (s.src_in, s.src_out):
            out.append(s)
            continue
        bounds = [s_in] + [x for c in cuts for x in c] + [s_out]
        for p0, p1 in zip(bounds[::2], bounds[1::2]):
            p0, p1 = frame(p0), frame(p1)
            if p1 - p0 < 2 / FPS:
                continue
            ws = [i for i in s.words if p0 - 0.05 <= (words[i]["start"] + words[i]["end"]) / 2 < p1 + 0.05]
            out.append(Segment(src_in=p0, src_out=p1, zoom=s.zoom, words=ws or s.words[:0], range_id=s.range_id))
    return out


def remove_overlaps(segs: list[Segment]) -> list[Segment]:
    """Consecutive segments must never replay the same source audio."""
    out: list[Segment] = []
    for s in segs:
        if out:
            p = out[-1]
            if p.src_in <= s.src_in < p.src_out:   # chronological neighbour that starts inside the previous one
                s = Segment(src_in=frame(p.src_out), src_out=s.src_out, zoom=s.zoom, words=s.words, range_id=s.range_id)
                if s.src_out - s.src_in < 2 / FPS:
                    continue
        out.append(s)
    return out
