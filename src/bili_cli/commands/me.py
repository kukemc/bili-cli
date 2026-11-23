"""bili me: view and edit my own profile."""

from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Any

import typer
from bilibili_api import Credential
from bilibili_api.utils.network import Api

from .. import config, guard, output
from ..errors import BiliError, from_exception
from ..runtime import run, web_client

app = typer.Typer(help="我的账号：资料、创作中心数据；修改昵称、签名、头像。", no_args_is_help=True)

ACCOUNT_URL = "https://api.bilibili.com/x/member/web/account"
MYINFO_URL = "https://api.bilibili.com/x/space/myinfo"
UPDATE_URL = "https://api.bilibili.com/x/member/web/update"
# Not wrapped by bilibili-api; same request as the web settings page and PiliPlus.
FACE_UPDATE_URL = "https://api.bilibili.com/x/member/web/face/update"

SIGN_MAX_LEN = 70
AVATAR_SIZE = 512


async def _account(cred: Credential) -> dict[str, Any]:
    return await Api(url=ACCOUNT_URL, method="GET", credential=cred).result


async def _update_profile(cred: Credential, **changes: str) -> None:
    """The update endpoint wants every field, so resend the current values with the changes applied."""
    acc = await _account(cred)
    data = {"uname": acc.get("uname"), "usersign": acc.get("sign"), "sex": acc.get("sex"), "birthday": acc.get("birthday")}
    data.update(changes)
    await Api(url=UPDATE_URL, method="POST", verify=True, credential=cred).update_data(**data).result


@app.command()
def show() -> None:
    """我的资料：UID、昵称、签名、等级、硬币、头像、改名是否免费。"""

    async def body():
        cred = config.require_credential()
        info = await Api(url=MYINFO_URL, method="GET", credential=cred).result
        acc = await _account(cred)
        if output.settings.raw:
            return {"myinfo": info, "account": acc}
        return {
            "mid": info.get("mid"),
            "name": info.get("name"),
            "sign": info.get("sign"),
            "level": info.get("level"),
            "coins": info.get("coins"),
            "face": info.get("face"),
            "sex": acc.get("sex"),
            "rename_free": bool(acc.get("nick_free")),
            "url": f"https://space.bilibili.com/{info.get('mid')}",
        }

    run(body)


@app.command()
def favs() -> None:
    """我的收藏夹：ID、名称、视频数（`bili video fav --folder` 用）。"""

    async def body():
        from .video import _folders

        return {"folders": await _folders(config.require_credential())}

    run(body)


FAV_VIDEO_TYPE = 2  # media type in a favorite folder: 2 video, 12 audio, 21 collection
FAV_MAX_PAGES = 50  # 20 per page; a --since scan that goes further reports `truncated` instead
# Pause between pages of one --since scan. Reading ~100 pages back to back got the account's
# fav/resource/list answered with HTTP 412 for over half an hour.
FAV_PAGE_DELAY = 5.0
# A --since scan stops itself (and returns what it has, with `next`) before run()'s 180 s deadline.
FAV_SCAN_BUDGET = 120.0
FAV_BATCH_MAX = 100  # videos per copy / move / sort request; 100 in one copy request has been tried against B 站
FAV_FOLDER_ADD_URL = "https://api.bilibili.com/x/v3/fav/folder/add"
FAV_FOLDER_SORT_URL = "https://api.bilibili.com/x/v3/fav/folder/sort"
FAV_RESOURCE_URL = "https://api.bilibili.com/x/v3/fav/resource/{action}"  # copy / move / sort


def default_folder(folders: list[dict[str, Any]]) -> dict[str, Any]:
    """The default folder, or the first one if none is marked (B 站 lists the default first)."""
    return next((f for f in folders if f["default"]), folders[0])


