# BiliLiveCut 抖音直播源 · v0.1

为 [BiliLiveCut](https://github.com/StarGazerQQD/BiliLiveCut) 提供抖音直播房间解析、开播状态查询与 FLV 取流。
自动录制、断流重连、分段、转写、高光识别、审核和切片使用主程序已有流程。

要求 Python 3.11+、FFmpeg，以及含 `app.plugins.live_source` 契约 v1 的 BiliLiveCut **0.1.18.4-alpha 或兼容版本**。
已验证宿主基线固定在 [289cf51](https://github.com/StarGazerQQD/BiliLiveCut/commit/289cf51b1ee6a2c7816b118c57f39e86ff2d4223)。
应用版本号相同的旧 Portable 也可能缺少接口，请按 [安装说明](docs/installation.md) 检查实际 Runtime。

## 安装与使用

1. 关闭宿主，将本仓库 `plugin/douyin-live` 完整复制到宿主的 `PLUGIN_DIR/douyin-live`。
   默认位置为 `storage/plugins/douyin-live`，必须保留入口、清单和 `douyin_live` 子目录。
   分发 ZIP 解压至 `PLUGIN_DIR` 即可。不要把整个仓库作为插件目录。
2. v0.1 无额外运行依赖：使用宿主已有 HTTPX、Pydantic 和公共插件 API，无需安装 Streamlink。
3. 启动主程序，在“插件”页刷新并启用“抖音直播源”。如页面访问受限，在插件设置填写自己的抖音 Cookie。
4. 添加 `https://live.douyin.com/房间ID` 并确认录制授权，分别开启自动录制、自动分析和自动渲染。
   需要无人值守出片时，再按实际需要开启自动审核并设置阈值；否则候选等待人工审核。
5. 在宿主查看直播间、场次、任务、热点与成品。插件不会开启自动上传，也不改变已有自动化开关。

CLI 与 Web 共用同一个工作目录、数据库、插件启用状态和设置：

```powershell
python -m app.cli add-room "https://live.douyin.com/你的房间ID" --authorize
python -m app.cli check "你的房间ID" --platform douyin
python -m app.cli record <宿主房间主键> --pipeline
```

CLI 没有独立插件启用命令，先在 Web 启用。裸房间 ID 必须显式指定 `--platform douyin`，否则宿主按 Bilibili 处理。

## 功能与边界

- 支持规范直播页、显式指定平台的字符串房间 ID，以及最终跳转到支持的直播页的 `v.douyin.com` 短链接。
- 不接受整段分享文案、短视频、个人主页或无法还原稳定网页房号的分享跳转；请复制浏览器中的直播间完整地址。
- 稳定身份使用网页路径房号（web RID），不使用每次开播变化的 `id_str` 场次号。
- 支持抖音公开网页的 `self.__pace_f` hydration 数据与 `flv_pull_url` 候选。
  v0.1 只返回 FLV；宿主偏好 HLS 时仍可使用返回的 FLV 候选。
- 质量按抖音标签排序，支持宿主 best / balanced / data_saver 意图；未知质量标签保留，但无法推断其真实码率。
- `status=2` 为直播、`status=4` 为下播，其他状态为未知；页面损坏、访问受限和空流不冒充下播。
- 每次取流重新访问页面，不缓存签名地址。宿主负责超时预算、退避、重连和停用收尾。
- Cookie 只发送到 `live.douyin.com` 的 HTTPS 网页请求；不转发给短链或媒体 CDN。
  不读取 Bilibili Cookie，不记录播放 URL 或 Cookie。宿主密码设置隐藏回显，但仍保存在其本地数据库中。
- **v0.1 不采集抖音弹幕**，宿主将弹幕证据标记为不可用；依靠音频、ASR 和现有分析能力切片。
- 页面结构和平台访问策略可能变化；本插件不执行网页 JavaScript、验证码或平台挑战。
  Cookie 不是确保取流成功的保证；403、限流和结构变化会明确报错。
- 仅录制有授权的内容。未配置本地 ASR 模型、FFmpeg 或宿主分析条件时，插件可取流不代表能够完成分析。

## 开发与验收

使用 `uv` 管理独立环境，`uv.lock` 锁定开发宿主和所有依赖。无需改动另一个 BiliLiveCut 工作区。

```powershell
uv sync --locked
uv run pytest tests/test_parser.py tests/test_source.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python scripts/build_plugin.py
uv build
uv run python scripts/verify_dist.py
uv pip check
```

测试只在临时目录使用隔离数据库；真实 FFmpeg 集成测试必须安装 FFmpeg/FFprobe，缺少时测试失败，不静默跳过。
本地 HTTP 服务和合成抖音页面用于离线回归，不代表已验证真实抖音实播。详见 [验收记录](docs/validation.md)。

ZIP 的 `release-manifest.json` 记录最终文件名、版本、大小、SHA-256 和 CRC32；产物位于 `dist/`，不提交缓存和构建包。
版本唯一源为 `douyin_live.__version__`，构建与测试检查插件清单一致性。

接口与设计见 [设计说明](docs/design.md)，变更见 [CHANGELOG](CHANGELOG.md)。许可证为 [MIT](LICENSE)。
