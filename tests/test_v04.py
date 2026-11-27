import asyncio

import pytest

from bili_cli import frames, guard, output
from bili_cli.commands import comment as comment_cmd
from bili_cli.commands import me as me_cmd
from bili_cli.commands import upload as upload_cmd
from bili_cli.commands import user as user_cmd
from bili_cli.errors import BiliError
from bili_cli.runtime import parse_seconds, parse_timestamp


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BILI_CLI_HOME", str(tmp_path))
    monkeypatch.delenv("BILI_CLI_NO_RATE_LIMIT", raising=False)
    monkeypatch.setattr(output.settings, "wait", False)
    return tmp_path


# ---- user ----

def test_resolve_mid_accepts_uid_and_exact_nickname(monkeypatch):
    async def fake_search(keyword, search_type):
        return {"result": [{"uname": "<em class=\"keyword\">冬冰</em>粉丝", "mid": 1}, {"uname": "<em class=\"keyword\">冬冰</em>", "mid": 2}]}

    monkeypatch.setattr(user_cmd.search, "search_by_type", fake_search)
    assert asyncio.run(user_cmd.resolve_mid("94782782")) == 94782782
    assert asyncio.run(user_cmd.resolve_mid("@冬冰")) == 2
    with pytest.raises(BiliError) as e:
        asyncio.run(user_cmd.resolve_mid("不存在的人"))
    assert e.value.code == "not_found"


# ---- stats ----

def test_summarize_stats_converts_basis_points_and_hides_empty_interactions():
    out = me_cmd.summarize_stats(
        overview={"play": 10, "play_last": 8, "like": 0, "fav": 0, "log_date": 20261019},
        fans={"all_fans": 5595, "follow": 10, "unfollow": 4, "interact_fans_per": 933},
        source={"play_proportion": {"new_mobile": 7225, "new_pc": 2419, "pc": 0}, "audience_proportion": {"fans": 25, "guest": 75}},
        compare={"play_pass_peer": 9780},
        survey={"20261019": {"arc_inc": [{"bvid": "BV1", "title": "t", "incr": 3}]}},
    )
    assert out["plays"] == {"now": 10, "previous": 8}
    assert "interactions" not in out
    assert out["fans"]["interacting_percent"] == 9.33
    assert out["play_platforms_percent"] == {"mobile": 72.25, "pc": 24.19}
    assert out["plays_from_fans_percent"] == 25.0
    assert out["beats_similar_ups_percent"]["play"] == 97.8
    assert out["top_videos_yesterday"] == [{"bvid": "BV1", "title": "t", "plays": 3}]


def test_video_rates_per_100_plays():
    rates = upload_cmd.video_rates({"view": 200, "like": 10, "favorite": 16, "coin": 0, "reply": 7})
    assert rates["like"] == 5.0 and rates["favorite"] == 8.0 and rates["reply"] == 3.5
    assert upload_cmd.video_rates({"view": 0})["like"] is None


# ---- upload edit ----

VIEW = {
    "archive": {
        "aid": 1, "title": "old", "copyright": 1, "tid": 231, "tag": "a,b", "desc": "d", "desc_format_id": 9999,
        "recreate": {"switch": -1}, "dynamic": "dyn", "no_reprint": 1, "is_only_self": 1,
        "creation_statement": {"id": 1}, "cover": "http://c", "cover43": "http://c43", "human_type2": {"id": 1011},
        "dtime": 0, "state": 0,
    },
    "videos": [{"filename": "f1", "title": "p1", "desc": "", "cid": 9}],
    "reply": {"state": 0, "up_selection": True},
    "subtitle": {"allow": False, "lan": ""},
    "no_disturbance": 0,
}


def test_build_edit_keeps_everything_not_changed():
    body = upload_cmd.build_edit(VIEW, {"title": "new", "tags": ["x", "y"]})
    assert body["title"] == "new" and body["tag"] == "x,y"
    # settings the edit endpoint would otherwise reset
    assert body["is_only_self"] == 1 and body["creation_statement"] == {"id": 1} and body["human_type2"] == 1011
    assert body["cover"] == "http://c" and body["cover43"] == "http://c43" and body["no_reprint"] == 1
    assert body["up_selection_reply"] is True and body["dynamic"] == "dyn"
    assert body["videos"] == [{"filename": "f1", "title": "p1", "desc": "", "cid": 9}]
    assert "dtime" not in body


def test_build_edit_can_drop_ai_declaration_and_keeps_pending_schedule():
    view = {**VIEW, "archive": {**VIEW["archive"], "dtime": 1800000000, "state": -40}}
    body = upload_cmd.build_edit(view, {"ai_declare": False, "cover": "http://new"})
    assert body["creation_statement"] == {"id": upload_cmd.STATEMENT_NONE}
    assert body["cover"] == "http://new" and body["dtime"] == 1800000000


# ---- comments ----

