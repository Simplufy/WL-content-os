import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from studio import db, jobs, postiz, publishing


class FakePostiz:
    def __init__(self):
        self.calls = []
        self.posts = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.calls.append((req.method, req.url.path))
        assert req.headers["Authorization"] == "k"
        p = req.url.path
        if p.endswith("/upload"):
            return httpx.Response(200, json={"id": "m1", "path": "https://postiz.example/uploads/v.mp4"})
        if p.endswith("/posts") and req.method == "POST":
            body = json.loads(req.content)
            self.last_body = body
            return httpx.Response(200, json=[{"postId": f"p{k}", "integration": c["integration"]["id"]}
                                             for k, c in enumerate(body["posts"])])
        if p.endswith("/posts") and req.method == "GET":
            return httpx.Response(200, json={"posts": self.posts})
        if "/posts/" in p and req.method == "DELETE":
            return httpx.Response(200, json={"id": p.rsplit("/", 1)[1]})
        return httpx.Response(404, json={})


RealClient = postiz.Client


@pytest.fixture
def fake(monkeypatch, tmp_path):
    f = FakePostiz()
    monkeypatch.setattr(postiz, "Client", lambda *a, **k: RealClient(transport=httpx.MockTransport(f.handler),
                                                                      base="https://postiz.example/api/public/v1", key="k"))
    return f


def _edit(tmp_path, idea_id=None):
    w = tmp_path / "edit"
    w.mkdir(exist_ok=True)
    (w / "final.mp4").write_bytes(b"\x00" * 64)
    (w / "words.json").write_text(json.dumps([{"word": "Red", "start": 0, "end": 0.3}, {"word": "means", "start": 0.3, "end": 0.6}]))
    return db.execute(
        "INSERT INTO edit_projects(title, idea_id, source_path, work_dir, status, result_json, render_count, created_at, updated_at) "
        "VALUES('Dash lights', ?, 's', ?, 'ready', ?, 3, ?, ?)",
        (idea_id, str(w), json.dumps({"kept_words": [0, 1]}), db.now(), db.now()))


CH = [{"integration_id": "i-tt", "provider": "tiktok", "name": "Brand TikTok", "text": "Red means stop. #cars", "title": "Dash lights"},
      {"integration_id": "i-yt", "provider": "youtube", "name": "Brand YouTube", "text": "Know your dash. #shorts", "title": "Which dash light means stop?"}]


def _future(h=3):
    return (datetime.now(timezone.utc) + timedelta(hours=h)).isoformat()


def test_build_body_merges_defaults_and_titles():
    b = publishing.build_body("schedule", "2026-10-02T15:00:00+00:00", CH, {"id": "m1", "path": "u"})
    tt, yt = b["posts"]
    assert tt["settings"]["__type"] == "tiktok" and tt["settings"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert tt["settings"]["title"] == "Dash lights"
    assert yt["settings"] == {"__type": "youtube", "title": "Which dash light means stop?", "type": "public",
                              "selfDeclaredMadeForKids": "no", "tags": []}
    assert tt["value"][0]["image"] == [{"id": "m1", "path": "u"}]


def test_create_validates(tmp_path):
    eid = _edit(tmp_path)
    with pytest.raises(ValueError, match="past"):
        publishing.create(eid, CH, when="schedule", date="2020-01-01T00:00:00+00:00")
    with pytest.raises(ValueError, match="Brand rule"):
        publishing.create(eid, [{**CH[0], "text": "Join the program for $497 a month"}], when="schedule", date=_future())
    with pytest.raises(ValueError, match="YouTube needs a title"):
        publishing.create(eid, [{**CH[1], "title": "x"}], when="draft", date=None)
    pid = publishing.create(eid, CH, when="schedule", date=_future())
    assert db.row("SELECT status FROM publications WHERE id=?", (pid,))["status"] == "submitting"
    assert jobs.claim(["publish_submit"])["ref_id"] == pid


def test_submit_sync_and_publish_moves_idea(tmp_path, fake):
    iid = db.execute("INSERT INTO ideas(title, hooks_json, stage, created_at, updated_at) VALUES('t','[]','edited',?,?)", (db.now(), db.now()))
    eid = _edit(tmp_path, iid)
    pid = publishing.create(eid, CH, when="now", date=None)
    publishing.submit(pid)
    p = db.row("SELECT * FROM publications WHERE id=?", (pid,))
    assert p["status"] == "publishing" and json.loads(p["postiz_ids_json"]) == ["p0", "p1"]
    assert [c for c in fake.calls if c[0] == "POST"] == [("POST", "/api/public/v1/upload"), ("POST", "/api/public/v1/posts")]
    assert fake.last_body["type"] == "now" and len(fake.last_body["posts"]) == 2
    assert db.scalar("SELECT stage FROM ideas WHERE id=?", (iid,)) == "scheduled"

    assert publishing.due_for_sync()
    fake.posts = [{"id": "p0", "state": "PUBLISHED", "releaseURL": "https://tiktok.com/x", "integration": {"providerIdentifier": "tiktok", "name": "Brand TikTok"}},
                  {"id": "p1", "state": "QUEUE", "integration": {"providerIdentifier": "youtube"}}]
    publishing.sync()
    assert db.scalar("SELECT status FROM publications WHERE id=?", (pid,)) == "publishing"
    fake.posts[1].update(state="PUBLISHED", releaseURL="https://youtube.com/shorts/y")
    db.execute("UPDATE publications SET last_sync_at = NULL WHERE id=?", (pid,))
    publishing.sync()
    p = db.row("SELECT * FROM publications WHERE id=?", (pid,))
    assert p["status"] == "published" and len(json.loads(p["results_json"])) == 2
    assert db.scalar("SELECT stage FROM ideas WHERE id=?", (iid,)) == "posted"


def test_error_state_and_cancel(tmp_path, fake):
    eid = _edit(tmp_path)
    pid = publishing.create(eid, CH, when="schedule", date=_future(1))
    publishing.submit(pid)
    assert db.scalar("SELECT status FROM publications WHERE id=?", (pid,)) == "scheduled"
    assert not publishing.due_for_sync()              # nothing to check before it's due
    publishing.cancel(pid)
    assert ("DELETE", "/api/public/v1/posts/p0") in fake.calls
    assert db.scalar("SELECT status FROM publications WHERE id=?", (pid,)) == "cancelled"


def test_extract_post_ids_shapes():
    assert postiz.extract_post_ids([{"postId": "a"}, {"postId": "b"}]) == ["a", "b"]
    assert postiz.extract_post_ids({"posts": [{"id": "x", "integration": {"id": "i"}}]}) == ["x"]
