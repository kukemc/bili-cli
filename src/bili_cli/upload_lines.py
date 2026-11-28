"""Upload line planning, stall detection and per-line fallback.

The bundled uploader picks a line by timing a 0.1 MB POST against all four edges and has no notion of
"this connection went quiet": when an edge accepts the TCP connection and then stops sending, the
upload sits there forever. `bda2` did exactly that twice in production, and half of the `bldsa` pool
serves expired certificates, so the auto probe is a lottery.

This module keeps the decision in our hands:

* a fixed, ordered plan (`ws,qn,bldsa` by default, `bda2` only when asked for),
* a per-page stall watchdog driven by the uploader's AFTER_CHUNK event,
* one retry budget per line (bldsa benefits from repeats because DNS hands out a different node).
"""

from __future__ import annotations

import asyncio
import socket
import time
from typing import Any

from bilibili_api.video_uploader import LINES_INFO, VideoUploaderEvents

from .errors import BiliError

DEFAULT_LINES = ("ws", "qn", "bldsa")
LINE_RETRIES = {"ws": 1, "qn": 1, "bldsa": 3, "bda2": 1}
ALL_LINES = tuple(LINES_INFO)


class UploadStalled(Exception):
    """No chunk finished for `stall_seconds`; the line is dead and we move on."""


def parse_line_plan(spec: str | None, single: str | None = None) -> list[str]:
    """`--lines "ws,qn,bldsa"` (or `--line ws`) -> validated ordered plan."""
    if single:
        spec = single
    raw = (spec or ",".join(DEFAULT_LINES)).replace(" ", "")
    plan = [item for item in raw.split(",") if item]
    if not plan:
        raise BiliError("invalid_argument", "线路列表不能为空。", hint="例如 --lines ws,qn,bldsa")
    unknown = [item for item in plan if item not in ALL_LINES]
    if unknown:
        raise BiliError(
            "invalid_argument",
            f"未知线路：{'、'.join(unknown)}。",
            hint=f"可选：{' / '.join(ALL_LINES)}",
        )
    seen: list[str] = []
    for item in plan:
        if item not in seen:
            seen.append(item)
    return seen


def attempts_for(plan: list[str]) -> list[str]:
    """Expand the plan into concrete attempts, honouring the per-line retry budget."""
    expanded: list[str] = []
    for line in plan:
        expanded.extend([line] * max(1, LINE_RETRIES.get(line, 1)))
    return expanded


def line_host(line: str) -> str:
    """The host this line uploads to, taken from bilibili-api's own table."""
    probe = str(LINES_INFO[line].get("probe_url") or "")
    return probe.split("//", 1)[-1].split("/", 1)[0] or "unknown"


def resolve_line_ip(line: str) -> str | None:
    try:
        return socket.gethostbyname(line_host(line))
    except OSError:
        return None


def resolve_host_ip(host: str) -> str | None:
    """The preupload response names the real upload host; that is the node worth logging."""
    try:
        return socket.gethostbyname(host)
    except OSError:
        return None


def host_from_endpoint(endpoint: str) -> str | None:
    """`//upos-sz-upcdnws.bilivideo.com` (or with a path) -> `upos-sz-upcdnws.bilivideo.com`."""
    if not endpoint:
        return None
    host = endpoint.split("//", 1)[-1].split("/", 1)[0].strip()
    return host or None


async def upload_page_watched(uploader, page, stall_seconds: float, on_progress=None) -> dict[str, Any]:
    """Upload one page, aborting when no chunk completes for `stall_seconds`."""
    beat = time.monotonic()
    done_chunks = 0

    def on_chunk(_data=None) -> None:
        nonlocal beat, done_chunks
        beat = time.monotonic()
        done_chunks += 1
        if on_progress is not None:
            on_progress(done_chunks)

    event = VideoUploaderEvents.AFTER_CHUNK.value
    uploader.add_event_listener(event, on_chunk)
    task = asyncio.ensure_future(uploader._upload_page(page))
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=1.0)
            if task in done:
                return task.result()
            if time.monotonic() - beat > stall_seconds:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: B014 - the point is to drop it
                    pass
                raise UploadStalled(
                    f"{stall_seconds:g} 秒没有任何分片完成（已收 {done_chunks} 个分片）"
                )
    finally:
        uploader.remove_event_listener(event, on_chunk)


def is_line_error(exc: BaseException) -> bool:
    """Certificate / connection failures mean "try the next line"; a rejected submission does not."""
    text = f"{type(exc).__name__}: {exc}".lower()
    markers = (
        "ssl", "certificate", "certificateverifyerror", "curl: (60)", "curl: (35)", "curl: (56)",
        "connection", "timed out", "timeout", "reset", "broken pipe", "eof", "network",
    )
    return any(marker in text for marker in markers)
