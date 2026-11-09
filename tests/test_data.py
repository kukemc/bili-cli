import asyncio
import json

import httpx
import pytest
from typer.testing import CliRunner

from bili_cli.commands import data
from bili_cli.errors import BiliError
from bili_cli.main import app


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BILI_CLI_HOME", str(tmp_path))
    return tmp_path


def _invoke(monkeypatch, payloads, argv):
    async def fake_get(cred, path, params=None):
        return payloads.get(path)

    monkeypatch.setattr(data, "get_data", fake_get)
    monkeypatch.setattr(data.config, "require_credential", lambda: object())
    result = CliRunner().invoke(app, argv)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["data"]


def _patch_creative_center(monkeypatch, overview=None, fans=None, source=None, compare=None, survey=None):
    from bilibili_api import creative_center

    async def overview_fn(cred, period):
        return overview

    async def fans_fn(cred, period):
        return fans

    async def source_fn(cred):
        return source

    async def compare_fn(cred):
        return compare

    async def survey_fn(cred):
        return survey

    monkeypatch.setattr(creative_center, "get_overview", overview_fn)
    monkeypatch.setattr(creative_center, "get_fan_overview", fans_fn)
    monkeypatch.setattr(creative_center, "get_video_source", source_fn)
    monkeypatch.setattr(creative_center, "get_compare", compare_fn)
    monkeypatch.setattr(creative_center, "get_video_survey", survey_fn)


def test_pct_num_share_and_dates():
    # creator-center ratios are 1/10000, and some panels send them as strings
    assert data.pct(3997) == 39.97
    assert data.pct("3997") == 39.97
    assert data.pct(None) is None and data.pct("") is None and data.pct("x") is None
    assert data.num("746") == 746 and data.num("") is None
    assert data.share(166, 3001) == 5.53 and data.share(1, 0) is None
    assert data.date_str(20261024) == "2026-10-24" and data.date_str(None) is None
    assert data.day(1792252800) == "2026-10-18"


def test_top_items_sorts_and_limits():
    assert data.top_items({"a": 3, "b": 0, "c": 9}, 2) == [{"name": "c", "count": 9}, {"name": "a", "count": 3}]


def test_diagnose_metrics_scales_and_skips_not_ready():
    stat = {
        "not_ready_field": ["interact_rate"],
        "full_play_ratio": 3997,
        "tm_rate": 111,
        "tm_rate_med": 46,
        "tm_pass_rate": 8723,
        "interact_rate": 0,
        "interact_rate_med": 388,
        "crash_rate_med": 2729,
        "play_trans_fan_rate": 81,
        "play_trans_fan_rate_med": 3,
        "total_new_attention_cnt": 1,
        "unfollow": 2,
    }
    out = data._diagnose_metrics(stat)
    assert out["completion_percent"] == 39.97
    assert out["play_to_fan_percent"] == 0.81
    assert out["new_fans"] == 1 and out["lost_fans"] == 2
    assert "interaction_percent" not in out  # B 站还没算出来，不要报 0
    assert "cover_click_percent" not in out  # 绝对值每次请求都变，不能当百分比
    assert "fan_plays_percent" not in out  # play_fan_rate 不是粉丝播放占比，没能确认含义，先不输出
    assert out["cover_click_vs_peer_median"] == 2.4
    assert "cover_click_percent" not in out["peer_median_percent"]
    assert out["peer_median_percent"]["early_exit_percent"] == 27.29
    assert out["better_than_peers_percent"]["cover_click"] == 87.23


def test_cover_click_ratio_is_stable_while_absolute_value_changes():
    def ratio(tm, median):
        return data._diagnose_metrics({"tm_rate": tm, "tm_rate_med": median, "not_ready_field": []})["cover_click_vs_peer_median"]

    # the three samples the reviewer measured on the same video
    assert [ratio(111, 46), ratio(975, 407), ratio(24, 10)] == [2.4, 2.4, 2.4]
    # one decimal place: tiny absolute values still land on the same multiple
    assert [ratio(86, 30), ratio(87, 30), ratio(88, 30)] == [2.9, 2.9, 2.9]


