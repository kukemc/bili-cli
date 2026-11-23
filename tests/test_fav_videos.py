import json

import pytest
from bilibili_api import favorite_list
from typer.testing import CliRunner

from bili_cli import config
from bili_cli.commands import me, video
from bili_cli.errors import BiliError
from bili_cli.main import app

FOLDERS = [
    {"id": 11, "title": "默认收藏夹", "count": 3, "has_video": False, "default": True},
    {"id": 22, "title": "慢慢看", "count": 45, "has_video": False, "default": False},
]


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BILI_CLI_HOME", str(tmp_path))
    monkeypatch.setattr(me, "FAV_PAGE_DELAY", 0)


def _media(n, fav_time, **kw):
    return {"id": n, "type": 2, "bvid": f"BV{n:010d}", "title": f"v{n}", "duration": 60 + n, "pubtime": 1700000000,
            "fav_time": fav_time, "attr": 0, "upper": {"mid": 9, "name": "up"}, **kw}


def _invoke(monkeypatch, pages, argv):
    """pages: {page number: (medias, has_more)}; returns (data, requested pages)."""
    calls = []

    async def fake_folders(cred, v=None):
        return FOLDERS

    async def fake_content(media_id, page=1, credential=None, **kw):
        calls.append((media_id, page))
        medias, more = pages[page]
        return {"info": {"id": media_id}, "medias": medias, "has_more": more}

    monkeypatch.setattr(video, "_folders", fake_folders)
    monkeypatch.setattr(favorite_list, "get_video_favorite_list_content", fake_content)
    config.save_credential(config.Credential(sessdata="s", bili_jct="j", dedeuserid="1"))
    result = CliRunner().invoke(app, ["me", "fav-videos", *argv])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["data"], calls


def test_pick_folder_by_id_title_or_default():
    assert me.pick_folder(FOLDERS, None)["id"] == 11
    assert me.pick_folder(FOLDERS, "22")["title"] == "慢慢看"
    assert me.pick_folder(FOLDERS, "慢慢看")["id"] == 22
    with pytest.raises(BiliError) as e:
        me.pick_folder(FOLDERS, "不存在")
    assert e.value.code == "not_found" and "慢慢看" in e.value.detail["folders"]
    with pytest.raises(BiliError):
        me.pick_folder([], None)


def test_invalid_and_non_video_items():
    items, skipped, reached = me.collect_fav_page(
        [_media(1, 300), _media(2, 200, attr=9, title="已失效视频"), {"id": 3, "type": 12, "fav_time": 100}], None
    )
    assert [i["bvid"] for i in items] == ["BV0000000001", "BV0000000002"]
    assert "invalid" not in items[0] and items[1]["invalid"] is True
    assert skipped == 1 and reached is False


def test_one_page_with_next_command(monkeypatch):
    data, calls = _invoke(monkeypatch, {2: ([_media(1, 500)], True)}, ["慢慢看", "--page", "2"])
    assert calls == [(22, 2)]
    assert data["folder"] == {"id": 22, "title": "慢慢看", "count": 45}
    assert data["page"] == 2 and data["has_more"] is True and data["next"] == "bili me fav-videos 22 --page 3"
    assert data["items"][0] == {"bvid": "BV0000000001", "title": "v1", "up": "up", "up_mid": 9, "duration": 61,
                                "pubtime": 1700000000, "fav_time": 500}


def test_since_pages_until_an_older_item(monkeypatch):
    # newest first: page 1 is all new, page 2 crosses `since`, page 3 must not be fetched
    pages = {
        1: ([_media(i, 1000 - i) for i in range(20)], True),
        2: ([_media(20, 950), _media(21, 900), _media(22, 850)], True),
        3: ([_media(23, 10)], False),
    }
    data, calls = _invoke(monkeypatch, pages, ["22", "--since", "900"])
    assert calls == [(22, 1), (22, 2)]
    # `since` is inclusive: the video favorited in that very second is listed again
    assert len(data["items"]) == 22 and data["items"][-1]["fav_time"] == 900
    assert data["since"] == 900 and data["newest_fav_time"] == 1000
    assert "page" not in data and "next" not in data


def test_since_with_nothing_new(monkeypatch):
    data, calls = _invoke(monkeypatch, {1: ([_media(1, 99)], False)}, ["--since", "100"])
    assert calls == [(11, 1)] and data["items"] == [] and "newest_fav_time" not in data


def test_since_stops_when_folder_runs_out(monkeypatch):
    data, calls = _invoke(monkeypatch, {1: ([_media(1, 500)], False)}, ["22", "--since", "0"])
    assert calls == [(22, 1)] and len(data["items"]) == 1


