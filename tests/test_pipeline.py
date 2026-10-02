import json

from studio import db, jobs, pipeline, platforms


def _listing(n, start=0):
    return {"info": {"display_name": "Detail King", "followers": 1234},
            "videos": [{"platform_id": str(i), "url": f"https://www.tiktok.com/@dk/video/{i}", "title": f"v{i}",
                        "description": None, "published_at": "2026-09-01T00:00:00+00:00", "duration": 30,
                        "thumbnail_url": None, "views": 1000 * (i + 1), "likes": 50, "comments": 5, "shares": 1, "saves": 2}
                       for i in range(start, start + n)]}


def test_first_check_backfills_then_detects_new(monkeypatch):
    cid = db.execute("INSERT INTO creators(platform, handle, profile_url, added_at) VALUES('tiktok','dk','https://www.tiktok.com/@dk',?)", (db.now(),))
    monkeypatch.setattr(platforms, "list_profile", lambda *a, **k: _listing(15))
    res = pipeline.check_creator(cid)
    assert res == {"found": 15, "new": 15, "known": 0, "skipped_old": 0}
    c = db.row("SELECT * FROM creators WHERE id=?", (cid,))
    assert c["baseline_done"] == 1 and c["display_name"] == "Detail King"
    queued = db.scalar("SELECT COUNT(*) FROM jobs WHERE kind='process_video'")
    assert queued == 11  # top 8 by views + newest 3 (no overlap here)
    assert db.scalar("SELECT COUNT(*) FROM videos WHERE is_new=1") == 0

    # second check: two brand-new posts appear, existing metrics update
    listing = _listing(15)
    fresh = _listing(2, start=100)["videos"]
    for v in fresh:
        v["published_at"] = db.now()
    listing["videos"] = fresh + listing["videos"]
    listing["videos"][2]["views"] = 999999
    monkeypatch.setattr(platforms, "list_profile", lambda *a, **k: listing)
    res = pipeline.check_creator(cid)
    assert res["new"] == 2
    new = db.rows("SELECT id FROM videos WHERE is_new=1")
    assert len(new) == 2
    for v in new:
        assert db.row("SELECT priority FROM jobs WHERE kind='process_video' AND ref_id=?", (v["id"],))["priority"] == 10
    assert db.scalar("SELECT views FROM videos WHERE platform_id='0'") == 999999
    assert db.scalar("SELECT COUNT(*) FROM metric_snapshots WHERE video_id=(SELECT id FROM videos WHERE platform_id='0')") == 2


def test_check_failure_recorded(monkeypatch):
    cid = db.execute("INSERT INTO creators(platform, handle, profile_url, added_at) VALUES('instagram','x','u',?)", (db.now(),))

    def boom(*a, **k):
        raise platforms.PlatformError("Instagram blocked")
    monkeypatch.setattr(platforms, "list_profile", boom)
    try:
        pipeline.check_creator(cid)
    except platforms.PlatformError:
        pass
    c = db.row("SELECT * FROM creators WHERE id=?", (cid,))
    assert c["last_check_status"] == "error" and "blocked" in c["last_check_error"]


def test_analyze_stores_hook_and_score(monkeypatch):
    cid = db.execute("INSERT INTO creators(platform, handle, profile_url, added_at) VALUES('tiktok','dk','u',?)", (db.now(),))
    vid = db.execute("INSERT INTO videos(creator_id, platform, platform_id, url, views, discovered_at, words_json, transcript_json, frames_json) "
                     "VALUES(?, 'tiktok', '1', 'u', 100, ?, '[]', '[]', '[]')", (cid, db.now()))
    from studio import analyze
    monkeypatch.setattr(analyze, "analyze", lambda *a: {"hook": {"score": 82, "type": "bold_claim"}, "topic": "pricing"})
    pipeline.analyze_video(vid)
    v = db.row("SELECT * FROM videos WHERE id=?", (vid,))
    assert v["status"] == "done" and v["hook_score"] == 82 and v["score"] == 82.0
    assert json.loads(v["analysis_json"])["topic"] == "pricing"


def test_new_posts_only_skips_old_posts_that_surface_later(monkeypatch):
    cid = db.execute("INSERT INTO creators(platform, handle, profile_url, added_at) VALUES('tiktok','dk','u',?)", (db.now(),))
    monkeypatch.setattr(platforms, "list_profile", lambda *a, **k: _listing(5))
    pipeline.check_creator(cid)
    old_surfaced = _listing(1, start=50)["videos"]            # published 2026-09-01, before the backfill
    brand_new = _listing(1, start=60)["videos"]
    brand_new[0]["published_at"] = db.now()
    listing = _listing(5)
    listing["videos"] = brand_new + listing["videos"] + old_surfaced
    monkeypatch.setattr(platforms, "list_profile", lambda *a, **k: listing)
    res = pipeline.check_creator(cid)
    assert res["new"] == 1 and res["skipped_old"] == 1
    assert db.scalar("SELECT COUNT(*) FROM videos WHERE platform_id='50'") == 0
    db.set_setting("new_posts_only", False)
    res = pipeline.check_creator(cid)
    assert res["new"] == 1 and db.scalar("SELECT COUNT(*) FROM videos WHERE platform_id='50'") == 1


def test_is_newly_posted_without_dates_uses_list_position():
    c = {"baseline_at": "2026-10-01T00:00:00+00:00"}
    assert pipeline.is_newly_posted({"published_at": None, "list_pos": 0, "kind": "short"}, c, {"short": 1})
    assert not pipeline.is_newly_posted({"published_at": None, "list_pos": 7, "kind": "short"}, c, {"short": 1})
    assert pipeline.is_newly_posted({"published_at": "2026-10-02T09:00:00+00:00", "list_pos": 9}, c, {"short": 0})


def test_backfill_counts_from_settings(monkeypatch):
    db.set_setting("backfill_top", 2)
    db.set_setting("backfill_newest", 1)
    cid = db.execute("INSERT INTO creators(platform, handle, profile_url, added_at) VALUES('tiktok','dk','u',?)", (db.now(),))
    monkeypatch.setattr(platforms, "list_profile", lambda *a, **k: _listing(10))
    pipeline.check_creator(cid)
    assert db.scalar("SELECT COUNT(*) FROM jobs WHERE kind='process_video'") == 3
