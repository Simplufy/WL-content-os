from fastapi.testclient import TestClient

from studio import db
from studio.api import app

client = TestClient(app, base_url="http://127.0.0.1")


def test_add_creator_validates_and_dedupes():
    r = client.post("/api/creators", json={"url": "https://www.tiktok.com/@x/video/1"})
    assert r.status_code == 400
    r = client.post("/api/creators", json={"url": "tiktok.com/@detailking"})
    assert r.status_code == 201 and r.json()["handle"] == "detailking"
    assert client.post("/api/creators", json={"url": "https://www.tiktok.com/@DetailKing"}).status_code == 409
    assert db.scalar("SELECT COUNT(*) FROM jobs WHERE kind='check_creator'") == 1


def test_instagram_warns_without_cookies():
    r = client.post("/api/creators", json={"url": "instagram.com/someone"})
    assert r.status_code == 201 and "cookies" in r.json()["warning"]


def test_dashboard_and_lists_empty():
    assert client.get("/api/dashboard").json()["kpis"]["creators"] == 0
    assert client.get("/api/videos").json() == {"total": 0, "items": []}
    assert client.get("/api/hooks").json() == []
    assert client.get("/api/api/nothing").status_code == 404


def test_cookie_upload_validation():
    bad = client.post("/api/settings/cookies/instagram", files={"file": ("c.txt", b"not cookies")})
    assert bad.status_code == 400
    good = ".instagram.com\tTRUE\t/\tTRUE\t1999999999\tsessionid\tabc\n"
    r = client.post("/api/settings/cookies/instagram", files={"file": ("c.txt", good.encode())})
    assert r.status_code == 200 and r.json()["cookies"]["instagram"] is True
    assert client.delete("/api/settings/cookies/instagram").json()["cookies"]["instagram"] is False


def test_chunked_upload_resumes():
    data = b"x" * 1000
    uid = client.post("/api/uploads", json={"filename": "clip.mov", "size": len(data)}).json()["id"]
    assert client.get(f"/api/uploads/{uid}").json()["received"] == 0
    assert client.put(f"/api/uploads/{uid}?offset=0", content=data[:600]).json()["received"] == 600
    # a dropped connection: the client asks what landed and continues from there
    st = client.get(f"/api/uploads/{uid}").json()
    assert st["received"] == 600 and st["size"] == 1000
    assert client.put(f"/api/uploads/{uid}?offset=0", content=data[:600]).json()["received"] == 600  # resent piece is a no-op
    assert client.put(f"/api/uploads/{uid}?offset=600", content=data[600:]).json()["received"] == 1000
    assert client.get("/api/uploads/nope").status_code == 404