def test_get_data_maps_bili_error_code(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"code": -101, "message": "账号未登录"})

    def fake_client(cred=None, referer=None, timeout=20):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(data, "web_client", fake_client)
    with pytest.raises(BiliError) as err:
        asyncio.run(data.get_data(object(), "/x/web/index/stat"))
    assert err.value.code == "not_logged_in"
    assert err.value.detail == {"bili_code": -101}


def test_get_data_maps_412_to_risk_control(monkeypatch):
    def handler(request):
        return httpx.Response(412, text="<html>blocked</html>")

    def fake_client(cred=None, referer=None, timeout=20):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(data, "web_client", fake_client)
    with pytest.raises(BiliError) as err:
        asyncio.run(data.get_data(object(), "/x/web/data/base"))
    assert err.value.code == "risk_control"
    assert err.value.detail == {"path": "/x/web/data/base"}


def test_audience_command(monkeypatch):
    base = {
        "period": {"module_three": "每周二12:00 a.m. 更新前一周数据"},
        "viewer_base": {
            "fan": {"male": 3, "female": 1, "age_one": 1, "age_two": 3, "age_three": 5, "age_four": 1},
            "not_fan": {"male": 1, "female": 1, "age_one": 0, "age_two": 1, "age_three": 1, "age_four": 0},
        },
        "viewer_area": {"fan": {"广东": 10, "上海": 5}, "not_fan": {"广东": 20}},
    }
    trend = {"fan": {"ty": {"荣格": 278600, "现象学": 274500}}, "not_fan": {"ty": {"MBTI": 500000}}}
    out = _invoke(monkeypatch, {"/x/web/data/base": base, "/x/web/data/trend": trend}, ["data", "audience"])
    assert out["gender_percent"]["fans"] == {"male": 75.0, "female": 25.0}
    assert out["age_percent"]["fans"]["25-40"] == 50.0
    assert out["regions"]["fans"][0] == {"name": "广东", "count": 10}
    assert out["interests"]["fans"][0] == {"name": "荣格", "viewers_percent": 27.86}
    assert "可多选" in out["interests_note"]
    assert out["update_schedule"] == {"module_three": "每周二12:00 a.m. 更新前一周数据"}
    assert "每周二" in out["note"]


def test_audience_handles_missing_data(monkeypatch):
    out = _invoke(monkeypatch, {"/x/web/data/base": {"period": {}, "viewer_base": {}, "viewer_area": {}}, "/x/web/data/trend": {}}, ["data", "audience"])
    assert out["gender_percent"]["fans"] is None
    assert out["regions"]["fans"] == []
    assert "兴趣标签是另一套统计" in out["note"]


def test_trend_command_uses_calendar_dates(monkeypatch):
    rows = [{"date_key": 1792339200, "total_inc": 20}, {"date_key": 1792252800, "total_inc": 10}]
    out = _invoke(monkeypatch, {"/x/web/data/pandect": rows}, ["data", "trend", "--type", "like", "--days", "1"])
    assert out["type"] == "like" and out["days"] == 1
    assert out["series"] == [{"date": "2026-10-19", "delta": 20}]
    assert "单位未确认" not in out["note"]


def test_charge_trend_says_the_unit_is_unconfirmed(monkeypatch):
    rows = [{"date_key": 1792339200, "total_inc": 30000}]
    out = _invoke(monkeypatch, {"/x/web/data/pandect": rows}, ["data", "trend", "--type", "charge"])
    assert out["total"] == 30000 and "单位未确认" in out["note"]


def test_videos_merges_diagnosis_and_sorts(monkeypatch):
    index = {
        "list": [
            {"bvid": "BV1", "title": "a", "pubtime": 1792922879, "duration": 60,
             "real_stat": {"play": "100", "likes": "5", "reply": "1", "fans": "2", "full_play_ratio": "2000"}},
            {"bvid": "BV2", "title": "b", "pubtime": 1792922879, "duration": 60,
             "real_stat": {"play": 900, "likes": 40, "reply": 3, "fans": 9, "full_play_ratio": 5000}},
        ],
        "pager": {"total": 2},
    }
    diag = {"list": [{"bvid": "BV2", "stat": {"interact_rate": 852, "not_ready_field": []}}]}
    out = _invoke(
        monkeypatch,
        {"/x/web/data/archive/index": index, "/x/web/data/archive_diagnose/compare": diag},
        ["data", "videos", "--limit", "2", "--sort", "plays"],
    )
    assert [row["bvid"] for row in out["items"]] == ["BV2", "BV1"]
    assert out["items"][0]["plays"] == 900 and out["items"][0]["interaction_percent"] == 8.52
    assert out["items"][1]["plays"] == 100 and out["items"][1]["completion_percent"] == 20.0


