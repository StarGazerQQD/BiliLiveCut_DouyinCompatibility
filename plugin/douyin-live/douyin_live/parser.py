"""解析抖音直播页的公开 hydration 数据；不执行网页 JavaScript。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.plugins.live_source import LiveStatus, SourceTemporaryError

_PACE = re.compile(r'self\.__pace_f\.push\(\s*\[\s*\d+\s*,\s*("(?:[^"\\]|\\.)*")\s*\]\s*\)', re.DOTALL)


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _text(value: object, limit: int) -> str | None:
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else None


@dataclass(frozen=True)
class RoomData:
    """页面状态与媒体数据；内部 id_str 为场次号，绝不用作稳定房间身份。"""

    status: LiveStatus
    title: str | None
    uploader_name: str | None
    flv_urls: dict[str, str]


def parse_room(html: str, source_id: str) -> RoomData:
    """读取当前 pace 数据；缺失、损坏或身份不一致时拒绝猜测下播。"""
    stores: list[dict[str, object]] = []
    for match in _PACE.finditer(html):
        try:
            packed: object = json.loads(match.group(1))
            if not isinstance(packed, str):
                continue
            _, separator, body = packed.partition(":")
            if not separator or '"roomStore"' not in body:
                continue
            nodes: object = json.loads(body)
        except (ValueError, RecursionError) as exc:
            raise SourceTemporaryError("抖音页面数据格式异常，请稍后重试或检查插件更新") from exc
        if not isinstance(nodes, list):
            continue
        for node in nodes:
            state = _object(_object(node).get("state"))
            store = _object(state.get("roomStore"))
            if store:
                stores.append(store)
    if not stores:
        raise SourceTemporaryError("未取得抖音直播页数据；请检查网络、平台访问限制或插件更新")
    # 最后的 hydration 快照为页面最终状态；不回退到较早的可能过期快照。
    info = _object(stores[-1].get("roomInfo"))
    room = _object(info.get("room"))
    owner = _object(room.get("owner")) or _object(info.get("anchor"))
    for value in (info.get("web_rid"), room.get("web_rid"), owner.get("web_rid")):
        if value is not None and str(value) != source_id:
            raise SourceTemporaryError("抖音页面返回了不同的房间身份，已拒绝取流")
    raw_status = room.get("status")
    status = LiveStatus.UNKNOWN
    if type(raw_status) is int:
        if raw_status == 2:
            status = LiveStatus.LIVE
        elif raw_status == 4:
            status = LiveStatus.OFFLINE
    urls = _object(_object(room.get("stream_url")).get("flv_pull_url"))
    streams: dict[str, str] = {}
    for quality, url in urls.items():
        if not isinstance(url, str) or not url:
            raise SourceTemporaryError("抖音播放候选格式异常")
        normalized = quality.lower()
        if normalized in streams or not re.fullmatch(r"[a-z0-9_]{1,80}", normalized):
            raise SourceTemporaryError("抖音清晰度标识异常")
        streams[normalized] = url
    return RoomData(status, _text(room.get("title"), 1000), _text(owner.get("nickname"), 200), streams)
