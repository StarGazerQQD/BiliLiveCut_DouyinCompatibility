from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from app.plugins import PluginContext


def page(
    *,
    status: object = 2,
    source_id: str = "123abc",
    session_id: str = "live-session-1",
    urls: dict[str, str] | None = None,
    room_exists: bool = True,
) -> str:
    room = {
        "status": status,
        "id_str": session_id,
        "title": "测试直播间",
        "owner": {"nickname": "测试主播", "web_rid": source_id},
        "stream_url": {
            "flv_pull_url": urls if urls is not None else {"FULL_HD1": "https://media.example/test.flv?sign=one"}
        },
    }
    payload = ["node", {"state": {"roomStore": {"roomInfo": {"room": room} if room_exists else {}}, "streamStore": {}}}]
    packed = json.dumps("a:" + json.dumps(payload, ensure_ascii=False), ensure_ascii=False)
    return f"<html><script>self.__pace_f.push([1,{packed}])</script></html>"


@pytest.fixture
def values() -> dict[str, str | float | bool | None]:
    return {}


@pytest.fixture
def context(tmp_path: Path, values: dict[str, str | float | bool | None]) -> PluginContext:
    return PluginContext(
        plugin_id="douyin-live",
        plugin_dir=tmp_path,
        _get_setting=lambda key, default: values.get(key, default),
        _set_setting=lambda key, value: values.__setitem__(key, value),
    )


@pytest.fixture
def host_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # 配置必须先于宿主 DB 模块导入，绝不打开用户的数据库。
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'host.db').as_posix()}")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    from app.core import config

    config.get_settings.cache_clear()
    config.get_settings()
    import importlib

    from app.db import session

    importlib.reload(session)
    session.init_db()
    yield
    session.engine.dispose()
