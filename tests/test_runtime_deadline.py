import asyncio
import json
import time

import pytest
import typer

from bili_cli import output, runtime


@pytest.fixture
def printed(monkeypatch):
    lines = []
    monkeypatch.setattr(output, "_write", lines.append)
    return lines


def test_a_stuck_command_becomes_a_network_error(monkeypatch, printed):
    monkeypatch.setenv("BILI_CLI_TIMEOUT", "0.3")

    async def body():
        await asyncio.sleep(10)

    t0 = time.monotonic()
    with pytest.raises(typer.Exit) as e:
        runtime.run(body)
    assert time.monotonic() - t0 < 5
    err = json.loads(printed[0])["error"]
    # 保留原始秒数：`int()` 曾把 0.3 抹成 0，而 hint 里「0 表示不限」会让它读起来自相矛盾。
    assert e.value.exit_code == 8 and err["code"] == "network_error"
    assert err["detail"]["timeout"] == pytest.approx(0.3)
    assert "0.3 秒" in err["message"]


def test_long_jobs_can_opt_out(monkeypatch, printed):
    monkeypatch.setenv("BILI_CLI_TIMEOUT", "0.1")

    async def body():
        await asyncio.sleep(0.4)
        return {"done": True}

    runtime.run(body, deadline=None)
    assert json.loads(printed[0]) == {"ok": True, "data": {"done": True}}


def test_default_deadline_reads_the_environment(monkeypatch):
    monkeypatch.delenv("BILI_CLI_TIMEOUT", raising=False)
    assert runtime.default_deadline() == runtime.DEFAULT_DEADLINE
    monkeypatch.setenv("BILI_CLI_TIMEOUT", "0")
    assert runtime.default_deadline() is None
    monkeypatch.setenv("BILI_CLI_TIMEOUT", "abc")
    assert runtime.default_deadline() == runtime.DEFAULT_DEADLINE


def test_watchdog_fires_when_the_event_loop_itself_is_blocked(monkeypatch, printed):
    fired = []
    monkeypatch.setattr(runtime, "HARD_EXIT_GRACE", 0.1)
    monkeypatch.setattr(runtime, "_hard_exit", lambda limit: fired.append(limit))

    async def body():
        time.sleep(0.8)  # blocks the loop: asyncio cannot time this out, only the thread can
        return {"ok": 1}

    runtime.run(body, deadline=0.2)
    assert fired == [0.2]
