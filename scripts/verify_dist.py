"""检查实际分发包完整性及敏感路径，再生成所有最终产物的校验清单。"""

from __future__ import annotations

import hashlib
import json
import tarfile
import zipfile
import zlib
from pathlib import Path, PurePosixPath

from douyin_live import __version__

ROOT = Path(__file__).resolve().parents[1]


def verify(directory: Path) -> None:
    """审计 ZIP、wheel、sdist 的内容；不解压归档到文件系统。"""
    names = [
        f"BiliLiveCut_DouyinCompatibility-v{__version__}.zip",
        f"bililivecut_douyin-{__version__}-py3-none-any.whl",
        f"bililivecut_douyin-{__version__}.tar.gz",
    ]
    records: list[dict[str, str | int]] = []
    for name in names:
        path = directory / name
        members: dict[str, bytes] = {}
        if name.endswith(".tar.gz"):
            with tarfile.open(path, "r:gz") as archive:
                for item in archive.getmembers():
                    if item.issym() or item.islnk():
                        raise ValueError("分发包不能包含链接")
                    if item.isfile():
                        stream = archive.extractfile(item)
                        if stream is None:
                            raise ValueError("无法读取分发文件")
                        members[item.name] = stream.read()
            required = ("tests/conftest.py", "scripts/build_plugin.py", "docs/installation.md", "uv.lock")
            if not all(any(member.endswith("/" + item) for member in members) for item in required):
                raise ValueError("源码包缺少开发或安装文件")
        else:
            with zipfile.ZipFile(path) as archive:
                if archive.testzip() is not None:
                    raise ValueError("ZIP CRC 校验失败")
                members = {item: archive.read(item) for item in archive.namelist() if not item.endswith("/")}
        for member, content in members.items():
            parts = PurePosixPath(member).parts
            if (
                member.startswith("/")
                or ".." in parts
                or any(
                    part in {".env", ".git", ".upstream", ".venv", ".cache", ".local", "__pycache__"} for part in parts
                )
                or member.endswith((".pyc", ".pyo"))
            ):
                raise ValueError(f"分发包包含不应分发的文件: {member}")
            if ROOT.as_posix().encode() in content or str(ROOT).encode() in content:
                raise ValueError(f"分发包泄露本机源码路径: {member}")
        data = path.read_bytes()
        records.append(
            {
                "filename": name,
                "version": __version__,
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "crc32": f"{zlib.crc32(data):08x}",
            }
        )
    (directory / "release-manifest.json").write_text(
        json.dumps({"artifacts": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    verify(ROOT / "dist")
    print("ZIP、wheel、sdist 内容审计和校验清单通过")