def pick_folder(folders: list[dict[str, Any]], name: str | None) -> dict[str, Any]:
    """Resolve a folder by ID or exact title; no name means the default folder."""
    if not folders:
        raise BiliError("not_found", "这个账号还没有收藏夹。")
    if name is None:
        return default_folder(folders)
    by_id = next((f for f in folders if str(f["id"]) == name), None)
    by_title = next((f for f in folders if f["title"] == name), None)
    if by_id is not None and by_title is not None and by_id["id"] != by_title["id"]:
        raise BiliError(
            "invalid_argument",
            f"{name!r} 既是一个收藏夹的 ID，又是另一个收藏夹的名称。",
            hint="改用另一个收藏夹的名称，或者先给同名收藏夹改名。",
            detail={"by_id": {"id": by_id["id"], "title": by_id["title"]}, "by_title": {"id": by_title["id"], "title": by_title["title"]}},
        )
    hit = by_id or by_title
    if hit is None:
        raise BiliError(
            "not_found",
            f"找不到收藏夹：{name}",
            hint="用 `bili me favs` 查看收藏夹的 ID 和名称。",
            detail={"folders": [f["title"] for f in folders]},
        )
    return hit


def fav_media(m: dict[str, Any]) -> dict[str, Any]:
    """One item of x/v3/fav/resource/list, trimmed for an agent."""
    upper = m.get("upper") or {}
    out = {
        "bvid": m.get("bvid") or m.get("bv_id"),
        "title": m.get("title"),
        "up": upper.get("name"),
        "up_mid": upper.get("mid"),
        "duration": m.get("duration"),
        "pubtime": m.get("pubtime"),
        "fav_time": m.get("fav_time"),
    }
    # attr != 0 marks videos deleted or hidden by their uploader ("已失效视频")
    if m.get("attr"):
        out["invalid"] = True
    return out


def collect_fav_page(medias: list[dict[str, Any]], since: int | None) -> tuple[list[dict[str, Any]], int, bool]:
    """Trim one page; returns (items, non-video count, whether an item older than `since` was reached).

    `since` is inclusive. A batch copy / move gives every video in it the same fav_time, so a poll
    that lands between two operations in one second would lose the second batch if that second
    were skipped. Videos from that second come back once more; callers dedupe by bvid.
    An item without fav_time is listed and does not end the scan: its age is unknown.
    """
    items, skipped = [], 0
    for m in medias:
        if m.get("type", FAV_VIDEO_TYPE) != FAV_VIDEO_TYPE:
            skipped += 1
            continue
        if since is not None and m.get("fav_time") is not None and m["fav_time"] < since:
            return items, skipped, True
        items.append(fav_media(m))
    return items, skipped, False


