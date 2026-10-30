"""Execution guardrails for write operations: local rate limits and an audit log.

Each `bili` invocation is a separate process, so the history lives in the
config dir. Limits are deliberately conservative: an agent that hits one gets
`rate_limited` with `retry_after` and should wait rather than retry blindly.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from . import config
from .errors import BiliError

# action -> (minimum seconds between two actions, maximum actions per hour)
LIMITS: dict[str, tuple[int, int]] = {
    "comment": (10, 30),
    "danmaku": (10, 30),
    "like": (3, 120),
    "delete": (3, 60),
    "pin": (10, 20),
    "dynamic": (60, 10),
    "profile": (30, 10),
    "upload": (300, 5),
    "upload_edit": (60, 10),
    "season": (10, 20),
    "autoreply": (10, 20),
    # B 站 throttles interactive components harder than plain danmaku (36703 within a minute).
    "interact": (30, 20),
    "dm": (10, 20),
    "video_like": (5, 60),
    "favorite": (5, 60),
    "coin": (10, 5),
    "follow": (10, 20),
    "block": (10, 20),
}
# action -> maximum units per 24 hours (coins cost real money and cannot be taken back)
DAILY_LIMITS: dict[str, int] = {"coin": 5}
WINDOW_SECONDS = 3600
DAY_SECONDS = 24 * 3600


def _state_path():
    return config.state_home() / "ratelimit.json"


def _load() -> dict[str, list[float]]:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _disabled() -> bool:
    return os.environ.get("BILI_CLI_NO_RATE_LIMIT") == "1"


# with --wait, sleep through limits that clear within this many seconds; longer ones still fail
MAX_AUTO_WAIT = 60


def check(action: str, units: int = 1) -> None:
    """Raise rate_limited if `action` would exceed its local limit. `units` = how much one call uses (e.g. 2 coins)."""
    from . import output

    try:
        _check(action, units)
    except BiliError as e:
        wait = (e.detail or {}).get("retry_after") or 0
        if not output.settings.wait or action in DAILY_LIMITS or wait > MAX_AUTO_WAIT:
            raise
        output.info(f"{action} 限流，等待 {wait} 秒…")
        time.sleep(wait)
        _check(action, units)


def _check(action: str, units: int) -> None:
    if _disabled():
        return
    min_interval, per_hour = LIMITS[action]
    now = time.time()
    all_history = _load().get(action, [])
    daily = DAILY_LIMITS.get(action)
    if daily is not None:
        today = [t for t in all_history if now - t < DAY_SECONDS]
        if len(today) + units > daily:
            wait = int(DAY_SECONDS - (now - today[0])) + 1 if today else 0
            raise BiliError(
                "rate_limited",
                f"{action} 24 小时内最多 {daily} 次，已用 {len(today)} 次。",
                hint=f"今天的额度用完了，约 {wait // 3600 + 1} 小时后恢复。",
                detail={"action": action, "retry_after": wait, "limit": {"per_day": daily, "used": len(today)}},
            )
    history = [t for t in all_history if now - t < WINDOW_SECONDS]
    if history and now - history[-1] < min_interval:
        wait = int(min_interval - (now - history[-1])) + 1
        raise BiliError(
            "rate_limited",
            f"{action} 操作间隔至少 {min_interval} 秒。",
            hint=f"等待 {wait} 秒后重试。",
            detail={"action": action, "retry_after": wait, "limit": {"min_interval": min_interval, "per_hour": per_hour}},
        )
    if len(history) + units > per_hour:
        wait = int(WINDOW_SECONDS - (now - history[0])) + 1
        raise BiliError(
            "rate_limited",
            f"{action} 操作 1 小时内最多 {per_hour} 次。",
            hint=f"等待 {wait} 秒后重试。",
            detail={"action": action, "retry_after": wait, "limit": {"min_interval": min_interval, "per_hour": per_hour}},
        )


def record(action: str, params: dict[str, Any], result: Any, units: int = 1) -> None:
    """Remember a successful write for rate limiting and append it to the audit log.

    This runs *after* the action already happened, so a state-file write failure
    must never turn a successful write into a failed command: an agent would see
    the error and retry, sending the same comment / DM twice. Lose the local
    bookkeeping, keep the result.
    """
    now = time.time()
    try:
        state = _load()
        keep = DAY_SECONDS if action in DAILY_LIMITS else WINDOW_SECONDS
        state[action] = [t for t in state.get(action, []) if now - t < keep] + [now] * units
        home = config.state_home()
        home.mkdir(parents=True, exist_ok=True)
        tmp = _state_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, _state_path())

        entry = {"time": now, "action": action, "params": params, "result": result}
        with open(home / "audit.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:
        from . import output

        output.info(f"警告：无法写入限流/审计状态（{exc}）；操作本身已经成功，继续。")

