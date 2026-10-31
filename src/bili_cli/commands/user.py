"""bili user: look up any user's profile and uploads (for competitor research and replying with context)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import typer
from bilibili_api import favorite_list, search, user
from bilibili_api.exceptions import ResponseCodeException
from bilibili_api.utils.network import Api

from .. import config, output
from ..errors import BiliError
from ..runtime import iso_time, run, strip_html

app = typer.Typer(help="查看任意用户：资料与主页总览、投稿（置顶 / 代表作在前）、关注与粉丝、公开的投币 / 收藏夹 / 追番 / 点赞 / 游戏、合集、专栏；关注 / 取关、拉黑 / 解除拉黑。", no_args_is_help=True)

USER_ARG = typer.Argument(..., help="UID，或完整昵称（会用搜索找到昵称完全一致的用户）。")
VIDEO_ORDERS = {"new": user.VideoOrder.PUBDATE, "views": user.VideoOrder.VIEW, "favorites": user.VideoOrder.FAVORITE}

NAVNUM_URL = "https://api.bilibili.com/x/space/navnum"
LIKE_URL = "https://api.bilibili.com/x/space/like/video"
GAME_URL = "https://api.bilibili.com/x/space/lastplaygame/v2"
COIN_URL = "https://api.bilibili.com/x/space/coin/video"
PRIVACY_CODE = 53013  # 用户隐私设置未公开
PREVIEW = 3
BANGUMI_TYPES = {"bangumi": user.BangumiType.BANGUMI, "cinema": user.BangumiType.DRAMA}
FOLLOW_STATUS = {1: "想看", 2: "在看", 3: "看过"}


def _next_with(cmd: str, mid: int, page: int, size: int, total: Any, default_size: int, extra: str = "") -> str | None:
    """有下一页时给出翻页命令。"""
    if not total or page * size >= total:
        return None
    s = f"bili user {cmd} {mid}{extra} --page {page + 1}"
    return s if size == default_size else f"{s} --size {size}"


async def _public(fetch: Callable[[], Awaitable[Any]]) -> Any:
    """接口结果；对方没公开（53013）时返回 None。收藏夹接口未公开时本身就返回 None，调用方一并当作隐藏。其他错误照常抛出。"""
    try:
        return await fetch()
    except ResponseCodeException as exc:
        if exc.code == PRIVACY_CODE:
            return None
        raise


async def _preview(
    fetch: Callable[[], Awaitable[Any]],
    pick: Callable[[Any], list[str]],
    count: Callable[[Any], Any] | None = None,
) -> dict[str, Any]:
    """取一个板块的预览：{"preview": [...]} / {"hidden": True} / {}（出错）。"""
    try:
        data = await _public(fetch)
        if data is None:
            return {"hidden": True}
        result = {}
        if count is not None:
            total = count(data)
            if total is not None:
                result["count"] = total
        result["preview"] = pick(data)[:PREVIEW]
        return result
    except Exception:  # noqa: BLE001
        return {}


def _public_folders(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [f for f in data.get("list") or [] if not (f.get("attr") or 0) & 1]


def _hidden(mid: int) -> dict[str, Any]:
    return {"mid": mid, "hidden": True, "items": []}


async def resolve_mid(who: str) -> int:
    """A UID as-is; otherwise the user whose current nickname matches exactly."""
    who = who.strip().lstrip("@")
    if who.isdigit():
        return int(who)
    data = await search.search_by_type(who, search_type=search.SearchObjectType.USER)
    for r in data.get("result") or []:
        if strip_html(r.get("uname")) == who:
            return int(r["mid"])
    raise BiliError("not_found", f"找不到昵称为 {who!r} 的用户。", hint=f"用 `bili search user {who}` 找到 UID 再传入。")


@app.command()
def info(who: str = USER_ARG) -> None:
    """用户资料，加上主页各板块的总览：数量和前几条预览（投稿、投币、点赞、收藏夹、追番、游戏、合集、专栏、关注…）。hidden 表示对方没公开；要看全部用 more_with 里的命令。"""

    async def body():
        mid = await resolve_mid(who)
        cred = config.optional_credential()
        u = user.User(mid, credential=cred)
        data = await u.get_user_info()
        rel = await u.get_relation_info()
        try:
            up = await u.get_up_stat()  # needs login; skip quietly when anonymous
        except Exception:  # noqa: BLE001
            up = {}
        try:
            nav = await Api(url=NAVNUM_URL, method="GET", credential=cred).update_params(mid=mid).result
        except Exception:  # noqa: BLE001
            nav = {}
        if output.settings.raw:
            return {"info": data, "relation": rel, "up_stat": up, "navnum": nav}
        official = data.get("official") or {}
        live = data.get("live_room") or {}
        out: dict[str, Any] = {
            "mid": data.get("mid"),
            "name": data.get("name"),
            "sign": data.get("sign") or None,
            "level": data.get("level"),
            "official": official.get("title") or None,
            "fans": rel.get("follower"),
            "following": rel.get("following"),
            "url": f"https://space.bilibili.com/{data.get('mid')}",
        }
        if up:
            out["total_views"] = (up.get("archive") or {}).get("view")
            out["total_likes"] = up.get("likes")
        if live.get("roomid"):
            out["live"] = {"live_now": live.get("liveStatus") == 1, "title": live.get("title"), "url": f"https://live.bilibili.com/{live.get('roomid')}"}
        out["videos_with"] = f"bili user videos {data.get('mid')}"
        previews = await asyncio.gather(
            _preview(
                lambda: u.get_videos(pn=1, ps=PREVIEW),
                lambda d: [v.get("title") or "" for v in (d.get("list") or {}).get("vlist") or []],
            ),
            _preview(
                lambda: Api(url=COIN_URL, method="GET", credential=cred).update_params(vmid=mid).result,
                lambda d: [v.get("title") or "" for v in d or []],
            ),
            _preview(
                lambda: Api(url=LIKE_URL, method="GET", credential=cred).update_params(vmid=mid).result,
                lambda d: [v.get("title") or "" for v in d.get("list") or []],
            ),
            _preview(
                lambda: favorite_list.get_video_favorite_list(mid, credential=cred),
                lambda d: [f.get("title") or "" for f in _public_folders(d)],
            ),
            _preview(
                lambda: u.get_subscribed_bangumi(type_=user.BangumiType.BANGUMI, pn=1, ps=PREVIEW),
                lambda d: [v.get("title") or "" for v in d.get("list") or []],
            ),
            _preview(
                lambda: Api(url=GAME_URL, method="GET", credential=cred).update_params(mid=mid).result,
                lambda d: [g.get("game_name") or "" for g in d.get("list") or []],
                count=lambda d: d.get("total_count"),
            ),
            _preview(
                lambda: u.get_channel_list(pn=1, ps=PREVIEW),
                lambda d: [
                    (entry.get("meta") or {}).get("name") or ""
                    for key in ("seasons_list", "series_list")
                    for entry in (d.get("items_lists") or {}).get(key) or []
                ],
                count=lambda d: ((d.get("items_lists") or {}).get("page") or {}).get("total"),
            ),
            _preview(
                lambda: u.get_articles(pn=1, ps=PREVIEW),
                lambda d: [a.get("title") or "" for a in d.get("articles") or []],
            ),
            _preview(
                lambda: u.get_followings(pn=1, ps=PREVIEW),
                lambda d: [f.get("uname") or "" for f in d.get("list") or []],
            ),
        )
        preview_by_section = dict(zip(
            ("videos", "coins", "likes", "favs", "bangumi", "games", "collections", "articles", "following"),
            previews,
        ))
        nav = nav or {}
        counts = {
            "videos": nav.get("video"),
            "coins": None,
            "likes": None,
            "favs": (nav.get("favourite") or {}).get("guest"),
            "bangumi": nav.get("bangumi"),
            "cinema": nav.get("cinema"),
            "games": None,
            "collections": None,
            "articles": nav.get("article"),
            "following": rel.get("following"),
            "album": nav.get("album"),
            "audio": nav.get("audio"),
            "opus": nav.get("opus"),
        }
        sections = {}
        for name, total in counts.items():
            section = {"count": total} if total is not None else {}
            section.update(preview_by_section.get(name) or {})
            if section:
                sections[name] = section
        out["sections"] = sections
        out["more_with"] = f"bili user coins|favs|bangumi|likes|games|collections|articles|following|followers {mid}"
        return out

    run(body)


def _archive_card(v: dict[str, Any], label: str) -> dict[str, Any]:
    duration = v.get("duration")
    stat = v.get("stat") or {}
    return {
        "bvid": v.get("bvid"),
        "title": v.get("title"),
        "length": f"{duration // 60:02d}:{duration % 60:02d}" if duration is not None else None,
        "play": stat.get("view"),
        "comment": stat.get("reply"),
        "danmaku": stat.get("danmaku"),
        "pubdate": iso_time(v.get("pubdate")),
        "label": label,
    }


@app.command()
def videos(
    who: str = USER_ARG,
    order: str = typer.Option("new", "--order", help="排序：new（最新）/ views（最多播放）/ favorites（最多收藏）。"),
    keyword: str = typer.Option("", "--keyword", "-k", help="只看标题含关键词的投稿，如某个系列名。"),
    page: int = typer.Option(1, "--page", min=1, help="页码，从 1 开始。"),
    size: int = typer.Option(20, "--size", min=1, max=50, help="每页条数。"),
) -> None:
    """用户的投稿列表，含播放、评论、弹幕数，适合找爆款或追某个系列。第一页会把置顶和代表作排在最前面，带 label。"""

    async def body():
        if order not in VIDEO_ORDERS:
            raise BiliError("invalid_argument", f"--order 只能是 {' / '.join(VIDEO_ORDERS)} 之一。")
        mid = await resolve_mid(who)
        u = user.User(mid, credential=config.optional_credential())
        data = await u.get_videos(pn=page, ps=size, keyword=keyword, order=VIDEO_ORDERS[order])
        if output.settings.raw:
            for v in (data.get("list") or {}).get("vlist") or []:
                v.pop("ip", None)
            return data
        items = [
            {
                "bvid": v.get("bvid"),
                "title": v.get("title"),
                "length": v.get("length"),
                "play": v.get("play"),
                "comment": v.get("comment"),
                "danmaku": v.get("video_review"),
                "pubdate": iso_time(v.get("created")),
            }
            for v in (data.get("list") or {}).get("vlist") or []
        ]
        if page == 1 and not keyword:
            try:
                top = await u.get_top_videos()
            except Exception:  # noqa: BLE001
                top = None
            try:
                mp = await u.get_masterpiece() or []
            except Exception:  # noqa: BLE001
                mp = []
            front = ([(top, "置顶")] if top else []) + [(m, "代表作") for m in mp]
            seen = set()
            front_items = []
            for v, label in front:
                bvid = v.get("bvid")
                if bvid in seen:
                    continue
                seen.add(bvid)
                front_items.append(_archive_card(v, label))
            items = front_items + [v for v in items if v.get("bvid") not in seen]
        return {
            "mid": mid,
            "page": page,
            "total": (data.get("page") or {}).get("count"),
            "items": items,
        }

    run(body)


@app.command()
def coins(who: str = USER_ARG) -> None:
    """用户主页公开的最近投币：视频、投了几个币、什么时候投的。对方没公开时 hidden 为 true。"""

    async def body():
        cred = config.require_credential()
        mid = await resolve_mid(who)
        data = await _public(lambda: Api(url=COIN_URL, method="GET", credential=cred).update_params(vmid=mid).result)
        if data is None:
            return _hidden(mid)
        if output.settings.raw:
            for v in data:
                v.pop("ip", None)
            return data
        return {
            "mid": mid,
            "hidden": False,
            "items": [
                {
                    "bvid": v.get("bvid"),
                    "title": v.get("title"),
                    "coins": v.get("coins"),
                    "coined_at": iso_time(v.get("time")),
                    "owner": (v.get("owner") or {}).get("name"),
                    "watch_with": f"bili video watch {v.get('bvid')}",
                }
                for v in data
            ],
        }

    run(body)


@app.command()
def favs(who: str = USER_ARG) -> None:
    """用户公开的收藏夹：名称、视频数。私有收藏夹不列出；不展开夹里的视频。"""

    async def body():
        cred = config.require_credential()
        mid = await resolve_mid(who)
        data = await _public(lambda: favorite_list.get_video_favorite_list(mid, credential=cred))
        if data is None:
            return _hidden(mid)
        public = _public_folders(data)
        if output.settings.raw:
            return {**data, "list": public, "count": len(public)}
        items = [
            {
                "id": f.get("id"),
                "title": f.get("title"),
                "media_count": f.get("media_count"),
            }
            for f in public
        ]
        return {"mid": mid, "hidden": False, "count": len(items), "items": items}

    run(body)


@app.command()
def bangumi(
    who: str = USER_ARG,
    type_: str = typer.Option("bangumi", "--type", help="类型：bangumi（追番）/ cinema（追剧，电影、电视剧、纪录片等）。"),
    page: int = typer.Option(1, "--page", min=1, help="页码，从 1 开始。"),
    size: int = typer.Option(20, "--size", min=1, help="每页条数。"),
) -> None:
    """用户公开的追番 / 追剧列表：在看什么、看到第几集。对方没公开时 hidden 为 true。"""

    async def body():
        if type_ not in BANGUMI_TYPES:
            raise BiliError("invalid_argument", "--type 只能是 bangumi / cinema 之一。")
        cred = config.require_credential()
        mid = await resolve_mid(who)
        data = await _public(
            lambda: user.User(mid, credential=cred).get_subscribed_bangumi(
                type_=BANGUMI_TYPES[type_], pn=page, ps=size
            )
        )
        if data is None:
            return _hidden(mid)
        if output.settings.raw:
            return data
        out = {
            "mid": mid,
            "hidden": False,
            "type": type_,
            "page": page,
            "total": data.get("total"),
            "items": [
                {
                    "season_id": v.get("season_id"),
                    "title": v.get("title"),
                    "follow_status": FOLLOW_STATUS.get(v.get("follow_status"), v.get("follow_status")),
                    "progress": v.get("progress") or None,
                    "total": v.get("total_count") if (v.get("total_count") or 0) > 0 else None,
                    "url": v.get("url"),
                }
                for v in data.get("list") or []
            ],
        }
        nw = _next_with("bangumi", mid, page, size, data.get("total"), 20, extra=f" --type {type_}")
        if nw:
            out["next_with"] = nw
        return out

    run(body)


@app.command()
def likes(who: str = USER_ARG) -> None:
    """用户公开的最近点赞视频（最多 20 个，没有点赞时间）。对方没公开时 hidden 为 true。"""

    async def body():
        cred = config.require_credential()
        mid = await resolve_mid(who)
        data = await _public(lambda: Api(url=LIKE_URL, method="GET", credential=cred).update_params(vmid=mid).result)
        if data is None:
            return _hidden(mid)
        if output.settings.raw:
            for v in data.get("list") or []:
                v.pop("ip", None)
            return data
        return {
            "mid": mid,
            "hidden": False,
            "items": [
                {
                    "bvid": v.get("bvid"),
                    "title": v.get("title"),
                    "owner": (v.get("owner") or {}).get("name"),
                    "play": (v.get("stat") or {}).get("view"),
                    "watch_with": f"bili video watch {v.get('bvid')}",
                }
                for v in data.get("list") or []
            ],
        }

    run(body)


@app.command()
def games(who: str = USER_ARG) -> None:
    """用户公开的最近玩过的游戏。对方没公开时 hidden 为 true。"""

    async def body():
        cred = config.require_credential()
        mid = await resolve_mid(who)
        data = await _public(lambda: Api(url=GAME_URL, method="GET", credential=cred).update_params(mid=mid).result)
        if data is None:
            return _hidden(mid)
        if output.settings.raw:
            return data
        return {
            "mid": mid,
            "hidden": False,
            "total": data.get("total_count"),
            "items": [
                {"name": g.get("game_name"), "tags": g.get("game_tags"), "url": g.get("detail_url")}
                for g in data.get("list") or []
            ],
        }

    run(body)


@app.command()
def collections(
    who: str = USER_ARG,
    page: int = typer.Option(1, "--page", min=1, help="页码，从 1 开始。"),
    size: int = typer.Option(20, "--size", min=1, help="每页条数。"),
) -> None:
    """用户的合集和系列：名称、视频数。"""

    async def body():
        cred = config.require_credential()
        mid = await resolve_mid(who)
        data = await _public(lambda: user.User(mid, credential=cred).get_channel_list(pn=page, ps=size))
        if data is None:
            return _hidden(mid)
        if output.settings.raw:
            return data
        lists = data.get("items_lists") or {}
        items = []
        for key, kind, id_key in (("seasons_list", "合集", "season_id"), ("series_list", "系列", "series_id")):
            for entry in lists.get(key) or []:
                meta = entry.get("meta") or {}
                items.append({"kind": kind, "id": meta.get(id_key), "title": meta.get("name"), "count": meta.get("total")})
        total = (lists.get("page") or {}).get("total")
        out = {"mid": mid, "hidden": False, "page": page, "total": total, "items": items}
        nw = _next_with("collections", mid, page, size, total, 20)
        if nw:
            out["next_with"] = nw
        return out

    run(body)


@app.command()
def articles(
    who: str = USER_ARG,
    page: int = typer.Option(1, "--page", min=1, help="页码，从 1 开始。"),
    size: int = typer.Option(20, "--size", min=1, help="每页条数。"),
) -> None:
    """用户投稿的专栏文章：标题、发布时间、阅读数。"""

    async def body():
        cred = config.require_credential()
        mid = await resolve_mid(who)
        data = await _public(lambda: user.User(mid, credential=cred).get_articles(pn=page, ps=size))
        if data is None:
            return _hidden(mid)
        if output.settings.raw:
            return data
        out = {
            "mid": mid,
            "hidden": False,
            "page": page,
            "total": data.get("count"),
            "items": [
                {
                    "id": a.get("id"),
                    "title": a.get("title"),
                    "published_at": iso_time(a.get("publish_time")),
                    "views": (a.get("stats") or {}).get("view"),
                    "url": f"https://www.bilibili.com/read/cv{a.get('id')}",
                }
                for a in data.get("articles") or []
            ],
        }
        nw = _next_with("articles", mid, page, size, data.get("count"), 20)
        if nw:
            out["next_with"] = nw
        return out

    run(body)


# x/space/wap/relation: attribute 0 = not following, 1 = following quietly, 2 = following, 6 = mutual, 128 = blocked
_ATTRS = {0: "none", 1: "following", 2: "following", 6: "mutual", 128: "blocked"}


async def _relation_with(u: user.User) -> dict[str, Any]:
    data = await u.get_relation()
    mine = (data.get("relation") or {}).get("attribute") or 0
    theirs = (data.get("be_relation") or {}).get("attribute") or 0
    return {"i_follow": mine in (1, 2, 6), "follows_me": theirs in (1, 2, 6), "relation": _ATTRS.get(mine, str(mine))}


# action -> (relation type to send, "already done" check on the current relation, audit / guard name)
_ACTIONS = {
    "follow": (user.RelationType.SUBSCRIBE, lambda r: r["i_follow"], "follow"),
    "unfollow": (user.RelationType.UNSUBSCRIBE, lambda r: not r["i_follow"], "follow"),
    "block": (user.RelationType.BLOCK, lambda r: r["relation"] == "blocked", "block"),
    "unblock": (user.RelationType.UNBLOCK, lambda r: r["relation"] != "blocked", "block"),
}


async def _set_relation(who: str, action: str) -> dict[str, Any]:
    from .. import guard

    relation_type, already, limit = _ACTIONS[action]
    cred = config.require_credential()
    mid = await resolve_mid(who)
    if str(mid) == str(cred.dedeuserid):
        raise BiliError("invalid_argument", "不能对自己做这个操作。")
    u = user.User(mid, credential=cred)
    before = await _relation_with(u)
    if action == "follow" and before["relation"] == "blocked":
        raise BiliError("invalid_argument", f"你已把 {mid} 拉黑，先运行 `bili user unblock {mid}`。")
    params = {"mid": mid, "action": action}
    if already(before):
        return {**params, "changed": False, **before}
    if output.settings.dry_run:
        return {"dry_run": True, "action": f"user.{action}", "params": params}
    guard.check(limit)
    await u.modify_relation(relation_type)
    guard.record(limit, params, "ok")
    return {**params, "changed": True, **await _relation_with(u)}


@app.command()
def follow(who: str = USER_ARG) -> None:
    """关注一个用户（已关注时什么也不做）。本地限制每小时最多 20 次关注 / 取关。"""
    run(lambda: _set_relation(who, "follow"))


@app.command()
def unfollow(who: str = USER_ARG) -> None:
    """取消关注一个用户（没关注时什么也不做）。"""
    run(lambda: _set_relation(who, "unfollow"))


@app.command()
def block(who: str = USER_ARG) -> None:
    """拉黑一个用户：对方不能再评论、私信、@ 你，你也会自动取关对方。用于应对骚扰。"""
    run(lambda: _set_relation(who, "block"))


@app.command()
def unblock(who: str = USER_ARG) -> None:
    """把一个用户移出黑名单（之前的关注不会恢复）。"""
    run(lambda: _set_relation(who, "unblock"))


@app.command()
def blocked(
    page: int = typer.Option(1, "--page", min=1, help="页码，从 1 开始。"),
    size: int = typer.Option(50, "--size", min=1, max=50, help="每页条数。"),
) -> None:
    """我的黑名单，按拉黑时间从新到旧。"""

    async def body():
        cred = config.require_credential()
        data = await user.get_self_black_list(cred, pn=page, ps=size)
        if output.settings.raw:
            return data
        return {
            "page": page,
            "total": data.get("total"),
            "items": [
                {"mid": b.get("mid"), "name": b.get("uname"), "blocked_at": iso_time(b.get("mtime"))}
                for b in data.get("list") or []
            ],
            "unblock_with": "bili user unblock <UID>",
        }

    run(body)


async def _follow_list(who: str | None, page: int, size: int, method: str, cmd: str) -> dict[str, Any]:
    cred = config.require_credential()
    mid = await resolve_mid(who) if who else config.my_mid(cred)
    data = await _public(lambda: getattr(user.User(mid, credential=cred), method)(pn=page, ps=size))
    if data is None:
        return _hidden(mid)
    if output.settings.raw:
        return data
    out = {
        "mid": mid,
        "page": page,
        "total": data.get("total"),
        "items": [
            {
                "mid": f.get("mid"),
                "name": f.get("uname"),
                "sign": f.get("sign") or None,
                "followed_at": iso_time(f.get("mtime")),
                **({"mutual": f.get("attribute") == 6} if not who else {}),
            }
            for f in data.get("list") or []
        ],
    }
    if who:
        nw = _next_with(cmd, mid, page, size, data.get("total"), 50)
        if nw:
            out["next_with"] = nw
    return out


@app.command()
def following(
    who: str | None = typer.Argument(None, help="UID 或完整昵称；不填就是我自己。"),
    page: int = typer.Option(1, "--page", min=1, help="页码，从 1 开始。"),
    size: int = typer.Option(50, "--size", min=1, max=50, help="每页条数。"),
) -> None:
    """我关注的人（默认），或别人公开的关注列表，按关注时间从新到旧。"""

    run(lambda: _follow_list(who, page, size, "get_followings", "following"))


@app.command()
def followers(
    who: str | None = typer.Argument(None, help="UID 或完整昵称；不填就是我自己。"),
    page: int = typer.Option(1, "--page", min=1, help="页码，从 1 开始。"),
    size: int = typer.Option(50, "--size", min=1, max=50, help="每页条数。"),
) -> None:
    """我的粉丝（默认），或别人的粉丝列表，按关注时间从新到旧。"""

    run(lambda: _follow_list(who, page, size, "get_followers", "followers"))
