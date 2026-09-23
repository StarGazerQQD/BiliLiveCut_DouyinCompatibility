"""声明插件设置并向宿主注册抖音直播源。"""

from app.plugins import BasePlugin, PluginContext, PluginSetting
from app.plugins.live_source import LIVE_SOURCE_API_VERSION, SourceUnavailable

from .source import DouyinSource


class Plugin(BasePlugin):
    """无需 Cookie 即可启用；来源关闭、录制和任务生命周期由宿主管理。"""

    settings_schema = (
        PluginSetting(
            key="cookie",
            label="抖音 Cookie",
            kind="password",
            default="",
            description="可留空尝试公开直播页；平台拒绝访问时填写自己的 Cookie，仅发送到 live.douyin.com。",
        ),
        PluginSetting(
            key="request_timeout",
            label="页面请求超时（秒）",
            kind="number",
            default=8,
            minimum=1,
            maximum=9,
            description="每次查询含短链跳转的总时限；重试与退避由宿主管理。",
        ),
    )

    def on_enable(self, context: PluginContext) -> None:
        """仅注册公共直播源，不导入宿主数据库、录制器或私有服务。"""
        if LIVE_SOURCE_API_VERSION != "1":
            raise SourceUnavailable("抖音插件需要直播源契约 v1")
        context.register_live_source(DouyinSource(context))
