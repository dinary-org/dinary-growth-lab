import json
from pathlib import Path

import pytest

from dgl.sources.instagram_apify import InstagramApifySource
from dgl.sources.youtube import QuotaBudget, QuotaExceeded, YouTubeSource, parse_iso8601_duration

FIX = json.loads((Path(__file__).parent / "fixtures" / "youtube_videos_list.json").read_text())


@pytest.mark.parametrize("s,expected", [("PT45S", 45), ("PT1M2S", 62), ("PT12M3S", 723), ("P1DT1H", 90000), ("PT0.5S", 0.5),
                                        (None, None), ("garbage", None)])
def test_duration(s, expected):
    assert parse_iso8601_duration(s) == expected


def test_youtube_rows_and_missing_metrics():
    video, snap = YouTubeSource.to_rows(FIX["items"][0], "chart:test")
    assert video["url"] == "https://www.youtube.com/shorts/TESTID00001"
    assert video["hashtags"] == ["shorts", "둘째태그", "테스트태그"]
    assert snap["views"] == 1200 and snap["likes"] == 30
    assert "shares" in snap["missing"] and "views" not in snap["missing"]
    long_video, long_snap = YouTubeSource.to_rows(FIX["items"][1], "x")
    assert long_video["url"].endswith("watch?v=TESTID00002")
    assert long_snap["likes"] is None and "likes" in long_snap["missing"]


def test_quota_budget_blocks_before_overspend():
    b = QuotaBudget(limit=150)
    b.spend("search")
    with pytest.raises(QuotaExceeded):
        b.spend("search")
    b.spend("videos")
    assert b.used == 101


def test_youtube_requires_key():
    with pytest.raises(RuntimeError):
        YouTubeSource("", QuotaBudget(10))


def test_apify_mapping_handles_hidden_likes():
    rows = InstagramApifySource.to_rows({"shortCode": "ABC", "caption": "첫줄 #태그\n둘째", "ownerUsername": "u",
                                         "timestamp": "2026-10-30T00:00:00Z", "videoPlayCount": 5000,
                                         "likesCount": -1, "commentsCount": 3}, "apify:test")
    video, snap = rows
    assert video["url"] == "https://www.instagram.com/reel/ABC/"
    assert video["hashtags"] == ["태그"]
    assert snap["views"] == 5000 and snap["likes"] is None and "likes" in snap["missing"]
    assert InstagramApifySource.to_rows({"caption": "no code"}, "x") is None
