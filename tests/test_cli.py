import json
import os
import subprocess
import sys

import pytest
from bilibili_api import exceptions as bex

from bili_cli.errors import BiliError, from_exception
from bili_cli.main import _hoist_global_flags
from bili_cli.runtime import iso_time, parse_video_id, read_text


@pytest.mark.parametrize(
    "value, expected",
    [
        ("BV1GJ411x7h7", {"bvid": "BV1GJ411x7h7"}),
        ("https://www.bilibili.com/video/BV1GJ411x7h7/?p=1", {"bvid": "BV1GJ411x7h7"}),
        ("av80433022", {"aid": 80433022}),
        ("AV80433022", {"aid": 80433022}),
        ("80433022", {"aid": 80433022}),
        ("https://www.bilibili.com/video/av80433022", {"aid": 80433022}),
    ],
)
def test_parse_video_id(value, expected):
    assert parse_video_id(value) == expected


def test_parse_video_id_invalid():
    with pytest.raises(BiliError) as e:
        parse_video_id("hello")
    assert e.value.code == "invalid_argument"


def test_hoist_global_flags():
    argv = ["video", "info", "BV1GJ411x7h7", "--compact", "--raw"]
    assert _hoist_global_flags(argv) == ["--compact", "--raw", "video", "info", "BV1GJ411x7h7"]
    # everything after `--` is left alone, e.g. a comment that is literally "--raw"
    argv = ["comment", "post", "BV1", "--", "--raw"]
    assert _hoist_global_flags(argv) == argv


@pytest.mark.parametrize(
    "bili_code, code, exit_code",
    [
        (-101, "not_logged_in", 3),
        (-404, "not_found", 4),
        (-352, "risk_control", 5),
        (12015, "captcha_required", 6),
        (-509, "rate_limited", 7),
        (99999, "api_error", 1),
    ],
)
def test_response_code_mapping(bili_code, code, exit_code):
    err = from_exception(bex.ResponseCodeException(bili_code, "msg", {}))
    assert err.code == code
    assert err.exit_code == exit_code
    assert err.detail == {"bili_code": bili_code}


def test_http_412_is_risk_control():
    assert from_exception(bex.NetworkException(412, "Precondition Failed")).code == "risk_control"


def test_unknown_exception_is_internal():
    err = from_exception(ValueError("boom"))
    assert err.code == "internal_error" and err.exit_code == 1


def test_iso_time_out_of_range_returns_none():
    # a malformed API timestamp must not blow up display formatting
    assert iso_time("9" * 30) is None
    assert iso_time("-" + "9" * 30) is None
    assert iso_time("1700000000") is not None
    assert iso_time(0) is None


def test_read_text(tmp_path):
    assert read_text("  hi  ", None) == "hi"
    f = tmp_path / "msg.txt"
    f.write_text("你好\n", encoding="utf-8")
    assert read_text(None, f) == "你好"
    for args in [(None, None), ("a", f), ("   ", None)]:
        with pytest.raises(BiliError):
            read_text(*args)


def _bili(*args, home):
    proc = subprocess.run(
        [sys.executable, "-m", "bili_cli.main", *args],
        capture_output=True,
        env={**__import__("os").environ, "BILI_CLI_HOME": str(home)},
    )
    return proc.returncode, json.loads(proc.stdout.decode("utf-8"))


def test_write_command_requires_login(tmp_path):
    rc, out = _bili("comment", "post", "BV1GJ411x7h7", "hello", home=tmp_path)
    assert rc == 3
    assert out["ok"] is False and out["error"]["code"] == "not_logged_in"
    assert "bili auth login" in out["error"]["hint"]


def test_usage_error_is_json(tmp_path):
    rc, out = _bili("video", "nope", home=tmp_path)
    assert rc == 2 and out["error"]["code"] == "invalid_argument"


def test_schema_lists_commands(tmp_path):
    rc, out = _bili("schema", home=tmp_path)
    assert rc == 0 and out["ok"]
    commands = {c["command"] for c in out["data"]["commands"]}
    assert {"bili auth login", "bili video subtitle", "bili comment reply"} <= commands
    assert any(e["code"] == "risk_control" for e in out["data"]["errors"])


def test_upload_rejects_missing_cover43(tmp_path):
    # validation happens before any network call, so a dummy credential is enough
    (tmp_path / "credential.json").write_text(json.dumps({"sessdata": "x", "bili_jct": "y"}), encoding="utf-8")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0")
    env = {**os.environ, "BILI_CLI_HOME": str(tmp_path)}
    proc = subprocess.run(
        [sys.executable, "-m", "bili_cli.main", "upload", "video", str(video), "--title", "t", "--tag", "a",
         "--cover43", str(tmp_path / "missing.png")],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    out = json.loads(proc.stdout)
    assert proc.returncode != 0
    assert out["error"]["code"] == "invalid_argument"
    assert "4:3" in out["error"]["message"]
