from __future__ import annotations

import asyncio
import shutil
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from conftest import page
from loguru import logger

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def media(tmp_path: Path) -> Iterator[tuple[str, list[dict[str, str]]]]:
    binary = shutil.which("ffmpeg")
    assert binary, "真实 FFmpeg 集成测试必须安装 ffmpeg"
    target = tmp_path / "sample.flv"
    subprocess.run(
        [
            binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000",
            "-t",
            "50",
            "-af",
            "volume='if(between(t,30,42),1,0.02)':eval=frame",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-g",
            "20",
            "-sc_threshold",
            "0",
            "-c:a",
            "aac",
            "-f",
            "flv",
            str(target),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    content = target.read_bytes()
    requests: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(dict(self.headers))
            self.send_response(200)
            self.send_header("Content-Type", "video/x-flv")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # 停录时允许客户端先关闭连接。

        def log_message(self, format: str, *args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/sample.flv", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


async def test_plugin_manager_ffmpeg_reconnect_and_pipeline(
    host_db: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    media: tuple[str, list[dict[str, str]]],
) -> None:
    from app.core.config import settings
    from app.db.entities import AppSetting, LiveRoom, RawSegment, RecordingSession, SegmentTask
    from app.db.session import get_session
    from app.pipeline.orchestrator import make_pipeline_callback
    from app.plugins.live_source import SourceUnavailable
    from app.plugins.manager import PluginManager
    from app.recording.danmaku import read_evidence
    from app.recording.recorder import Recorder
    from app.sources.registry import SourceRegistry, source_registry
    from app.sources.rooms import register_room, room_source, room_source_view
    from sqlmodel import select

    page_requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "live.douyin.com"
        page_requests.append(request)
        return httpx.Response(
            200,
            text=page(
                session_id=f"transient-session-{len(page_requests)}",
                urls={"full_hd1": f"https://cdn.example/live.flv?signature={len(page_requests)}"},
            ),
        )

    original_init = httpx.AsyncClient.__init__

    def init_client(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        kwargs["transport"] = httpx.MockTransport(respond)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", init_client)
    original_spawn = asyncio.create_subprocess_exec
    observed_urls: list[str] = []

    async def route_test_cdn(program: str, *args: str, **kwargs: object) -> asyncio.subprocess.Process:
        # 仅将保留域名 CDN 的网络目的地接到本地 HTTP 夹具，仍运行真实 FFmpeg。
        values = list(args)
        if "-i" in values:
            index = values.index("-i") + 1
            if values[index].startswith("https://cdn.example/"):
                observed_urls.append(values[index])
                values[index] = media[0]
        return await original_spawn(program, *values, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", route_test_cdn)
    monkeypatch.setattr(source_registry, "_entries", SourceRegistry()._entries)
    monkeypatch.setattr(settings, "segment_duration_s", 50)
    monkeypatch.setattr(settings, "collect_danmaku", True)
    monkeypatch.setattr(settings, "bilibili_cookie", "other-platform-private")
    directory = tmp_path / "plugins"
    shutil.copytree(
        ROOT / "plugin" / "douyin-live",
        directory / "douyin-live",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    manager = PluginManager(directory, registry=source_registry)
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)))
    try:
        await manager.start()
        assert (await manager.set_enabled("douyin-live", True))["loaded"] is True
        payload = manager.update_settings("douyin-live", {"cookie": "sessionid=plugin-private"})
        password = next(item for item in payload["fields"] if item["key"] == "cookie")
        assert password["value"] == "" and password["configured"] is True
        room = await register_room("https://live.douyin.com/123abc", True)
        duplicate = await register_room("123abc", True, "douyin")
        assert room.id == duplicate.id and room.room_id is None
        assert room.platform == "douyin" and room_source(room).source_id == "123abc"
        assert room.id is not None
        with get_session() as db:
            stored = db.get(LiveRoom, room.id)
            assert stored is not None
            assert not any(
                (stored.auto_record, stored.auto_analyze, stored.auto_render, stored.auto_approve, stored.auto_upload)
            )
            stored.auto_analyze = True
            db.add(stored)
        callback = make_pipeline_callback(room_id=room.id)

        async def received(segment: RawSegment) -> None:
            await callback(segment)
            await callback(segment)
            if len(observed_urls) >= 2:
                recorder.stop()

        recorder = Recorder(room_source(room), room.id, on_segment=received)
        await asyncio.wait_for(recorder.run(), 40)
        assert len(observed_urls) == 2 and observed_urls[0] != observed_urls[1]
        assert recorder.session_id is not None
        with get_session() as db:
            recording = db.get(RecordingSession, recorder.session_id)
            segments = db.exec(select(RawSegment)).all()
            tasks = db.exec(select(SegmentTask)).all()
            assert recording is not None and recording.status == "stopped" and recording.ended_at is not None
            assert recording.stream_url is None and recording.reconnect_count == 1
            assert len(segments) >= 2 and len(segments) == len(tasks)
            assert all(Path(item.file_path).stat().st_size > 1024 for item in segments)
            assert len({item.file_path for item in segments}) == len(segments)
            evidence = read_evidence(db, recorder.session_id)
            assert evidence.status == "unsupported" and not evidence.intervals
            metadata = " ".join(
                row.value for row in db.exec(select(AppSetting)).all() if not row.key.startswith("plugin.")
            )
            assert "signature=" not in metadata and "plugin-private" not in metadata
        assert media[1] and all("Cookie" not in headers for headers in media[1])
        assert all("plugin-private" in request.headers["cookie"] for request in page_requests)
        assert "signature=" not in " ".join(messages) and "plugin-private" not in " ".join(messages)
        await asyncio.to_thread(advance_to_clip, room.id, recorder.session_id, monkeypatch)
        await manager.set_enabled("douyin-live", False)
        assert not source_registry.available("douyin")
        assert not room_source_view(room)["source_available"]
        with pytest.raises(SourceUnavailable):
            await source_registry.get_room_info(room_source(room))
        await manager.set_enabled("douyin-live", True)
        await manager.stop()
        restarted = PluginManager(directory, registry=source_registry)
        await restarted.start()
        try:
            assert source_registry.available("douyin")
            assert (await register_room("123abc", True, "douyin")).id == room.id
        finally:
            await restarted.stop()
    finally:
        logger.remove(sink)
        await manager.stop()


def advance_to_clip(room_id: int, session_id: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """只替换 ASR/LLM 外部模型边界，实际执行宿主分析、审核及 FFmpeg 渲染。"""
    from app.analysis import llm
    from app.analysis.transcription.backends import FasterWhisperBackend
    from app.analysis.transcription.models import ASRSegmentResult, ASRTranscriptResult, Word
    from app.clipping.core import probe_media
    from app.core.config import settings
    from app.db.entities import FinalClip, HighlightCandidate, LiveRoom, SegmentTask, TaskStatus, Transcript
    from app.db.session import get_session
    from app.pipeline import scheduler
    from app.pipeline.approval import approve_event_and_task
    from app.pipeline.claiming import pop_and_claim
    from sqlmodel import select

    for key, value in {
        "asr_primary": "whisper",
        "asr_sensevoice": False,
        "asr_sensevoice_enabled": False,
        "asr_funasr_review": False,
        "asr_fallback_whisper": False,
        "hotspot_asr_enabled": False,
        "clip_vertical": False,
        "clip_subtitle": False,
        "clip_remove_silence": False,
        "clip_preset": "ultrafast",
        "hotspot_bucket_s": 5,
        "hotspot_min_baseline_buckets": 2,
        "hotspot_detector_tick_s": 15,
        "hotspot_baseline_window_s": 60,
        "hotspot_detection_threshold": 0.15,
    }.items():
        monkeypatch.setattr(settings, key, value)
    text = "主播观察对手位置之后果断出击，这波配合完成了漂亮的五杀。随后解释关键操作与技能释放顺序。"
    calls: list[str] = []

    def transcribe(
        self: FasterWhisperBackend,
        audio_path: str,
        initial_prompt: str | None = None,
    ) -> ASRTranscriptResult:
        assert Path(audio_path).is_file()
        calls.append(audio_path)
        return ASRTranscriptResult(
            text=text,
            backend="whisper",
            model_id="offline-test",
            audio_duration=50,
            segments=[ASRSegmentResult(start=30, end=42, text=text, words=[Word(word=text, start=30, end=42)])],
        )

    def model(prompt: str, **kwargs: object) -> None:
        return None  # 外部模型缺省，宿主实际执行规则回退。

    monkeypatch.setattr(FasterWhisperBackend, "transcribe", transcribe)
    monkeypatch.setattr(llm, "call_text", model)
    with get_session() as db:
        room = db.get(LiveRoom, room_id)
        assert room is not None
        room.highlight_threshold = 0.01
        room.review_threshold = 0.01
        room.auto_render = True
        db.add(room)
    for _ in range(15):
        scheduler.advance_recorded()
        scheduler.advance_transcribed()
        progressed = False
        for stage in (TaskStatus.QUEUED_FOR_ANALYSIS, TaskStatus.QUEUED_FOR_TRANS):
            while (claimed := pop_and_claim(stage)) is not None:
                progressed = True
                scheduler.execute_task(claimed.id, claimed.stage, claimed.lease_token)
        scheduler.advance_candidate()
        if not progressed:
            break
    with get_session() as db:
        tasks = db.exec(select(SegmentTask)).all()
        assert not any(task.stage in {TaskStatus.TRANSIENT_FAILED, TaskStatus.FAILED} for task in tasks), [
            (task.stage, task.last_error) for task in tasks
        ]
        assert len(db.exec(select(Transcript)).all()) >= 2 and calls
        candidates = db.exec(select(HighlightCandidate)).all()
        assert candidates and all(item.session_id == session_id for item in candidates)
        pending = [task for task in tasks if task.stage == TaskStatus.AWAITING_REVIEW]
        assert pending and not db.exec(select(FinalClip)).all()
    for task in pending:
        assert approve_event_and_task(
            task_id=task.id,
            event_id=task.event_id,
            source="human",
            approved_by="plugin-test",
            review_decision="approved_solo",
        )
    scheduler.advance_approved()
    while (claimed := pop_and_claim(TaskStatus.QUEUED_FOR_RENDER)) is not None:
        scheduler.execute_task(claimed.id, claimed.stage, claimed.lease_token)
    with get_session() as db:
        clips = db.exec(select(FinalClip)).all()
        assert clips
        for clip in clips:
            duration, width, height = probe_media(clip.file_path)
            assert duration >= 1 and width > 0 and height > 0
            assert Path(clip.file_path).stat().st_size > 1024
    scheduler.advance_rendered()
    with get_session() as db:
        assert not db.exec(select(SegmentTask).where(SegmentTask.stage == TaskStatus.QUEUED_FOR_PUBLISH)).all()


def test_runtime_imports_only_public_host_contracts() -> None:
    import ast

    for path in (ROOT / "plugin" / "douyin-live").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
                assert node.module in {"app.plugins", "app.plugins.live_source"}, (path, node.module)


def test_manifest_matches_package_version_and_contract() -> None:
    from app.plugins.contracts import PluginManifest
    from douyin_live import __version__

    manifest = PluginManifest.model_validate_json(
        (ROOT / "plugin" / "douyin-live" / "plugin.json").read_text(encoding="utf-8")
    )
    assert manifest.version == __version__ == "0.1"
    assert manifest.id == "douyin-live"
    assert manifest.live_source_api_version == "1" and manifest.capabilities == ("live_source",)
