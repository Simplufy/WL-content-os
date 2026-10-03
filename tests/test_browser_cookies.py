from studio import db, platforms


def _fake_probe(signed_in):
    def probe(browser, platform):
        if browser in signed_in:
            return {"status": "logged_in", "detail": "signed in"}
        return {"status": "absent", "detail": "x"} if browser != "firefox" else {"status": "no_login", "detail": "not signed in"}
    return probe


def test_auto_picks_signed_in_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(platforms.config, "COOKIES_DIR", tmp_path)
    monkeypatch.setattr(platforms, "_probe", _fake_probe({"safari", "brave"}))
    assert platforms.browser_mode("instagram") == "auto"
    cmd = platforms._base_cmd("instagram")
    assert cmd[cmd.index("--cookies-from-browser") + 1] == "safari"  # priority order: chrome, safari, …
    found = db.get_setting("browser_cookies_found")["instagram"]
    assert set(found["browsers"]) == {"safari", "brave", "firefox"}  # absent browsers aren't listed


def test_auto_cached_and_forgotten(tmp_path, monkeypatch):
    monkeypatch.setattr(platforms.config, "COOKIES_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(platforms, "_probe", lambda b, p: calls.append(b) or {"status": "logged_in", "detail": ""})
    platforms.browser_source("instagram")
    n = len(calls)
    platforms.browser_source("instagram")
    assert len(calls) == n  # cached
    platforms.forget_browser("instagram")
    platforms.browser_source("instagram")
    assert len(calls) == 2 * n


def test_uploaded_file_beats_auto_but_not_manual_choice(tmp_path, monkeypatch):
    monkeypatch.setattr(platforms.config, "COOKIES_DIR", tmp_path)
    monkeypatch.setattr(platforms, "_probe", _fake_probe({"chrome"}))
    (tmp_path / "instagram.txt").write_text("# Netscape HTTP Cookie File\n")
    cmd = platforms._base_cmd("instagram")
    assert "--cookies" in cmd and "--cookies-from-browser" not in cmd
    db.set_setting("browser_cookies", {"instagram": "firefox"})
    cmd = platforms._base_cmd("instagram")
    assert cmd[cmd.index("--cookies-from-browser") + 1] == "firefox"


def test_nothing_signed_in(tmp_path, monkeypatch):
    monkeypatch.setattr(platforms.config, "COOKIES_DIR", tmp_path)
    monkeypatch.setattr(platforms, "_probe", _fake_probe(set()))
    assert not platforms.has_cookies("instagram")
    assert platforms.browser_mode("tiktok") == "off" and platforms.browser_source("tiktok") is None
