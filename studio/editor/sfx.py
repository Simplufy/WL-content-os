"""Synthesized UI sound design for graphics (no stock audio): soft pops, ticks, whooshes.

Mixed well under the voice; deterministic (seeded noise).
"""
from __future__ import annotations

from typing import Any

import numpy as np

SR = 48000


def _env(n: int, attack: float, decay: float) -> np.ndarray:
    t = np.arange(n) / SR
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    return a * np.exp(-t / max(decay, 1e-4))


def pop(length: float = 0.18) -> np.ndarray:
    n = int(SR * length)
    t = np.arange(n) / SR
    f = 520 + 380 * np.exp(-t * 28)                     # quick downward chirp, rounded
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, 0.002, 0.045) * 0.9


def tick(length: float = 0.06) -> np.ndarray:
    n = int(SR * length)
    t = np.arange(n) / SR
    return (np.sin(2 * np.pi * 2400 * t) * 0.6 + np.sin(2 * np.pi * 3600 * t) * 0.25) * _env(n, 0.0005, 0.012)


def whoosh(length: float = 0.42, seed: int = 7) -> np.ndarray:
    n = int(SR * length)
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(n)
    # band-limit with a moving average (cheap low-pass), then shape a rise-and-fall swell
    k = 24
    smooth = np.convolve(noise, np.ones(k) / k, mode="same")
    shape = np.sin(np.pi * np.linspace(0, 1, n)) ** 1.6
    return smooth * shape * 0.9


def thump(length: float = 0.35) -> np.ndarray:
    n = int(SR * length)
    t = np.arange(n) / SR
    f = 110 - 60 * t / length
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, 0.003, 0.09)


VOICES = {"pop": pop, "tick": tick, "whoosh": whoosh, "thump": thump}


def cues_for(graphics: list[dict[str, Any]]) -> list[tuple[float, str, float]]:
    """(time, voice, gain) per graphic, timed to how the components animate."""
    cues: list[tuple[float, str, float]] = []
    for g in graphics:
        s, total = g["start"], g["end"] - g["start"]
        t = g.get("type")
        if t == "word_slam":
            cues += [(s - 0.18, "whoosh", 0.7), (s + 0.05, "thump", 0.8)]
            continue
        if t == "hook_title":
            cues += [(s + 0.02, "whoosh", 0.45), (s + 0.1, "pop", 0.6)]
            continue
        cues.append((s + 0.04, "pop", 0.55))
        if t in ("checklist", "steps"):
            n = len(g.get("items") or [])
            gap = min(0.6, (total * 1000 - 900) / max(1, n) / 1000)
            cues += [(s + 0.3 + k * gap, "tick", 0.5) for k in range(n)]
        elif t == "bulb_check":
            n = len(g.get("items") or []) or 6
            off0 = max(1.1, total * 0.5)
            step = min(0.2, (total - off0 - 0.45) / n)
            cues += [(s + 0.3, "tick", 0.6)] + [(s + off0 + k * step, "tick", 0.35) for k in range(n)]
        elif t == "traffic_light":
            span = (total - 0.65) / (1 if g.get("active") not in ("", None, "sequence") else 3)
            k_max = 1 if g.get("active") not in ("", None, "sequence") else 3
            cues += [(s + 0.35 + k * span, "tick", 0.6) for k in range(k_max)]
        elif t in ("before_after",):
            cues.append((s + 0.88, "pop", 0.45))
    return cues


def render_track(cues: list[tuple[float, str, float]], duration: float, level_db: float = -22.0) -> np.ndarray:
    out = np.zeros(int(SR * duration) + SR, dtype=np.float32)
    for t, voice, gain in cues:
        if t < 0:
            t = 0.0
        x = VOICES[voice]().astype(np.float32) * gain
        a = int(t * SR)
        b = min(len(out), a + len(x))
        out[a:b] += x[: b - a]
    peak = np.max(np.abs(out)) or 1.0
    out = out / peak * (10 ** (level_db / 20))
    return out[: int(SR * duration)]


def mix_into(voice: np.ndarray, fx: np.ndarray) -> np.ndarray:
    n = len(voice)
    fx = np.pad(fx, (0, max(0, n - len(fx))))[:n]
    mixed = voice + fx
    peak = np.max(np.abs(mixed))
    limit = 10 ** (-1.0 / 20)
    return mixed * (limit / peak) if peak > limit else mixed
