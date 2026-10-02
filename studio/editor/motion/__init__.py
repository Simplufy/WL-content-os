"""Animated graphics: HTML/Web-Animations scenes rendered frame-by-frame in headless Chromium
with a transparent background, encoded as alpha .mov clips and overlaid on the edit.

Same technique as Remotion, without the per-seat company licence. The graphic spec is plain
JSON so another renderer (Remotion, Tesseract) can be swapped in later.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

from ... import config

HERE = Path(__file__).resolve().parent
PAGE = HERE / "graphics.html"
LUCIDE_DIR = config.ROOT / "web" / "node_modules" / "lucide-static" / "icons"
FPS = 30

TYPES = ["dash_light", "bulb_check", "traffic_light", "stat", "before_after", "icon", "checklist", "word_slam"]
DASH_ICONS = ["oil", "temp", "battery", "check_engine", "tire_pressure", "abs", "traction", "brake", "high_beam",
              "cruise", "seatbelt"]


def _brand() -> dict[str, Any]:
    from ... import brand
    return brand.public_info()


def lucide_svg(name: str) -> str | None:
    p = LUCIDE_DIR / f"{name}.svg"
    if not p.exists():
        return None
    svg = p.read_text()
    return svg.replace('width="24"', 'width="100%"').replace('height="24"', 'height="100%"').replace(
        'stroke-width="2"', 'stroke-width="2.2"')


def lucide_names() -> list[str]:
    return sorted(p.stem for p in LUCIDE_DIR.glob("*.svg")) if LUCIDE_DIR.exists() else []


_KEYWORDS = ("car", "wrench", "gauge", "fuel", "battery", "thermometer", "clock", "timer", "hourglass", "calendar",
             "dollar", "wallet", "piggy", "banknote", "coins", "receipt", "trending", "chart", "users", "user-check",
             "user-plus", "user-x", "phone", "smartphone", "message", "mail", "camera", "send", "circle-check",
             "badge-check", "circle-x", "triangle-alert", "siren", "shield", "star", "trophy", "target", "flame",
             "zap", "bell", "map-pin", "building", "store", "warehouse", "briefcase", "clipboard", "file-text",
             "list-checks", "settings", "cog", "hammer", "key", "lock", "rocket", "handshake", "thumbs", "heart",
             "coffee", "moon", "sun", "bed", "house", "plane", "mountain", "laptop", "monitor", "bot", "brain",
             "lightbulb", "megaphone", "search", "network", "workflow", "repeat", "refresh", "ban", "hand",
             "crown", "graduation", "school", "baby", "flag", "road", "traffic", "fuel", "droplet", "paintbrush",
             "spray-can", "sparkles", "scale", "percent", "calculator", "presentation", "inbox", "headset", "id-card")


def curated_icons() -> list[str]:
    """A few hundred icons worth offering Claude (the full set is ~2,000)."""
    return [n for n in lucide_names() if any(n == k or n.startswith(k + "-") for k in _KEYWORDS) and not n[-1].isdigit()]


async def _render_one(page, g: dict[str, Any], out: Path) -> None:
    total = int(round((g["end"] - g["start"]) * 1000))
    band = g["band"]
    if g.get("icon") and g["icon"] not in DASH_ICONS:
        svg = lucide_svg(g["icon"])
        if svg:
            await page.evaluate("([n, s]) => { LUCIDE[n] = s; }", [g["icon"], svg])
    await page.evaluate("([g, t, b]) => window.__mount(g, t, b)", [g, total, band])
    n = max(1, int(round(total / 1000 * FPS)))
    proc = await asyncio.create_subprocess_exec(
        config.FFMPEG, "-y", "-v", "error", "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "png", "-i", "-",
        "-c:v", "png", "-pix_fmt", "rgba", str(out), stdin=asyncio.subprocess.PIPE)
    clip = {"x": 0, "y": band["top"], "width": 1080, "height": band["height"]}
    assert proc.stdin
    for k in range(n):
        await page.evaluate("ms => window.__seek(ms)", k * 1000 / FPS)
        png = await page.screenshot(clip=clip, omit_background=True, type="png")
        proc.stdin.write(png)
        await proc.stdin.drain()
    proc.stdin.close()
    if await proc.wait() != 0:
        raise RuntimeError(f"graphic encode failed for {g.get('type')}")


async def _render_all(graphics: list[dict[str, Any]], work: Path, workers: int, prefix: str = "gfx") -> list[dict[str, Any]]:
    from playwright.async_api import async_playwright

    work.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--disable-gpu", "--font-render-hinting=none"])
        queue: asyncio.Queue = asyncio.Queue()
        for k, g in enumerate(graphics):
            queue.put_nowait((k, g))
        done: list[dict[str, Any]] = []

        async def worker() -> None:
            page = await browser.new_page(viewport={"width": 1080, "height": 1920}, device_scale_factor=1)
            await page.goto(PAGE.as_uri())
            await page.evaluate("document.fonts.ready")
            await page.evaluate("b => window.__brand(b)", _brand())
            while not queue.empty():
                k, g = queue.get_nowait()
                out = work / f"{prefix}_{k:03d}.mov"
                await _render_one(page, g, out)
                done.append({**g, "file": str(out)})
            await page.close()

        await asyncio.gather(*(worker() for _ in range(min(workers, len(graphics)) or 1)))
        await browser.close()
    return sorted(done, key=lambda g: g["start"])


def render_graphics(graphics: list[dict[str, Any]], work: Path, workers: int = 6, prefix: str = "gfx") -> list[dict[str, Any]]:
    """Each graphic: {type, start, end, band: {top, height}, ...props} → adds 'file' (alpha .mov)."""
    for old in work.glob(f"{prefix}_*.mov"):
        old.unlink()
    if not graphics:
        return []
    return asyncio.run(_render_all(graphics, work, workers, prefix))


def preview(g: dict[str, Any], out_png: Path, at_ms: int = 900) -> None:
    """Single still of a graphic (for tests/review)."""
    async def run() -> None:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            b = await p.chromium.launch()
            page = await b.new_page(viewport={"width": 1080, "height": 1920})
            await page.goto(PAGE.as_uri())
            await page.evaluate("document.fonts.ready")
            await page.evaluate("b => window.__brand(b)", _brand())
            if g.get("icon") and g["icon"] not in DASH_ICONS and (svg := lucide_svg(g["icon"])):
                await page.evaluate("([n, s]) => { LUCIDE[n] = s; }", [g["icon"], svg])
            total = int((g["end"] - g["start"]) * 1000)
            await page.evaluate("([g, t, b]) => window.__mount(g, t, b)", [g, total, g["band"]])
            await page.evaluate("ms => window.__seek(ms)", at_ms)
            await page.screenshot(path=str(out_png), clip={"x": 0, "y": g["band"]["top"], "width": 1080,
                                                           "height": g["band"]["height"]}, omit_background=True)
            await b.close()
    asyncio.run(run())


__all__ = ["render_graphics", "preview", "TYPES", "DASH_ICONS", "lucide_names", "json"]
