# 安装、更新与排错

## 源码宿主

1. 确认运行宿主的 Python 能执行：

   ```powershell
   python -c "from app.plugins.live_source import LIVE_SOURCE_API_VERSION; print(LIVE_SOURCE_API_VERSION)"
   ```

   应输出 `1`。最低宿主版本为 `0.1.18.4-alpha`，新版本兼容性以契约和验收为准。
2. 关闭宿主，将 `douyin-live` 目录放入已配置的 `PLUGIN_DIR`。
3. 执行 `python -m pip check` 检查宿主依赖；插件无额外依赖，不要安装第二份宿主。
4. 启动并在插件中心启用，按 README 添加房间及设置自动化开关。

更新先停用插件并等待宿主录制收尾，再备份原插件目录、替换文件、重新启用。
插件不迁移或删除宿主数据。回退时用旧插件目录替换；保留与宿主版本匹配的数据库与媒体。

## Portable

先由 Launcher 完成初始化，停止服务，在安装根目录运行：

```powershell
$blcCurrent = Get-Content -LiteralPath runtime/current.json -Raw | ConvertFrom-Json
$blcSource = Resolve-Path (Join-Path runtime/releases $blcCurrent.release_id)
$env:BLC_SOURCE_DIR = $blcSource.Path
$env:PYTHONPATH = $blcSource.Path
.\.venv\Scripts\python.exe -c "from app.plugins.live_source import LIVE_SOURCE_API_VERSION; print(LIVE_SOURCE_API_VERSION)"
.\.venv\Scripts\python.exe -m pip check
```

应输出契约 `1`。在该宿主 `storage/plugins` 下安装插件，关闭当前 PowerShell 结束临时变量。
本插件无需额外 wheel；不改 Portable 核心依赖锁，不使用系统 Python、Engine Pack Python 或安装第二份 BiliLiveCut。
旧版本缺接口应升级到包含新 Runtime 的宿主，不直接覆盖主程序源码。

## 设置与错误

| 设置 | 默认值 | 说明 |
| --- | --- | --- |
| 抖音 Cookie | 空 | 支持匿名尝试；只发送到抖音直播网页，修改后下一次请求生效 |
| 页面请求超时 | 8 秒 | 范围 1–9 秒，包含短链重定向，宿主另有总预算 |

401/403：检查平台是否允许在浏览器正常打开；按需要更新 Cookie，再手动重新开始录制解除宿主暂停限制。
412/429：由宿主等待 Retry-After 或默认 60 秒，不反复手动重试。
页面数据缺失/未知：不是下播；可能是网页结构变化、风控页面或尚未取得房间数据。
短链不支持：复制浏览器中 `https://live.douyin.com/房间ID` 地址，不使用分享文案或主播主页。
已录制但没出片：在宿主检查本地模型、任务状态、阈值、审核状态与自动渲染开关。

只在插件密码设置中保存 Cookie，不放到命令行、Git、日志或问题反馈中。
