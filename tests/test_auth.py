import pytest
from fastapi.testclient import TestClient

from studio import auth, db
from studio.api import app

REMOTE = {"cf-connecting-ip": "203.0.113.9", "host": "studio.example.com"}


@pytest.fixture
def local():
    return TestClient(app, base_url="http://127.0.0.1")


@pytest.fixture
def remote():
    return TestClient(app, base_url="https://studio.example.com", headers={"cf-connecting-ip": "203.0.113.9"})


@pytest.fixture(autouse=True)
def reset_attempts():
    auth._attempts.clear()


def test_is_local():
    assert auth.is_local("127.0.0.1:8794", {})
    assert auth.is_local("localhost", {})
    assert not auth.is_local("127.0.0.1:8794", {"cf-connecting-ip": "1.2.3.4"})
    assert not auth.is_local("studio.example.com", {})


def test_password_hashing():
    h = auth.hash_password("correct horse battery")
    assert auth.verify_password("correct horse battery", h)
    assert not auth.verify_password("wrong", h)
    with pytest.raises(ValueError):
        auth.create_user("a@b.co", "short")


def test_remote_requires_login_local_does_not(local, remote):
    assert local.get("/api/dashboard").status_code == 200
    assert remote.get("/api/dashboard").status_code == 401
    assert remote.get("/api/health").status_code == 200
    me = remote.get("/api/auth/me").json()
    assert me["user"] is None and me["local"] is False
    assert remote.get("/").status_code in (200, 503)   # the SPA shell itself carries no data


def test_first_user_from_local_then_remote_login(local, remote):
    assert remote.post("/api/auth/users", json={"email": "x@y.co", "password": "longenough1"}).status_code == 401
    r = local.post("/api/auth/users", json={"email": "Owner@DDA.com", "password": "longenough1"})
    assert r.status_code == 201
    assert auth.list_users()[0]["is_admin"] == 1          # first account is admin
    assert remote.post("/api/auth/login", json={"email": "owner@dda.com", "password": "nope-nope-1"}).status_code == 401
    r = remote.post("/api/auth/login", json={"email": "owner@dda.com", "password": "longenough1"})
    assert r.status_code == 200
    ck = r.headers["set-cookie"].lower()
    assert "httponly" in ck and "secure" in ck and "samesite=lax" in ck
    assert remote.get("/api/dashboard").status_code == 200
    assert remote.get("/api/auth/me").json()["user"]["email"] == "owner@dda.com"
    remote.post("/api/auth/logout")
    assert remote.get("/api/dashboard").status_code == 401


def test_cross_site_post_blocked(local, remote):
    local.post("/api/auth/users", json={"email": "o@d.co", "password": "longenough1"})
    remote.post("/api/auth/login", json={"email": "o@d.co", "password": "longenough1"})
    r = remote.post("/api/creators", json={"url": "tiktok.com/@x"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    r = remote.post("/api/creators", json={"url": "tiktok.com/@x"}, headers={"origin": "https://studio.example.com"})
    assert r.status_code == 201


def test_login_rate_limited(remote):
    codes = [remote.post("/api/auth/login", json={"email": "a@b.co", "password": "whatever123"}).status_code for _ in range(12)]
    assert codes[-1] == 429


def test_import_is_local_only(local, remote):
    local.post("/api/auth/users", json={"email": "o@d.co", "password": "longenough1"})
    remote.post("/api/auth/login", json={"email": "o@d.co", "password": "longenough1"})
    assert remote.post("/api/edits/import", json={"path": "~/Downloads/x.mov"}).status_code == 403


def test_chunked_upload(local, monkeypatch):
    from studio import editing
    created = {}
    monkeypatch.setattr(editing, "create", lambda src, **kw: created.setdefault("pid", db.execute(
        "INSERT INTO edit_projects(title, source_path, work_dir, status, created_at, updated_at) VALUES(?,?,?, 'queued', ?, ?)",
        (kw.get("title"), str(src), str(src.parent), db.now(), db.now()))))
    data = b"a" * 1000 + b"b" * 500
    up = local.post("/api/uploads", json={"filename": "clip.MOV", "size": len(data)}).json()
    assert local.put(f"/api/uploads/{up['id']}?offset=0", content=data[:1000]).json()["received"] == 1000
    assert local.put(f"/api/uploads/{up['id']}?offset=0", content=data[:1000]).json()["received"] == 1000   # retry is a no-op
    assert local.post(f"/api/uploads/{up['id']}/complete", json={}).status_code == 409                        # incomplete
    assert local.put(f"/api/uploads/{up['id']}?offset=2000", content=b"x").status_code == 409                # gap
    local.put(f"/api/uploads/{up['id']}?offset=1000", content=data[1000:])
    r = local.post(f"/api/uploads/{up['id']}/complete", json={"title": "Raw"})
    assert r.status_code == 201 and r.json()["title"] == "Raw"
    assert local.post("/api/uploads", json={"filename": "x.exe", "size": 5}).status_code == 400
