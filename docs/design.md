# 设计与接口依据

宿主基线：`289cf51b1ee6a2c7816b118c57f39e86ff2d4223`，来源契约 v1。
仅导入 `app.plugins` 和 `app.plugins.live_source`，内部录制、SQLModel、任务推进均由宿主管理。

`Plugin.on_enable` 创建 `DouyinSource` 并暂存注册，宿主管理原子提交、冲突、关闭和失败回滚。
来源持有一个异步 HTTPX 连接池，不开启线程、后台任务或自行重试。每次请求读取本插件设置。
单次总超时覆盖全部重定向，连接、响应读取和操作取消均可中断。关闭 HTTP 客户端后所有调用明确拒绝。

抖音网页响应由独立纯解析函数处理；只读取 JSON，不执行 JavaScript。识别 roomStore.roomInfo.room，
保留最后 hydration 快照，校验可获得的 web_rid；缺少页面结构抛暂时错误，存在房间信息但状态未知返回 UNKNOWN。
稳定房间身份来自规范网页路径，不从内部直播场次 id_str 推断。

数据结构依据：

- [宿主公共契约](https://github.com/StarGazerQQD/BiliLiveCut/blob/289cf51b1ee6a2c7816b118c57f39e86ff2d4223/docs/live-source-plugins.md)
- [宿主抖音插件交接说明](https://github.com/StarGazerQQD/BiliLiveCut/blob/289cf51b1ee6a2c7816b118c57f39e86ff2d4223/docs/douyin-plugin-handoff.md)
- [Streamlink 维护者的抖音适配器](https://github.com/streamlink/streamlink/blob/master/src/streamlink/plugins/douyin.py)

参考访问日期为 2026-09-23。Streamlink 的实现确认了 pace hydration、roomStore、FLV 候选和平台质量顺序；
本项目独立实现异步 HTTP 和 JSON 解析，不复制 Streamlink 源文件，不将 Streamlink 作为依赖。
平台未为此网页取流方式提供稳定的公开开发者接口承诺。

网络边界：页面及短链只允许声明的精确域名，逐跳验证，不跟随任意 URL。请求上限 8 MiB、最多 4 次重定向。
Cookie 不发送到媒体地址；仅给媒体传递公开 Referer/User-Agent。拒绝非 HTTP(S)、嵌入凭据和明显本机 IP。
媒体域名解析、CDN 后续重定向由宿主 FFmpeg 管理，插件不是通用 SSRF 防护代理。

没有弹幕协议就不声明可选能力；宿主负责记录 unsupported，不产生假零样本。
设置即时生效，`.env` 不增加新的部署配置：宿主已有 PLUGIN_DIR/FFMPEG_PATH 等配置继续使用原设置。
