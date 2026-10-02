from studio import db, scoring


def _creator():
    return db.execute("INSERT INTO creators(platform, handle, profile_url, added_at) VALUES('tiktok','a','u',?)", (db.now(),))


def _video(cid, pid, views, likes=0, published="2026-01-01T00:00:00+00:00"):
    return db.execute(
        "INSERT INTO videos(creator_id, platform, platform_id, url, views, likes, comments, shares, saves, published_at, discovered_at) "
        "VALUES(?, 'tiktok', ?, 'u', ?, ?, 0, 0, 0, ?, ?)", (cid, pid, views, likes, published, db.now()))


def test_log_scale_and_composite():
    assert scoring._log_scale(1) == 50
    assert scoring._log_scale(2) == 75
    assert scoring._log_scale(16) == 100
    assert scoring.composite(None, None, None) is None
    assert scoring.composite(2, None, None) is None  # unanalyzed posts get no overall score
    assert scoring.composite(2, None, 50) == round((0.5 * 75 + 0.3 * 50) / 0.8, 1)
    assert scoring.composite(1, 1, 80) == round((0.5 * 50 + 0.2 * 50 + 0.3 * 80) / 1.0, 1)


def test_outlier_against_median_of_others():
    cid = _creator()
    for i, v in enumerate([1000, 1000, 2000, 1000]):
        _video(cid, str(i), v, likes=v // 10)
    hit = _video(cid, "hit", 6000, likes=1200)
    res = scoring.rescore_video(hit)
    assert res["outlier"] == 6.0
    assert res["engagement_rate"] == 20.0
    assert res["engagement_rel"] == 2.0


def test_no_baseline_with_too_few_posts():
    cid = _creator()
    _video(cid, "1", 100)
    only = _video(cid, "2", 500)
    assert scoring.rescore_video(only)["outlier"] is None


def test_baseline_is_per_kind_and_skips_fresh_unknown_age():
    cid = _creator()
    for i, v in enumerate([1000, 1000, 1000, 1000]):
        _video(cid, f"s{i}", v)
    db.execute("INSERT INTO videos(creator_id, platform, platform_id, url, kind, views, discovered_at) VALUES(?, 'youtube', 'L1', 'u', 'long', 900000, ?)", (cid, db.now()))
    for i, v in enumerate([10, 20, 30]):  # newest in listing, date unknown -> excluded
        db.execute("INSERT INTO videos(creator_id, platform, platform_id, url, views, list_pos, discovered_at) VALUES(?, 'tiktok', ?, 'u', ?, ?, ?)", (cid, f"n{i}", v, i, db.now()))
    assert scoring.creator_baseline(cid)["median_views"] == 1000
    assert scoring.creator_baseline(cid, kind="long")["median_views"] is None