def test_since_keeps_a_batch_that_shares_one_second(monkeypatch):
    # a batch copy / move stamps every video with the same fav_time
    pages = {1: ([_media(1, 700), _media(2, 700), _media(3, 700), _media(4, 699)], False)}
    data, _ = _invoke(monkeypatch, pages, ["22", "--since", "700"])
    assert [i["bvid"] for i in data["items"]] == ["BV0000000001", "BV0000000002", "BV0000000003"]


def test_since_reports_truncation_after_max_pages(monkeypatch):
    monkeypatch.setattr(me, "FAV_MAX_PAGES", 3)
    pages = {n: ([_media(n, 1000 - n)], True) for n in range(1, 5)}
    data, calls = _invoke(monkeypatch, pages, ["22", "--since", "0"])
    assert calls == [(22, 1), (22, 2), (22, 3)]
    assert data["truncated"] is True and data["next"] == "bili me fav-videos 22 --since 0 --page 4"
    assert "stopped" not in data
    assert len(data["items"]) == 3


def test_since_not_truncated_when_it_reaches_older_items(monkeypatch):
    monkeypatch.setattr(me, "FAV_MAX_PAGES", 1)
    data, _ = _invoke(monkeypatch, {1: ([_media(1, 500), _media(2, 100)], True)}, ["22", "--since", "200"])
    assert "truncated" not in data and "next" not in data


def test_since_pauses_between_pages(monkeypatch):
    import asyncio

    monkeypatch.setattr(me, "FAV_PAGE_DELAY", 0.125)
    slept = []
    real_sleep = asyncio.sleep

    async def fake_sleep(s, *a, **kw):
        if s == 0.125:
            slept.append(s)
            return
        await real_sleep(s, *a, **kw)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    pages = {1: ([_media(1, 900)], True), 2: ([_media(2, 800)], True), 3: ([_media(3, 10)], False)}
    _, calls = _invoke(monkeypatch, pages, ["22", "--since", "100"])
    assert len(calls) == 3 and slept == [0.125, 0.125]


def _scan_with_failure(monkeypatch, fail_page, argv):
    calls = []

    async def fake_folders(cred, v=None):
        return FOLDERS

    async def fake_content(media_id, page=1, credential=None, **kw):
        calls.append(page)
        if page == fail_page:
            raise BiliError("risk_control", "请求被 B 站风控拦截（HTTP 412）。")
        return {"medias": [_media(page, 1000 - page)], "has_more": True}

    monkeypatch.setattr(video, "_folders", fake_folders)
    monkeypatch.setattr(favorite_list, "get_video_favorite_list_content", fake_content)
    config.save_credential(config.Credential(sessdata="s", bili_jct="j", dedeuserid="1"))
    result = CliRunner().invoke(app, ["me", "fav-videos", *argv])
    return result, json.loads(result.output), calls


def test_since_risk_control_mid_scan_keeps_what_was_read(monkeypatch):
    result, out, calls = _scan_with_failure(monkeypatch, 3, ["22", "--since", "0"])
    data = out["data"]
    assert result.exit_code == 0 and calls == [1, 2, 3]
    assert [i["fav_time"] for i in data["items"]] == [999, 998]
    assert data["truncated"] is True and data["stopped"]["code"] == "risk_control"
    assert data["next"] == "bili me fav-videos 22 --since 0 --page 3"
    assert "不要循环重试" in data["note"]


def test_since_failure_on_first_page_is_an_error(monkeypatch):
    result, out, _ = _scan_with_failure(monkeypatch, 1, ["22", "--since", "0"])
    assert result.exit_code != 0 and out["error"]["code"] == "risk_control"


def test_since_resumes_from_page(monkeypatch):
    result, out, calls = _scan_with_failure(monkeypatch, 7, ["22", "--since", "0", "--page", "5"])
    assert calls == [5, 6, 7] and out["data"]["next"] == "bili me fav-videos 22 --since 0 --page 7"


def test_since_stops_at_time_budget(monkeypatch):
    monkeypatch.setattr(me, "FAV_SCAN_BUDGET", -1)
    result, out, calls = _scan_with_failure(monkeypatch, None, ["22", "--since", "0"])
    data = out["data"]
    assert calls == [1] and data["stopped"]["code"] == "time_budget"
    assert data["next"] == "bili me fav-videos 22 --since 0 --page 2" and len(data["items"]) == 1


def test_since_item_without_fav_time_does_not_end_scan(monkeypatch):
    pages = {1: ([_media(1, 500), _media(2, None), _media(3, 400), _media(4, 100)], False)}
    data, _ = _invoke(monkeypatch, pages, ["22", "--since", "300"])
    assert [i["bvid"] for i in data["items"]] == ["BV0000000001", "BV0000000002", "BV0000000003"]
    assert data["newest_fav_time"] == 500


