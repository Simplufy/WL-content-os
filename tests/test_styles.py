import json

from fastapi.testclient import TestClient

from studio import db, jobs, llm, styles
from studio.api import app

client = TestClient(app, base_url="http://127.0.0.1")


def test_to_options_maps_every_knob():
    o = styles.to_options({**styles.SIGNATURE["params"], "pace": "rapid", "zoom_mode": "none", "graphics_density": "none",
                           "caption_font": "impact", "color_grade": "moody", "sfx": "off"})
    assert o["max_pause"] == 0.12 and o["zooms"] is False and o["graphics"] is False
    assert o["caption_style"] == "impact" and o["color_grade"] == "moody" and o["sfx"] is False
    assert styles.GRADES[o["color_grade"]].startswith("eq=")


def _fingerprinted(n):
    row = db.row("SELECT id FROM creators WHERE handle='pro'")
    cid = row["id"] if row else db.execute("INSERT INTO creators(platform, handle, profile_url, added_at) VALUES('tiktok','pro','u',?)", (db.now(),))
    start = db.scalar("SELECT COUNT(*) FROM videos")
    ids = []
    for i in range(start, start + n):
        ids.append(db.execute(
            "INSERT INTO videos(creator_id, platform, platform_id, url, status, score, analysis_json, style_json, discovered_at) "
            "VALUES(?, 'tiktok', ?, 'u', 'done', ?, '{}', ?, ?)",
            (cid, str(i), 90 - i, json.dumps({"metrics": {"cuts_per_min": 20 + i}, "look": {"energy": "high"}}), db.now())))
    return ids


def test_synthesize_creates_styles_and_queues_previews(monkeypatch):
    ids = _fingerprinted(6)
    params = {**styles.SIGNATURE["params"], "pace": "fast", "caption_words": 1}
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: {"retire": [], "changelog": "first", "styles": [
        {"existing_id": None, "name": "Rapid Fire", "description": "d", "best_for": "TOFU", "inspired_by": [ids[0], 9999], "params": params},
        {"existing_id": None, "name": "Calm Expert", "description": "d", "best_for": "MOFU", "inspired_by": [ids[1]], "params": {**params, "pace": "relaxed"}}]})
    new = styles.synthesize()
    assert len(new) == 2
    st = db.row("SELECT * FROM edit_styles WHERE id=?", (new[0],))
    assert json.loads(st["inspired_by"]) == [ids[0]] and json.loads(st["params_json"])["caption_words"] == 1
    assert db.scalar("SELECT COUNT(*) FROM jobs WHERE kind='style_preview'") == 2


def test_build_all_waits_for_fingerprints():
    cid = db.execute("INSERT INTO creators(platform, handle, profile_url, added_at) VALUES('tiktok','pro','u',?)", (db.now(),))
    db.execute("INSERT INTO videos(creator_id, platform, platform_id, url, status, score, media_path, discovered_at) "
               "VALUES(?, 'tiktok', 'x', 'u', 'done', 50, '/tmp/x.mp4', ?)", (cid, db.now()))
    res = styles.build_all()
    assert "waiting" in res
    assert db.scalar("SELECT COUNT(*) FROM jobs WHERE kind='style_fingerprint'") == 1
    assert db.scalar("SELECT COUNT(*) FROM jobs WHERE kind='style_build' AND status='pending'") == 1
    assert db.row("SELECT 1 FROM edit_styles WHERE source='builtin'")


def test_api_lists_patches_and_applies_style(tmp_path):
    listing = client.get("/api/styles").json()
    sig = listing["items"][0]
    assert sig["source"] == "builtin" and "pace" in listing["knobs"]
    r = client.patch(f"/api/styles/{sig['id']}", json={"params": {"pace": "fast", "caption_words": 99}})
    assert r.json()["params"]["pace"] == "fast" and r.json()["params"]["caption_words"] == 3   # invalid value ignored
    eid = db.execute("INSERT INTO edit_projects(title, source_path, work_dir, status, options_json, created_at, updated_at) "
                     "VALUES('t','s',?, 'ready', '{}', ?, ?)", (str(tmp_path), db.now(), db.now()))
    out = client.post(f"/api/edits/{eid}/style", json={"style_id": sig["id"]}).json()
    assert out["options"]["style_id"] == sig["id"] and out["options"]["max_pause"] == 0.18
    assert client.post("/api/styles/build").status_code == 409


def test_evolve_keeps_ids_refines_adds_retires(monkeypatch):
    ids = _fingerprinted(6)
    base = {**styles.SIGNATURE["params"], "pace": "fast"}
    keep = db.execute("INSERT INTO edit_styles(name, description, params_json, inspired_by, source, preview_status, created_at, updated_at) "
                      "VALUES('Rapid Fire','d',?, '[]','library','ready',?,?)", (json.dumps(base), db.now(), db.now()))
    stale = db.execute("INSERT INTO edit_styles(name, description, params_json, inspired_by, source, preview_status, created_at, updated_at) "
                       "VALUES('Old Thing','d',?, '[]','library','ready',?,?)", (json.dumps(base), db.now(), db.now()))
    db.execute("INSERT INTO edit_styles(name, description, params_json, source, archived, retired_by, created_at, updated_at) "
               "VALUES('Silent Skit','d','{}','library',1,'owner',?,?)", (db.now(), db.now()))
    seen = {}

    def fake(prompt, schema, **k):
        seen["prompt"] = prompt
        return {"changelog": "c", "retire": [{"id": stale, "reason": "faded"}], "styles": [
            {"existing_id": keep, "name": "Rapid Fire", "description": "d2", "best_for": "", "inspired_by": [ids[0]],
             "params": {**base, "zoom_strength": "strong"}},
            {"existing_id": None, "name": "Split Board", "description": "n", "best_for": "", "inspired_by": [], "params": base}]}
    monkeypatch.setattr(llm, "ask_json", fake)
    touched = styles.synthesize()
    assert keep in touched and len(touched) == 2
    assert "Silent Skit" in seen["prompt"]                                   # owner-removed style is off-limits
    k = db.row("SELECT * FROM edit_styles WHERE id=?", (keep,))
    assert k["archived"] == 0 and json.loads(k["params_json"])["zoom_strength"] == "strong" and k["preview_status"] == "pending"
    s_ = db.row("SELECT * FROM edit_styles WHERE id=?", (stale,))
    assert s_["archived"] == 1 and s_["retired_by"] == "evolve"
    assert db.scalar("SELECT COUNT(*) FROM jobs WHERE kind='style_preview'") == 2   # refined + new
    assert db.get_setting("styles_last_build")["added"] == 1


def test_auto_rebuild_needs_ten_new_fingerprints():
    assert styles.maybe_auto_rebuild() is False                               # never built: owner starts the first build
    db.set_setting("styles_last_build", {"at": "2020-01-01T00:00:00+00:00", "fingerprints": 0})
    _fingerprinted(9)
    assert styles.maybe_auto_rebuild() is False
    _fingerprinted(1)
    assert styles.maybe_auto_rebuild() is True
    db.set_setting("auto_styles", False)
    assert styles.maybe_auto_rebuild() is False
