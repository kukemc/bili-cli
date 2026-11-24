"""`_choose_line_guarded`: bilibili-api's silent None becomes an actionable error."""

import asyncio

import pytest

from bili_cli.commands import upload
from bili_cli.errors import BiliError


def test_probe_failure_raises_network_error(monkeypatch):
    """四条线路全部失败时 _probe() 返回 None，不能让它变成 NoneType 的 TypeError。"""

    async def fake_choose_line(line):
        return None

    monkeypatch.setattr(upload.vu, "_choose_line", fake_choose_line)
    with pytest.raises(BiliError) as err:
        asyncio.run(upload._choose_line_guarded(None))
    assert err.value.code == "network_error"
    assert "--line" in (err.value.hint or "")
    assert err.value.detail["step"] == "upload.choose_line"
    assert set(err.value.detail["lines"]) == {"bda2", "qn", "ws", "bldsa"}


def test_resolved_line_passes_through(monkeypatch):
    """正常测速时原样返回那条线路的信息。"""
    info = {"os": "upos", "upcdn": "upcdnbda2", "probe_version": "20221109"}

    async def fake_choose_line(line):
        return info

    monkeypatch.setattr(upload.vu, "_choose_line", fake_choose_line)
    assert asyncio.run(upload._choose_line_guarded(None)) is info


def test_explicit_line_skips_probe(monkeypatch):
    """传了 --line 时不应该再测速（bilibili-api 会直接从 LINES_INFO 取）。"""
    calls = []

    async def fake_choose_line(line):
        calls.append(line)
        return upload.vu.LINES_INFO["bda2"]

    monkeypatch.setattr(upload.vu, "_choose_line", fake_choose_line)
    got = asyncio.run(upload._choose_line_guarded(upload.vu.Lines("bda2")))
    assert got is upload.vu.LINES_INFO["bda2"]
    assert calls == [upload.vu.Lines("bda2")]
