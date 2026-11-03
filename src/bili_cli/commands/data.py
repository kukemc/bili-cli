"""bili data: the numbers from 创作中心 › 数据中心.

`bili me stats` and `bili upload stats` cover two of the panels there; this group covers
the rest: lifetime totals, the 30-day increment curve, the audience profile, fan growth
and sources, the per-video table, and per-video diagnosis.

To keep one source of truth the period comparison is not reimplemented here:
`bili data overview` calls `me.summarize_stats()` (the same code as `bili me stats`) and
adds the lifetime totals and yesterday's increments on top. Likewise `bili data video`
reports the same completion rate as `bili upload stats` and only adds the audience split,
the province breakdown and the diagnosis metrics.

All of these read `member.bilibili.com/x/web/...` directly: bilibili-api does not wrap
them. Like the web page, numbers settle once a day around noon for the previous day.
"""

from __future__ import annotations

from typing import Any

import typer
from bilibili_api import Credential

from .. import config, output
from ..errors import BILI_CODE_MAP, BiliError
from ..runtime import iso_time, parse_video_id, run, web_client
from .me import summarize_stats

app = typer.Typer(help="数据中心：账号总览、30 天趋势、观众画像、粉丝、稿件数据、单稿诊断。", no_args_is_help=True)

BASE = "https://member.bilibili.com"
REFERER = "https://member.bilibili.com/platform/data-center"
LAG_NOTE = "创作中心的数据次日中午前更新（当天的数据还看不到）。"
CHARGE_NOTE = "charge 是 B 站原始数值，单位未确认"

PERIODS = {"yesterday": -1, "week": 0, "month": 1, "3months": 2}
TREND_TYPES = {"play": 1, "danmaku": 2, "comment": 3, "share": 4, "coin": 5, "favorite": 6, "charge": 7, "like": 8}

AGE_KEYS = {"age_one": "0-16", "age_two": "16-25", "age_three": "25-40", "age_four": "40+"}

WEEKDAYS = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}


def pct(value: Any) -> float | None:
    """Creator-center ratios are in 1/10000 (9695 -> 96.95%); some panels send them as strings."""
    if value in (None, ""):
        return None
    try:
        return round(float(value) / 100, 2)
    except (TypeError, ValueError):
        return None


def num(value: Any) -> int | None:
    """Counts also arrive as strings in the archive table (`"play": "3997"`)."""
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def share(part: Any, whole: Any) -> float | None:
    return round((part or 0) * 100 / whole, 2) if whole else None


def date_str(value: Any) -> str | None:
    """`20261024` -> `2026-10-24`."""
    text = str(value or "")
    return f"{text[:4]}-{text[4:6]}-{text[6:]}" if len(text) == 8 and text.isdigit() else None


def day(ts: Any) -> str | None:
    """A daily timestamp -> `YYYY-MM-DD`; the time of day carries no meaning here."""
    iso = iso_time(ts)
    return iso[:10] if iso else None


