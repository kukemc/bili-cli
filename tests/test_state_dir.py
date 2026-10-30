import json

from bili_cli import cards, config, guard


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
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(config, "state_home", lambda: blocker)
    monkeypatch.setattr(guard, "_state_path", lambda: blocker / "ratelimit.json")

    guard.record("comment", {"bvid": "BV1test"}, "ok")  # must not raise

    assert "警告" in capsys.readouterr().err


def test_mark_seen_failure_does_not_raise(monkeypatch, tmp_path, capsys):
    blocker = tmp_path / "not-a-dir-2"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(cards, "_seen_path", lambda: blocker / "seen.json")

    cards.mark_seen("BV1test")  # must not raise

    assert "警告" in capsys.readouterr().err