def test_default_folder_is_the_one_without_attr_bit_2(monkeypatch):
    import asyncio

    async def fake_list(mid, v=None, credential=None):
        return {"list": [{"id": 1, "title": "默认收藏夹", "attr": 1}, {"id": 2, "title": "MC", "attr": 23},
                         {"id": 3, "title": "新的", "attr": 7}, {"id": 4, "title": "公开默认", "attr": 0},
                         {"id": 5, "title": "没有 attr"}]}

    monkeypatch.setattr(favorite_list, "get_video_favorite_list", fake_list)
    cred = config.Credential(sessdata="s", bili_jct="j", dedeuserid="1")
    folders = asyncio.run(video._folders(cred))
    assert [f["default"] for f in folders] == [True, False, False, True, False]


# --- writes: create / copy / move / sort ---

MC_FOLDERS = [
    {"id": 11, "title": "默认收藏夹", "count": 3, "has_video": False, "default": True},
    {"id": 22, "title": "DST", "count": 1, "has_video": False, "default": False},
    {"id": 33, "title": "MC", "count": 5, "has_video": False, "default": False},
    {"id": 44, "title": "音樂", "count": 2, "has_video": False, "default": False},
    {"id": 55, "title": "MC-生电", "count": 0, "has_video": False, "default": False},
]
BV_A, AID_A = "BV1Qg5qz3EgM", 114354141795447
BV_B, AID_B = "BV1c25izZEvb", 114354326342207


def _write(monkeypatch, argv, result=None):
    """Run a write command; returns (exit code, parsed output, POSTs sent as (url, data))."""
    posts = []

    async def fake_folders(cred, v=None):
        return MC_FOLDERS

    async def fake_post(cred, url, **data):
        posts.append((url, data))
        return result

    monkeypatch.setattr(video, "_folders", fake_folders)
    monkeypatch.setattr(me, "_fav_post", fake_post)
    config.save_credential(config.Credential(sessdata="s", bili_jct="j", dedeuserid="7"))
    res = CliRunner().invoke(app, argv)
    return res.exit_code, json.loads(res.output), posts


def test_fav_copy_dry_run_sends_nothing(monkeypatch):
    code, out, posts = _write(monkeypatch, ["--dry-run", "me", "fav-copy", "MC", "MC-生电", BV_A, BV_B])
    assert code == 0 and posts == []
    assert out["data"] == {"dry_run": True, "action": "me.fav-copy", "params": {
        "from": {"id": 33, "title": "MC"}, "to": {"id": 55, "title": "MC-生电"}, "bvids": [BV_A, BV_B]}}


def test_fav_copy_sends_one_batch_with_aids(monkeypatch):
    code, out, posts = _write(monkeypatch, ["me", "fav-copy", "33", "MC-生电", BV_A, BV_B, BV_A])
    assert code == 0 and out["data"]["count"] == 2
    assert posts == [("https://api.bilibili.com/x/v3/fav/resource/copy", {
        "src_media_id": 33, "tar_media_id": 55, "resources": f"{AID_A}:2,{AID_B}:2", "platform": "web", "mid": 7})]


def test_fav_move_has_no_mid(monkeypatch):
    code, _, posts = _write(monkeypatch, ["me", "fav-move", "MC", "MC-生电", BV_A])
    assert code == 0 and posts[0][0].endswith("/resource/move") and "mid" not in posts[0][1]


@pytest.mark.parametrize("argv", [
    ["me", "fav-copy", "MC", "MC", BV_A],
    ["me", "fav-copy", "MC", "MC-生电", "av170001"],
    ["me", "fav-sort", "MC"],
    ["me", "fav-sort", "MC", "--first", "--after", "DST"],
    ["me", "fav-sort", "默认收藏夹", "--first"],
    ["me", "fav-sort", "MC", "--after", "MC"],
    ["me", "fav-sort-videos", "MC", BV_A],
    ["me", "fav-sort-videos", "MC", BV_A, "--after", BV_A],
    ["me", "fav-create", "MC"],
])
def test_bad_write_arguments(monkeypatch, argv):
    code, out, posts = _write(monkeypatch, argv)
    assert code != 0 and out["error"]["code"] == "invalid_argument" and posts == []


def test_fav_copy_batch_limit(monkeypatch):
    monkeypatch.setattr(me, "FAV_BATCH_MAX", 1)
    code, out, posts = _write(monkeypatch, ["me", "fav-copy", "MC", "MC-生电", BV_A, BV_B])
    assert code != 0 and out["error"]["code"] == "invalid_argument" and posts == []