def test_needs_reply_skips_answered_and_own_comments():
    rows = [
        {"rpid": 1, "mid": 5, "up_action": {"reply": False}},
        {"rpid": 2, "mid": 6, "up_action": {"reply": True}},
        {"rpid": 3, "mid": 99, "up_action": {"reply": False}},  # the uploader's own comment
        {"rpid": 4, "mid": 7},
    ]
    assert [r["rpid"] for r in comment_cmd.needs_reply(rows, 99)] == [1, 4]


# ---- --wait ----

def test_wait_sleeps_through_a_short_limit(monkeypatch):
    slept = []
    monkeypatch.setattr(guard.time, "sleep", lambda s: slept.append(s) or monkeypatch.setenv("BILI_CLI_NO_RATE_LIMIT", "1"))
    guard.record("comment", {}, None)
    with pytest.raises(BiliError):
        guard.check("comment")
    monkeypatch.setattr(output.settings, "wait", True)
    guard.check("comment")
    assert slept and 0 < slept[0] <= guard.LIMITS["comment"][0] + 1


def test_wait_never_waits_out_hourly_or_daily_caps(monkeypatch):
    monkeypatch.setattr(output.settings, "wait", True)
    monkeypatch.setattr(guard.time, "sleep", lambda s: pytest.fail("should not sleep"))
    monkeypatch.setitem(guard.LIMITS, "dynamic", (0, 1))
    guard.record("dynamic", {}, None)
    with pytest.raises(BiliError):  # ~1 hour away
        guard.check("dynamic")
    for _ in range(guard.DAILY_LIMITS["coin"]):
        guard.record("coin", {}, None)
    with pytest.raises(BiliError):
        guard.check("coin")


# ---- fractional timestamps ----

def test_parse_seconds_allows_fractions_but_chapters_stay_whole():
    assert parse_seconds("1:30.5") == 90.5 and parse_seconds("27.25") == 27.25 and parse_seconds("0:01:30") == 90
    assert parse_timestamp("1:30") == 90
    with pytest.raises(BiliError):
        parse_timestamp("1:30.5")
    with pytest.raises(BiliError):
        parse_seconds("1:nan")


def test_sub_second_frames_get_their_own_file_and_label():
    assert frames.frame_name(90) == "frame_000090.jpg"
    assert frames.frame_name(90.5) == "frame_000090_500.jpg"
    assert frames.fmt_time(90.5) == "1:30.5" and frames.fmt_time(3725) == "1:02:05"
    assert frames.plan_times(100, at=[27.0, 27.5]) == [27.0, 27.5]


def test_edit_refuses_what_build_edit_cannot_preserve():
    assert upload_cmd.edit_blocker(VIEW) is None
    assert "评论区" in upload_cmd.edit_blocker({**VIEW, "reply": {"state": 1}})
    web_upload = {**VIEW, "archive": {**VIEW["archive"], "copyright": 3}, "staffs": []}
    assert upload_cmd.edit_blocker(web_upload) is None  # copyright 3 alone is fine: it is sent back as-is
    assert upload_cmd.edit_blocker({**VIEW, "staffs": [{"apply_staff_mid": 2}]}) is None  # members are now sent back


def test_edit_keeps_staff_and_event_and_replaces_video():
    view = {**VIEW, "archive": {**VIEW["archive"], "mission_id": 4067875, "topic_id": 1348290},
            "staffs": [{"apply_staff_mid": 94782782, "apply_title": "策划", "staff_title": "策划", "apply_state": 1}]}
    body = upload_cmd.build_edit(view, {})
    assert body["staffs"] == [{"title": "策划", "mid": 94782782}] and body["handle_staff"] is False
    assert body["mission_id"] == 4067875 and body["topic_id"] == 1348290 and body["new_web_edit"] == 1
    new = [{"filename": "n123", "cid": 9, "title": "t", "desc": ""}]
    assert upload_cmd.build_edit(view, {"videos": new})["videos"] == new
    assert "staffs" not in upload_cmd.build_edit(VIEW, {})


# ---- hide in space (“在个人空间-投稿中隐藏”) ----

def test_build_edit_keeps_space_hidden_and_can_flip_it():
    # archive/view (real response, 2026-11-27): top-level space_hidden 1/2, archive.is_space_hidden 1/0
    hidden = {**VIEW, "space_hidden": upload_cmd.SPACE_HIDDEN, "archive": {**VIEW["archive"], "is_space_hidden": 1}}
    assert upload_cmd.is_space_hidden(hidden) is True
    assert upload_cmd.is_space_hidden({**VIEW, "space_hidden": upload_cmd.SPACE_SHOWN}) is False
    assert upload_cmd.is_space_hidden({**VIEW, "archive": {**VIEW["archive"], "is_space_hidden": 1}}) is True
    assert upload_cmd.build_edit(hidden, {"title": "new"})["space_hidden"] == upload_cmd.SPACE_HIDDEN
    assert upload_cmd.build_edit(hidden, {"hide_in_space": False})["space_hidden"] == upload_cmd.SPACE_SHOWN
    assert upload_cmd.build_edit(VIEW, {"hide_in_space": True})["space_hidden"] == upload_cmd.SPACE_HIDDEN
    # a view without the field: don't invent one unless asked
    assert "space_hidden" not in upload_cmd.build_edit(VIEW, {"title": "new"})
