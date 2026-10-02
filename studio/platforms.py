"""Profile URL parsing and all yt-dlp calls (listing, metadata, download)."""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import config

PLATFORMS = ("tiktok", "youtube", "instagram")


class PlatformError(RuntimeError):
    pass


class NotAVideo(PlatformError):
    """Photo/slideshow posts: nothing to transcribe or tear down."""


@dataclass
class Profile:
    platform: str
    handle: str
    profile_url: str


def parse_profile_url(raw: str) -> Profile:
    """Turn whatever the user pasted into a canonical profile.

    Accepts profile links for TikTok, YouTube (incl. /shorts tabs) and Instagram,
    with or without scheme / www / trailing paths.
    """
    s = (raw or "").strip()
    if not s:
        raise ValueError("Paste a profile link.")
    if not re.match(r"^https?://", s):
        s = "https://" + s
    u = urlparse(s)
    host = (u.netloc or "").lower().removeprefix("www.").removeprefix("m.")
    parts = [p for p in (u.path or "").split("/") if p]

    if host.endswith("tiktok.com"):
        if parts and parts[0].startswith("@") and len(parts[0]) > 1:
            if len(parts) >= 2 and parts[1] == "video":
                raise ValueError("That's a single TikTok video. Paste the creator's profile link instead.")
            h = parts[0][1:]
            return Profile("tiktok", h, f"https://www.tiktok.com/@{h}")
        raise ValueError("TikTok profile links look like tiktok.com/@handle")

    if host.endswith("youtube.com"):
        if parts and parts[0] in ("watch", "shorts", "live"):
            raise ValueError("That's a single YouTube video. Paste the channel link instead.")
        if parts and parts[0].startswith("@") and len(parts[0]) > 1:
            h = parts[0][1:]
            return Profile("youtube", h, f"https://www.youtube.com/@{h}")
        if len(parts) >= 2 and parts[0] in ("channel", "c", "user"):
            return Profile("youtube", parts[1], f"https://www.youtube.com/{parts[0]}/{parts[1]}")
        raise ValueError("YouTube channel links look like youtube.com/@handle")
    if host == "youtu.be":
        raise ValueError("That's a single YouTube video. Paste the channel link instead.")

    if host.endswith("instagram.com"):
        if parts and parts[0] in ("p", "reel", "reels", "tv", "stories", "explore"):
            raise ValueError("That's a single Instagram post. Paste the profile link instead.")
        if parts:
            h = parts[0]
            return Profile("instagram", h, f"https://www.instagram.com/{h}/")
        raise ValueError("Instagram profile links look like instagram.com/handle")

    raise ValueError("Only TikTok, YouTube and Instagram profile links are supported.")


# --------------------------------------------------------------------------- yt-dlp

def cookies_file(platform: str) -> Path | None:
    p = config.COOKIES_DIR / f"{platform}.txt"
    return p if p.exists() and p.stat().st_size > 0 else None


def _base_cmd(platform: str) -> list[str]:
    cmd = [config.YTDLP, "--no-warnings", "--ignore-config", "--no-progress"]
    if platform == "youtube" and config.NODE:
        cmd += ["--js-runtimes", f"node:{config.NODE}"]  # YouTube's JS challenges (needs yt-dlp[default])
    ck = cookies_file(platform)
    if ck:
        cmd += ["--cookies", str(ck)]
    return cmd


def _run(cmd: list[str], timeout: int = 240) -> str:
    env = os.environ.copy()
    env["PATH"] = f"{Path.home() / '.local' / 'bin'}:{env.get('PATH', '')}"
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired as e:
        raise PlatformError(f"yt-dlp timed out after {timeout}s") from e
    if res.returncode != 0:
        raise PlatformError(friendly_error(res.stderr or res.stdout))
    return res.stdout


def friendly_error(stderr: str) -> str:
    msg = (stderr or "").strip().splitlines()
    last = next((l for l in reversed(msg) if "ERROR" in l), msg[-1] if msg else "unknown error")
    low = last.lower()
    if "instagram" in low and ("login" in low or "unable to extract" in low or "rate" in low or "cookies" in low):
        return ("Instagram blocked the request. Add Instagram cookies in Settings "
                "(export from a logged-in browser). Raw: " + last[-300:])
    if "private" in low:
        return "This account is private. Raw: " + last[-300:]
    return last[-500:]


