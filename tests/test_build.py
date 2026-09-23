from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
import zlib
from pathlib import Path


def test_zip_is_repeatable_and_checksums_match(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("build_plugin", root / "scripts" / "build_plugin.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    first = module.build(tmp_path / "one")
    second = module.build(tmp_path / "two")
    assert first.read_bytes() == second.read_bytes()
    record = json.loads((first.parent / "release-manifest.json").read_text(encoding="utf-8"))["artifacts"][0]
    data = first.read_bytes()
    assert record["filename"] == first.name and record["version"] == "0.1"
    assert record["size_bytes"] == len(data)
    assert record["sha256"] == hashlib.sha256(data).hexdigest()
    assert record["crc32"] == f"{zlib.crc32(data):08x}"
    with zipfile.ZipFile(first) as archive:
        names = archive.namelist()
        assert "douyin-live/plugin.json" in names and "douyin-live/main.py" in names
        assert "douyin-live/douyin_live/source.py" in names and "douyin-live/LICENSE" in names
        assert not any(
            ".env" in name or "__pycache__" in name or ".pyc" in name or ".upstream" in name for name in names
        )
        assert archive.testzip() is None


def test_built_zip_loads_its_bundled_code_in_fresh_host(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("build_plugin", root / "scripts" / "build_plugin.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    archive_path = module.build(tmp_path / "dist")
    plugins = tmp_path / "plugins"
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(plugins)
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'isolated.db').as_posix()}"
    env["STORAGE_ROOT"] = str(tmp_path / "storage")
    script = """
import asyncio
import sys
from pathlib import Path
from app.db.session import init_db
from app.plugins.manager import PluginManager
from app.sources.registry import SourceRegistry
init_db()
async def main():
    root = Path(sys.argv[1])
    manager = PluginManager(root, registry=SourceRegistry())
    await manager.start()
    try:
        result = await manager.set_enabled("douyin-live", True)
        assert result["loaded"]
        import douyin_live.source
        assert Path(douyin_live.source.__file__).resolve().is_relative_to(root.resolve())
    finally:
        await manager.stop()
asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-I", "-X", "utf8", "-c", script, str(plugins)],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
