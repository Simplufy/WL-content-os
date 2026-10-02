import json

import pytest
from fastapi.testclient import TestClient

from studio import db, ideas, jobs, llm
from studio.api import app

client = TestClient(app, base_url="http://127.0.0.1")

ANALYSIS = {"hook": {"spoken": "Stop pricing by the hour", "onscreen_text": "", "type": "contrarian", "score": 80,
                     "template": "Stop [habit] if you want [result]"},
            "format": "talking head", "topic": "pricing", "angle": "value pricing", "beats": [{"label": "setup"}],
            "relevance": 9, "brand_angle": "..."}


def _library(n=2, transcript="you need to raise your prices today because the market will pay for real quality work"):
    cid = db.execute("INSERT INTO creators(platform, handle, profile_url, added_at) VALUES('tiktok','pro','u',?)", (db.now(),))
    ids = []
    for i in range(n):
        ids.append(db.execute(
            "INSERT INTO videos(creator_id, platform, platform_id, url, views, status, score, outlier, analysis_json, "
            "transcript_text, processed_at, discovered_at) VALUES(?, 'tiktok', ?, 'u', 1000, 'done', 70, 3, ?, ?, ?, ?)",
            (cid, str(i), json.dumps(ANALYSIS), transcript, db.now(), db.now())))
    return ids


def _concept(vid):
    return {"title": "Price the job not the hour", "pillar": "Pricing & profit", "format": "talking head",
            "angle": "a", "why": "w", "inspired_by": [vid, 9999],
            "hooks": [{"text": "h1", "onscreen_text": "", "type": "contrarian", "source_video_id": vid},
                      {"text": "h2", "onscreen_text": "", "type": "question", "source_video_id": 12345},
                      {"text": "h3", "onscreen_text": "", "type": "story", "source_video_id": None}]}


def test_batch_creates_ideas_and_drops_unknown_video_ids(monkeypatch):
    vids = _library()
    seen = {}

    def fake(prompt, schema, **kw):
        seen["prompt"] = prompt
        return {"concepts": [_concept(vids[0]), _concept(vids[1])]}
    monkeypatch.setattr(llm, "ask_json", fake)
    bid = ideas.create_batch(2, focus="pricing")
    assert jobs.claim(["generate_concepts"])["ref_id"] == bid
    created = ideas.run_batch(bid)
    assert len(created) == 2 and "pricing" in seen["prompt"]
    i = db.row("SELECT * FROM ideas WHERE id=?", (created[0],))
    assert json.loads(i["inspired_by"]) == [vids[0]]
    hooks = json.loads(i["hooks_json"])
    assert [h["source_video_id"] for h in hooks] == [vids[0], None, None]
    assert db.row("SELECT status FROM idea_batches WHERE id=?", (bid,))["status"] == "ready"


def test_batch_without_library_fails():
    bid = ideas.create_batch(3)
    with pytest.raises(RuntimeError):
        ideas.run_batch(bid)


def test_write_script_numbers_lines_moves_stage_and_flags_copying(monkeypatch):
    vids = _library()
    iid = db.execute("INSERT INTO ideas(title, hooks_json, chosen_hook, inspired_by, created_at, updated_at) VALUES('t', ?, 0, ?, ?, ?)",
                     (json.dumps([{"text": "h"}]), json.dumps(vids), db.now(), db.now()))
    script = {"hook": {"spoken": "Here's the thing nobody tells you", "onscreen_text": "", "visual": ""},
              "lines": [{"section": "setup", "text": "Most shops undercharge.", "onscreen_text": "", "broll": ""},
                        {"section": "value", "text": "Honestly the market will pay for real quality work if you show it.", "onscreen_text": "", "broll": ""}],
              "caption": "c", "hashtags": [], "duration_s": 20, "filming_notes": ""}
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: json.loads(json.dumps(script)))
    out = ideas.write_script(iid)
    assert [l["id"] for l in out["lines"]] == ["L1", "L2"]
    i = db.row("SELECT * FROM ideas WHERE id=?", (iid,))
    assert i["stage"] == "scripted" and i["script_status"] == "ready" and i["script_version"] == 1
    orig = json.loads(i["originality_json"])
    assert not orig["ok"] and orig["matches"][0]["line"] == "L2"


