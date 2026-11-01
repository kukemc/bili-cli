import json

import pytest
from bilibili_api import exceptions as bex
from typer.testing import CliRunner

from bili_cli import guard
from bili_cli.commands import video as video_cmd
from bili_cli.errors import BiliError
from bili_cli.main import app


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BILI_CLI_HOME", str(tmp_path))
    return tmp_path


class FakeVideo:
    """Stands in for bilibili_api.video.Video without touching the network or real coins."""

    def __init__(
        self,
        paid_like: bool = False,
        already_liked: bool = False,
        like_raises: Exception | None = None,
        relation_raises_after_coin: bool = False,
    ):
        self.paid_like = paid_like
        self.liked = already_liked
        self.like_raises = like_raises
        self.relation_raises_after_coin = relation_raises_after_coin
        self.like_calls = 0
        self.coins = 0

    async def pay_coin(self, num: int = 1, like: bool = False):
        self.coins = num
        return {"like": self.paid_like}

    async def like(self, status: bool = True):
        self.like_calls += 1
        if self.like_raises is not None:
            raise self.like_raises
        self.liked = bool(status)
        return {}

    async def get_relation(self):
        if self.relation_raises_after_coin and self.coins:
            raise RuntimeError("relation timeout")
        return {"like": self.liked, "coin": self.coins, "favorite": False, "attention": False}


def _run_coin(monkeypatch, fake: FakeVideo, like_limited: bool = False, with_like: bool = True):
    calls: list[tuple[str, str]] = []

    def fake_check(action, units=1):
        calls.append(("check", action))
        if action == "video_like" and like_limited:
            raise BiliError("rate_limited", "video_like 操作间隔至少 5 秒。", detail={"retry_after": 5})

    def fake_record(action, params, result, units=1):
        calls.append(("record", action))

    monkeypatch.setattr(video_cmd, "_own_video", lambda video_id: (fake, "BV1test"))
    monkeypatch.setattr(guard, "check", fake_check)
    monkeypatch.setattr(guard, "record", fake_record)
    argv = ["--compact", "video", "coin", "BV1test", "--coins", "1"] + (["--like"] if with_like else [])
    result = CliRunner().invoke(app, argv)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["data"], calls


def test_coin_like_falls_back_when_library_ignores_the_flag(monkeypatch):
    """bilibili-api 17.4.2 sends `like` instead of `select_like`: the coin works, the like does not."""
    fake = FakeVideo(paid_like=False)
    data, calls = _run_coin(monkeypatch, fake)
    assert fake.coins == 1
    assert fake.like_calls == 1
    assert data["liked"] is True
    assert "like_error" not in data
    assert calls == [("check", "coin"), ("record", "coin"), ("check", "video_like"), ("record", "video_like")]


def test_coin_is_recorded_before_the_like(monkeypatch):
    """If the like blows up, the coin must already be in the rate-limit ledger (otherwise a retry pays twice)."""
    fake = FakeVideo(paid_like=False, like_raises=RuntimeError("network down"))
    data, calls = _run_coin(monkeypatch, fake)
    assert calls.index(("record", "coin")) < calls.index(("check", "video_like"))
    assert ("record", "video_like") not in calls
    assert data["like_error"]["code"] == "internal_error"
    assert "network down" in data["like_error"]["message"]


def test_like_error_keeps_the_bili_error_code(monkeypatch):
    """A -352 risk-control rejection must surface as risk_control so the agent stops."""
    fake = FakeVideo(paid_like=False, like_raises=bex.ResponseCodeException(-352, "风控拦截", {}))
    data, _ = _run_coin(monkeypatch, fake)
    assert data["like_error"]["code"] == "risk_control"


def test_coin_still_succeeds_when_video_like_is_rate_limited(monkeypatch):
    fake = FakeVideo(paid_like=False)
    data, calls = _run_coin(monkeypatch, fake, like_limited=True)
    assert fake.like_calls == 0
    assert data["like_error"]["code"] == "rate_limited"
    assert ("record", "video_like") not in calls
    assert ("record", "coin") in calls


def test_coin_succeeds_when_reading_the_relation_fails(monkeypatch):
    """The coin is already spent and recorded; a failed relation read must not look like a failed coin."""
    fake = FakeVideo(paid_like=False, relation_raises_after_coin=True)
    data, calls = _run_coin(monkeypatch, fake, with_like=False)
    assert ("record", "coin") in calls
    assert data["relation_error"]["code"] == "internal_error"
    assert "relation timeout" in data["relation_error"]["message"]


def test_coin_like_does_not_repeat_when_library_reports_success(monkeypatch):
    fake = FakeVideo(paid_like=True)
    data, _ = _run_coin(monkeypatch, fake)
    assert fake.like_calls == 0
    assert "like_error" not in data


def test_coin_like_skips_when_already_liked(monkeypatch):
    fake = FakeVideo(paid_like=False, already_liked=True)
    data, _ = _run_coin(monkeypatch, fake)
    assert fake.like_calls == 0
    assert data["liked"] is True


def test_coin_without_like_never_calls_like(monkeypatch):
    fake = FakeVideo(paid_like=False)
    data, calls = _run_coin(monkeypatch, fake, with_like=False)
    assert fake.like_calls == 0
    assert "like_error" not in data
    assert ("check", "video_like") not in calls
