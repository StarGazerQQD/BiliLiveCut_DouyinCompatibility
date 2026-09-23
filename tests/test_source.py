from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
from app.plugins import PluginContext
from app.plugins.live_source import (
    LiveStatus,
    SourceAuthenticationError,
    SourceInvalidInput,
    SourceRateLimited,
    SourceRoom,
    SourceTemporaryError,
    SourceUnavailable,
    StreamPreference,
)
from conftest import page
from douyin_live.config import PAGE_LIMIT
from douyin_live.source import DouyinSource


@pytest.mark.parametrize(
    "value", ["123abc", "https://live.douyin.com/123abc", "http://live.douyin.com/123abc/?a=b#fragment"]
)
async def test_room_identity_uses_web_rid_without_network(context: PluginContext, value: str) -> None:
    def no_network(request: httpx.Request) -> httpx.Response:
        pytest.fail("规范房间解析不应访问网络")

    source = DouyinSource(context, transport=httpx.MockTransport(no_network))
    try:
        room = await source.resolve_room(value)
        assert room == SourceRoom(platform="douyin", source_id="123abc", canonical_url="https://live.douyin.com/123abc")
    finally:
        await source.aclose()


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.example/123abc",
        "https://live.douyin.com.evil.example/123abc",
        "https://user:password@live.douyin.com/123abc",
        "https://live.douyin.com:8080/123abc",
        "https://live.douyin.com/a/b",
        "https://live.douyin.com/%2e%2e",
        "file:///test",
        "https://live.douyin.com/room\nHeader:x",
        "分享文案 https://live.douyin.com/123abc",
        "",
    ],
)
async def test_invalid_inputs_are_rejected(context: PluginContext, value: str) -> None:
    source = DouyinSource(context)
    try:
        with pytest.raises(SourceInvalidInput):
            await source.resolve_room(value)
    finally:
        await source.aclose()


async def test_short_link_validates_every_hop_and_does_not_send_cookie(
    context: PluginContext,
    values: dict[str, str | float | bool | None],
) -> None:
    values["cookie"] = "sessionid=private-secret"
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "v.douyin.com":
            return httpx.Response(302, headers={"location": "https://live.douyin.com/123abc?from=share"})
        return httpx.Response(200, text=page())

    source = DouyinSource(context, transport=httpx.MockTransport(respond))
    try:
        room = await source.resolve_room("https://v.douyin.com/short/")
        assert room.source_id == "123abc"
        assert not requests[0].headers["cookie"]
        assert "private-secret" in requests[1].headers["cookie"]
    finally:
        await source.aclose()


@pytest.mark.parametrize(
    "location", ["https://evil.example/a", "http://127.0.0.1/", "https://webcast.amemv.com/douyin/webcast/reflow/123"]
)
async def test_unsupported_redirect_is_not_requested(context: PluginContext, location: str) -> None:
    seen: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": location})

    source = DouyinSource(context, transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(SourceInvalidInput):
            await source.resolve_room("https://v.douyin.com/short/")
        assert len(seen) == 1
    finally:
        await source.aclose()


async def test_redirect_loop_is_bounded(context: PluginContext) -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"location": str(request.url)})

    source = DouyinSource(context, transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(SourceInvalidInput):
            await source.resolve_room("https://v.douyin.com/loop/")
        assert calls == 5
    finally:
        await source.aclose()


async def test_streams_refresh_signatures_keep_identity_and_never_forward_cookies(
    context: PluginContext,
    values: dict[str, str | float | bool | None],
) -> None:
    values["cookie"] = "sessionid=first-secret"
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=page(
                session_id=f"session-{len(requests)}",
                urls={"hd1": f"https://cdn.example/stream.flv?signature={len(requests)}"},
            ),
            headers={"set-cookie": "stale=secret; Domain=.douyin.com; Path=/"},
        )

    source = DouyinSource(context, transport=httpx.MockTransport(respond))
    try:
        room = await source.resolve_room("123abc")
        first = await source.get_streams(room, StreamPreference())
        values["cookie"] = "sessionid=second-secret"
        second = await source.get_streams(room, StreamPreference())
        values["cookie"] = ""
        await source.get_room_info(room)
        assert first[0].url != second[0].url
        assert room.source_id == "123abc"
        assert all("cookie" not in {k.lower() for k in stream.headers} for stream in first + second)
        assert "first-secret" in requests[0].headers["cookie"]
        assert "second-secret" in requests[1].headers["cookie"]
        assert "secret" not in requests[2].headers["cookie"]
        assert "signature" not in repr(first[0])
    finally:
        await source.aclose()


