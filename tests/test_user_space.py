import json

import pytest
from typer.testing import CliRunner
from bilibili_api.exceptions import ResponseCodeException

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
    responses = {}
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
            value = type(self).responses.get(self.url)
            if isinstance(value, BaseException):
                raise value
            return value
        return res()


class FakeUser:
    responses = {}
    calls = []

    def __init__(self, mid, credential=None):
        self.mid = mid
        self.credential = credential

    async def _call(self, method, **kwargs):
        type(self).calls.append({"mid": self.mid, "method": method, "kwargs": kwargs})
        value = type(self).responses.get(method)
        if isinstance(value, BaseException):
            raise value
        return value

    async def get_user_info(self):
        value = await self._call("get_user_info")
        return value if value is not None else {"mid": self.mid, "name": "n"}

    async def get_relation_info(self):
        return await self._call("get_relation_info") or {}

    async def get_up_stat(self):
        return await self._call("get_up_stat") or {}

    async def get_videos(self, **kwargs):
        return await self._call("get_videos", **kwargs) or {"page": {}, "list": {"vlist": []}}

    async def get_top_videos(self):
        return await self._call("get_top_videos")

    async def get_masterpiece(self):
        return await self._call("get_masterpiece") or []

    async def get_followings(self, **kwargs):
        return await self._call("get_followings", **kwargs) or {"total": 0, "list": []}

    async def get_followers(self, **kwargs):
        return await self._call("get_followers", **kwargs) or {"total": 0, "list": []}

    async def get_channel_list(self, **kwargs):
        return await self._call("get_channel_list", **kwargs) or {"items_lists": {}}

    async def get_articles(self, **kwargs):
        return await self._call("get_articles", **kwargs) or {"pn": 1, "ps": 20}


@pytest.fixture(autouse=True)
def reset_doubles():
    FakeApi.responses = {}
    FakeApi.calls = []
    FakeUser.responses = {}
    FakeUser.calls = []


@pytest.fixture
def patch_user(monkeypatch):
    monkeypatch.setattr(user_cmd, "Api", FakeApi, raising=False)
    monkeypatch.setattr(user_cmd.user, "User", FakeUser)


def _privacy_error():
    return ResponseCodeException(53013, "用户隐私设置未公开", {})


def _video(bvid, **extra):
    return {"bvid": bvid, "title": bvid, "duration": 231, "pubdate": 1700000000,
            "owner": {"mid": 7, "name": "up"},
            "stat": {"view": 12, "reply": 3, "danmaku": 4}, **extra}


def test_info_navnum_sections_raw_and_nav_error(patch_user):
    nav_url = "https://api.bilibili.com/x/space/navnum"
    FakeApi.responses[nav_url] = {"video": 9, "favourite": {"master": 8, "guest": 7},
                                  "bangumi": 6, "cinema": 5, "article": 4,
                                  "album": 3, "audio": 2, "opus": 1}
    data = _data(runner.invoke(app, ["user", "info", "2"]))
    assert data["sections"] == {"videos": 9, "favs": 7, "bangumi": 6, "cinema": 5,
                                "articles": 4, "album": 3, "audio": 2, "opus": 1}
    assert data["more_with"] == "bili user coins|favs|bangumi|likes|games|collections|articles|following|followers 2"
    FakeApi.responses[nav_url] = RuntimeError("offline")
    data = _data(runner.invoke(app, ["user", "info", "2"]))
    assert "sections" not in data and "more_with" in data
    assert runner.invoke(app, ["user", "info", "2"]).exit_code == 0
    FakeApi.responses[nav_url] = {"navnum": 1}
    raw = _data(runner.invoke(app, ["--raw", "user", "info", "2"]))
    assert "navnum" in raw


def test_videos_front_items_deduplicate_and_metrics(patch_user):
    _login()
    FakeUser.responses.update({
        "get_top_videos": _video("BVtop"),
        "get_masterpiece": [_video("BVtop"), _video("BVmp")],
        "get_videos": {"page": {"count": 4}, "list": {"vlist": [
            {"bvid": "BVtop", "title": "duplicate"},
            {"bvid": "BVmp", "title": "duplicate mp"},
            {"bvid": "BVnormal", "title": "normal", "length": "01:02", "play": 5,
             "comment": 6, "video_review": 7, "created": 1700000000},
        ]}},
    })
    data = _data(runner.invoke(app, ["user", "videos", "2"]))
    assert [x.get("label") for x in data["items"][:2]] == ["置顶", "代表作"]
    assert [x["bvid"] for x in data["items"]] == ["BVtop", "BVmp", "BVnormal"]
    assert data["items"][0]["length"] == "03:51"
    assert {k: data["items"][0][k] for k in ("play", "comment", "danmaku")} == {
        "play": 12, "comment": 3, "danmaku": 4}
    assert "label" not in data["items"][-1]


def test_videos_top_missing_and_skip_front_requests(patch_user):
    _login()
    FakeUser.responses["get_top_videos"] = ResponseCodeException(53016, "没有置顶视频", {})
    FakeUser.responses["get_masterpiece"] = []
    FakeUser.responses["get_videos"] = {"page": {}, "list": {"vlist": []}}
    assert _data(runner.invoke(app, ["user", "videos", "2"]))["items"] == []
    for args in (["user", "videos", "2", "--page", "2"], ["user", "videos", "2", "-k", "xx"]):
        FakeUser.calls = []
        _data(runner.invoke(app, args))
        assert not {c["method"] for c in FakeUser.calls} & {"get_top_videos", "get_masterpiece"}


