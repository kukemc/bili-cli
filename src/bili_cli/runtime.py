"""Glue between synchronous Typer commands and the async bilibili-api."""

from __future__ import annotations

import asyncio
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from . import output
from .errors import BiliError, from_exception

T = TypeVar("T")

CST = timezone(timedelta(hours=8))

# For requests made with httpx directly (endpoints bilibili-api does not wrap);
# without a browser UA and Referer Bilibili answers HTTP 412.
WEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}


def web_client(cred=None, referer: str | None = None, timeout: float = 20):
    """An httpx client that looks like the web app, optionally logged in."""
    import httpx

    headers = dict(WEB_HEADERS)
    if referer:
        headers["Referer"] = referer
    cookies = {}
    if cred is not None:
        cookies = {
            k: v
            for k, v in {
                "SESSDATA": cred.sessdata,
                "bili_jct": cred.bili_jct,
                "DedeUserID": cred.dedeuserid,
                "buvid3": cred.buvid3,
                "buvid4": cred.buvid4,
            }.items()
            if v
        }
    return httpx.AsyncClient(headers=headers, cookies=cookies, timeout=timeout)


DEFAULT_DEADLINE = 180.0  # seconds; a read command that runs longer than this is stuck, not slow
HARD_EXIT_GRACE = 20.0
_DEFAULT = object()


def default_deadline() -> float | None:
    """BILI_CLI_TIMEOUT overrides the limit (seconds); 0 turns it off."""
    import os

    raw = os.environ.get("BILI_CLI_TIMEOUT")
    try:
        value = float(raw) if raw else DEFAULT_DEADLINE
    except ValueError:
        value = DEFAULT_DEADLINE
    return value if value > 0 else None


def _timeout_error(deadline: float) -> BiliError:
    # `int()` 会把 0.001 抹成 `0`，配上 hint 里那句「0 表示不限」，读起来像自相矛盾。
    return BiliError(
        "network_error",
        f"命令超过 {deadline:g} 秒还没完成，已中止（多半是和 B 站的连接卡住了）。",
        hint="稍后重试即可，读操作重试是安全的。确实需要更久时，把 BILI_CLI_TIMEOUT 调大"
             "（单位秒；显式设成 0 才表示不限）。",
        detail={"timeout": deadline},
    )


def _hard_exit(deadline: float) -> None:
    """Last resort from a watchdog thread when the event loop itself is stuck and cannot be cancelled.

    The error is written from yet another thread: if stdout is a pipe nobody reads (a full pipe is one
    way a process "hangs"), the write would block too, and the process must exit anyway.
    """
    import json
    import os
    import threading

    from . import health

    err = health.enrich(_timeout_error(deadline))
    doc = (json.dumps({"ok": False, "error": err.to_dict()}, ensure_ascii=False) + "\n").encode("utf-8")

    def write() -> None:
        try:
            sys.stdout.buffer.write(doc)
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass

    writer = threading.Thread(target=write, daemon=True)
    writer.start()
    writer.join(2)
    os._exit(err.exit_code)


async def _within(coro_fn: Callable[[], Awaitable[Any]], deadline: float | None) -> Any:
    from . import health

    probe = asyncio.ensure_future(health.loop_probe())
    try:
        if not deadline:
            return await coro_fn()
        task = asyncio.ensure_future(coro_fn())
        # asyncio.wait, not wait_for: wait_for can swallow the cancellation before Python 3.12
        done, _ = await asyncio.wait({task}, timeout=deadline)
        if task in done:
            return task.result()
        task.cancel()
        await asyncio.wait({task}, timeout=5)
        raise _timeout_error(deadline)
    finally:
        probe.cancel()