def test_originality_clean_script():
    _library()
    res = ideas.check_originality({"hook": {"spoken": "Three numbers every detailer should know"},
                                   "lines": [{"id": "L1", "text": "Start with your real cost per car."}]})
    assert res["ok"] and res["checked_against"] == 2


def test_auto_batch_needs_fresh_teardowns():
    assert ideas.maybe_auto_batch() is None
    _library(3)
    bid = ideas.maybe_auto_batch()
    assert bid is not None
    assert ideas.maybe_auto_batch() is None  # one pending at a time / once a day
    db.set_setting("auto_ideas", False)
    db.execute("DELETE FROM idea_batches")
    assert ideas.maybe_auto_batch() is None


def test_api_flow():
    assert client.post("/api/ideas/generate", json={"count": 3}).status_code == 409
    _library()
    assert client.post("/api/ideas/generate", json={"count": 3, "focus": "hiring"}).status_code == 202
    r = client.post("/api/ideas", json={"title": "My own idea"})
    iid = r.json()["id"]
    assert client.patch(f"/api/ideas/{iid}", json={"stage": "nope"}).status_code == 400
    assert client.patch(f"/api/ideas/{iid}", json={"stage": "filmed", "starred": True}).json()["starred"] == 1
    assert client.post(f"/api/ideas/{iid}/script", json={"instruction": "make it 90s"}).status_code == 202
    j = db.row("SELECT payload FROM jobs WHERE kind='write_script' AND ref_id=?", (iid,))
    assert json.loads(j["payload"]) == {"instruction": "make it 90s"}
    bad = client.put(f"/api/ideas/{iid}/script", json={"script": {"lines": []}})
    assert bad.status_code == 400
    ok = client.put(f"/api/ideas/{iid}/script", json={"script": {"hook": {"spoken": "a b"}, "lines": [{"text": "c"}]}})
    assert ok.json()["script"]["lines"][0]["id"] == "L1"
    listing = client.get("/api/ideas").json()
    assert listing["library_size"] == 2 and len(listing["batches"]) == 1


def _script(*lines, caption=""):
    return {"hook": {"spoken": lines[0], "onscreen_text": ""},
            "lines": [{"id": f"L{k}", "text": t, "onscreen_text": ""} for k, t in enumerate(lines[1:], 1)], "caption": caption}


def test_brand_rules_allow_real_proof_numbers():
    s = _script("We went from 60 hours a week to under 15.",
                "We do about $4M a year, and the coaching program is how we teach it.",
                "Your $89 service is underpriced.", caption="Join the waitlist.")
    assert ideas.check_brand_rules(s) == []


def test_brand_rules_flag_offer_pricing_and_banned_terms():
    s = _script("This is guaranteed results.",
                "The program is only $497 a month.",
                "Sign up now.", caption="Limited time: 20% off!")
    terms = {f["term"] for f in ideas.check_brand_rules(s)}
    assert {"guaranteed results", "price", "limited time", "% off"} <= terms


def test_banned_terms_are_configurable():
    db.set_setting("banned_terms", [["acme crm", "Never name the software"]])
    assert ideas.check_brand_rules(_script("Acme CRM does your reporting."))[0]["term"] == "acme crm"


def test_regex_banned_terms():
    db.set_setting("banned_terms", [["re:\\bVAs?\\b", "Local hires, not VAs"]])
    assert ideas.check_brand_rules(_script("Hire a VA for the phones."))[0]["term"] == "VA"
    assert ideas.check_brand_rules(_script("The value is in the system.")) == []
