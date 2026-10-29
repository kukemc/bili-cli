import json

from typer.testing import CliRunner

from bili_cli import guard
from bili_cli.commands import video as video_cmd
from bili_cli.main import app


class FakeVideo:
    """Stands in for bilibili_api.video.Video without touching the network or real coins."""

    def __init__(self, paid_like: bool = False, already_liked: bool = False):
        self.paid_like = paid_like
        self.liked = already_liked
        self.like_calls = 0
        self.coins = 0

    async def pay_coin(self, num: int = 1, like: bool = False):
        self.coins = num
        return {"like": self.paid_like}

    async def like(self, status: bool = True):
        self.like_calls += 1
        self.liked = bool(status)
        return {}

    async def get_relation(self):
        return {"like": self.liked, "coin": self.coins, "favorite": False, "attention": False}


def _run_coin(monkeypatch, fake: FakeVideo) -> dict:
    monkeypatch.setattr(video_cmd, "_own_video", lambda video_id: (fake, "BV1test"))
    monkeypatch.setattr(guard, "check", lambda *a, **k: None)
    monkeypatch.setattr(guard, "record", lambda *a, **k: None)
    result = CliRunner().invoke(app, ["--compact", "video", "coin", "BV1test", "--coins", "1", "--like"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["data"]


def test_coin_like_falls_back_when_library_ignores_the_flag(monkeypatch):
    """bilibili-api 17.4.2 sends `like` instead of `select_like`: the coin works, the like does not."""
    fake = FakeVideo(paid_like=False)
    data = _run_coin(monkeypatch, fake)
    assert fake.coins == 1
    assert fake.like_calls == 1
    assert data["liked"] is True


def test_coin_like_does_not_repeat_when_library_reports_success(monkeypatch):
    fake = FakeVideo(paid_like=True)
    _run_coin(monkeypatch, fake)
    assert fake.like_calls == 0


def test_coin_like_skips_when_already_liked(monkeypatch):
    fake = FakeVideo(paid_like=False, already_liked=True)
    data = _run_coin(monkeypatch, fake)
    assert fake.like_calls == 0
    assert data["liked"] is True


def test_coin_without_like_never_calls_like(monkeypatch):
    fake = FakeVideo(paid_like=False)
    monkeypatch.setattr(video_cmd, "_own_video", lambda video_id: (fake, "BV1test"))
    monkeypatch.setattr(guard, "check", lambda *a, **k: None)
    monkeypatch.setattr(guard, "record", lambda *a, **k: None)
    result = CliRunner().invoke(app, ["--compact", "video", "coin", "BV1test", "--coins", "1"])
    assert result.exit_code == 0, result.output
    assert fake.like_calls == 0
