"""Paths and settings for the Content Studio.

Everything is overridable via environment variables or ROOT/.env (KEY=VALUE lines).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _env_path(key: str, default: Path) -> Path:
    return Path(os.environ.get(key, str(default))).expanduser()


DATA_DIR = _env_path("STUDIO_DATA_DIR", ROOT / "data" / "studio")
MEDIA_DIR = _env_path("STUDIO_MEDIA_DIR", ROOT / "media")
DB_PATH = DATA_DIR / "studio.db"
COOKIES_DIR = DATA_DIR / "cookies"
WEB_DIST = ROOT / "web" / "dist"

YTDLP = os.environ.get("STUDIO_YTDLP", str(HOME / ".local" / "bin" / "yt-dlp"))
FFMPEG = os.environ.get("STUDIO_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("STUDIO_FFPROBE", "ffprobe")
NODE = os.environ.get("STUDIO_NODE") or shutil.which("node") or str(HOME / ".local" / "bin" / "node")
CLAUDE = os.environ.get("STUDIO_CLAUDE", str(HOME / ".local" / "bin" / "claude"))

WHISPER_CLI = _env_path("STUDIO_WHISPER_CLI", HOME / "whisper.cpp" / "build-vulkan" / "bin" / "whisper-cli")
WHISPER_MODELS_DIR = HOME / "whisper.cpp" / "models"
# Best model first; the first one that exists wins.
WHISPER_MODEL_PREFERENCE = [
    "ggml-large-v3-turbo.bin",
    "ggml-large-v3-turbo-q8_0.bin",
    "ggml-large-v3-turbo-q5_0.bin",
    "ggml-medium.en.bin",
    "ggml-small.en.bin",
    "ggml-base.en.bin",
]

PORT = int(os.environ.get("STUDIO_PORT", "8794"))
HOST = os.environ.get("STUDIO_HOST", "127.0.0.1")

# How many recent posts to read from a profile on each check.
LIST_LIMIT = int(os.environ.get("STUDIO_LIST_LIMIT", "30"))
# When a creator is first added, fully analyze this many of their top posts
# (plus the newest few) so the dashboard has something real to show immediately.
BACKFILL_TOP = int(os.environ.get("STUDIO_BACKFILL_TOP", "8"))
BACKFILL_NEWEST = int(os.environ.get("STUDIO_BACKFILL_NEWEST", "3"))
# Default monitor interval per creator.
CHECK_INTERVAL_MIN = int(os.environ.get("STUDIO_CHECK_INTERVAL_MIN", "180"))
# Re-pull metrics at these ages (hours) so outlier scores settle.
METRIC_REFRESH_HOURS = [24, 72, 168]

LLM_MODEL = os.environ.get("STUDIO_LLM_MODEL", "sonnet")
LLM_TIMEOUT_S = int(os.environ.get("STUDIO_LLM_TIMEOUT_S", "300"))


def whisper_model() -> Path | None:
    override = os.environ.get("STUDIO_WHISPER_MODEL")
    if override:
        return Path(override).expanduser()
    for name in WHISPER_MODEL_PREFERENCE:
        p = WHISPER_MODELS_DIR / name
        if p.exists() and p.stat().st_size > 10_000_000:
            return p
    return None


def ensure_dirs() -> None:
    for d in (DATA_DIR, MEDIA_DIR, COOKIES_DIR, MEDIA_DIR / "videos", MEDIA_DIR / "frames", MEDIA_DIR / "work"):
        d.mkdir(parents=True, exist_ok=True)
