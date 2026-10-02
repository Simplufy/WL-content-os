import json

from fastapi.testclient import TestClient

from studio import analyze, brand, config, ideas


def test_defaults_are_neutral():
    b = brand.get()
    assert b["product_name"] == "Content Studio" and "DRAFT" in b["brief"]
    assert ideas.default_pillars() == b["pillars"]


def test_brand_json_overrides_and_reaches_prompts(tmp_path, monkeypatch):
    (tmp_path / "brand.json").write_text(json.dumps({
        "product_name": "Acme Studio", "org_name": "Acme", "colors": {"accent": "#ff0055"},
        "brief": "Acme sells rockets to coyotes.", "pillars": ["Rockets", "Coyotes"]}))
    monkeypatch.setattr(config, "ROOT", tmp_path)
    brand.get.cache_clear()
    try:
        b = brand.get()
        assert b["product_name"] == "Acme Studio" and b["colors"]["accent"] == "#ff0055"
        assert b["colors"]["ink"] == brand.DEFAULTS["colors"]["ink"]          # deep-merged, not replaced
        assert analyze.brand_brief() == "Acme sells rockets to coyotes."
        assert ideas.pillars() == ["Rockets", "Coyotes"]
        from studio.api import app
        info = TestClient(app, base_url="http://127.0.0.1").get("/api/brand").json()
        assert info["org_name"] == "Acme" and "brief" not in info               # brief stays server-side
    finally:
        brand.get.cache_clear()


def test_data_migration_renames_old_angle_field():
    from studio import db
    cid = db.execute("INSERT INTO creators(platform, handle, profile_url, added_at) VALUES('tiktok','m','u',?)", (db.now(),))
    vid = db.execute("INSERT INTO videos(creator_id, platform, platform_id, url, analysis_json, discovered_at) "
                     "VALUES(?, 'tiktok', '1', 'u', ?, ?)", (cid, json.dumps({"accelerator_angle": "old", "topic": "t"}), db.now()))
    db.reset_for_tests()   # next connection re-runs schema + data migrations
    a = json.loads(db.row("SELECT analysis_json FROM videos WHERE id=?", (vid,))["analysis_json"])
    assert a == {"brand_angle": "old", "topic": "t"}