def _ts_to_iso(ts: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(microsecond=0).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _date_to_iso(d: Any) -> str | None:
    if not d or not re.fullmatch(r"\d{8}", str(d)):
        return None
    return datetime.strptime(str(d), "%Y%m%d").replace(tzinfo=timezone.utc).isoformat()


def _int(v: Any) -> int | None:
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None


def normalize_entry(platform: str, handle: str, e: dict[str, Any]) -> dict[str, Any] | None:
    """Map a yt-dlp entry (flat or full) to our video fields."""
    vid = e.get("id")
    if not vid:
        return None
    url = e.get("webpage_url") or e.get("url")
    if not url or not str(url).startswith("http"):
        url = {
            "tiktok": f"https://www.tiktok.com/@{handle}/video/{vid}",
            "youtube": f"https://www.youtube.com/watch?v={vid}",
            "instagram": f"https://www.instagram.com/reel/{vid}/",
        }[platform]
    thumb = e.get("thumbnail")
    if not thumb and e.get("thumbnails"):
        thumb = (e["thumbnails"][-1] or {}).get("url")
    return {
        "platform_id": str(vid),
        "url": url,
        "title": e.get("title") or e.get("fulltitle"),
        "description": e.get("description"),
        "published_at": _ts_to_iso(e.get("timestamp")) or _date_to_iso(e.get("upload_date")),
        "duration": e.get("duration"),
        "thumbnail_url": thumb,
        "views": _int(e.get("view_count")),
        "likes": _int(e.get("like_count")),
        "comments": _int(e.get("comment_count")),
        "shares": _int(e.get("repost_count")),
        "saves": _int(e.get("save_count")),
    }


def list_profile(platform: str, handle: str, profile_url: str, limit: int | None = None) -> dict[str, Any]:
    """Return {'info': {...creator fields}, 'videos': [...]} newest first."""
    limit = limit or config.LIST_LIMIT
    urls = [profile_url]
    if platform == "youtube":
        # Most creators we care about post Shorts; long-form lives on /videos.
        base = profile_url.rstrip("/")
        urls = [base + "/shorts", base + "/videos"]
    elif platform == "instagram" and not cookies_file("instagram"):
        raise PlatformError(
            "Instagram needs cookies from a logged-in account. Add them in Settings, then check again."
        )
    kinds = {u: ("long" if u.endswith("/videos") else "short") for u in urls}

    videos: list[dict[str, Any]] = []
    seen: set[str] = set()
    info: dict[str, Any] = {}
    errors: list[str] = []
    for url in urls:
        try:
            out = _run(_base_cmd(platform) + ["--flat-playlist", "-J", "--playlist-end", str(limit), url])
        except PlatformError as e:
            errors.append(str(e))
            continue
        data = json.loads(out)
        if not info:
            info = {
                "display_name": data.get("channel") or data.get("uploader") or data.get("title"),
                "followers": _int(data.get("channel_follower_count")),
            }
            thumbs = data.get("thumbnails") or []
            avatar = next((t.get("url") for t in thumbs if "avatar" in str(t.get("id", ""))), None)
            if avatar:
                info["avatar_url"] = avatar
        for pos, e in enumerate(e for e in (data.get("entries") or []) if e):
            v = normalize_entry(platform, handle, e)
            if v:
                v["kind"] = kinds[url]
                v["list_pos"] = pos
            if v and v["platform_id"] not in seen:
                seen.add(v["platform_id"])
                videos.append(v)
    if not videos and errors:
        raise PlatformError(errors[0])
    if (info.get("display_name") or "").endswith(" - Shorts"):
        info["display_name"] = info["display_name"][: -len(" - Shorts")]
    return {"info": info, "videos": videos}


def video_metadata(platform: str, handle: str, url: str) -> dict[str, Any]:
    out = _run(_base_cmd(platform) + ["-J", "--skip-download", "--no-playlist", url], timeout=180)
    data = json.loads(out)
    v = normalize_entry(platform, handle, data)
    if v is None:
        raise PlatformError("No metadata returned")
    v["_raw_uploader"] = data.get("uploader")
    return v


def download_video(platform: str, url: str, dest_stem: Path) -> Path:
    dest_stem.parent.mkdir(parents=True, exist_ok=True)
    for old in dest_stem.parent.glob(dest_stem.name + ".*"):
        old.unlink()
    _run(
        _base_cmd(platform)
        + [
            "--no-playlist",
            "-f", "bv*[height<=1080][vcodec!*=av01]+ba/b[height<=1080]/bv*+ba/b",
            "--merge-output-format", "mp4",
            "-o", str(dest_stem) + ".%(ext)s",
            url,
        ],
        timeout=600,
    )
    files = sorted(p for p in dest_stem.parent.glob(dest_stem.name + ".*") if p.suffix in (".mp4", ".webm", ".mkv", ".mov"))
    if not files:
        audio_only = [p for p in dest_stem.parent.glob(dest_stem.name + ".*") if p.suffix in (".mp3", ".m4a", ".opus")]
        for p in audio_only:
            p.unlink()
        if audio_only:
            raise NotAVideo("Photo/slideshow post — there's no video to analyze")
        raise PlatformError("Download finished but no video file was written")
    return files[0]


def self_update() -> str:
    """Platforms break scrapers constantly; keep yt-dlp current."""
    res = subprocess.run(["uv", "tool", "upgrade", "yt-dlp"], capture_output=True, text=True, timeout=300,
                         env={**os.environ, "PATH": f"{Path.home() / '.local' / 'bin'}:{os.environ.get('PATH', '')}"})
    return (res.stdout + res.stderr).strip()[-300:]
