"""Profile URL parsing and all yt-dlp calls (listing, metadata, download)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
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


# Priority when several browsers are signed in: the ones people actually browse in first.
BROWSERS = ("chrome", "safari", "firefox", "edge", "brave", "chromium", "opera", "vivaldi", "whale")
LOGIN_COOKIES = {"instagram": ("instagram.com", "sessionid"), "tiktok": ("tiktok.com", "sessionid,sid_tt"),
                 "youtube": ("youtube.com", "SAPISID,__Secure-3PSID")}
_SNAP_PROFILES = {"chromium": Path.home() / "snap/chromium/common/chromium",
                  "chrome": Path.home() / ".var/app/com.google.Chrome/config/google-chrome"}
_FOUND_TTL, _MISS_TTL = 6 * 3600, 15 * 60


def _ytdlp_python() -> str | None:
    """yt-dlp's interpreter (it's a uv/pipx tool), so the probe can use its cookie readers."""
    try:
        first = Path(config.YTDLP).read_bytes()[:200].split(b"\n", 1)[0].decode()
    except OSError:
        return None
    return first[2:].strip() if first.startswith("#!") and "python" in first else None


def _probe(browser: str, platform: str) -> dict[str, str]:
    py = _ytdlp_python()
    if not py:
        return {"status": "error", "detail": "yt-dlp is a standalone build; browser detection needs the Python install"}
    domain, names = LOGIN_COOKIES[platform]
    try:
        res = subprocess.run([py, str(Path(__file__).with_name("browser_probe.py")), browser, domain, names],
                             capture_output=True, text=True, timeout=25)
        return json.loads(res.stdout.strip().splitlines()[-1])
    except subprocess.TimeoutExpired:
        return {"status": "error", "detail": "timed out (keychain prompt waiting?)"}
    except (ValueError, IndexError):
        return {"status": "error", "detail": (res.stderr or "no output")[-200:]}


def detect_browser(platform: str) -> dict[str, Any]:
    """Check every browser on this machine for a signed-in session and remember the winner."""
    from concurrent.futures import ThreadPoolExecutor
    from . import db
    cands = list(BROWSERS)
    for b, d in _SNAP_PROFILES.items():  # snap/flatpak installs keep profiles where yt-dlp doesn't look
        if d.is_dir():
            cands.insert(cands.index(b) + 1, f"{b}:{d}")
    with ThreadPoolExecutor(len(cands)) as ex:
        results = dict(zip(cands, ex.map(lambda b: _probe(b, platform), cands)))
    browser = next((b for b in cands if results[b]["status"] == "logged_in"), None)
    seen = {b: r for b, r in results.items() if r["status"] != "absent"}
    entry = {"browser": browser, "at": time.time(), "browsers": seen}
    cache = db.get_setting("browser_cookies_found", {}) or {}
    cache[platform] = entry
    db.set_setting("browser_cookies_found", cache)
    return entry


def browser_mode(platform: str) -> str:
    """'auto', 'off', or a specific browser name. Instagram defaults to auto (it needs a login);
    TikTok/YouTube default to off since they work anonymously and a login can change what they serve."""
    from . import db
    return (db.get_setting("browser_cookies", {}) or {}).get(platform) or ("auto" if platform == "instagram" else "off")


def browser_source(platform: str, detect: bool = True) -> str | None:
    """Which browser's live login to use. In auto mode this is whichever browser on this machine is signed in."""
    from . import db
    mode = browser_mode(platform)
    if mode == "off":
        return None
    if mode != "auto":
        return mode if mode.split(":")[0] in BROWSERS else None
    hit = (db.get_setting("browser_cookies_found", {}) or {}).get(platform)
    fresh = hit and time.time() - hit["at"] < (_FOUND_TTL if hit.get("browser") else _MISS_TTL)
    if not fresh and detect:
        hit = detect_browser(platform)
    return (hit or {}).get("browser")


def forget_browser(platform: str) -> None:
    """A login stopped working: re-scan the browsers on the next request."""
    from . import db
    cache = db.get_setting("browser_cookies_found", {}) or {}
    if cache.pop(platform, None) is not None:
        db.set_setting("browser_cookies_found", cache)


def has_cookies(platform: str) -> bool:
    return bool(cookies_file(platform) or browser_source(platform))


def _base_cmd(platform: str) -> list[str]:
    cmd = [config.YTDLP, "--no-warnings", "--ignore-config", "--no-progress"]
    if platform == "youtube" and config.NODE:
        cmd += ["--js-runtimes", f"node:{config.NODE}"]  # YouTube's JS challenges (needs yt-dlp[default])
    # A browser chosen by hand wins, then an uploaded cookies.txt, then whatever browser auto-detect found.
    ck = cookies_file(platform)
    browser = None if (ck and browser_mode(platform) == "auto") else browser_source(platform)
    if browser:
        cmd += ["--cookies-from-browser", browser]
    elif ck:
        cmd += ["--cookies", str(ck)]
    return cmd


def test_login(platform: str) -> dict[str, Any]:
    """One read-only lookup to prove the cookies work. Never posts or changes anything."""
    probe = {"instagram": "https://www.instagram.com/instagram/", "tiktok": "https://www.tiktok.com/@tiktok",
             "youtube": "https://www.youtube.com/@YouTube/shorts"}[platform]
    if browser_mode(platform) == "auto" and not cookies_file(platform):
        found = detect_browser(platform)
        if not found["browser"]:
            seen = ", ".join(f"{b.split(':')[0]}: {r['detail']}" for b, r in found["browsers"].items()) or "no browsers found"
            return {"ok": False, "detail": f"No browser on this machine is signed in to {platform} ({seen})"}
    try:
        out = _run(_base_cmd(platform) + ["--flat-playlist", "-J", "--playlist-end", "1", probe], timeout=90)
        n = len(json.loads(out).get("entries") or [])
        return {"ok": n > 0, "detail": "Signed-in lookup worked" if n else "No posts came back — the login may not be active"}
    except PlatformError as e:
        return {"ok": False, "detail": str(e)}


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
        return ("Instagram blocked the request. Check the Instagram login in Settings → Platform cookies "
                "(sign the browser on this machine back in, or upload fresh cookies). Raw: " + last[-300:])
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
    elif platform == "instagram" and not has_cookies("instagram"):
        raise PlatformError(
            "Instagram needs a logged-in session: sign into Instagram in any browser on this machine "
            "(Chrome, Safari, Firefox…) or upload cookies in Settings, then check again."
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
        forget_browser(platform)  # the login may have moved to another browser; re-scan next time
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
