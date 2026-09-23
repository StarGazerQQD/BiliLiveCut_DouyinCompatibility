"""从显式文件白名单构建可重复的插件 ZIP 并计算最终文件校验值。"""

from __future__ import annotations

import hashlib
import json
import zipfile
import zlib
from pathlib import Path

from douyin_live import __version__

ROOT = Path(__file__).resolve().parents[1]


def build(output: Path | None = None) -> Path:
    """创建只包含运行源码、许可证和说明的包，不读取凭据或缓存。"""
    directory = output or ROOT / "dist"
    directory.mkdir(parents=True, exist_ok=True)
    plugin = ROOT / "plugin" / "douyin-live"
    manifest = json.loads((plugin / "plugin.json").read_text(encoding="utf-8"))
    if manifest["version"] != __version__:
        raise ValueError("plugin.json 与版本唯一源不一致")
    files = {f"douyin-live/{name}": plugin / name for name in ("main.py", "plugin.json", "requirements.txt")}
    for source in sorted((plugin / "douyin_live").glob("*.py")):
        files[f"douyin-live/douyin_live/{source.name}"] = source
    files["douyin-live/README.md"] = ROOT / "README.md"
    files["douyin-live/LICENSE"] = ROOT / "LICENSE"
    for name in ("installation.md", "design.md", "validation.md"):
        files[f"douyin-live/docs/{name}"] = ROOT / "docs" / name
    files["douyin-live/CHANGELOG.md"] = ROOT / "CHANGELOG.md"
    target = directory / f"BiliLiveCut_DouyinCompatibility-v{__version__}.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, source in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 23, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes())
    data = target.read_bytes()
    record = {
        "filename": target.name,
        "version": __version__,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "crc32": f"{zlib.crc32(data):08x}",
    }
    (directory / "release-manifest.json").write_text(
        json.dumps({"artifacts": [record]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


if __name__ == "__main__":
    print(build())