@pytest.mark.parametrize(("intent", "quality"), [("best", "full_hd1"), ("balanced", "sd2"), ("data_saver", "sd1")])
async def test_quality_order_and_flv_fallback(context: PluginContext, intent: str, quality: str) -> None:
    urls = {name: f"https://cdn.example/{name}.flv" for name in ("sd1", "full_hd1", "hd1", "sd2")}
    source = DouyinSource(context, transport=httpx.MockTransport(lambda _: httpx.Response(200, text=page(urls=urls))))
    try:
        streams = await source.get_streams(
            await source.resolve_room("123abc"), StreamPreference.model_validate({"intent": intent})
        )
        assert streams[0].quality_id == quality
        assert all(stream.transport == "flv" for stream in streams)
    finally:
        await source.aclose()


@pytest.mark.parametrize("status", [None, 0, "2"])
async def test_unknown_status_is_not_offline_and_cannot_produce_streams(context: PluginContext, status: object) -> None:
    source = DouyinSource(
        context, transport=httpx.MockTransport(lambda _: httpx.Response(200, text=page(status=status)))
    )
    try:
        room = await source.resolve_room("123abc")
        assert (await source.get_room_info(room)).status == LiveStatus.UNKNOWN
        with pytest.raises(SourceTemporaryError):
            await source.get_streams(room, StreamPreference())
    finally:
        await source.aclose()


async def test_live_with_no_stream_is_temporary_but_explicit_offline_returns_empty(context: PluginContext) -> None:
    status = 2
    source = DouyinSource(
        context, transport=httpx.MockTransport(lambda _: httpx.Response(200, text=page(status=status, urls={})))
    )
    try:
        room = await source.resolve_room("123abc")
        with pytest.raises(SourceTemporaryError):
            await source.get_streams(room, StreamPreference())
        status = 4
        assert (await source.get_room_info(room)).status == LiveStatus.OFFLINE
        assert await source.get_streams(room, StreamPreference()) == []
    finally:
        await source.aclose()


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, SourceAuthenticationError),
        (403, SourceAuthenticationError),
        (412, SourceRateLimited),
        (429, SourceRateLimited),
        (503, SourceTemporaryError),
        (404, SourceInvalidInput),
        (204, SourceTemporaryError),
    ],
)
async def test_http_errors_have_public_safe_messages(
    context: PluginContext, status: int, error: type[Exception]
) -> None:
    source = DouyinSource(
        context, transport=httpx.MockTransport(lambda _: httpx.Response(status, text="private-secret"))
    )
    try:
        with pytest.raises(error) as caught:
            await source.get_room_info(await source.resolve_room("123abc"))
        assert "private-secret" not in str(caught.value)
    finally:
        await source.aclose()


@pytest.mark.parametrize(("header", "expected"), [("120", 120), ("NaN", 60), ("garbage", 60), ("-1", 0)])
async def test_retry_after_is_finite_and_bounded(context: PluginContext, header: str, expected: float) -> None:
    source = DouyinSource(
        context, transport=httpx.MockTransport(lambda _: httpx.Response(429, headers={"retry-after": header}))
    )
    try:
        with pytest.raises(SourceRateLimited) as caught:
            await source.get_room_info(await source.resolve_room("123abc"))
        assert caught.value.retry_after == expected
    finally:
        await source.aclose()


