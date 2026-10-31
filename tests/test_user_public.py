import json

import pytest
from typer.testing import CliRunner

from bilibili_api.exceptions import ResponseCodeException
from bilibili_api.user import BangumiType

from bili_cli import config
from bili_cli.commands import user as user_cmd
from bili_cli.main import app


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BILI_CLI_HOME", str(tmp_path))


runner = CliRunner()


def _login():
    config.save_credential(config.Credential(sessdata="s", bili_jct="j", dedeuserid="100"))


def _data(result):
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    return payload["data"]


class FakeApi:
    response = None
    calls = []

    def __init__(self, url, method="GET", credential=None, **kw):
        self.url = url
        self.method = method
        self.credential = credential
        self.kw = kw
        self.params = {}
        type(self).calls.append(self)

    def update_params(self, **params):
        self.params.update(params)
        return self

    @property
    def result(self):
        async def res():
            if isinstance(type(self).response, BaseException):
                raise type(self).response
            return type(self).response
        return res()


class FakeUser:
    response = None
    calls = []

    def __init__(self, mid, credential=None):
        self.mid = mid
        self.credential = credential
        type(self).calls.append(self)

    async def get_subscribed_bangumi(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(type(self).response, BaseException):
            raise type(self).response
        return type(self).response


@pytest.fixture(autouse=True)
def reset_doubles():
    FakeApi.calls = []
    FakeApi.response = None
    FakeUser.calls = []
    FakeUser.response = None


@pytest.fixture
def patch_user(monkeypatch):
    monkeypatch.setattr(user_cmd, "Api", FakeApi, raising=False)
    monkeypatch.setattr(user_cmd.user, "User", FakeUser)


def _privacy_error():
    return ResponseCodeException(53013, "用户隐私设置未公开", {})


def test_coins_fields_and_raw_ip_redaction(monkeypatch, patch_user):
    _login()
    FakeApi.response = [{"bvid": "BVabc", "title": "视频", "coins": 2, "time": 1700000000,
                         "owner": {"name": "UP"}, "ip": "某地"}]
    result = runner.invoke(app, ["user", "coins", "2"])
    data = _data(result)
    assert data["mid"] == 2 and data["hidden"] is False
    item = data["items"][0]
    assert {k: item[k] for k in ("bvid", "title", "coins", "owner", "watch_with")} == {
        "bvid": "BVabc", "title": "视频", "coins": 2, "owner": "UP",
        "watch_with": "bili video watch BVabc",
    }
    assert item["coined_at"].endswith("+08:00")
    assert '"ip"' not in result.stdout

    FakeApi.response = [{"bvid": "BVabc", "ip": "某地"}]
    raw = runner.invoke(app, ["--raw", "user", "coins", "2"])
    assert raw.exit_code == 0, raw.output
    assert '"ip"' not in raw.stdout


def test_coins_private_and_non_private_errors(monkeypatch, patch_user):
    _login()
    FakeApi.response = _privacy_error()
    assert _data(runner.invoke(app, ["user", "coins", "2"])) == {
        "mid": 2, "hidden": True, "items": []
    }
    FakeApi.response = ResponseCodeException(-352, "risk", {})
    result = runner.invoke(app, ["user", "coins", "2"])
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "risk_control"


def test_public_empty_results_are_not_hidden(monkeypatch, patch_user):
    _login()
    FakeApi.response = []
    assert _data(runner.invoke(app, ["user", "coins", "2"])) == {
        "mid": 2, "hidden": False, "items": []
    }
    FakeUser.response = {"total": 0, "list": []}
    data = _data(runner.invoke(app, ["user", "bangumi", "2"]))
    assert data["hidden"] is False and data["items"] == []
    assert "next_with" not in data


def test_all_public_commands_require_login_before_fetch(monkeypatch, patch_user):
    fetches = []

    async def fake_favs(*args, **kwargs):
        fetches.append((args, kwargs))
        return {"list": []}

    monkeypatch.setattr(user_cmd.favorite_list, "get_video_favorite_list", fake_favs, raising=False)
    for argv in (["user", "coins", "2"], ["user", "favs", "2"], ["user", "bangumi", "2"]):
        result = runner.invoke(app, argv)
        assert result.exit_code == 3, result.output
        assert json.loads(result.stdout)["error"]["code"] == "not_logged_in"
    assert FakeApi.calls == [] and FakeUser.calls == [] and fetches == []


def test_favs_filter_private_and_raw(monkeypatch, patch_user):
    _login()
    data = {"count": 99, "list": [
        {"id": 1, "title": "公开默认", "media_count": 10, "attr": 0},
        {"id": 2, "title": "私有甲", "media_count": 20, "attr": 1},
        {"id": 3, "title": "公开非默认", "media_count": 30, "attr": 2},
        {"id": 4, "title": "私有乙", "media_count": 40, "attr": 23},
        {"id": 5, "title": "公开非默认二", "media_count": 50, "attr": 22},
    ]}

    async def fake_favs(mid, credential=None):
        return data

    monkeypatch.setattr(user_cmd.favorite_list, "get_video_favorite_list", fake_favs, raising=False)
    result = runner.invoke(app, ["user", "favs", "2"])
    out = _data(result)
    assert out["count"] == 3
    assert [item["id"] for item in out["items"]] == [1, 3, 5]
    assert all("default" not in item for item in out["items"])
    assert "私有甲" not in result.stdout and "私有乙" not in result.stdout

    raw = runner.invoke(app, ["--raw", "user", "favs", "2"])
    raw_data = _data(raw)
    assert raw_data["count"] == 3
    assert [item["id"] for item in raw_data["list"]] == [1, 3, 5]
    assert "私有甲" not in raw.stdout and "私有乙" not in raw.stdout


def test_favs_null_and_privacy_code_are_hidden(monkeypatch, patch_user):
    _login()
    async def none_favs(mid, credential=None):
        return None
    monkeypatch.setattr(user_cmd.favorite_list, "get_video_favorite_list", none_favs, raising=False)
    assert _data(runner.invoke(app, ["user", "favs", "2"])) == {
        "mid": 2, "hidden": True, "items": []
    }

    async def private_favs(mid, credential=None):
        raise _privacy_error()
    monkeypatch.setattr(user_cmd.favorite_list, "get_video_favorite_list", private_favs, raising=False)
    assert _data(runner.invoke(app, ["user", "favs", "2"]))["hidden"] is True


def test_bangumi_types_pagination_and_mapping(monkeypatch, patch_user):
    _login()
    FakeUser.response = {"total": 50, "list": [
        {"season_id": 11, "title": "想看", "follow_status": 1, "progress": "第 1 集", "total_count": 12},
        {"season_id": 12, "title": "在看", "follow_status": 2, "progress": "", "total_count": 0},
        {"season_id": 13, "title": "看过", "follow_status": 3, "total_count": -1,
         "url": "https://example.test/season"},
        {"season_id": 14, "title": "未知", "follow_status": 9},
    ]}
    result = runner.invoke(app, ["user", "bangumi", "2", "--type", "cinema", "--page", "2", "--size", "5"])
    data = _data(result)
    assert FakeUser.calls[-1].kwargs == {"type_": BangumiType.DRAMA, "pn": 2, "ps": 5}
    assert data["type"] == "cinema" and data["page"] == 2
    items = data["items"]
    assert [x["follow_status"] for x in items] == ["想看", "在看", "看过", 9]
    assert items[1]["progress"] is None and items[1]["total"] is None
    assert items[0]["total"] == 12
    assert items[0]["url"] is None
    assert items[2]["url"] == "https://example.test/season"
    assert data["next_with"] == "bili user bangumi 2 --type cinema --page 3 --size 5"

    result = runner.invoke(app, ["user", "bangumi", "2"])
    assert result.exit_code == 0, result.output
    assert FakeUser.calls[-1].kwargs == {"type_": BangumiType.BANGUMI, "pn": 1, "ps": 20}


def test_bangumi_next_with_default_size(monkeypatch, patch_user):
    _login()
    FakeUser.response = {"total": 50, "list": []}
    data = _data(runner.invoke(app, ["user", "bangumi", "2"]))
    assert data["next_with"] == "bili user bangumi 2 --type bangumi --page 2"


def test_bangumi_hidden_validation_and_last_page(monkeypatch, patch_user):
    _login()
    FakeUser.response = _privacy_error()
    assert _data(runner.invoke(app, ["user", "bangumi", "2"]))["hidden"] is True

    FakeUser.response = {"total": 0, "list": []}
    result = runner.invoke(app, ["user", "bangumi", "2", "--size", "31"])
    assert result.exit_code == 0, result.output
    assert FakeUser.calls[-1].kwargs["ps"] == 31
    result = runner.invoke(app, ["user", "bangumi", "2", "--type", "anime"])
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "invalid_argument"

    FakeUser.response = {"total": 25, "list": []}
    data = _data(runner.invoke(app, ["user", "bangumi", "2", "--page", "2", "--size", "20"]))
    assert "next_with" not in data

    FakeUser.response = ResponseCodeException(-352, "risk", {})
    result = runner.invoke(app, ["user", "bangumi", "2"])
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "risk_control"


def test_bangumi_raw_response(monkeypatch, patch_user):
    _login()
    original = {"total": 0, "list": []}
    FakeUser.response = original
    assert _data(runner.invoke(app, ["--raw", "user", "bangumi", "2"])) == original
