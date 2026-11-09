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

    async def get_subscribed_bangumi(self, **kwargs):
        return await self._call("get_subscribed_bangumi", **kwargs) or {"list": []}

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


def test_info_sections_preview_all_public_and_raw(patch_user, monkeypatch):
    _login()
    nav_url = user_cmd.NAVNUM_URL
    FakeApi.responses.update({
        nav_url: {"video": 9, "favourite": {"master": 8, "guest": 7}, "bangumi": 6,
                  "cinema": 5, "article": 4, "album": 3, "audio": 2, "opus": 1},
        user_cmd.COIN_URL: [{"title": f"coin{i}"} for i in range(4)],
        user_cmd.LIKE_URL: {"list": [{"title": f"like{i}"} for i in range(4)]},
        user_cmd.GAME_URL: {"total_count": 10, "list": [{"game_name": f"game{i}"} for i in range(4)]},
    })
    FakeUser.responses.update({
        "get_relation_info": {"following": 2},
        "get_videos": {"list": {"vlist": [{"title": f"video{i}"} for i in range(4)]}},
        "get_subscribed_bangumi": {"list": [{"title": f"anime{i}"} for i in range(4)]},
        "get_channel_list": {"items_lists": {"page": {"total": 12},
            "seasons_list": [{"meta": {"name": f"season{i}"}} for i in range(2)],
            "series_list": [{"meta": {"name": f"series{i}"}} for i in range(2)]}},
        "get_articles": {"articles": [{"title": f"article{i}"} for i in range(4)]},
        "get_followings": {"list": [{"uname": f"follow{i}"} for i in range(4)]},
    })
    async def favs(mid, credential=None):
        return {"list": [{"title": f"fav{i}", "attr": 0} for i in range(4)] + [{"title": "private-folder", "attr": 1}]}
    monkeypatch.setattr(user_cmd.favorite_list, "get_video_favorite_list", favs)

    data = _data(runner.invoke(app, ["user", "info", "2", "--sections"]))
    sections = data["sections"]
    assert sections["videos"] == {"count": 9, "preview": ["video0", "video1", "video2"]}
    assert sections["coins"] == {"preview": ["coin0", "coin1", "coin2"]}
    assert sections["likes"] == {"preview": ["like0", "like1", "like2"]}
    assert sections["favs"] == {"count": 7, "preview": ["fav0", "fav1", "fav2"]}
    assert "private-folder" not in json.dumps(sections)
    assert sections["bangumi"] == {"count": 6, "preview": ["anime0", "anime1", "anime2"]}
    assert sections["cinema"] == {"count": 5}
    assert sections["games"] == {"count": 10, "preview": ["game0", "game1", "game2"]}
    assert sections["collections"] == {"count": 12, "preview": ["season0", "season1", "series0"]}
    assert sections["articles"] == {"count": 4, "preview": ["article0", "article1", "article2"]}
    assert sections["following"] == {"count": 2, "preview": ["follow0", "follow1", "follow2"]}
    assert sections["album"] == {"count": 3} and sections["audio"] == {"count": 2} and sections["opus"] == {"count": 1}
    assert data["more_with"] == "bili user coins|favs|bangumi|likes|games|collections|articles|following|followers 2"

    FakeUser.calls = []
    FakeApi.calls = []
    raw = _data(runner.invoke(app, ["--raw", "user", "info", "2", "--sections"]))
    assert "navnum" in raw
    assert not {call["method"] for call in FakeUser.calls} & {"get_videos", "get_subscribed_bangumi", "get_channel_list", "get_articles", "get_followings"}
    assert len([call for call in FakeApi.calls if call.url != nav_url]) == 0