async def test_retry_after_http_date(context: PluginContext) -> None:
    header = format_datetime(datetime.now(UTC) + timedelta(seconds=120), usegmt=True)
    source = DouyinSource(
        context, transport=httpx.MockTransport(lambda _: httpx.Response(429, headers={"retry-after": header}))
    )
    try:
        with pytest.raises(SourceRateLimited) as caught:
            await source.get_room_info(await source.resolve_room("123abc"))
        assert caught.value.retry_after is not None and 110 < caught.value.retry_after <= 120
    finally:
        await source.aclose()


@pytest.mark.parametrize(
    "cookie", ["session=x\r\nInjected:yes", "中文", "a" * 16385], ids=["injection", "unicode", "too-long"]
)
async def test_invalid_cookie_fails_before_network(
    context: PluginContext,
    values: dict[str, str | float | bool | None],
    cookie: str,
) -> None:
    values["cookie"] = cookie
    source = DouyinSource(context)
    try:
        with pytest.raises(SourceInvalidInput):
            await source.get_room_info(await source.resolve_room("123abc"))
    finally:
        await source.aclose()


@pytest.mark.parametrize(
    "url",
    [
        "file:///secret",
        "https://127.0.0.1/a",
        "https://[::1]/a",
        "https://localhost/a",
        "https://u:p@cdn.example/a",
        "https://cdn.example/a\r\nsecret",
    ],
)
async def test_unsafe_stream_addresses_are_rejected(context: PluginContext, url: str) -> None:
    source = DouyinSource(
        context, transport=httpx.MockTransport(lambda _: httpx.Response(200, text=page(urls={"hd1": url})))
    )
    try:
        with pytest.raises(SourceTemporaryError):
            await source.get_streams(await source.resolve_room("123abc"), StreamPreference())
    finally:
        await source.aclose()


async def test_network_failure_has_original_cause_and_safe_public_message(context: PluginContext) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("signed-secret", request=request)

    source = DouyinSource(context, transport=httpx.MockTransport(fail))
    try:
        with pytest.raises(SourceTemporaryError) as caught:
            await source.get_room_info(await source.resolve_room("123abc"))
        assert isinstance(caught.value.__cause__, httpx.ConnectError)
        assert "signed-secret" not in str(caught.value)
    finally:
        await source.aclose()


class SlowBody(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        """保持响应未完成，以验证取消后的资源释放。"""
        self.started.set()
        await asyncio.Event().wait()
        yield b""

    async def aclose(self) -> None:
        self.closed = True


async def test_cancellation_closes_active_response(context: PluginContext) -> None:
    body = SlowBody()
    source = DouyinSource(context, transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=body)))
    task = asyncio.create_task(source.get_room_info(await source.resolve_room("123abc")))
    try:
        await asyncio.wait_for(body.started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert body.closed
    finally:
        await source.aclose()
    await source.aclose()
    with pytest.raises(SourceUnavailable):
        await source.resolve_room("123abc")


async def test_total_timeout_closes_response(
    context: PluginContext,
    values: dict[str, str | float | bool | None],
) -> None:
    values["request_timeout"] = 1
    body = SlowBody()
    source = DouyinSource(context, transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=body)))
    try:
        with pytest.raises(SourceTemporaryError, match="超时"):
            await source.get_room_info(await source.resolve_room("123abc"))
        assert body.closed
    finally:
        await source.aclose()


async def test_large_response_is_bounded(context: PluginContext) -> None:
    source = DouyinSource(
        context, transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * (PAGE_LIMIT + 1)))
    )
    try:
        with pytest.raises(SourceTemporaryError, match="大小"):
            await source.get_room_info(await source.resolve_room("123abc"))
    finally:
        await source.aclose()
