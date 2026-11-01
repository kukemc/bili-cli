import json
import threading

import pytest
from typer.testing import CliRunner

from bili_cli import cards, config, guard
from bili_cli.commands import video as video_cmd
from bili_cli.errors import BiliError
from bili_cli.main import app


def _blocker(tmp_path, name="not-a-dir"):
    path = tmp_path / name
    path.write_text("x", encoding="utf-8")
    return path


def test_state_home_follows_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BILI_CLI_STATE_DIR", str(tmp_path / "state"))
    assert config.state_home() == tmp_path / "state"


def test_state_home_defaults_to_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("BILI_CLI_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("BILI_CLI_STATE_DIR", raising=False)
    assert config.state_home() == tmp_path / "home"


def test_record_writes_into_state_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("BILI_CLI_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("BILI_CLI_STATE_DIR", str(tmp_path / "state"))
    guard.record("like", {"bvid": "BV1test"}, "ok")

    state = json.loads((tmp_path / "state" / "ratelimit.json").read_text(encoding="utf-8"))
    assert len(state["like"]) == 1
    assert (tmp_path / "state" / "audit.log").exists()
    assert not (tmp_path / "home" / "ratelimit.json").exists()


def test_record_failure_does_not_fail_the_action(monkeypatch, tmp_path, capsys):
    """The action already happened; losing the local bookkeeping must not look like a failure."""
    blocker = _blocker(tmp_path)
    monkeypatch.setattr(config, "state_home", lambda: blocker)
    monkeypatch.setattr(guard, "_state_path", lambda: blocker / "ratelimit.json")

    guard.record("comment", {"bvid": "BV1test"}, "ok")  # must not raise

    assert "警告" in capsys.readouterr().err


def test_check_refuses_when_state_dir_unwritable(monkeypatch, tmp_path):
    """Without a writable state dir every limit would silently pass, including the daily coin cap."""
    monkeypatch.setattr(config, "state_home", lambda: _blocker(tmp_path, "blocked-state"))
    with pytest.raises(BiliError) as err:
        guard.check("coin")
    assert err.value.code == "invalid_argument"
    assert "BILI_CLI_STATE_DIR" in (err.value.hint or "")
    assert err.value.detail["state_dir"]


def test_check_skips_the_probe_when_rate_limit_is_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "state_home", lambda: _blocker(tmp_path, "blocked-state-2"))
    monkeypatch.setenv("BILI_CLI_NO_RATE_LIMIT", "1")
    guard.check("coin")  # must not raise


def test_write_command_sends_nothing_when_state_is_unwritable(monkeypatch, tmp_path):
    sent = []

    class FakeVideo:
        async def like(self, status=True):
            sent.append(status)
            return {}

    monkeypatch.setattr(video_cmd, "_own_video", lambda video_id: (FakeVideo(), "BV1test"))
    monkeypatch.setattr(config, "state_home", lambda: _blocker(tmp_path, "blocked-state-3"))

    result = CliRunner().invoke(app, ["--compact", "video", "like", "BV1test"])

    assert result.exit_code != 0
    assert sent == []  # the API call never happened
    assert "BILI_CLI_STATE_DIR" in result.output


def test_mark_seen_failure_does_not_raise(monkeypatch, tmp_path, capsys):
    blocker = _blocker(tmp_path, "not-a-dir-2")
    monkeypatch.setattr(cards, "_seen_path", lambda: blocker / "seen.json")

    cards.mark_seen("BV1test")  # must not raise

    assert "警告" in capsys.readouterr().err

def test_ensure_state_writable_survives_concurrency(monkeypatch, tmp_path):
    """A fixed probe filename made concurrent writers fail with WinError 32; a unique temp file must not."""
    monkeypatch.setenv("BILI_CLI_STATE_DIR", str(tmp_path / "state"))
    errors: list[Exception] = []

    def worker():
        for _ in range(50):
            try:
                guard._ensure_state_writable()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert list((tmp_path / "state").iterdir()) == []