def test_following_default_mutual_and_other_pagination(patch_user):
    _login()
    FakeUser.responses["get_followings"] = {"total": 120, "list": [
        {"mid": 3, "attribute": 6, "uname": "mutual", "mtime": 1700000000}]}
    own = _data(runner.invoke(app, ["user", "following"]))
    assert own["items"][0]["mutual"] is True and own["total"] == 120
    assert FakeUser.calls[-1]["mid"] == 100 and "next_with" not in own
    other = _data(runner.invoke(app, ["user", "following", "2"]))
    assert "mutual" not in other["items"][0]
    assert other["next_with"] == "bili user following 2 --page 2"
    FakeUser.responses["get_followings"] = _privacy_error()
    assert _data(runner.invoke(app, ["user", "following", "2"]))["hidden"] is True


def test_followers_other_pagination_and_privacy(patch_user):
    _login()
    FakeUser.responses["get_followers"] = {"total": 120, "list": [{"mid": 4, "uname": "fan"}]}
    data = _data(runner.invoke(app, ["user", "followers", "2"]))
    assert data["next_with"] == "bili user followers 2 --page 2"
    assert FakeUser.calls[-1]["method"] == "get_followers"
    FakeUser.responses["get_followers"] = _privacy_error()
    assert _data(runner.invoke(app, ["user", "followers", "2"]))["hidden"] is True


def test_likes_mapping_redaction_and_private(patch_user):
    _login()
    url = "https://api.bilibili.com/x/space/like/video"
    FakeApi.responses[url] = {"list": [{"bvid": "BVlike", "title": "like", "owner": {"name": "UP"},
                                         "stat": {"view": 88}, "ip": "secret"}]}
    result = runner.invoke(app, ["user", "likes", "2"])
    data = _data(result)
    assert data["items"] == [{"bvid": "BVlike", "title": "like", "owner": "UP", "play": 88,
                              "watch_with": "bili video watch BVlike"}]
    assert '"ip"' not in result.stdout
    raw = runner.invoke(app, ["--raw", "user", "likes", "2"])
    assert '"ip"' not in raw.stdout
    FakeApi.responses[url] = _privacy_error()
    assert _data(runner.invoke(app, ["user", "likes", "2"]))["hidden"] is True


def test_games_mapping_only_mid_and_private(patch_user):
    _login()
    url = "https://api.bilibili.com/x/space/lastplaygame/v2"
    FakeApi.responses[url] = {"total_count": 6, "list": [{"game_name": "game", "game_tags": ["rpg"],
                                                               "detail_url": "https://game.test"}]}
    data = _data(runner.invoke(app, ["user", "games", "2"]))
    assert data["total"] == 6 and data["items"] == [{"name": "game", "tags": ["rpg"], "url": "https://game.test"}]
    assert FakeApi.calls[-1].params == {"mid": 2}
    FakeApi.responses[url] = _privacy_error()
    assert _data(runner.invoke(app, ["user", "games", "2"]))["hidden"] is True


def test_collections_mapping_and_next(patch_user):
    _login()
    FakeUser.responses["get_channel_list"] = {"items_lists": {"page": {"total": 12},
        "seasons_list": [{"meta": {"season_id": 1, "name": "season", "total": 4}}],
        "series_list": [{"meta": {"series_id": 2, "name": "series", "total": 5}}]}}
    data = _data(runner.invoke(app, ["user", "collections", "2", "--size", "5"]))
    assert data["total"] == 12
    assert data["items"] == [
        {"kind": "合集", "id": 1, "title": "season", "count": 4},
        {"kind": "系列", "id": 2, "title": "series", "count": 5},
    ]
    assert data["next_with"] == "bili user collections 2 --page 2 --size 5"


def test_articles_mapping_and_empty(patch_user):
    _login()
    FakeUser.responses["get_articles"] = {"count": 1, "articles": [{"id": 123, "title": "article",
        "publish_time": 1700000000, "stats": {"view": 20}}]}
    data = _data(runner.invoke(app, ["user", "articles", "2"]))
    article = data["items"][0]
    assert article["url"] == "https://www.bilibili.com/read/cv123" and article["views"] == 20
    assert article["published_at"].endswith("+08:00")
    FakeUser.responses["get_articles"] = {"pn": 1, "ps": 20}
    data = _data(runner.invoke(app, ["user", "articles", "2"]))
    assert data["items"] == [] and data["hidden"] is False and "next_with" not in data


def test_public_commands_require_login_before_fetch(patch_user):
    commands = (["likes", "2"], ["games", "2"], ["collections", "2"], ["articles", "2"],
                ["followers", "2"], ["following"])
    for args in commands:
        result = runner.invoke(app, ["user", *args])
        assert result.exit_code == 3, result.output
        assert json.loads(result.stdout)["error"]["code"] == "not_logged_in"
    assert FakeApi.calls == [] and FakeUser.calls == []