def test_info_preview_privacy_failures_and_empty(patch_user, monkeypatch):
    _login()
    nav_url = user_cmd.NAVNUM_URL
    FakeApi.responses.update({nav_url: {"video": 3, "favourite": {"guest": 7}},
        user_cmd.COIN_URL: _privacy_error(), user_cmd.LIKE_URL: ResponseCodeException(-352, "risk", {}),
        user_cmd.GAME_URL: {"total_count": 0, "list": []}})
    async def favs_none(mid, credential=None):
        return None
    monkeypatch.setattr(user_cmd.favorite_list, "get_video_favorite_list", favs_none)
    FakeUser.responses.update({"get_videos": {"list": {"vlist": [{"title": "video"}]}},
        "get_subscribed_bangumi": {"list": []}, "get_channel_list": {"items_lists": {"page": {"total": 0}}},
        "get_articles": {"pn": 1, "ps": 3}, "get_followings": {"total": 4, "list": []}})
    data = _data(runner.invoke(app, ["user", "info", "2", "--sections"]))
    assert data["sections"]["coins"] == {"hidden": True}
    assert data["sections"]["favs"] == {"count": 7, "hidden": True}
    assert data["sections"]["likes"] == {"error": "risk_control"}
    assert data["sections"]["videos"] == {"count": 3, "preview": ["video"]}
    assert data["sections"]["articles"]["preview"] == []
    assert "more_with" in data

    FakeApi.responses[nav_url] = RuntimeError("offline")
    FakeApi.responses[user_cmd.COIN_URL] = [{"title": "coin"}]
    FakeApi.responses[user_cmd.LIKE_URL] = {"list": []}
    data = _data(runner.invoke(app, ["user", "info", "2", "--sections"]))
    assert data["sections"]["videos"] == {"preview": ["video"]}
    assert "count" not in data["sections"]["videos"]
    assert "more_with" in data
    assert runner.invoke(app, ["user", "info", "2", "--sections"]).exit_code == 0


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
    assert [x.get("label") for x in data["pinned"]] == ["置顶", "代表作"]
    assert [x["bvid"] for x in data["items"]] == ["BVtop", "BVmp", "BVnormal"]
    assert data["pinned"][0]["length"] == "03:51"
    assert {k: data["pinned"][0][k] for k in ("play", "comment", "danmaku")} == {
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
    assert "next_with" not in other
    FakeUser.responses["get_followings"] = _privacy_error()
    assert _data(runner.invoke(app, ["user", "following", "2"]))["hidden"] is True


def test_followers_other_pagination_and_privacy(patch_user):
    _login()
    FakeUser.responses["get_followers"] = {"total": 120, "list": [{"mid": 4, "uname": "fan"}]}
    data = _data(runner.invoke(app, ["user", "followers", "2"]))
    assert "next_with" not in data
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


@pytest.mark.parametrize("raw", [False, True])
@pytest.mark.parametrize("logged_in", [False, True])
def test_info_default_only_requests_profile(patch_user, monkeypatch, raw, logged_in):
    if logged_in:
        _login()

    async def unexpected_favs(*args, **kwargs):
        pytest.fail("default info must not request folders")

    monkeypatch.setattr(user_cmd.favorite_list, "get_video_favorite_list", unexpected_favs)
    args = (["--raw"] if raw else []) + ["user", "info", "2"]
    data = _data(runner.invoke(app, args))
    assert "sections" not in data and "more_with" not in data and "navnum" not in data
    assert [c["method"] for c in FakeUser.calls] == ["get_user_info", "get_relation_info", "get_up_stat"]
    assert FakeApi.calls == []


@pytest.mark.parametrize("order", ["new", "views", "favorites"])
def test_videos_preserve_sort_size_and_page_boundaries(patch_user, monkeypatch, order):
    pages = {
        1: [{"bvid": f"BV{i}", "play": 100 - i} for i in range(5)],
        2: [{"bvid": f"BV{i}", "play": 100 - i} for i in range(5, 10)],
    }
    FakeUser.responses.update({"get_top_videos": _video("BV7"),
                               "get_masterpiece": [_video("BV2"), _video("BVmp")]})

    async def get_videos(self, **kwargs):
        FakeUser.calls.append({"method": "get_videos", "kwargs": kwargs})
        return {"page": {"count": 10}, "list": {"vlist": pages[kwargs["pn"]]}}

    monkeypatch.setattr(FakeUser, "get_videos", get_videos)
    results = []
    for page in (1, 2):
        result = _data(runner.invoke(app, ["user", "videos", "2", "--order", order,
                                         "--size", "5", "--page", str(page)]))
        assert len(result["items"]) == 5 and result["total"] == 10 and result["page"] == page
        assert [v["bvid"] for v in result["items"]] == [v["bvid"] for v in pages[page]]
        assert [v["play"] for v in result["items"]] == [v["play"] for v in pages[page]]
        results.extend(v["bvid"] for v in result["items"])
        assert [v["bvid"] for v in result["pinned"]] == (["BV7", "BV2", "BVmp"] if page == 1 else [])
    assert len(set(results)) == 10
    calls = [c for c in FakeUser.calls if c["method"] == "get_videos"]
    assert [c["kwargs"] for c in calls] == [
        {"pn": p, "ps": 5, "keyword": "", "order": user_cmd.VIDEO_ORDERS[order]} for p in (1, 2)]


def test_videos_raw_preserves_api_pagination_and_redacts_ip(patch_user):
    FakeUser.responses["get_videos"] = {"page": {"count": 2}, "list": {"vlist": [
        {"bvid": "BVsecond", "play": 2, "ip": "secret"}, {"bvid": "BVfirst", "play": 1}]}}
    data = _data(runner.invoke(app, ["--raw", "user", "videos", "2", "--order", "views", "--size", "2"]))
    assert data == {"page": {"count": 2}, "list": {"vlist": [
        {"bvid": "BVsecond", "play": 2}, {"bvid": "BVfirst", "play": 1}]}}
    assert [c["method"] for c in FakeUser.calls] == ["get_videos"]


@pytest.mark.parametrize("cmd,method,code", [
    ("following", "get_followings", 22115), ("followers", "get_followers", 22118),
    ("following", "get_followings", 53013), ("followers", "get_followers", 53013),
])
@pytest.mark.parametrize("raw", [False, True])
def test_follow_list_privacy_codes(patch_user, cmd, method, code, raw):
    _login()
    FakeUser.responses[method] = ResponseCodeException(code, "private", {})
    args = (["--raw"] if raw else []) + ["user", cmd, "2"]
    assert _data(runner.invoke(app, args)) == {"mid": 2, "hidden": True, "items": []}


@pytest.mark.parametrize("cmd,method", [("following", "get_followings"), ("followers", "get_followers")])
@pytest.mark.parametrize("raw", [False, True])
def test_follow_list_identity_and_page_limits(patch_user, monkeypatch, cmd, method, raw):
    _login()
    FakeUser.responses[method] = {"total": 10000, "list": [{"mid": 9, "uname": "n", "attribute": 6}]}

    async def search_by_type(who, **kwargs):
        return {"result": [{"mid": 100 if who == "myself" else 2, "uname": who}]}

    monkeypatch.setattr(user_cmd.search, "search_by_type", search_by_type)
    prefix = (["--raw"] if raw else []) + ["user", cmd]
    for target in ([], ["100"], ["myself"]):
        data = _data(runner.invoke(app, prefix + target + ["--page", "6"]))
        assert FakeUser.calls[-1]["mid"] == 100
        assert FakeUser.calls[-1]["kwargs"] == {"pn": 6, "ps": 50}
        assert "next_with" not in data
        if not raw:
            assert data["items"][0]["mutual"] is True
    for target in ("2", "someone"):
        for page in (1, 5):
            data = _data(runner.invoke(app, prefix + [target, "--page", str(page), "--size", "5"]))
            assert "next_with" not in data
            assert FakeUser.calls[-1]["kwargs"] == {"pn": page, "ps": 5}
            if not raw:
                assert "mutual" not in data["items"][0]
        FakeUser.calls = []
        result = runner.invoke(app, prefix + [target, "--page", "6"])
        assert result.exit_code == 2
        assert json.loads(result.stdout)["error"]["code"] == "invalid_argument"
        assert FakeUser.calls == []  # rejected before fetching the target list, including --raw


@pytest.mark.parametrize("cmd,method", [("following", "get_followings"), ("followers", "get_followers")])
def test_follow_list_missing_identity_cannot_bypass_limit(patch_user, cmd, method):
    config.save_credential(config.Credential(sessdata="s", bili_jct="j"))
    FakeUser.responses[method] = {"total": 0, "list": []}
    assert _data(runner.invoke(app, ["user", cmd, "2"]))["mid"] == 2
    FakeUser.calls = []
    result = runner.invoke(app, ["user", cmd, "2", "--page", "6"])
    assert result.exit_code == 2 and FakeUser.calls == []
    result = runner.invoke(app, ["user", cmd])
    assert result.exit_code == 3
    assert json.loads(result.stdout)["error"]["code"] == "auth_expired"


@pytest.mark.parametrize("error,expected", [
    (ResponseCodeException(-412, "risk", {}), "risk_control"),
    (ResponseCodeException(-509, "limited", {}), "rate_limited"),
    (ResponseCodeException(99999, "unknown", {}), "api_error"),
    (TimeoutError("offline"), "network_error"),
])
def test_section_errors_are_distinct_from_private_and_empty(patch_user, monkeypatch, error, expected):
    _login()
    FakeApi.responses[user_cmd.LIKE_URL] = error
    FakeApi.responses[user_cmd.COIN_URL] = []
    FakeApi.responses[user_cmd.GAME_URL] = {"list": []}
    FakeUser.responses["get_followings"] = ResponseCodeException(22115, "private", {})

    async def favs(*args, **kwargs):
        return {"list": []}

    monkeypatch.setattr(user_cmd.favorite_list, "get_video_favorite_list", favs)
    sections = _data(runner.invoke(app, ["user", "info", "2", "--sections"]))["sections"]
    assert sections["likes"] == {"error": expected}
    assert sections["following"] == {"hidden": True}
    assert sections["coins"] == {"preview": []}


def test_section_concurrency_is_bounded(patch_user, monkeypatch):
    import asyncio

    active = 0
    maximum = 0
    calls = 0

    async def fetch_preview(fetch, pick, count=None):
        nonlocal active, maximum, calls
        calls += 1
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0)  # let other previews enter; no real HTTP or timing dependency
        active -= 1
        return {"preview": []}

    monkeypatch.setattr(user_cmd, "_preview", fetch_preview)
    _data(runner.invoke(app, ["user", "info", "2", "--sections"]))
    assert calls == 9 and maximum == 2


@pytest.mark.parametrize("raw", [False, True])
def test_info_reports_navnum_failure(patch_user, monkeypatch, raw):
    FakeApi.responses[user_cmd.NAVNUM_URL] = ResponseCodeException(-352, "risk", {})
    async def preview(*args, **kwargs):
        return {"preview": []}
    monkeypatch.setattr(user_cmd, "_preview", preview)
    data = _data(runner.invoke(app, (["--raw"] if raw else []) + ["user", "info", "2", "--sections"]))
    assert data["sections_error"] == {"navnum": "risk_control"}
