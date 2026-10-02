"""Thin client for the self-hosted Postiz public API.

Docs: https://docs.postiz.com/public-api  (create-post 90/h, list-posts 30/h — keep calls rare)
Config: POSTIZ_URL + POSTIZ_API_KEY in .env, falling back to the legacy secrets.json.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

from . import config


class PostizError(RuntimeError):
    pass


def settings() -> tuple[str, str | None]:
    url = os.environ.get("POSTIZ_URL")
    key = os.environ.get("POSTIZ_API_KEY")
    legacy = config.ROOT / "secrets.json"
    if (not url or not key) and legacy.exists():
        try:
            p = json.loads(legacy.read_text()).get("postiz") or {}
            url = url or p.get("base_url")
            key = key or p.get("api_key")
        except (ValueError, OSError):
            pass
    return (url or "http://localhost:4007/api/public/v1").rstrip("/"), key


# Provider identifiers Postiz uses → our display names and the settings each needs.
PROVIDERS = {
    "tiktok": "TikTok",
    "youtube": "YouTube",
    "instagram": "Instagram",
    "instagram-standalone": "Instagram",
    "facebook": "Facebook",
    "linkedin": "LinkedIn",
    "linkedin-page": "LinkedIn Page",
    "x": "X",
    "threads": "Threads",
}


class Client:
    def __init__(self, base: str | None = None, key: str | None = None, transport: httpx.BaseTransport | None = None):
        b, k = settings()
        self.base = (base or b).rstrip("/")
        self.key = key or k
        self._http = httpx.Client(timeout=httpx.Timeout(30, read=600), transport=transport)

    def _req(self, method: str, path: str, **kw: Any) -> Any:
        if not self.key:
            raise PostizError("Postiz API key is not configured")
        try:
            r = self._http.request(method, f"{self.base}{path}", headers={"Authorization": self.key}, **kw)
        except httpx.HTTPError as e:
            raise PostizError(f"Postiz unreachable: {e}") from e
        if r.status_code >= 400:
            raise PostizError(f"Postiz {method} {path} → {r.status_code}: {r.text[:400]}")
        return r.json() if r.content else None

    # ---- read
    def connected(self) -> bool:
        return bool((self._req("GET", "/is-connected") or {}).get("connected"))

    def integrations(self) -> list[dict[str, Any]]:
        return self._req("GET", "/integrations") or []

    def posts(self, start: str, end: str) -> list[dict[str, Any]]:
        return (self._req("GET", "/posts", params={"startDate": start, "endDate": end}) or {}).get("posts") or []

    def find_slot(self, integration_id: str) -> str | None:
        return (self._req("GET", f"/find-slot/{integration_id}") or {}).get("date")

    # ---- write
    def upload(self, path: Path) -> dict[str, Any]:
        with path.open("rb") as fh:
            return self._req("POST", "/upload", files={"file": (path.name, fh, "video/mp4")})

    def create_post(self, body: dict[str, Any]) -> Any:
        return self._req("POST", "/posts", json=body)

    def delete_post(self, post_id: str) -> Any:
        return self._req("DELETE", f"/posts/{post_id}")


def default_settings(provider: str, *, title: str = "") -> dict[str, Any]:
    """Safe per-platform defaults (Postiz schema). The owner can change them before scheduling."""
    p = provider
    if p == "tiktok":
        return {"__type": "tiktok", "title": title[:90], "privacy_level": "PUBLIC_TO_EVERYONE", "duet": False,
                "stitch": False, "comment": True, "autoAddMusic": "no", "brand_content_toggle": False,
                "brand_organic_toggle": False, "video_made_with_ai": False, "content_posting_method": "DIRECT_POST"}
    if p == "youtube":
        return {"__type": "youtube", "title": (title or "New video")[:100], "type": "public",
                "selfDeclaredMadeForKids": "no", "tags": []}
    if p in ("instagram", "instagram-standalone"):
        return {"__type": p, "post_type": "post"}
    if p == "facebook":
        return {"__type": "facebook", "post_type": "post", **({"title": title[:100]} if title else {})}
    if p in ("linkedin", "linkedin-page"):
        return {"__type": p, "post_as_images_carousel": False}
    if p == "x":
        return {"__type": "x", "who_can_reply_post": "everyone", "made_with_ai": False, "paid_partnership": False,
                "post_type": "post"}
    return {"__type": p}


def extract_post_ids(resp: Any) -> list[str]:
    """Create-post response isn't documented; accept list/dict shapes and pull out every post id."""
    ids: list[str] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            for k in ("postId", "id"):
                if isinstance(x.get(k), str):
                    ids.append(x[k])
                    break
            for k, v in x.items():
                if k in ("integration", "integrations", "image", "settings"):
                    continue  # ids in there belong to channels/media, not posts
                if isinstance(v, (list, dict)):
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(resp)
    seen: set[str] = set()
    return [i for i in ids if not (i in seen or seen.add(i))]