def test_video_command(monkeypatch):
    archive = {
        "stat": {"play": 272, "like": 18, "coin": 4, "fav": 11, "reply": 2, "dm": 1, "share": 1, "elec": 0},
        "group": {"fans": 150, "guest": 122},
        "play": {"rate": 3997, "avg_duration": 82, "duration": 207},
        "area": [{"location": "广东", "count": 36}],
    }
    diag = {"list": [{"bvid": "BV1ARHK6jEZx", "title": "t", "pubtime": 1792838129, "stat": {"interact_rate": 852, "not_ready_field": []}}]}
    portrait = {
        # 真实返回里 gender / viewer_age 是计数，不是 1/10000；输出只给百分比
        "gender": {"male": 166, "female": 39},
        "viewer_age": {"age_one": 26, "age_two": 171, "age_three": 160, "age_four": 79},
        "viewer_area": [{"location": "广东", "count": 64}],
        "viewer_ty": [
            {"tag_name": "哲学", "count": 2357},
            {"tag_name": "现象学", "count": 16500},
            {"tag_name": "分析哲学", "count": 2357},
        ],
    }
    out = _invoke(
        monkeypatch,
        {
            "/x/web/data/archive": archive,
            "/x/web/data/archive_diagnose/compare": diag,
            "/x/web/data/survey": {"20261024": {"arc_inc": []}},
            "/x/web/data/archive_diagnose/overview": portrait,
        },
        ["data", "video", "BV1ARHK6jEZx"],
    )
    assert out["title"] == "t" and out["data_date"] == "2026-10-24"
    assert out["audience"] == {"fan_plays": 150, "guest_plays": 122, "fan_percent": 55.15}
    assert out["completion_percent"] == 39.97 and out["avg_watch_seconds"] == 82
    assert out["regions"] == [{"name": "广东", "count": 36}]  # 省份只出一份，画像里的 viewer_area 不重复
    assert out["gender_percent"] == {"male": 80.98, "female": 19.02}
    assert out["age_percent"] == {"0-16": 5.96, "16-25": 39.22, "25-40": 36.7, "40+": 18.12}
    # 权重相同时按名称排，结果才稳定（接口每次返回顺序不同）
    assert [row["name"] for row in out["interests"]] == ["现象学", "分析哲学", "哲学"]
    assert out["interests"][0] == {"name": "现象学", "weight": 16500}
    assert "单位未确认" in out["interests_note"]
    assert "不是 1/10000 占比" in out["portrait_note"]
    assert out["diagnose"]["interaction_percent"] == 8.52
    assert "单位未确认" in out["note"]


def test_video_of_foreign_archive_is_not_found(monkeypatch):
    # 查别人的稿件时 archive 先返回 -403（全局映射成 risk_control），不能让它看起来像风控
    async def fake_get(cred, path, params=None):
        if path == "/x/web/data/archive":
            raise BiliError("risk_control", "x", detail={"bili_code": -403})
        return {
            "/x/web/data/archive_diagnose/compare": {"list": []},
            "/x/web/data/survey": {},
            "/x/web/data/archive_diagnose/overview": {"gender": {}, "viewer_age": {}, "viewer_ty": []},
        }.get(path)

    monkeypatch.setattr(data, "get_data", fake_get)
    monkeypatch.setattr(data.config, "require_credential", lambda: object())
    result = CliRunner().invoke(app, ["data", "video", "BV1ARHK6jEZx"])
    assert result.exit_code == 4
    error = json.loads(result.output)["error"]
    assert error["code"] == "not_found"
    assert "只能看自己的稿件" in error["message"]
    assert error["detail"]["bili_code"] == -403


