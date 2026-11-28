"""Line plan, retry budget, stall watchdog."""

import asyncio

import pytest

from bili_cli import upload_lines
from bili_cli.errors import BiliError


def test_default_plan_skips_bda2():
    assert upload_lines.parse_line_plan(None) == ["ws", "qn", "bldsa"]


def test_explicit_plan_and_dedupe():
    assert upload_lines.parse_line_plan("qn, ws ,qn,bda2") == ["qn", "ws", "bda2"]


def test_single_line_wins():
    assert upload_lines.parse_line_plan("ws,qn", "bldsa") == ["bldsa"]


def test_unknown_line_is_rejected():
    with pytest.raises(BiliError) as err:
        upload_lines.parse_line_plan("ws,ftp")
    assert err.value.code == "invalid_argument"
    assert "ftp" in err.value.message


def test_empty_plan_is_rejected():
    with pytest.raises(BiliError):
        upload_lines.parse_line_plan(",")


def test_attempts_expand_retry_budget():
    assert upload_lines.attempts_for(["ws", "qn", "bldsa"]) == ["ws", "qn", "bldsa", "bldsa", "bldsa"]


def test_line_host_is_the_upload_edge():
    host = upload_lines.line_host("ws")
    assert host.endswith(".bilivideo.com")
    assert "ws" in host


class FakeUploader:
    def __init__(self, page_impl):
        self._page_impl = page_impl
        self.listeners = {}
        self.line = None

    def add_event_listener(self, name, handler):
        self.listeners[name] = handler

    def remove_event_listener(self, name, handler):
        self.listeners.pop(name, None)

    async def _upload_page(self, page):
        return await self._page_impl(self)


def test_watchdog_aborts_a_silent_page():
    async def silent(uploader):
        await asyncio.sleep(30)

    async def run():
        uploader = FakeUploader(silent)
        with pytest.raises(upload_lines.UploadStalled):
            await upload_lines.upload_page_watched(uploader, object(), stall_seconds=1.0)
        assert uploader.listeners == {}   # 监听器必须被摘掉，否则换线后会继续累积

    asyncio.run(run())


def test_watchdog_passes_chunks_through():
    async def chatty(uploader):
        for _ in range(3):
            uploader.listeners["AFTER_CHUNK"]({})
            await asyncio.sleep(0.05)
        return {"filename": "x", "cid": 1}

    seen = []
    asyncio.run(upload_lines.upload_page_watched(FakeUploader(chatty), object(), 5, seen.append))
    assert seen == [1, 2, 3]


def test_line_errors_are_retryable_but_rejections_are_not():
    class ResponseCodeException(Exception):
        pass

    assert upload_lines.is_line_error(RuntimeError("curl: (60) SSL certificate problem"))
    assert upload_lines.is_line_error(TimeoutError("connection reset by peer"))
    assert not upload_lines.is_line_error(ResponseCodeException("稿件标题不合规"))
