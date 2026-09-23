from __future__ import annotations

import pytest
from app.plugins.live_source import LiveStatus, SourceTemporaryError
from conftest import page
from douyin_live.parser import parse_room


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (2, LiveStatus.LIVE),
        (4, LiveStatus.OFFLINE),
        (0, LiveStatus.UNKNOWN),
        (None, LiveStatus.UNKNOWN),
        ("2", LiveStatus.UNKNOWN),
        (True, LiveStatus.UNKNOWN),
    ],
)
def test_only_explicit_numeric_status_changes_live_state(status: object, expected: LiveStatus) -> None:
    result = parse_room(page(status=status), "123abc")
    assert result.status == expected
    assert result.title == "测试直播间" and result.uploader_name == "测试主播"
    assert list(result.flv_urls) == ["full_hd1"]


def test_room_without_details_is_unknown() -> None:
    result = parse_room(page(room_exists=False), "123abc")
    assert result.status == LiveStatus.UNKNOWN
    assert result.title is None and result.uploader_name is None


@pytest.mark.parametrize("html", ["<html>challenge</html>", '<script>self.__pace_f.push([1,"a:{bad"])</script>'])
def test_unrecognized_page_does_not_mean_offline(html: str) -> None:
    with pytest.raises(SourceTemporaryError):
        parse_room(html, "123abc")


def test_last_snapshot_wins_without_using_older_live_data() -> None:
    result = parse_room(page() + page(status=4), "123abc")
    assert result.status == LiveStatus.OFFLINE


def test_mismatched_web_rid_is_rejected() -> None:
    with pytest.raises(SourceTemporaryError, match="不同的房间"):
        parse_room(page(source_id="different"), "123abc")


def test_stream_quality_collision_is_rejected() -> None:
    with pytest.raises(SourceTemporaryError):
        parse_room(page(urls={"HD1": "https://cdn.example/a", "hd1": "https://cdn.example/b"}), "123abc")