def run(coro_fn: Callable[[], Awaitable[Any]], deadline: Any = _DEFAULT) -> None:
    """Run a command body, print its result as JSON, and map every failure to a BiliError.

    Every command gets an overall deadline (default_deadline(), 3 minutes) so a stalled connection
    turns into a network_error instead of a process that never returns; long jobs pass their own
    deadline or None. If the event loop is blocked and cannot even be cancelled, a watchdog thread
    prints the same error and exits HARD_EXIT_GRACE seconds later.
    """
    import threading

    from . import health

    if sys.platform == "win32":
        # The proactor loop spams "Event loop is closed" on exit with some HTTP clients.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    health.start_heartbeat()
    health.install_request_guard()
    limit = default_deadline() if deadline is _DEFAULT else deadline
    watchdog = None
    if limit:
        watchdog = threading.Timer(limit + HARD_EXIT_GRACE, _hard_exit, args=(limit,))
        watchdog.daemon = True
        watchdog.start()
    try:
        result = asyncio.run(_within(coro_fn, limit))
    except KeyboardInterrupt:
        raise
    except BaseException as exc:  # noqa: BLE001 - every failure must become JSON
        import typer

        if isinstance(exc, (typer.Exit, SystemExit)):
            raise
        output.fail(health.enrich(from_exception(exc)))
    else:
        if result is not None:
            output.success(result)
    finally:
        if watchdog is not None:
            watchdog.cancel()


_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
_AV_RE = re.compile(r"(?:^|/|\b)av(\d+)", re.IGNORECASE)


def parse_video_id(value: str) -> dict[str, Any]:
    """Accept BV号, av号 (av170001 / 170001) or a video URL; return kwargs for bilibili_api.video.Video."""
    value = value.strip()
    m = _BV_RE.search(value)
    if m:
        return {"bvid": m.group(1)}
    m = _AV_RE.search(value)
    if m:
        return {"aid": int(m.group(1))}
    if value.isdigit():
        return {"aid": int(value)}
    raise BiliError("invalid_argument", f"无法识别的视频 ID：{value!r}，支持 BV 号、av 号或视频链接。")


def iso_time(ts: int | float | str | None) -> str | None:
    # some endpoints (dynamic vote cards) send timestamps as numeric strings
    if not ts or not str(ts).lstrip("-").replace(".", "", 1).isdigit() or float(ts) == 0:
        return None
    return datetime.fromtimestamp(float(ts), CST).isoformat()


_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    import html

    return html.unescape(_TAG_RE.sub("", text or ""))


def read_text(text: str | None, file: Path | None) -> str:
    """Resolve message text from a positional argument, --file PATH, or --file - (stdin)."""
    if text is not None and file is not None:
        raise BiliError("invalid_argument", "文本参数和 --file 只能二选一。")
    if file is not None:
        if str(file) == "-":
            content = sys.stdin.buffer.read().decode("utf-8")
        else:
            try:
                content = file.read_text(encoding="utf-8")
            except OSError as exc:
                raise BiliError("invalid_argument", f"无法读取文件 {file}：{exc}") from exc
    elif text is not None:
        content = text
    else:
        raise BiliError("invalid_argument", "缺少文本内容：传入文本参数或使用 --file。")
    content = content.strip()
    if not content:
        raise BiliError("invalid_argument", "文本内容为空。")
    return content


def parse_timestamp(value: str) -> int:
    """'90', '1:30' or '0:01:30' -> seconds."""
    seconds = parse_seconds(value)
    if not seconds.is_integer():
        raise BiliError("invalid_argument", f"这里的时间只能精确到秒，收到 {value!r}。")
    return int(seconds)


def parse_seconds(value: str) -> float:
    """Like parse_timestamp, but the seconds part may have a fraction: '1:30.5' -> 90.5."""
    parts = value.strip().replace("：", ":").split(":")
    try:
        nums = [int(p) for p in parts[:-1]] + [float(parts[-1])]
    except ValueError as exc:
        raise BiliError("invalid_argument", f"无法识别的时间 {value!r}，请用 秒、分:秒 或 时:分:秒（秒可带小数，如 1:30.5）。") from exc
    if len(nums) > 3 or any(n < 0 for n in nums) or any(n != n or n == float("inf") for n in nums):
        raise BiliError("invalid_argument", f"无法识别的时间 {value!r}。")
    total = 0.0
    for n in nums:
        total = total * 60 + n
    return total