def top_items(counts: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    rows = [{"name": k, "count": v} for k, v in (counts or {}).items() if v]
    return sorted(rows, key=lambda r: -r["count"])[:limit]


async def get_data(cred: Credential, path: str, params: dict[str, Any] | None = None) -> Any:
    """One creator-center endpoint -> its `data`, with Bilibili's error codes mapped."""
    async with web_client(cred, referer=REFERER) as client:
        resp = await client.get(BASE + path, params=params or {})
    if resp.status_code == 412:
        raise BiliError(
            "risk_control",
            f"创作中心接口 {path} 被 B 站风控拦截（HTTP 412）。",
            hint="降低请求频率，停一会儿再试；不要换接口或换参数重试。",
            detail={"path": path},
        )
    if resp.status_code != 200:
        raise BiliError("api_error", f"创作中心接口 {path} 返回 HTTP {resp.status_code}。", hint="稍后重试；如果一直失败，可能这个面板已改版。")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise BiliError("api_error", f"创作中心接口 {path} 返回的不是 JSON。") from exc
    code = payload.get("code")
    if code != 0:
        raise BiliError(
            BILI_CODE_MAP.get(code, "api_error"),
            f"创作中心接口 {path} 返回 {code}：{payload.get('message')}",
            detail={"bili_code": code},
        )
    return payload.get("data")


def _period(period: str) -> int:
    if period not in PERIODS:
        raise BiliError("invalid_argument", f"--period 只能是 {' / '.join(PERIODS)} 之一。")
    return PERIODS[period]


def _diagnose_metrics(stat: dict[str, Any]) -> dict[str, Any]:
    """Scale the diagnosis ratios and drop the fields B 站 marks as not ready yet."""
    not_ready = set(stat.get("not_ready_field") or [])
    out: dict[str, Any] = {}
    for src, dst in {
        "full_play_ratio": "completion_percent",
        "play_viewer_rate": "guest_plays_percent",
        "crash_rate": "early_exit_percent",
        "interact_rate": "interaction_percent",
        "play_trans_fan_rate": "play_to_fan_percent",
    }.items():
        if src not in not_ready and src in stat:
            out[dst] = pct(stat.get(src))
    # tm_rate / tm_rate_med change on every request (B 站 randomises the absolute value),
    # but their ratio and the peer percentile are stable, so that is all we report.
    # `*_med` is the median of comparable videos from other UP 主, not this account's median.
    tm_rate, tm_median = stat.get("tm_rate"), stat.get("tm_rate_med")
    if tm_rate is not None and tm_median and "tm_rate" not in not_ready:
        out["cover_click_vs_peer_median"] = round(tm_rate / tm_median, 1)
    for src, dst in (("total_new_attention_cnt", "new_fans"), ("unfollow", "lost_fans")):
        if src not in not_ready and src in stat:
            out[dst] = stat.get(src)
    peer_median = {
        "early_exit_percent": "crash_rate_med",
        "interaction_percent": "interact_rate_med",
        "play_to_fan_percent": "play_trans_fan_rate_med",
    }
    medians = {dst: pct(stat[src]) for dst, src in peer_median.items() if src in stat and src not in not_ready}
    if medians:
        out["peer_median_percent"] = medians
    peers = {
        "cover_click": "tm_pass_rate",
        "cover_click_fans": "tm_fan_pass_rate",
        "cover_click_guests": "tm_viewer_pass_rate",
    }
    better = {name: pct(stat[src]) for name, src in peers.items() if src in stat and src not in not_ready and stat.get(src)}
    if better:
        out["better_than_peers_percent"] = better
    return out


@app.command()
def overview(
    period: str = typer.Option("week", "--period", help="统计周期：yesterday / week / month / 3months（和上一周期对比）。"),
) -> None:
    """账号总览：累计总量与昨日增量，加上和 `bili me stats` 一样的周期对比（同一份计算，见 me.summarize_stats）。"""

    async def body():
        from bilibili_api import creative_center as cc

        p = _period(period)
        cred = config.require_credential()
        stat = await get_data(cred, "/x/web/index/stat") or {}
        graph = await get_data(cred, "/x/web/data/v2/fans/stat/graph", {"period": 0, "type": "all_fans"}) or {}
        overview, fans = await cc.get_overview(cred, cc.GraphPeriod(p)), await cc.get_fan_overview(cred, cc.FanGraphPeriod(p))
        source, compare, survey = await cc.get_video_source(cred), await cc.get_compare(cred), await cc.get_video_survey(cred)
        if output.settings.raw:
            return {"index_stat": stat, "fans_graph": graph, "overview": overview, "fans": fans, "source": source, "compare": compare, "survey": survey}

        # 新账号这几个接口都会返回 null（见 #10），统一按空对象处理
        overview, fans, source, compare, survey = overview or {}, fans or {}, source or {}, compare or {}, survey or {}
        summary = summarize_stats(overview, fans, source, compare, survey)
        summary["data_date"] = date_str(summary.get("data_date")) or summary.get("data_date")
        # versus_peers below carries the same numbers in more detail
        summary.pop("beats_similar_ups_percent", None)
        # 粉丝总数是实时值，其他累计值是结算值；昨日净涨粉从粉丝趋势的最后两天算，
        # 不能用 incr_fans（它等于「现在的粉丝数 − 前一天的粉丝数」）。
        tendency = sorted(
            (row for row in (graph.get("tendency") or []) if row.get("total_inc") is not None),
            key=lambda row: row.get("date_key") or 0,
        )
        fans_net = tendency[-1]["total_inc"] - tendency[-2]["total_inc"] if len(tendency) >= 2 else None
        totals = {
            "plays": stat.get("total_click"),
            "likes": stat.get("total_like"),
            "coins": stat.get("total_coin"),
            "favorites": stat.get("total_fav"),
            "comments": stat.get("total_reply"),
            "danmaku": stat.get("total_dm"),
            "shares": stat.get("total_share"),
            "charge": stat.get("total_elec"),
        }
        yesterday = {
            "plays": stat.get("incr_click"),
            "fans_net": fans_net,
            "likes": stat.get("inc_like"),
            "coins": stat.get("inc_coin"),
            "favorites": stat.get("inc_fav"),
            "comments": stat.get("incr_reply"),
            "danmaku": stat.get("incr_dm"),
            "shares": stat.get("inc_share"),
            "charge": stat.get("inc_elec"),
        }
        return {
            **summary,
            "period": period,
            "totals": totals,
            "yesterday": yesterday,
            "totals_note": f"累计值和昨日增量都截至 data_date；{CHARGE_NOTE}。粉丝总数是实时值（fans.total）。",
            "versus_peers": {
                "plays": {"you": compare.get("play"), "median_of_peers": compare.get("play_mid"), "better_than_percent": pct(compare.get("play_pass_peer"))},
                "likes": {"you": compare.get("like"), "median_of_peers": compare.get("like_mid"), "better_than_percent": pct(compare.get("like_pass_peer"))},
                "new_fans": {"you": compare.get("new_fans"), "median_of_peers": compare.get("new_fans_mid"), "better_than_percent": pct(compare.get("new_fans_pass_peer"))},
                "comments_and_dms": {"you": compare.get("replydm"), "median_of_peers": compare.get("replydm_mid"), "better_than_percent": pct(compare.get("replydm_pass_peer"))},
            },
            "note": f"{LAG_NOTE} `data overview` 和 `bili me stats` 的周期对比是同一份计算；要看逐日变化用 `bili data trend`。",
        }

    run(body)


@app.command()
def trend(
    type: str = typer.Option("play", "--type", help="数据类型：play / like / favorite / coin / comment / danmaku / share / charge。"),
    days: int = typer.Option(30, "--days", min=1, max=30, help="看最近多少天（B 站最多给 30 天）。"),
) -> None:
    """最近 30 天的每日增量（播放、点赞、收藏……），用来看某天为什么涨/跌。"""

    async def body():
        if type not in TREND_TYPES:
            raise BiliError("invalid_argument", f"--type 只能是 {' / '.join(TREND_TYPES)} 之一。")
        cred = config.require_credential()
        data = await get_data(cred, "/x/web/data/pandect", {"type": TREND_TYPES[type]}) or []
        rows = sorted(data, key=lambda r: r.get("date_key") or 0)[-days:]
        if output.settings.raw:
            return {"type": type, "data": data}
        series = [{"date": day(r.get("date_key")), "delta": r.get("total_inc")} for r in rows]
        note = LAG_NOTE + (f" {CHARGE_NOTE}，不要当金额用。" if type == "charge" else "")
        return {"type": type, "days": len(series), "total": sum(r["delta"] or 0 for r in series), "series": series, "note": note}

    run(body)


@app.command()
def audience() -> None:
    """观众画像：粉丝/路人的性别、年龄、省份，以及两边的兴趣标签（B 站按周更新，见 note）。"""

    async def body():
        cred = config.require_credential()
        base = await get_data(cred, "/x/web/data/base") or {}
        interest = await get_data(cred, "/x/web/data/trend") or {}
        if output.settings.raw:
            return {"base": base, "trend": interest}
        viewer = base.get("viewer_base") or {}
        area = base.get("viewer_area") or {}

        def gender(group: dict[str, Any]) -> dict[str, float] | None:
            total = (group.get("male") or 0) + (group.get("female") or 0)
            return {"male": share(group.get("male"), total), "female": share(group.get("female"), total)} if total else None

        def ages(group: dict[str, Any]) -> dict[str, float] | None:
            total = sum(group.get(k) or 0 for k in AGE_KEYS)
            return {label: share(group.get(key), total) for key, label in AGE_KEYS.items()} if total else None

        def interests(group: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
            weights = group.get("ty") or {}
            # 覆盖率不是占比：可多选，合计会超过 100%（实测粉丝侧 133 万、路人侧 146 万）
            rows = [{"name": k, "viewers_percent": round(v / 10000, 2)} for k, v in weights.items() if v]
            return sorted(rows, key=lambda r: -r["viewers_percent"])[:limit]

        has_demographics = any(
            (viewer.get(group) or {}).get(key) for group in ("fan", "not_fan") for key in ("male", "female", "age_one")
        ) or any((area.get(group) or {}) for group in ("fan", "not_fan"))
        note = (
            "性别 / 年龄 / 地区由 B 站每周二更新前一周的数据（接口 period.module_three）；兴趣标签是另一套统计，"
            "周期不同，两者都为空才说明这个账号还没有画像。"
            if has_demographics
            else "这个账号还没有性别 / 年龄 / 地区数据：新账号，或 B 站本周的画像还没生成（每周二更新前一周的数据）。"
            "兴趣标签是另一套统计，仍可能有数据。"
        )
        return {
            "gender_percent": {"fans": gender(viewer.get("fan") or {}), "guests": gender(viewer.get("not_fan") or {})},
            "age_percent": {"fans": ages(viewer.get("fan") or {}), "guests": ages(viewer.get("not_fan") or {})},
            "regions": {"fans": top_items(area.get("fan") or {}, 10), "guests": top_items(area.get("not_fan") or {}, 10)},
            "interests": {"fans": interests(interest.get("fan") or {}), "guests": interests(interest.get("not_fan") or {})},
            "interests_note": "兴趣标签可多选，viewers_percent 合计会超过 100%（是覆盖率，不是占比）。",
            "update_schedule": base.get("period") or {},
            "note": note,
        }

    run(body)


@app.command()
def fans(
    period: str = typer.Option("week", "--period", help="统计周期：yesterday / week / month / 3months。"),
) -> None:
    """粉丝数据：总数、周期内新增/取关、活跃粉丝、粉丝来源、涨粉趋势、最活跃的粉丝。"""

    async def body():
        p = _period(period)
        cred = config.require_credential()
        numbers = await get_data(cred, "/x/web/data/v2/fans/stat/num", {"period": p}) or {}
        graph = await get_data(cred, "/x/web/data/v2/fans/stat/graph", {"period": p, "type": "all_fans"}) or {}
        fan = await get_data(cred, "/x/web/data/fan") or {}
        if output.settings.raw:
            return {"stat": numbers, "graph": graph, "fan": fan}
        summary = fan.get("summary") or {}
        sources = fan.get("source") or {}
        follow, unfollow = numbers.get("follow") or 0, numbers.get("unfollow") or 0
        follow_last, unfollow_last = numbers.get("follow_last") or 0, numbers.get("unfollow_last") or 0
        ranks = fan.get("rank_list") or {}

        def names(key: str) -> list[str]:
            return [item.get("uname") for item in (ranks.get(key) or [])[:5] if item.get("uname")]

        return {
            "total": numbers.get("all_fans"),
            "data_date": date_str(numbers.get("log_date")),
            "period": {
                "name": period,
                "follow": follow,
                "follow_previous": follow_last,
                "unfollow": unfollow,
                "unfollow_previous": unfollow_last,
                "net": follow - unfollow,
                "net_previous": follow_last - unfollow_last,
            },
            "interacting_fans": numbers.get("interact_fans"),
            "interacting_percent": pct(numbers.get("interact_fans_per")),
            "better_than_peers_percent": {"interacting_fans": pct(numbers.get("interact_pass_per")), "total_fans": pct(numbers.get("over_peer_percent"))},
            "active_fans": {"count": summary.get("active"), "window": "最近 30 天有过互动"},
            "sources_percent": {k: pct(v) for k, v in sources.items() if v},
            "trend": [{"date": day(r.get("date_key")), "total": r.get("total_inc")} for r in (graph.get("tendency") or [])],
            "top_fans": {"by_video_activity": names("video_act"), "by_dynamic_activity": names("dynamic_act"), "by_play": names("video_play")},
            "note": LAG_NOTE,
        }

    run(body)


@app.command()
def active_hours() -> None:
    """粉丝活跃时间：粉丝每小时（近 30 天）、每周哪天的活跃分布，以及同期大盘（同类用户）的对比。"""

    async def body():
        cred = config.require_credential()
        mid = config.my_mid(cred)
        # 小时曲线在「观众画像」里（v2 才带 tmid），星期分布和「最活跃时段」在活跃分析里
        portrait = await get_data(cred, "/x/web/data/v2/fans/stat/portrayal", {"tmid": mid}) or {}
        # 这个面板的错误不降级：风控（412）、登录过期、网络问题都必须原样抛出去，agent 才知道该停
        idea = await get_data(cred, "/x/web/data/account_diagnose/active_fans_improve_idea") or {}
        if output.settings.raw:
            return {"portrayal": portrait, "improve_idea": idea}

        def series(rows: list[dict[str, Any]], key: str, value: str) -> dict[Any, Any]:
            return {row.get(key): row.get(value) or 0 for row in rows}

        fans = series(portrait.get("fans_activity") or [], "hour_key", "total_inc")
        peers = series(portrait.get("peer_fans_activity") or [], "hour_key", "total_inc")
        hours = []
        for hour in range(24):
            own_percent, peer_percent = pct(fans.get(hour)), pct(peers.get(hour))
            hours.append(
                {
                    "hour": hour,
                    "fans_percent": own_percent,
                    "peer_percent": peer_percent,
                    "gap_percent": round(own_percent - peer_percent, 2) if own_percent is not None and peer_percent is not None else None,
                }
            )
        weekdays = [
            {"weekday": WEEKDAYS.get(row.get("period")), "fans_percent": pct(row.get("cnt"))}
            for row in (idea.get("fans_active_week_day") or [])
        ]
        peak = idea.get("fans_active_period") or {}
        peak_hour = peak.get("hour")
        return {
            "hours_window": "最近 30 天",
            "hours": hours,
            "weekdays": weekdays,
            "top_hours": sorted((h for h in hours if h["fans_percent"]), key=lambda h: -h["fans_percent"])[:3],
            "peak": (
                {"weekday": WEEKDAYS.get(peak.get("day_of_week")), "hour": peak_hour, "text": f"{WEEKDAYS.get(peak.get('day_of_week'), '')} {peak_hour} 点".strip()}
                if peak_hour is not None
                else None
            ),
            "better_than_peers_percent": pct(idea.get("active_fans_pass_per")),
            "recent_upload_slots": [
                {"weekday": WEEKDAYS.get(row.get("day_of_week")), "hour": row.get("hour")}
                for row in (idea.get("recent_archive_active_period") or [])
            ],
            "note": (
                "百分比是 B 站返回的 1/10000 分布（24 小时合计 100%），`gap_percent` 是「你的粉丝 − 大盘」在占比上的差；"
                "小时按 B 站口径（北京时间 UTC+8）。`hours_window` 取网页上这张图的标注（近 30 天），"
                "星期分布和最活跃时段来自「粉丝活跃分析」，接口没标统计窗口，这里不做假设。"
                "`recent_upload_slots` 是你最近 5 个稿件的发布时段，"
                "`better_than_peers_percent` 是活跃粉丝表现超过多少同类 UP 主。"
                " 这个是活跃时间；性别 / 年龄 / 地区看 `bili data audience`。"
            ),
        }

    run(body)


@app.command()
def videos(
    limit: int = typer.Option(20, "--limit", min=1, max=50, help="看最近多少个稿件（1-50）。"),
    sort: str = typer.Option("date", "--sort", help="排序：date / plays / completion / fans / likes。"),
) -> None:
    """稿件数据表：最近稿件的播放、点赞、评论、涨粉、完播率，以及诊断指标和同类对比。"""

    async def body():
        if sort not in ("date", "plays", "completion", "fans", "likes"):
            raise BiliError("invalid_argument", "--sort 只能是 date / plays / completion / fans / likes 之一。")
        cred = config.require_credential()
        data = await get_data(cred, "/x/web/data/archive/index", {"pn": 1, "ps": limit}) or {}
        diagnose = await get_data(cred, "/x/web/data/archive_diagnose/compare", {"size": limit}) or {}
        if output.settings.raw:
            return {"list": data, "diagnose": diagnose}
        diag = {item.get("bvid"): item.get("stat") or {} for item in (diagnose.get("list") or [])}
        items = []
        for arc in data.get("list") or []:
            stat = arc.get("real_stat") or arc.get("stat") or {}
            row = {
                "bvid": arc.get("bvid"),
                "title": arc.get("title"),
                "published": iso_time(arc.get("pubtime")),
                "duration": arc.get("duration"),
                "plays": num(stat.get("play")),
                "likes": num(stat.get("likes") if stat.get("likes") is not None else stat.get("like")),
                "comments": num(stat.get("reply") if stat.get("reply") is not None else stat.get("comment")),
                "fans_gained": num(stat.get("fans")),
                "completion_percent": pct(stat.get("full_play_ratio")),
            }
            row.update(_diagnose_metrics(diag.get(arc.get("bvid")) or {}))
            items.append(row)
        if sort != "date":
            key = {"plays": "plays", "completion": "completion_percent", "fans": "fans_gained", "likes": "likes"}[sort]
            items.sort(key=lambda r: -(r.get(key) or 0))
        return {
            "total": (data.get("pager") or {}).get("total"),
            "sort": sort,
            "items": items,
            "note": "播放 / 点赞 / 评论是当前总数（接近实时）；完播率和诊断指标按次日结算。`bili data video` 的播放是结算值，两个命令的播放数口径不同。",
        }

    run(body)


def _pick_archive(data: dict[str, Any], wanted: dict[str, Any]) -> dict[str, Any] | None:
    for item in data.get("list") or []:
        if wanted.get("bvid") and item.get("bvid") == wanted["bvid"]:
            return item
        if wanted.get("aid") and item.get("aid") == wanted["aid"]:
            return item
    return None


@app.command()
def video(target: str = typer.Argument(..., help="自己的稿件：BV 号、av 号或视频链接。")) -> None:
    """单稿数据：完播率（和 `bili upload stats` 同一个数）+ 观众画像、省份、3 秒退出率、封面点击倍数、播转粉率。"""

    async def body():
        wanted = parse_video_id(target)
        cred = config.require_credential()
        data = await get_data(cred, "/x/web/data/archive", wanted) or {}
        diagnose = await get_data(cred, "/x/web/data/archive_diagnose/compare", {"size": 30}) or {}
        survey = await get_data(cred, "/x/web/data/survey", {"type": 1}) or {}
        item = _pick_archive(diagnose, wanted)
        if output.settings.raw:
            return {"archive": data, "diagnose": item, "survey": survey}
        stat = data.get("stat") or {}
        play = data.get("play") or {}
        group = data.get("group") or {}
        plays = (group.get("fans") or 0) + (group.get("guest") or 0)
        data_date = date_str(next(iter(survey), None))
        out: dict[str, Any] = {
            "bvid": (item or {}).get("bvid") or wanted.get("bvid"),
            "title": (item or {}).get("title"),
            "published": iso_time((item or {}).get("pubtime")),
            "duration": play.get("duration") or (item or {}).get("duration"),
            "data_date": data_date,
            "stat": {
                "plays": stat.get("play"),
                "likes": stat.get("like"),
                "coins": stat.get("coin"),
                "favorites": stat.get("fav"),
                "comments": stat.get("reply"),
                "danmaku": stat.get("dm"),
                "shares": stat.get("share"),
                "charge": stat.get("elec"),
            },
            "audience": {"fan_plays": group.get("fans"), "guest_plays": group.get("guest"), "fan_percent": share(group.get("fans"), plays)},
            "completion_percent": pct(play.get("rate")),
            "avg_watch_seconds": play.get("avg_duration"),
            "regions": top_items({r.get("location"): r.get("count") for r in (data.get("area") or [])}, 10),
        }
        if item:
            out["diagnose"] = _diagnose_metrics(item.get("stat") or {})
        else:
            out["diagnose_note"] = "这个稿件不在最近 30 个稿件里，创作中心没有它的诊断数据。"
        out["note"] = (
            f"`stat` / 观众 / 完播 / 省份都是截至 {data_date} 的结算数据（当天还在涨的播放量不算）；"
            "看实时总数用 `bili video info`。完播率和 `bili upload stats` 是同一个数。"
            if data_date
            else LAG_NOTE
        ) + f" `stat` 里的 {CHARGE_NOTE}。"
        return out

    run(body)


@app.command()
def diagnose(
    size: int = typer.Option(10, "--size", min=1, max=50, help="比较最近多少个稿件（1-50）。"),
) -> None:
    """稿件诊断：完播率、3 秒退出率、封面点击（相对同类中位的倍数）、互动率、播转粉率，以及同类分位。"""

    async def body():
        cred = config.require_credential()
        data = await get_data(cred, "/x/web/data/archive_diagnose/compare", {"size": size}) or {}
        if output.settings.raw:
            return data
        items = []
        for item in data.get("list") or []:
            stat = item.get("stat") or {}
            row = {"bvid": item.get("bvid"), "title": item.get("title"), "published": iso_time(item.get("pubtime")), "plays": stat.get("play")}
            row.update(_diagnose_metrics(stat))
            items.append(row)
        return {"size": size, "items": items, "note": LAG_NOTE + " 封面点击率 B 站每次返回的绝对值会变，这里只给「相对同类中位的倍数」和同类分位。"}

    run(body)