def test_fav_create_is_private_by_default(monkeypatch):
    code, out, posts = _write(monkeypatch, ["me", "fav-create", "MC-建筑"], result={"id": 66})
    assert code == 0 and out["data"] == {"id": 66, "title": "MC-建筑", "intro": "", "private": True}
    assert posts == [("https://api.bilibili.com/x/v3/fav/folder/add", {"title": "MC-建筑", "intro": "", "privacy": 1})]


def test_folder_order_places_after_or_first():
    t = MC_FOLDERS[4]
    assert [f["id"] for f in me.folder_order(MC_FOLDERS, t, MC_FOLDERS[2])] == [11, 22, 33, 55, 44]
    assert [f["id"] for f in me.folder_order(MC_FOLDERS, t, None)] == [11, 55, 22, 33, 44]
    assert [f["id"] for f in me.folder_order(MC_FOLDERS, t, MC_FOLDERS[0])] == [11, 55, 22, 33, 44]
    # the default folder stays first even if the API listed it elsewhere
    shuffled = [MC_FOLDERS[1], MC_FOLDERS[0], *MC_FOLDERS[2:]]
    assert [f["id"] for f in me.folder_order(shuffled, t, None)][0] == 11


def test_fav_sort_sends_full_order(monkeypatch):
    code, out, posts = _write(monkeypatch, ["me", "fav-sort", "MC-生电", "--after", "MC"])
    assert code == 0 and out["data"]["changed"] is True
    assert posts == [("https://api.bilibili.com/x/v3/fav/folder/sort", {"sort": "11,22,33,55,44"})]


def test_fav_sort_noop_sends_nothing(monkeypatch):
    code, out, posts = _write(monkeypatch, ["me", "fav-sort", "DST", "--first"])
    assert code == 0 and out["data"]["changed"] is False and posts == []


def test_video_sort_steps():
    assert me.video_sort_steps([1, 2, 3], None) == "0:0:1:2,1:2:2:2,2:2:3:2"
    assert me.video_sort_steps([5], 9) == "9:2:5:2"


def test_fav_sort_videos_sends_steps(monkeypatch):
    code, _, posts = _write(monkeypatch, ["me", "fav-sort-videos", "MC", BV_B, "--after", BV_A])
    assert code == 0
    assert posts == [("https://api.bilibili.com/x/v3/fav/resource/sort", {"media_id": 33, "sort": f"{AID_A}:2:{AID_B}:2"})]


def test_pick_folder_refuses_an_id_that_is_also_another_title():
    folders = [{"id": 222, "title": "333", "default": True}, {"id": 333, "title": "MC", "default": False}]
    with pytest.raises(BiliError) as e:
        me.pick_folder(folders, "333")
    assert e.value.code == "invalid_argument"
    assert me.pick_folder(folders, "222")["id"] == 222 and me.pick_folder(folders, "MC")["id"] == 333


def test_bv_case_variants_are_one_video(monkeypatch):
    code, out, posts = _write(monkeypatch, ["me", "fav-copy", "MC", "MC-生电", "bv1Qg5qz3EgM", BV_A])
    assert code == 0 and out["data"]["bvids"] == [BV_A] and posts[0][1]["resources"] == f"{AID_A}:2"


@pytest.mark.parametrize("argv", [
    ["me", "fav-copy", "MC", "MC-生电", "BV1Qg5qz3Eg!"],
    ["me", "fav-copy", "MC", "MC-生电", "BV0000000000"],
    ["me", "fav-sort-videos", "MC", BV_A, "--after", "bv1Qg5qz3EgM"],
    ["me", "fav-create", "33"],
])
def test_more_bad_write_arguments(monkeypatch, argv):
    code, out, posts = _write(monkeypatch, argv)
    assert code != 0 and out["error"]["code"] == "invalid_argument" and posts == []


def test_fav_sort_refuses_without_a_single_default_folder(monkeypatch):
    no_default = [{**f, "default": False} for f in MC_FOLDERS]
    posts = []

    async def fake_folders(cred, v=None):
        return no_default

    async def fake_post(cred, url, **data):
        posts.append(url)

    monkeypatch.setattr(video, "_folders", fake_folders)
    monkeypatch.setattr(me, "_fav_post", fake_post)
    config.save_credential(config.Credential(sessdata="s", bili_jct="j", dedeuserid="7"))
    res = CliRunner().invoke(app, ["me", "fav-sort", "MC-生电", "--after", "MC"])
    assert res.exit_code != 0 and json.loads(res.output)["error"]["code"] == "api_error" and posts == []
