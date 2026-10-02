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