@app.command("fav-videos")
def fav_videos(
    folder: str = typer.Argument(None, help="收藏夹 ID 或名称；不填是“默认收藏夹”。用 `bili me favs` 查看。"),
    page: int = typer.Option(
        1, "--page", min=1, help="页码，从 1 开始，每页 20 条，按收藏时间从新到旧。和 --since 一起用时是起始页，用来接着上次 `truncated` 的地方读。"
    ),
    since: int = typer.Option(
        None,
        "--since",
        help=(
            "只列收藏时间（fav_time，Unix 秒）不早于这个值的视频，自动翻页直到更早的为止；用于轮询新加入的视频。"
            "和 since 同一秒的视频会再列一次，请按 bvid 去重。"
        ),
    ),
) -> None:
    """列出收藏夹里的视频：BV 号、标题、UP 主、时长、收藏时间。只读。"""

    async def body():
        import asyncio

        from bilibili_api import favorite_list

        from .video import _folders

        cred = config.require_credential()
        f = pick_folder(await _folders(cred), folder)
        pages = range(page, page + FAV_MAX_PAGES) if since is not None else [page]
        items: list[dict[str, Any]] = []
        raw_pages = []
        has_more = reached = False
        skipped = 0
        stopped: dict[str, Any] | None = None  # why a --since scan ended early, if it did
        resume = page
        started = time.monotonic()
        for pn in pages:
            resume = pn
            if since is not None and pn > page:
                if time.monotonic() - started + FAV_PAGE_DELAY > FAV_SCAN_BUDGET:
                    stopped = {"code": "time_budget", "message": f"这次扫描已经用了约 {int(FAV_SCAN_BUDGET)} 秒，先返回读到的部分。"}
                    break
                await asyncio.sleep(FAV_PAGE_DELAY)
            try:
                data = await favorite_list.get_video_favorite_list_content(f["id"], page=pn, credential=cred) or {}
            except Exception as exc:
                err = exc if isinstance(exc, BiliError) else from_exception(exc)
                if since is None or pn == page:
                    raise err from exc
                stopped = {"code": err.code, "message": err.message}  # keep the pages already read
                break
            raw_pages.append(data)
            has_more = bool(data.get("has_more"))
            got, skip, reached = collect_fav_page(data.get("medias") or [], since)
            items += got
            skipped += skip
            resume = pn + 1
            if since is None or reached or not has_more:
                break
        if output.settings.raw:
            return raw_pages[0] if since is None else raw_pages
        out: dict[str, Any] = {"folder": {"id": f["id"], "title": f["title"], "count": f["count"]}}
        if since is None:
            out["page"] = page
            out["has_more"] = has_more
            if has_more:
                out["next"] = f"bili me fav-videos {f['id']} --page {page + 1}"
        else:
            out["since"] = since
            times = [i["fav_time"] for i in items if i["fav_time"] is not None]
            if times:
                out["newest_fav_time"] = max(times)
            if stopped is not None or (has_more and not reached):
                out["truncated"] = True
                out["next"] = f"bili me fav-videos {f['id']} --since {since} --page {resume}"
                if stopped is not None:
                    out["stopped"] = stopped
                if stopped is not None and stopped["code"] in ("risk_control", "captcha_required"):
                    head = "被 B 站风控拦截，结果不完整。先停下来告诉用户，不要循环重试；用户同意后再运行 next 接着读"
                elif stopped is not None:
                    head = "扫描被中断，结果不完整。稍后运行 next 接着读"
                else:
                    head = f"翻了 {FAV_MAX_PAGES} 页还没遇到更早的视频，结果不完整。运行 next 接着读"
                out["note"] = head + "；两次之间收藏夹有增减时，接着读可能重复或漏掉几条，按 bvid 去重。"
        if skipped:
            out["skipped_non_video"] = skipped
        out["items"] = items
        return out

    run(body)


def _aid(bvid: str) -> int:
    from bilibili_api.utils.aid_bvid_transformer import aid2bvid, bvid2aid

    bad = BiliError("invalid_argument", f"不是有效的 BV 号：{bvid}", hint="收藏夹操作只接受 BV 号，例如 BV1xx411c7mD。")
    if not (len(bvid) == 12 and bvid[:2].upper() == "BV"):
        raise bad
    canonical = "BV" + bvid[2:]
    try:
        aid = bvid2aid(canonical)
    except (ValueError, KeyError, IndexError) as exc:
        raise bad from exc
    if aid <= 0 or aid2bvid(aid) != canonical:
        raise bad
    return aid


def _unique_videos(videos: list[str]) -> list[tuple[str, int]]:
    """(canonical bvid, aid) per distinct video; `bv1…` and `BV1…` are the same video."""
    from bilibili_api.utils.aid_bvid_transformer import aid2bvid

    seen: dict[int, str] = {}
    for v in videos:
        seen.setdefault(_aid(v), "")
    if len(seen) > FAV_BATCH_MAX:
        raise BiliError("invalid_argument", f"一次最多 {FAV_BATCH_MAX} 个视频，这次是 {len(seen)} 个。", hint="分成几批执行。")
    return [(aid2bvid(a), a) for a in seen]



async def _fav_post(cred: Credential, url: str, **data: Any) -> Any:
    return await Api(url=url, method="POST", verify=True, credential=cred).update_data(**data).result


def _folder_card(f: dict[str, Any]) -> dict[str, Any]:
    return {"id": f["id"], "title": f["title"]}


