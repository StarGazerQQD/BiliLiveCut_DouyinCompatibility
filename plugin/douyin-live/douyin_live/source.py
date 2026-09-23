"""仅通过宿主公共契约提供可取消的抖音房间查询和临时取流。"""

from __future__ import annotations

import asyncio
import ipaddress
import math
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit, urlunsplit
from uuid import uuid4

import httpx
from app.plugins import PluginContext
from app.plugins.live_source import (
    LiveStatus,
    RoomSnapshot,
    SourceAuthenticationError,
    SourceDescriptor,
    SourceInvalidInput,
    SourceRateLimited,
    SourceRoom,
    SourceTemporaryError,
    SourceUnavailable,
    StreamPreference,
    StreamSpec,
)
from pydantic import ValidationError

from .config import LIVE_HOST, MAX_REDIRECTS, PAGE_LIMIT, SHORT_HOST, USER_AGENT, RequestSettings
from .parser import RoomData, parse_room

_ROOM_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_QUALITY_ORDER = {"origin": 5, "uhd": 4, "full_hd1": 3, "hd1": 2, "sd2": 1, "sd1": 0}


def _page_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {LIVE_HOST, SHORT_HOST}
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or "\\" in value
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError("invalid page URL")
    except ValueError as exc:
        raise SourceInvalidInput("仅支持抖音直播间完整地址或 v.douyin.com 短链接") from exc
    return urlunsplit(("https", parsed.hostname, parsed.path, parsed.query, ""))


def _room(value: str) -> SourceRoom:
    parsed = urlsplit(_page_url(value))
    source_id = parsed.path.strip("/")
    if parsed.hostname != LIVE_HOST or not _ROOM_ID.fullmatch(source_id):
        raise SourceInvalidInput("请使用 https://live.douyin.com/房间ID 的直播间地址")
    return SourceRoom(platform="douyin", source_id=source_id, canonical_url=f"https://{LIVE_HOST}/{source_id}")


def _retry_after(value: str | None) -> float:
    if value is not None:
        try:
            seconds = float(value)
        except ValueError:
            try:
                date = parsedate_to_datetime(value)
                seconds = (date - datetime.now(UTC)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                return 60.0
        if math.isfinite(seconds):
            return min(86400.0, max(0.0, seconds))
    return 60.0


def _media_url(value: str) -> str:
    """不向媒体 CDN 发送 Cookie，并拒绝非网络协议和明显的本机地址。"""
    try:
        if "\\" in value or any(ord(char) <= 32 or ord(char) == 127 for char in value):
            raise ValueError("invalid media URL characters")
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        _ = parsed.port
        if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            raise ValueError("invalid media URL")
        normalized_host = host.rstrip(".").lower()
        if normalized_host in {"localhost", "localhost.localdomain"} or normalized_host.endswith(
            (".localhost", ".local")
        ):
            raise ValueError("local media URL")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError("non-public media URL")
        # 抖音公开网页中的旧 http 播放链接同样通过 HTTPS 访问。
        return urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, parsed.fragment))
    except ValueError as exc:
        raise SourceTemporaryError("抖音返回了不安全或无效的播放地址") from exc


