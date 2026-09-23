"""抖音平台常量及插件设置的集中校验。"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.plugins import PluginContext
from app.plugins.live_source import SourceInvalidInput

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
LIVE_HOST = "live.douyin.com"
SHORT_HOST = "v.douyin.com"
PAGE_LIMIT = 8 * 1024 * 1024
MAX_REDIRECTS = 4


@dataclass(frozen=True)
class RequestSettings:
    """每次操作读取设置，使 Cookie 变更即时生效且不进入 repr。"""

    cookie: str = field(repr=False)
    timeout_s: float

    @classmethod
    def read(cls, context: PluginContext) -> RequestSettings:
        """校验持久设置；配置错误不转化成下播或网络失败。"""
        cookie = context.get_setting("cookie", "")
        timeout = context.get_setting("request_timeout", 8.0)
        if not isinstance(cookie, str) or len(cookie) > 16384:
            raise SourceInvalidInput("抖音 Cookie 必须是长度不超过 16384 的字符串")
        if any(ord(char) < 32 or ord(char) >= 127 for char in cookie):
            raise SourceInvalidInput("抖音 Cookie 不能含控制字符或非 ASCII 字符")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 9:
            raise SourceInvalidInput("抖音请求超时必须为 1 至 9 秒")
        return cls(cookie=cookie.strip(), timeout_s=float(timeout))