@app.command("fav-create")
def fav_create(
    title: str = typer.Argument(..., help="收藏夹名称。"),
    intro: str = typer.Option("", "--intro", help="收藏夹简介。"),
    public: bool = typer.Option(False, "--public", help="公开收藏夹；默认私密。"),
) -> None:
    """新建收藏夹（默认私密）。新收藏夹排在默认收藏夹之后、其他收藏夹之前；用 `bili me fav-sort` 调位置。"""

    async def body():
        from .video import _folders

        cred = config.require_credential()
        folders = await _folders(cred)
        if any(f["title"] == title for f in folders):
            raise BiliError("invalid_argument", f"已经有叫 {title!r} 的收藏夹。", hint="换一个名称；同名收藏夹会让按名称选收藏夹变得不确定。")
        if any(str(f["id"]) == title for f in folders):
            raise BiliError("invalid_argument", f"{title!r} 是另一个收藏夹的 ID。", hint="换一个名称；否则按 ID 或名称选收藏夹会分不清。")
        params = {"title": title, "intro": intro, "private": not public}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "me.fav-create", "params": params}
        guard.check("favorite")
        res = await _fav_post(cred, FAV_FOLDER_ADD_URL, title=title, intro=intro, privacy=0 if public else 1) or {}
        guard.record("favorite", params, "ok")
        return {"id": res.get("id"), **params}

    run(body)


def _transfer(action: str, src: str, dst: str, videos: list[str]) -> None:
    async def body():
        from .video import _folders

        cred = config.require_credential()
        folders = await _folders(cred)
        source, target = pick_folder(folders, src), pick_folder(folders, dst)
        if source["id"] == target["id"]:
            raise BiliError("invalid_argument", "源收藏夹和目标收藏夹是同一个。")
        vids = _unique_videos(videos)
        bvids, aids = [b for b, _ in vids], [a for _, a in vids]
        params = {"from": _folder_card(source), "to": _folder_card(target), "bvids": bvids}
        if output.settings.dry_run:
            return {"dry_run": True, "action": f"me.fav-{action}", "params": params}
        guard.check("favorite")
        data: dict[str, Any] = {
            "src_media_id": source["id"],
            "tar_media_id": target["id"],
            "resources": ",".join(f"{a}:{FAV_VIDEO_TYPE}" for a in aids),
            "platform": "web",
        }
        if action == "copy":
            data["mid"] = config.my_mid(cred)
        await _fav_post(cred, FAV_RESOURCE_URL.format(action=action), **data)
        guard.record("favorite", params, "ok")
        return {
            **params,
            "count": len(bvids),
            "note": (
                "B 站返回成功，但没有逐个确认：不在源收藏夹里的视频也算在 count 里。"
                "目标收藏夹里这些视频的收藏时间（fav_time）是这次操作的时间，原来的收藏时间不保留；同一批视频的收藏时间相同。"
            ),
        }

    run(body)


@app.command("fav-copy")
def fav_copy(
    src: str = typer.Argument(..., help="源收藏夹 ID 或名称。"),
    dst: str = typer.Argument(..., help="目标收藏夹 ID 或名称。"),
    videos: list[str] = typer.Argument(..., help=f"BV 号（一次最多 {FAV_BATCH_MAX} 个）。"),
) -> None:
    """把视频从一个收藏夹复制到另一个，源收藏夹保持不变。目标里的收藏时间会变成现在。"""
    _transfer("copy", src, dst, videos)


@app.command("fav-move")
def fav_move(
    src: str = typer.Argument(..., help="源收藏夹 ID 或名称。"),
    dst: str = typer.Argument(..., help="目标收藏夹 ID 或名称。"),
    videos: list[str] = typer.Argument(..., help=f"BV 号（一次最多 {FAV_BATCH_MAX} 个）。"),
) -> None:
    """把视频从一个收藏夹移到另一个（源收藏夹里就没有了）。目标里的收藏时间会变成现在。"""
    _transfer("move", src, dst, videos)


