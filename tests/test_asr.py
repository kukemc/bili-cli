import asyncio
import json
import shutil
import subprocess

import httpx
import pytest

from bili_cli.asr import engines, settings
from bili_cli.asr.segment import MAX_SEGMENT, Segment, _cut, silence_segments
from bili_cli.errors import BiliError

SECOND = b"\x01\x00" * 16000


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BILI_CLI_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def mock_http(monkeypatch):
    """Route every httpx.AsyncClient through a handler and record requests."""
    seen = []

    def install(handler):
        real = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(lambda req: (seen.append(req), handler(req))[1])
            return real(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", factory)
        return seen

    return install


def test_cut_splits_long_spans_and_drops_blips():
    pcm = SECOND * 50
    segs = _cut(pcm, [(0.0, 45.0), (46.0, 46.1)])
    assert len(segs) == 3 and all(s.end - s.start <= MAX_SEGMENT for s in segs)
    assert segs[0].start == 0 and segs[-1].end == 45.0
    assert len(segs[0].pcm) == int(15 * 16000) * 2


def test_segment_wav_bytes_roundtrip():
    import io
    import wave

    w = wave.open(io.BytesIO(Segment(0, 1, SECOND).wav_bytes()))
    assert (w.getnchannels(), w.getframerate(), w.getnframes()) == (1, 16000, 16000)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_silence_segments_finds_speech_between_silences(tmp_path):
    wav = tmp_path / "t.wav"
    # 2 s tone, 2 s silence, 2 s tone
    subprocess.run([
        "ffmpeg", "-nostdin", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=f=440:d=2", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=2", "-f", "lavfi", "-i", "sine=f=440:d=2",
        "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1", "-ac", "1", "-ar", "16000", str(wav),
    ], check=True)
    segs = silence_segments(wav)
    assert len(segs) == 2
    assert segs[0].start == pytest.approx(0, abs=0.3) and segs[1].start == pytest.approx(4, abs=0.5)


def test_openai_compatible_engine(mock_http):
    seen = mock_http(lambda req: httpx.Response(200, json={"text": " 你好 "}))
    eng = engines.OpenAICompatEngine("https://api.example.com/v1/", "sk-test", "whisper-large-v3")
    out = asyncio.run(eng.transcribe([Segment(0, 1, SECOND)] * 3, context="标题"))
    assert out == ["你好"] * 3
    req = seen[0]
    assert str(req.url) == "https://api.example.com/v1/audio/transcriptions"
    assert req.headers["authorization"] == "Bearer sk-test"
    body = req.content.decode(errors="ignore")
    assert "whisper-large-v3" in body and "标题" in body and "RIFF" in body


def test_aliyun_engine_sends_base64_audio(mock_http):
    seen = mock_http(lambda req: httpx.Response(200, json={"choices": [{"message": {"content": "识别结果"}}]}))
    out = asyncio.run(engines.AliyunEngine("key").transcribe([Segment(0, 1, SECOND)]))
    assert out == ["识别结果"]
    body = json.loads(seen[0].content)
    assert body["model"] == "qwen3-asr-flash"
    assert body["messages"][-1]["content"][0]["input_audio"]["data"].startswith("data:audio/wav;base64,UklGR")


def test_tencent_engine_signs_and_parses(mock_http):
    seen = mock_http(lambda req: httpx.Response(200, json={"Response": {"Result": "腾讯结果", "RequestId": "x"}}))
    out = asyncio.run(engines.TencentEngine("AKIDtest", "secret").transcribe([Segment(0, 1, SECOND)]))
    assert out == ["腾讯结果"]
    req = seen[0]
    auth = req.headers["authorization"]
    assert auth.startswith("TC3-HMAC-SHA256 Credential=AKIDtest/") and "/asr/tc3_request" in auth
    assert req.headers["x-tc-action"] == "SentenceRecognition"
    payload = json.loads(req.content)
    assert payload["EngSerViceType"] == "16k_zh" and payload["DataLen"] == len(Segment(0, 1, SECOND).wav_bytes())


def test_tencent_error_is_reported(mock_http):
    mock_http(lambda req: httpx.Response(200, json={"Response": {"Error": {"Code": "AuthFailure", "Message": "bad"}}}))
    with pytest.raises(BiliError) as e:
        asyncio.run(engines.TencentEngine("a", "b").transcribe([Segment(0, 1, SECOND)]))
    assert "AuthFailure" in e.value.message


def test_cloud_retries_on_429(mock_http, monkeypatch):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(429) if calls["n"] == 1 else httpx.Response(200, json={"text": "ok"})

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(engines.asyncio, "sleep", no_wait)
    mock_http(handler)
    assert asyncio.run(engines.OpenAICompatEngine("https://x/v1", "k", "m").transcribe([Segment(0, 1, SECOND)])) == ["ok"]
    assert calls["n"] == 2


def test_build_selects_engine_and_explains_missing():
    with pytest.raises(BiliError) as e:
        engines.build("auto")
    assert "bili asr setup" in e.value.hint
    settings.save({"cloud": {"provider": "aliyun", "api_key": "k"}})
    assert isinstance(engines.build("auto"), engines.AliyunEngine)
    with pytest.raises(BiliError):
        engines.build("local")


def test_masked_hides_secrets():
    m = settings.masked({"cloud": {"api_key": "sk-1234567890", "secret_key": "abcdefgh", "model": "m"}})
    assert m["cloud"] == {"api_key": "***7890", "secret_key": "***efgh", "model": "m"}
