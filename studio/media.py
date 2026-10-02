"""ffmpeg/ffprobe helpers and whisper.cpp transcription with word timings."""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from . import config


class MediaError(RuntimeError):
    pass


def _run(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise MediaError(f"{Path(cmd[0]).name} timed out") from e
    if res.returncode != 0:
        raise MediaError(f"{Path(cmd[0]).name} failed: {(res.stderr or res.stdout)[-400:]}")
    return res


def probe_duration(path: Path) -> float | None:
    res = _run([config.FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)], 60)
    try:
        return float(json.loads(res.stdout)["format"]["duration"])
    except (KeyError, ValueError, TypeError):
        return None


def extract_frames(video: Path, out_dir: Path, duration: float | None) -> list[dict[str, Any]]:
    """Hook frames (first 3s) plus a spread across the video for format reading."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.jpg"):
        old.unlink()
    dur = duration or probe_duration(video) or 0
    times = [0.2, 1.0, 2.0, 3.0]
    if dur > 6:
        times += [round(dur * f, 2) for f in (0.25, 0.5, 0.75)]
    times = [t for t in times if dur <= 0 or t < dur - 0.05]
    frames = []
    for i, t in enumerate(times):
        out = out_dir / f"f{i:02d}_{t:06.2f}.jpg"
        try:
            _run([config.FFMPEG, "-y", "-v", "error", "-ss", str(t), "-i", str(video),
                  "-frames:v", "1", "-vf", "scale='min(720,iw)':-2", "-q:v", "4", str(out)], 60)
        except MediaError:
            continue
        if out.exists():
            frames.append({"t": t, "path": str(out), "hook": t <= 3.0})
    return frames


def transcribe(video: Path) -> dict[str, Any]:
    """Return {'words': [...], 'segments': [...], 'text': str, 'model': name}."""
    model = config.whisper_model()
    if model is None or not config.WHISPER_CLI.exists():
        raise MediaError("whisper.cpp or a whisper model is missing")
    with tempfile.TemporaryDirectory(dir=config.MEDIA_DIR / "work") as td:
        wav = Path(td) / "audio.wav"
        _run([config.FFMPEG, "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
              "-c:a", "pcm_s16le", str(wav)], 300)
        stem = Path(td) / "out"
        _run([str(config.WHISPER_CLI), "-m", str(model), "-f", str(wav), "-l", "en", "-np",
              "-ml", "1", "-sow", "-oj", "-of", str(stem), "-t", "8"], 900)
        data = json.loads((stem.with_suffix(".json")).read_text(errors="replace"))
    words = []
    for seg in data.get("transcription", []):
        text = seg.get("text", "")
        if not text.strip():
            continue
        off = seg.get("offsets") or {}
        words.append({
            "start": round(off.get("from", 0) / 1000, 3),
            "end": round(off.get("to", 0) / 1000, 3),
            "word": text.strip(),
        })
    words = [w for w in words if not re.fullmatch(r"\[.*\]|\(.*\)", w["word"])]  # [Music], (laughs)
    segments = group_words(words)
    return {
        "words": words,
        "segments": segments,
        "text": " ".join(s["text"] for s in segments).strip(),
        "model": model.name,
    }


def group_words(words: list[dict[str, Any]], max_gap: float = 0.6, max_len: int = 18) -> list[dict[str, Any]]:
    """Group word timings into readable sentence-ish segments."""
    segs: list[dict[str, Any]] = []
    cur: list[dict[str, Any]] = []
    for w in words:
        if cur and (w["start"] - cur[-1]["end"] > max_gap or len(cur) >= max_len):
            segs.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.?!]$", w["word"]):
            segs.append(cur)
            cur = []
    if cur:
        segs.append(cur)
    return [
        {"start": s[0]["start"], "end": s[-1]["end"], "text": " ".join(w["word"] for w in s)}
        for s in segs
    ]


def text_in_window(words: list[dict[str, Any]], start: float, end: float) -> str:
    return " ".join(w["word"] for w in words if w["start"] < end and w["end"] > start).strip()