def folder_order(folders: list[dict[str, Any]], target: dict[str, Any], after: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The full folder list with `target` placed after `after` (None: right after the default folder)."""
    default = default_folder(folders)
    if target["id"] == default["id"]:
        raise BiliError("invalid_argument", "默认收藏夹固定在最前面，不能排序。")
    rest = [f for f in folders if f["id"] not in (default["id"], target["id"])]
    pos = 0 if after is None or after["id"] == default["id"] else next(i for i, f in enumerate(rest) if f["id"] == after["id"]) + 1
    return [default, *rest[:pos], target, *rest[pos:]]


@app.command("fav-sort")
def fav_sort(
    folder: str = typer.Argument(..., help="要移动的收藏夹 ID 或名称。"),
    after: str = typer.Option(None, "--after", help="排到这个收藏夹（ID 或名称）后面。"),
    first: bool = typer.Option(False, "--first", help="排到最前面（默认收藏夹之后）。"),
) -> None:
    """调整收藏夹之间的顺序：把一个收藏夹排到另一个后面，或排到最前面。"""

    async def body():
        from .video import _folders

        if (after is None) == (not first):
            raise BiliError("invalid_argument", "--after 和 --first 要指定一个，且只能指定一个。")
        cred = config.require_credential()
        folders = await _folders(cred)
        target = pick_folder(folders, folder)
        ref = pick_folder(folders, after) if after is not None else None
        if ref is not None and ref["id"] == target["id"]:
            raise BiliError("invalid_argument", "不能排到自己后面。")
        order = folder_order(folders, target, ref)
        cards = [_folder_card(f) for f in order]
        if sum(1 for f in folders if f["default"]) != 1:
            raise BiliError("api_error", "认不出唯一的默认收藏夹，为了不打乱顺序没有排序。", detail={"folders": [_folder_card(f) for f in folders]})
        if [f["id"] for f in order] == [f["id"] for f in folders]:
            return {"changed": False, "order": cards}
        params = {"folder": target["title"], "order": [f["title"] for f in order]}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "me.fav-sort", "params": params}
        guard.check("favorite")
        await _fav_post(cred, FAV_FOLDER_SORT_URL, sort=",".join(str(f["id"]) for f in order))
        guard.record("favorite", params, "ok")
        return {"changed": True, "order": cards, "note": "B 站返回成功，结果没有读回确认；用 `bili me favs` 查看。"}

    run(body)


def video_sort_steps(aids: list[int], after: int | None) -> str:
    """resource/sort steps placing `aids` in order, the first one after `after` (None: at the top)."""
    prev = "0:0" if after is None else f"{after}:{FAV_VIDEO_TYPE}"
    steps = []
    for a in aids:
        steps.append(f"{prev}:{a}:{FAV_VIDEO_TYPE}")
        prev = f"{a}:{FAV_VIDEO_TYPE}"
    return ",".join(steps)


@app.command("fav-sort-videos")
def fav_sort_videos(
    folder: str = typer.Argument(..., help="收藏夹 ID 或名称。"),
    videos: list[str] = typer.Argument(..., help="BV 号，按想要的先后顺序。"),
    after: str = typer.Option(None, "--after", help="第一个视频排到这个视频（BV 号）后面。"),
    top: bool = typer.Option(False, "--top", help="第一个视频排到最前面。"),
) -> None:
    """调整收藏夹里视频的顺序：把这些视频按给定顺序排到最前面或某个视频后面。不改变收藏时间。"""

    async def body():
        from .video import _folders

        if (after is None) == (not top):
            raise BiliError("invalid_argument", "--after 和 --top 要指定一个，且只能指定一个。")
        vids = _unique_videos(videos)
        bvids, aids = [b for b, _ in vids], [a for _, a in vids]
        after_aid = _aid(after) if after is not None else None
        if after_aid is not None and after_aid in aids:
            raise BiliError("invalid_argument", "--after 的视频不能同时在要排序的视频里。")
        steps = video_sort_steps(aids, after_aid)
        cred = config.require_credential()
        f = pick_folder(await _folders(cred), folder)
        params = {"folder": _folder_card(f), "bvids": bvids, "after": after, "top": top}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "me.fav-sort-videos", "params": params}
        guard.check("favorite")
        await _fav_post(cred, FAV_RESOURCE_URL.format(action="sort"), media_id=f["id"], sort=steps)
        guard.record("favorite", params, "ok")
        return {
            **params,
            "note": (
                "B 站对不在这个收藏夹里的视频、格式不对的排序也可能返回成功，结果没有读回确认。排序不改变收藏时间；"
                "`bili me fav-videos` 会按新顺序列出，--since 轮询假设新收藏的视频在最前面，被排到后面的新视频会漏掉。"
            ),
        }

    run(body)


STATS_PERIODS = {"yesterday": -1, "week": 0, "month": 1, "3months": 2}


def _pct(basis_points: Any) -> float | None:
    """Creator-center ratios come in 1/10000 (9695 -> 96.95%)."""
    return None if basis_points is None else round(basis_points / 100, 2)


def summarize_stats(overview: dict, fans: dict, source: dict, compare: dict, survey: dict) -> dict[str, Any]:
    # a new account with no data gets null for overview / compare / audience and {"<date>": null} for survey
    overview, fans, source, compare, survey = overview or {}, fans or {}, source or {}, compare or {}, survey or {}

    def pair(key: str) -> dict[str, Any]:
        return {"now": overview.get(key), "previous": overview.get(f"{key}_last")}

    platforms = {k.removeprefix("new_"): v for k, v in (source.get("play_proportion") or {}).items() if k.startswith("new_") and v}
    audience = source.get("audience_proportion") or {}
    viewers = (audience.get("fans") or 0) + (audience.get("guest") or 0)
    day = next(iter(survey.values()), None) or {}
    return {
        "data_date": str(overview.get("log_date") or fans.get("log_date") or "") or None,
        "plays": pair("play"),
        "visitors": pair("visitor"),
        "new_fans": pair("fan"),
        # this endpoint currently reports interactions as 0; leave them out rather than mislead
        **({"interactions": inter} if any((inter := {k: overview.get(k) for k in ("like", "fav", "coin", "comment", "dm", "share")}).values()) else {}),
        "fans": {
            "total": fans.get("all_fans"),
            "followed": fans.get("follow"),
            "unfollowed": fans.get("unfollow"),
            "interacting_percent": _pct(fans.get("interact_fans_per")),
        },
        "play_platforms_percent": {k: _pct(v) for k, v in sorted(platforms.items(), key=lambda kv: -kv[1])},
        "plays_from_fans_percent": round(audience.get("fans", 0) * 100 / viewers, 2) if viewers else None,
        "beats_similar_ups_percent": {k: _pct(compare.get(f"{k}_pass_peer")) for k in ("play", "like", "new_fans", "replydm")},
        "top_videos_yesterday": [{"bvid": a.get("bvid"), "title": a.get("title"), "plays": a.get("incr")} for a in (day.get("arc_inc") or [])[:5]],
    }


@app.command()
def stats(
    period: str = typer.Option("week", "--period", help="统计周期：yesterday / week / month / 3months（和上一周期对比）。"),
) -> None:
    """创作中心数据：播放、访客、涨粉、互动、播放端分布、粉丝占比、超过多少同类 UP、昨日涨得最多的视频。数据次日更新。累计总量和昨日增量看 `bili data overview`。"""

    async def body():
        from bilibili_api import creative_center as cc

        if period not in STATS_PERIODS:
            raise BiliError("invalid_argument", f"--period 只能是 {' / '.join(STATS_PERIODS)} 之一。")
        cred = config.require_credential()
        p = STATS_PERIODS[period]
        overview = await cc.get_overview(cred, cc.GraphPeriod(p))
        fans = await cc.get_fan_overview(cred, cc.FanGraphPeriod(p))
        source, compare, survey = await cc.get_video_source(cred), await cc.get_compare(cred), await cc.get_video_survey(cred)
        if output.settings.raw:
            return {"overview": overview, "fans": fans, "source": source, "compare": compare, "survey": survey}
        return {"period": period, **summarize_stats(overview, fans, source, compare, survey), "per_video_with": "bili upload stats <BV>"}

    run(body)


@app.command("set-sign")
def set_sign(sign: str = typer.Argument(..., help=f"新的个性签名，最多 {SIGN_MAX_LEN} 字；传空字符串可清空。")) -> None:
    """修改个性签名。"""

    async def body():
        cred = config.require_credential()
        if len(sign) > SIGN_MAX_LEN:
            raise BiliError("invalid_argument", f"签名最多 {SIGN_MAX_LEN} 字，当前 {len(sign)} 字。")
        if output.settings.dry_run:
            return {"dry_run": True, "action": "me.set_sign", "params": {"sign": sign}}
        guard.check("profile")
        await _update_profile(cred, usersign=sign)
        guard.record("profile", {"sign": sign}, "ok")
        return {"sign": sign, "updated": True}

    run(body)


@app.command("set-name")
def set_name(
    name: str = typer.Argument(..., help="新的昵称。"),
    allow_cost: bool = typer.Option(
        False, "--allow-cost", help="改名需要消耗硬币时，必须显式传入此参数才会执行。"
    ),
) -> None:
    """修改昵称。非免费改名会消耗硬币，需要 --allow-cost；昵称是否可用会先检查。"""

    async def body():
        from bilibili_api.user import check_nickname

        cred = config.require_credential()
        ok, reason = await check_nickname(name)
        if not ok:
            raise BiliError("invalid_argument", f"昵称不可用：{reason}")
        acc = await _account(cred)
        free = bool(acc.get("nick_free"))
        if not free and not allow_cost:
            raise BiliError(
                "invalid_argument",
                "本次改名需要消耗硬币。",
                hint="确认愿意花费硬币后，加上 --allow-cost 重新执行。",
                detail={"rename_free": False},
            )
        if output.settings.dry_run:
            return {"dry_run": True, "action": "me.set_name", "params": {"name": name, "rename_free": free}}
        guard.check("profile")
        await _update_profile(cred, uname=name)
        guard.record("profile", {"name": name}, "ok")
        return {"name": name, "updated": True, "rename_free": free}

    run(body)


def _square_png(path: Path) -> bytes:
    """Center-crop to a square and downscale, like the web uploader does."""
    from PIL import Image, UnidentifiedImageError

    try:
        img = Image.open(path)
        img.load()
    except (OSError, UnidentifiedImageError) as exc:
        raise BiliError("invalid_argument", f"无法读取图片 {path}：{exc}") from exc
    img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB")
    side = min(img.size)
    left, upper = (img.width - side) // 2, (img.height - side) // 2
    img = img.crop((left, upper, left + side, upper + side))
    if side > AVATAR_SIZE:
        img = img.resize((AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@app.command("set-avatar")
def set_avatar(image: Path = typer.Argument(..., help="头像图片路径（会居中裁成正方形）。")) -> None:
    """修改头像。"""

    async def body():
        cred = config.require_credential()
        png = _square_png(image)
        if output.settings.dry_run:
            return {"dry_run": True, "action": "me.set_avatar", "params": {"image": str(image), "bytes": len(png)}}
        guard.check("profile")
        async with web_client(cred, referer="https://account.bilibili.com/", timeout=30) as client:
            resp = await client.post(
                FACE_UPDATE_URL,
                params={"csrf": cred.bili_jct},
                data={"dopost": "save", "DisplayRank": "10000"},
                files={"face": ("avatar.png", png, "image/png")},
            )
            resp.raise_for_status()
            res = resp.json()
        if res.get("code") != 0:
            from bilibili_api.exceptions import ResponseCodeException

            raise ResponseCodeException(res.get("code"), res.get("message"), res)
        guard.record("profile", {"avatar": str(image)}, "ok")
        return {
            "updated": True,
            "face": res.get("data"),
            "note": "新头像需要通过 B 站审核后才会显示，`bili me show` 在此之前仍返回旧头像。",
        }

    run(body)
