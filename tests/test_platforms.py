import pytest

from studio.platforms import normalize_entry, parse_profile_url


@pytest.mark.parametrize("raw,platform,handle,url", [
    ("https://www.tiktok.com/@detailking?lang=en", "tiktok", "detailking", "https://www.tiktok.com/@detailking"),
    ("tiktok.com/@detail.king", "tiktok", "detail.king", "https://www.tiktok.com/@detail.king"),
    ("https://youtube.com/@AlexHormozi/shorts", "youtube", "AlexHormozi", "https://www.youtube.com/@AlexHormozi"),
    ("https://m.youtube.com/@AlexHormozi", "youtube", "AlexHormozi", "https://www.youtube.com/@AlexHormozi"),
    ("https://www.youtube.com/channel/UCabc", "youtube", "UCabc", "https://www.youtube.com/channel/UCabc"),
    ("https://www.instagram.com/someone/", "instagram", "someone", "https://www.instagram.com/someone/"),
    ("instagram.com/someone/reels", "instagram", "someone", "https://www.instagram.com/someone/"),
])
def test_parse_profiles(raw, platform, handle, url):
    p = parse_profile_url(raw)
    assert (p.platform, p.handle, p.profile_url) == (platform, handle, url)


@pytest.mark.parametrize("raw", [
    "https://www.tiktok.com/@x/video/123",
    "https://www.youtube.com/watch?v=abc",
    "https://www.youtube.com/shorts/abc",
    "https://youtu.be/abc",
    "https://www.instagram.com/reel/abc/",
    "https://www.instagram.com/p/abc/",
    "https://example.com/@x",
    "",
])
def test_rejects_non_profiles(raw):
    with pytest.raises(ValueError):
        parse_profile_url(raw)


def test_normalize_tiktok_flat_entry():
    v = normalize_entry("tiktok", "x", {"id": "1", "url": "https://www.tiktok.com/@x/video/1", "view_count": 100,
                                        "like_count": 10, "comment_count": 2, "repost_count": 1, "save_count": 3,
                                        "timestamp": 1790805450, "duration": 30, "title": "t"})
    assert v["views"] == 100 and v["saves"] == 3 and v["published_at"].startswith("2026-")


def test_normalize_youtube_builds_url():
    v = normalize_entry("youtube", "x", {"id": "abc", "url": "abc", "view_count": 5})
    assert v["url"] == "https://www.youtube.com/watch?v=abc"
    assert v["published_at"] is None
