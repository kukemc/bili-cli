"""One compact video card for every list an agent browses (recommend, popular, rank, weekly...),
plus the local seen-list that lets an agent skip videos it already watched."""

from __future__ import annotations

import json
import time
from typing import Any

from . import config
from .runtime import iso_time

DESC_MAX = 80


def _reason(item: dict[str, Any]) -> str | None:
    r = item.get("rcmd_reason")
    if isinstance(r, dict):
        r = r.get("content")
    return r or None


def video_card(item: dict[str, Any]) -> dict[str, Any]:
    owner = item.get("owner") or {}
    stat = item.get("stat") or {}
    desc = (item.get("desc") or "").strip()
    bvid = item.get("bvid")
    card: dict[str, Any] = {
        "bvid": bvid,
        "title": item.get("title"),
        "author": {"mid": owner.get("mid"), "name": owner.get("name")},
        "duration": item.get("duration"),
        "pubdate": iso_time(item.get("pubdate")),
        "stat": {k: stat.get(k) for k in ("view", "like", "danmaku", "reply")},
        "reason": _reason(item),
        "url": f"https://www.bilibili.com/video/{bvid}",
    }
    if desc and desc != "-":
        card["desc"] = desc[:DESC_MAX] + ("…" if len(desc) > DESC_MAX else "")
    if item.get("is_followed"):
        card["followed_author"] = True
    return card


# ---- seen-list (local, per account profile)


def _seen_path():
    return config.state_home() / "seen.json"


def load_seen() -> dict[str, float]:
    try:
        return json.loads(_seen_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def mark_seen(bvid: str) -> None:
    seen = load_seen()
    seen[bvid] = time.time()
    try:
        path = _seen_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(seen), encoding="utf-8")
    except OSError as exc:
        from . import output

        output.info(f"警告：无法保存已看列表（{exc}）；不影响本次结果。")


def filter_unseen(cards: list[dict[str, Any]], unseen: bool) -> list[dict[str, Any]]:
    if not unseen:
        return cards
    seen = load_seen()
    return [c for c in cards if c.get("bvid") not in seen]