class DouyinSource:
    """每次请求读取插件设置和新网页数据，不缓存签名地址，不自行录制或重试。"""

    descriptor = SourceDescriptor(platform="douyin", name="抖音", domains=(LIVE_HOST, SHORT_HOST))

    def __init__(self, context: PluginContext, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._context = context
        self._client = httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            timeout=8,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
        )

    def _ensure_open(self) -> None:
        if self._client.is_closed:
            raise SourceUnavailable("抖音直播源已关闭，请重新启用插件")

    async def _fetch(self, url: str, settings: RequestSettings) -> tuple[str, str]:
        self._ensure_open()
        current = _page_url(url)
        for _ in range(MAX_REDIRECTS + 1):
            host = urlsplit(current).hostname
            cookie = ""
            if host == LIVE_HOST:
                cookie = settings.cookie
                if not any(part.strip().startswith("__ac_nonce=") for part in cookie.split(";")):
                    cookie = f"{cookie}; " if cookie else ""
                    cookie += "__ac_nonce=" + uuid4().hex[:21]
            # 显式空 Cookie 禁止 AsyncClient cookie jar 把此前的 Set-Cookie 带到另一域名/配置。
            headers = {"User-Agent": USER_AGENT, "Referer": f"https://{LIVE_HOST}/", "Cookie": cookie}
            try:
                async with self._client.stream("GET", current, headers=headers, timeout=settings.timeout_s) as response:
                    code = response.status_code
                    if code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise SourceTemporaryError("抖音重定向缺少目标地址")
                        current = _page_url(urljoin(current, location))
                        continue
                    if code in {401, 403}:
                        raise SourceAuthenticationError("抖音拒绝访问；请检查插件 Cookie 或平台访问限制")
                    if code in {412, 429}:
                        raise SourceRateLimited(
                            "抖音限制了访问频率", retry_after=_retry_after(response.headers.get("retry-after"))
                        )
                    if code >= 500:
                        raise SourceTemporaryError("抖音页面服务暂时不可用")
                    if code in {404, 410}:
                        raise SourceInvalidInput("抖音直播页面不存在，请核对直播间地址")
                    if code != 200:
                        raise SourceTemporaryError("抖音页面返回了非预期响应")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > PAGE_LIMIT:
                            raise SourceTemporaryError("抖音页面超过大小限制")
                        body.extend(chunk)
                    return current, body.decode("utf-8", errors="replace")
            except httpx.HTTPError as exc:
                raise SourceTemporaryError("抖音网络请求失败，请检查网络后重试") from exc
        raise SourceInvalidInput("抖音短链接跳转次数过多，请改用直播间完整地址")

    async def resolve_room(self, value: str) -> SourceRoom:
        """用网页房间标识保持跨开播稳定；仅解析指向支持的直播页的短链接。"""
        self._ensure_open()
        value = value.strip()
        if _ROOM_ID.fullmatch(value):
            return _room(f"https://{LIVE_HOST}/{value}")
        url = _page_url(value)
        if urlsplit(url).hostname == LIVE_HOST:
            return _room(url)
        settings = RequestSettings.read(self._context)
        try:
            async with asyncio.timeout(settings.timeout_s):
                final, _ = await self._fetch(url, settings)
        except TimeoutError as exc:
            raise SourceTemporaryError("抖音短链接解析超时") from exc
        return _room(final)

    async def _read_room(self, room: SourceRoom) -> RoomData:
        self._ensure_open()
        if room.platform != "douyin" or _room(room.canonical_url) != room:
            raise SourceInvalidInput("抖音房间标识与规范地址不一致")
        settings = RequestSettings.read(self._context)
        try:
            async with asyncio.timeout(settings.timeout_s):
                final, html = await self._fetch(room.canonical_url, settings)
        except TimeoutError as exc:
            raise SourceTemporaryError("抖音直播间查询超时") from exc
        if _room(final) != room:
            raise SourceTemporaryError("抖音直播页重定向到其他房间，已拒绝取流")
        return parse_room(html, room.source_id)

    async def get_room_info(self, room: SourceRoom) -> RoomSnapshot:
        """只有明确 status=4 才报告下播，未识别的状态报告 UNKNOWN。"""
        data = await self._read_room(room)
        return RoomSnapshot(status=data.status, title=data.title, uploader_name=data.uploader_name)

    async def get_streams(self, room: SourceRoom, preference: StreamPreference) -> list[StreamSpec]:
        """刷新并排序 FLV 候选；Cookie 只用于直播网页，不交给 FFmpeg。"""
        data = await self._read_room(room)
        if data.status == LiveStatus.OFFLINE:
            return []
        if data.status == LiveStatus.UNKNOWN or not data.flv_urls:
            raise SourceTemporaryError("抖音尚未提供可确认的直播流，请稍后重试")
        qualities = sorted(
            (key for key in data.flv_urls if key in _QUALITY_ORDER), key=lambda key: -_QUALITY_ORDER[key]
        )
        unknown = sorted(key for key in data.flv_urls if key not in _QUALITY_ORDER)
        if preference.intent == "data_saver":
            qualities.reverse()
        elif preference.intent == "balanced":
            middle = len(qualities) // 2
            qualities = qualities[middle:] + qualities[:middle]
        qualities += unknown
        streams: list[StreamSpec] = []
        try:
            for quality in qualities:
                streams.append(
                    StreamSpec(
                        url=_media_url(data.flv_urls[quality]),
                        transport="flv",
                        container="flv",
                        headers={"User-Agent": USER_AGENT, "Referer": f"https://{LIVE_HOST}/"},
                        quality_id=quality,
                        quality_label=quality,
                    )
                )
        except ValidationError as exc:
            raise SourceTemporaryError("抖音播放候选不符合宿主契约") from exc
        return streams

    async def aclose(self) -> None:
        """幂等关闭连接池；不吞掉调用方取消。"""
        await self._client.aclose()
