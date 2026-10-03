from studio import db, platforms


def test_browser_source_beats_cookie_file(tmp_path, monkeypatch):
    monkeypatch.setattr(platforms.config, "COOKIES_DIR", tmp_path)
    (tmp_path / "instagram.txt").write_text("# Netscape HTTP Cookie File\n")
    assert platforms.has_cookies("instagram")
    assert "--cookies" in platforms._base_cmd("instagram")

    db.set_setting("browser_cookies", {"instagram": "firefox"})
    cmd = platforms._base_cmd("instagram")
    assert cmd[cmd.index("--cookies-from-browser") + 1] == "firefox"
    assert "--cookies" not in cmd
    assert platforms.browser_source("tiktok") is None


def test_unknown_browser_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(platforms.config, "COOKIES_DIR", tmp_path)
    db.set_setting("browser_cookies", {"instagram": "netscape4"})
    assert not platforms.has_cookies("instagram")
    assert "--cookies-from-browser" not in platforms._base_cmd("instagram")
