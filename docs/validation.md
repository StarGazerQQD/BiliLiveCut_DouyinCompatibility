# v0.1 验收记录

日期：2026-09-23。环境：Windows x64、Python 3.12.14、FFmpeg 8.0.1。
宿主固定为 `289cf51b1ee6a2c7816b118c57f39e86ff2d4223`（`0.1.18.4-alpha`，直播源契约 v1）。

## 验证范围

- 纯解析与异步 HTTP 回归：稳定 web RID、变化的直播场次 ID、未知与下播区分、格式异常、
  短链逐跳验证、限流、Cookie 隔离与热更新、候选刷新、清晰度、取消、超时、响应大小。
- 正式 PluginManager 从磁盘启用插件；房间登记与重复登记、配置回显隐藏、停用和重启恢复。
- 真实 FFmpeg 录制与重连、片段入库、重复回调的任务幂等、无弹幕证据。
- 真实宿主任务领取、热点分析、人工审核和 FFmpeg 切片输出，不预置候选或最终成品。
- 仅在网络和 ASR/LLM 外部模型边界使用替身：合成抖音网页；把保留域名 CDN 的网络目的地
  路由到本地 HTTP 服务；模型返回固定转写，LLM 使用宿主规则回退。
  不替换 Recorder、数据库、热点检测器、持久任务状态机和渲染器。
- 实际 ZIP 在独立 Python 进程中启用，确认导入的是 ZIP 内的代码。
- ZIP 两次构建字节一致；最终 ZIP、wheel、sdist 审计及 SHA-256/CRC32 清单。

## 实际命令

本地运行使用本项目 `.venv/Scripts/python.exe`；等价的可复现入口为 README 中的 `uv run`。
Windows 使用 `--basetemp .local/<独立目录>`；先创建 `.local`。

| 命令 | 结果 |
| --- | --- |
| `python -m pytest tests/test_parser.py tests/test_source.py` | 目标回归通过 |
| `python -m pytest tests/test_host.py tests/test_build.py` | 正式加载、真实媒体流水线、包验证通过 |
| `python -m pytest` | 全部插件测试通过，无跳过 |
| `python -m ruff check .` | 通过 |
| `python -m ruff format --check .` | 通过 |
| `python -m mypy` | 严格类型检查通过；直接分析宿主公共契约，无 Any 基类豁免 |
| `python scripts/build_plugin.py` | 插件 ZIP 构建通过 |
| `uv build` | sdist、wheel 构建通过 |
| `python scripts/verify_dist.py` | 内容完整性、缓存/路径检查与最终产物校验通过 |
| `uv pip check --python .venv/Scripts/python.exe` | 插件隔离环境依赖兼容 |

另外在固定宿主快照上运行以下回归，全部通过：
`tests/unit/test_live_sources.py`、
`tests/integration/test_source_rooms.py`、
`tests/integration/test_source_runtime.py`、
`tests/integration/test_live_source_example.py`。
该宿主测试出现 FastAPI/Starlette 关于 HTTPX 和 AnyIO API 的弃用警告，未修改宿主依赖规则来屏蔽警告。

## 真实网络边界

未携带任何账号 Cookie 的公开首页检查得到 HTTP 200；首页未取得用于本次验证的规范房间链接。
**未完成真实抖音房间取流、实播录制、真实 ASR/LLM 模型质量或真实账号 Cookie 的联调。**
离线链路通过不等于平台实时接口始终可用。首版不采集抖音弹幕，只提供 FLV。
测试未读取、迁移或改写用户现有数据库。开发环境的主程序来自独立锁定提交，未修改旁边的用户工作区。
