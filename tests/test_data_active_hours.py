import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from bili_cli.commands import data
from bili_cli.errors import BiliError
from bili_cli.main import app


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BILI_CLI_HOME", str(tmp_path))
    return tmp_path


def _run(monkeypatch, payloads, argv, mid="7818988"):
    async def fake_get(cred, path, params=None):
        value = payloads.get(path)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(data, "get_data", fake_get)
    monkeypatch.setattr(data.config, "require_credential", lambda: SimpleNamespace(dedeuserid=mid))
    return CliRunner().invoke(app, argv)


def _invoke(monkeypatch, payloads, argv, mid="7818988"):
    result = _run(monkeypatch, payloads, argv, mid)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["data"]


def _hourly(values):
    return [{"hour_key": hour, "total_inc": value} for hour, value in enumerate(values)]


def test_active_hours_scales_1_of_10000_and_compares_peers(monkeypatch):
    fans = [0] * 24
    peers = [0] * 24
    fans[22], fans[0], peers[12] = 569, 474, 569
    portrait = {"fans_activity": _hourly(fans), "peer_fans_activity": _hourly(peers)}
    out = _invoke(monkeypatch, {"/x/web/data/v2/fans/stat/portrayal": portrait}, ["data", "active-hours"])

    assert out["hours_window"] == "最近 30 天"
    assert len(out["hours"]) == 24
    hour22 = out["hours"][22]
    assert hour22 == {"hour": 22, "fans_percent": 5.69, "peer_percent": 0.0, "gap_percent": 5.69}
    # 大盘的峰值落在 12 点，自己这边是 22 点，两边的分布各算各的
    assert out["hours"][12]["gap_percent"] == -5.69
    assert [row["hour"] for row in out["top_hours"]] == [22, 0]


def test_active_hours_weekday_peak_and_recent_uploads(monkeypatch):
    portrait = {
        "fans_activity": _hourly([1] * 24),
        "peer_fans_activity": _hourly([1] * 24),
    }
    idea = {
        "fans_active_week_day": [{"period": day, "cnt": 1000 + day} for day in range(1, 8)],
        "fans_active_period": {"day_of_week": 7, "hour": 22},
        "active_fans_pass_per": 8607,
        "recent_archive_active_period": [{"day_of_week": 6, "hour": 22}, {"day_of_week": 4, "hour": 18}],
    }
    out = _invoke(
        monkeypatch,
        {
            "/x/web/data/v2/fans/stat/portrayal": portrait,
            "/x/web/data/account_diagnose/active_fans_improve_idea": idea,
        },
        ["data", "active-hours"],
    )
    assert out["weekdays"][0] == {"weekday": "周一", "fans_percent": 10.01}
    assert out["weekdays"][6] == {"weekday": "周日", "fans_percent": 10.07}
    assert out["peak"] == {"weekday": "周日", "hour": 22, "text": "周日 22 点"}
    assert out["better_than_peers_percent"] == 86.07
    assert out["recent_upload_slots"] == [
        {"weekday": "周六", "hour": 22},
        {"weekday": "周四", "hour": 18},
    ]


@pytest.mark.parametrize("code", ["risk_control", "auth_expired", "network_error"])
def test_active_hours_never_swallows_panel_errors(monkeypatch, code):
    """活跃分析面板报错时不能再降级：风控、登录过期、网络问题都要原样抛出去，agent 才知道该停。"""
    portrait = {"fans_activity": _hourly([2] * 24), "peer_fans_activity": _hourly([1] * 24)}
    result = _run(
        monkeypatch,
        {
            "/x/web/data/v2/fans/stat/portrayal": portrait,
            "/x/web/data/account_diagnose/active_fans_improve_idea": BiliError(code, "面板报错"),
        },
        ["data", "active-hours"],
    )
    assert result.exit_code != 0
    assert json.loads(result.output)["error"]["code"] == code


def test_active_hours_on_empty_account(monkeypatch):
    out = _invoke(monkeypatch, {}, ["data", "active-hours"])
    assert out["hours"][0] == {"hour": 0, "fans_percent": None, "peer_percent": None, "gap_percent": None}
    assert out["top_hours"] == [] and out["peak"] is None


def test_active_hours_raw(monkeypatch):
    portrait = {"fans_activity": _hourly([1] * 24)}
    out = _invoke(
        monkeypatch,
        {
            "/x/web/data/v2/fans/stat/portrayal": portrait,
            "/x/web/data/account_diagnose/active_fans_improve_idea": {"active_fans_pass_per": 8607},
        },
        ["--raw", "data", "active-hours"],
    )
    assert out["portrayal"] == portrait
    assert out["improve_idea"] == {"active_fans_pass_per": 8607}