def test_video_portrait_of_foreign_archive_is_not_found(monkeypatch):
    async def fake_get(cred, path, params=None):
        if path == "/x/web/data/archive_diagnose/overview":
            raise BiliError("api_error", "x", detail={"bili_code": 20980})
        return {
            "/x/web/data/archive": {"stat": {}, "group": {}, "play": {}, "area": []},
            "/x/web/data/archive_diagnose/compare": {"list": []},
            "/x/web/data/survey": {},
        }.get(path)

    monkeypatch.setattr(data, "get_data", fake_get)
    monkeypatch.setattr(data.config, "require_credential", lambda: object())
    result = CliRunner().invoke(app, ["data", "video", "BV1ARHK6jEZx"])
    assert result.exit_code == 4
    error = json.loads(result.output)["error"]
    assert error["code"] == "not_found"
    assert "只能看自己的稿件" in error["message"]
    assert error["detail"]["bili_code"] == 20980


def test_fans_command(monkeypatch):
    numbers = {
        "follow": 32, "follow_last": 48, "unfollow": 20, "unfollow_last": 12,
        "all_fans": 16184, "interact_fans": 1096, "interact_fans_per": 677,
        "interact_pass_per": 8145, "over_peer_percent": 3575, "log_date": 20261024,
    }
    graph = {"tendency": [{"date_key": 1792252800, "total_inc": 16179}]}
    fan = {"summary": {"active": 1098}, "source": {"main": 4437, "space": 312}, "rank_list": {"video_act": [{"uname": "甲"}]}}
    out = _invoke(
        monkeypatch,
        {"/x/web/data/v2/fans/stat/num": numbers, "/x/web/data/v2/fans/stat/graph": graph, "/x/web/data/fan": fan},
        ["data", "fans", "--period", "week"],
    )
    assert out["total"] == 16184 and out["data_date"] == "2026-10-24"
    assert out["period"]["net"] == 12 and out["period"]["net_previous"] == 36
    assert out["interacting_percent"] == 6.77
    assert out["trend"] == [{"date": "2026-10-18", "total": 16179}]


def test_overview_command(monkeypatch):
    _patch_creative_center(
        monkeypatch,
        overview={"play": 3507, "play_last": 10071, "visitor": 294, "visitor_last": 246, "fan": 12, "fan_last": 36, "log_date": 20261024},
        fans={"all_fans": 16184, "follow": 32, "unfollow": 20},
        source={"play_proportion": {"new_mobile": 8309, "new_pc": 984}, "audience_proportion": {"fans": 2143, "guest": 23921}},
        compare={"play": 26065, "play_mid": 283500, "play_pass_peer": 3064},
        survey={"20261024": {"arc_inc": [{"bvid": "BV1", "title": "t", "incr": 272}]}},
    )
    stat = {"total_click": 1967602, "incr_click": 1028, "inc_like": 42, "total_like": 69080, "total_elec": 8000, "inc_elec": 0}
    # shuffled on purpose: fans_net must sort by date_key, not trust the response order
    graph = {"tendency": [{"date_key": 1792944000, "total_inc": 16187}, {"date_key": 1792857600, "total_inc": 16180}]}
    out = _invoke(
        monkeypatch,
        {"/x/web/index/stat": stat, "/x/web/data/v2/fans/stat/graph": graph},
        ["data", "overview", "--period", "week"],
    )
    assert out["data_date"] == "2026-10-24"
    assert out["totals"]["plays"] == 1967602
    assert out["yesterday"]["plays"] == 1028
    # settled fan delta comes from the fan trend, not from the realtime incr_fans
    assert out["yesterday"]["fans_net"] == 7
    assert out["plays"] == {"now": 3507, "previous": 10071}
    assert out["versus_peers"]["plays"]["better_than_percent"] == 30.64
    assert "beats_similar_ups_percent" not in out  # 同样的分位只留一份
    assert out["plays_from_fans_percent"] == 8.22
    assert "单位未确认" in out["totals_note"]


def test_overview_on_empty_account(monkeypatch):
    """#10 的原始数据：新账号的 overview / compare / source / fans 都是 null，survey 是 {"<date>": null}。"""
    _patch_creative_center(monkeypatch, overview=None, fans=None, source=None, compare=None, survey={"20261024": None})
    out = _invoke(monkeypatch, {"/x/web/index/stat": None, "/x/web/data/v2/fans/stat/graph": None}, ["data", "overview"])
    assert out["data_date"] is None
    assert out["totals"]["plays"] is None
    assert out["yesterday"]["fans_net"] is None
    assert out["plays"] == {"now": None, "previous": None}
    assert out["top_videos_yesterday"] == []
    assert out["versus_peers"]["plays"]["you"] is None
