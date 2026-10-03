"""Runs under yt-dlp's own Python (not Studio's): reports whether a browser holds a signed-in session.

Usage: python browser_probe.py <browser> <domain> <cookie,names>
Prints one JSON line: {"status": "logged_in" | "no_login" | "locked" | "absent" | "error", "detail": str}
Never prints cookie values."""
import json
import sys
import time


def main() -> None:
    browser, domain, names = sys.argv[1], sys.argv[2], set(sys.argv[3].split(","))
    try:
        from yt_dlp.cookies import extract_cookies_from_browser
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"status": "error", "detail": f"yt-dlp not importable: {e}"}))
        return

    class Quiet:
        def debug(self, *a, **k): pass
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, *a, **k): pass

    try:
        name, _, profile = browser.partition(":")
        jar = extract_cookies_from_browser(name, profile or None, logger=Quiet())
    except FileNotFoundError:
        print(json.dumps({"status": "absent", "detail": "not installed / never opened"}))
        return
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        absent = "could not find" in msg.lower() or "not found" in msg.lower() or "unsupported platform" in msg.lower()
        print(json.dumps({"status": "absent" if absent else "error", "detail": msg[:200]}))
        return
    now = time.time()
    found = [c for c in jar if c.name in names and c.domain.lstrip(".").endswith(domain) and (not c.expires or c.expires > now)]
    if any(c.value for c in found):
        print(json.dumps({"status": "logged_in", "detail": "signed in"}))
    elif found:
        print(json.dumps({"status": "locked", "detail": "signed in, but the browser's cookie store is locked to the desktop keychain"}))
    else:
        print(json.dumps({"status": "no_login", "detail": "not signed in"}))


if __name__ == "__main__":
    main()
