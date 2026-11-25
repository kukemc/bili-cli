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


def test_cache_path_tolerates_slashes_in_engine_id(tmp_path, monkeypatch):
    """Cloud model ids are namespaced ("Qwen/Qwen3-ASR-1.7B"); the cache path must stay flat.

    Regression: the id was interpolated straight into the filename, so the path
    silently gained a directory component and the first transcribe of every video
    died with FileNotFoundError.
    """
    from bili_cli.asr import pipeline

    path = pipeline._cache_path("BV1xax363E5y_p1", "cloud-openai-Qwen/Qwen3-ASR-1.7B")

    assert path.parent == tmp_path / "transcripts"
    assert "/" not in path.name
    assert path.name.endswith(".json")

    # The same engine must map to a stable name, and the file must be writable
    # without any pre-created subdirectory.
    again = pipeline._cache_path("BV1xax363E5y_p1", "cloud-openai-Qwen/Qwen3-ASR-1.7B")
    assert again == path
    path.write_text("{}", encoding="utf-8")
    assert path.exists()


def test_safe_name_handles_other_separators_and_spaces():
    from bili_cli.asr.pipeline import _safe_name

    assert _safe_name("Qwen/Qwen3-ASR-1.7B") == "Qwen_Qwen3-ASR-1.7B"
    assert _safe_name("openai/whisper-large-v3") == "openai_whisper-large-v3"
    assert "\\" not in _safe_name("a\\b")
    assert " " not in _safe_name("has space")


# ---------------------------------------------------------------- oruk-fourier-2b


def test_fourier_bundle_manifest_pins_revision_and_places_root_config_last():
    from bili_cli.asr import setup as asr_setup

    assert asr_setup.FOURIER_MODEL in asr_setup.LOCAL_MODELS
    bundle = asr_setup.HF_SHERPA_BUNDLES[asr_setup.FOURIER_MODEL]
    assert bundle["repo"] == "oruk/oruk-fourier-2b"
    assert bundle["revision"] == "3fda023e157ce7986f137f76fcf326ed6e08fa73"
    assert bundle["language"] == "en"
    assert bundle["license"] == "CC BY-NC 4.0"

    files = bundle["files"]
    assert len(files) == 11
    # Repo-root config.json must be downloaded LAST as hf_config.json (no collision with tokenizer/config.json).
    assert files[-1][0] == "config.json"
    assert files[-1][1] == "hf_config.json"
    local_paths = [local_rel for _, local_rel, _, _ in files]
    assert len(set(local_paths)) == len(local_paths)
    assert "tokenizer/config.json" in local_paths
    assert {"conv_frontend.onnx", "encoder.int8.onnx", "encoder.int8.onnx.data", "decoder.int8.onnx", "decoder.int8.onnx.data"} <= set(local_paths)
    assert sum(size for _, _, size, _ in files) == 2409911288
    for _, _, size, sha in files:
        assert size > 0
        assert len(sha) == 64 and all(ch in "0123456789abcdef" for ch in sha)


def test_fourier_plan_uses_cpu_even_with_gpu_and_rejects_explicit_cuda(monkeypatch):
    from bili_cli.asr import setup as asr_setup

    monkeypatch.setattr(asr_setup, "detect_gpu", lambda: {"name": "RTX 4090", "vram_mb": 24576})

    p = asr_setup.plan("auto", "oruk-fourier-2b")
    assert p["device"] == "cpu"
    assert p["model"] == "oruk-fourier-2b"
    assert p["language"] == "en"
    assert "CC BY-NC 4.0" in p["license"]
    assert p["packages"] == ["sherpa-onnx", "numpy"]
    assert any("oruk/oruk-fourier-2b@3fda023e" in item and "CC BY-NC 4.0" in item for item in p["downloads"])

    with pytest.raises(BiliError) as exc:
        asr_setup.plan("cuda", "oruk-fourier-2b")
    assert exc.value.code == "invalid_argument"
    assert "qwen3-asr" in exc.value.message

    # Default qwen3-asr plan stays unchanged (auto selects cuda on >= 4 GB GPU).
    q = asr_setup.plan("auto", "qwen3-asr")
    assert q["device"] == "cuda" and q["model"] == "qwen3-asr"


def test_fetch_hf_bundle_downloads_in_order_verifies_sha256_and_resumes(monkeypatch):
    import hashlib

    from bili_cli.asr import setup as asr_setup

    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    payloads = {
        "onnx/sherpa-int8/conv_frontend.onnx": b"conv-bytes",
        "onnx/sherpa-int8/tokenizer/config.json": b'{"tokenizer": true}',
        "config.json": b'{"model_type": "qwen3_asr"}',
    }
    bundle = {
        "dir_name": "test-fourier",
        "repo": "oruk/oruk-fourier-2b",
        "revision": "3fda023e157ce7986f137f76fcf326ed6e08fa73",
        "files": (
            ("onnx/sherpa-int8/conv_frontend.onnx", "conv_frontend.onnx", len(payloads["onnx/sherpa-int8/conv_frontend.onnx"]), hashlib.sha256(payloads["onnx/sherpa-int8/conv_frontend.onnx"]).hexdigest()),
            ("onnx/sherpa-int8/tokenizer/config.json", "tokenizer/config.json", len(payloads["onnx/sherpa-int8/tokenizer/config.json"]), hashlib.sha256(payloads["onnx/sherpa-int8/tokenizer/config.json"]).hexdigest()),
            ("config.json", "hf_config.json", len(payloads["config.json"]), hashlib.sha256(payloads["config.json"]).hexdigest()),
        ),
    }

    seen_urls: list[str] = []
    real_client = httpx.Client

    def handler(req: httpx.Request) -> httpx.Response:
        seen_urls.append(str(req.url))
        prefix = "/oruk/oruk-fourier-2b/resolve/3fda023e157ce7986f137f76fcf326ed6e08fa73/"
        assert req.url.path.startswith(prefix)
        rel = req.url.path[len(prefix):]
        return httpx.Response(200, content=payloads[rel], headers={"content-length": str(len(payloads[rel]))})

    monkeypatch.setattr(httpx, "Client", lambda *a, **kw: real_client(*a, transport=httpx.MockTransport(handler), **kw))

    target = asr_setup._fetch_hf_bundle(bundle, hf_mirror=False)
    assert seen_urls == [
        "https://huggingface.co/oruk/oruk-fourier-2b/resolve/3fda023e157ce7986f137f76fcf326ed6e08fa73/onnx/sherpa-int8/conv_frontend.onnx",
        "https://huggingface.co/oruk/oruk-fourier-2b/resolve/3fda023e157ce7986f137f76fcf326ed6e08fa73/onnx/sherpa-int8/tokenizer/config.json",
        "https://huggingface.co/oruk/oruk-fourier-2b/resolve/3fda023e157ce7986f137f76fcf326ed6e08fa73/config.json",
    ]
    assert (target / "conv_frontend.onnx").read_bytes() == b"conv-bytes"
    assert (target / "tokenizer" / "config.json").read_bytes() == b'{"tokenizer": true}'
    assert (target / "hf_config.json").read_bytes() == b'{"model_type": "qwen3_asr"}'

    # Re-running skips all already-complete files (zero HTTP traffic).
    seen_urls.clear()
    asr_setup._fetch_hf_bundle(bundle, hf_mirror=False)
    assert seen_urls == []

    # Removing hf_config.json and setting --hf-mirror fetches only the missing file from hf-mirror.com.
    (target / "hf_config.json").unlink()
    asr_setup._fetch_hf_bundle(bundle, hf_mirror=True)
    assert seen_urls == [
        "https://hf-mirror.com/oruk/oruk-fourier-2b/resolve/3fda023e157ce7986f137f76fcf326ed6e08fa73/config.json",
    ]


def test_fetch_hf_bundle_aborts_before_root_config_on_sha256_mismatch(monkeypatch):
    from bili_cli.asr import setup as asr_setup

    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    bundle = {
        "dir_name": "bad-fourier",
        "repo": "oruk/oruk-fourier-2b",
        "revision": "3fda023e157ce7986f137f76fcf326ed6e08fa73",
        "files": (
            ("onnx/sherpa-int8/conv_frontend.onnx", "conv_frontend.onnx", 10, "0" * 64),
            ("config.json", "hf_config.json", 5, "1" * 64),
        ),
    }
    seen_urls: list[str] = []
    real_client = httpx.Client

    def handler(req: httpx.Request) -> httpx.Response:
        seen_urls.append(str(req.url))
        return httpx.Response(200, content=b"corrupted!", headers={"content-length": "10"})

    monkeypatch.setattr(httpx, "Client", lambda *a, **kw: real_client(*a, transport=httpx.MockTransport(handler), **kw))

    with pytest.raises(BiliError) as exc:
        asr_setup._fetch_hf_bundle(bundle)
    assert exc.value.code == "network_error"
    assert "conv_frontend.onnx" in exc.value.message
    # Must fail on the first file and NEVER fetch repo-root config.json.
    assert len(seen_urls) == 1 and seen_urls[0].endswith("conv_frontend.onnx")
    model_dir = settings.models_dir() / "bad-fourier"
    assert not (model_dir / "conv_frontend.onnx").exists()
    assert not (model_dir / "conv_frontend.onnx.part").exists()
    assert not (model_dir / "hf_config.json").exists()


def test_sherpa_engine_builds_fourier_with_from_qwen3_asr_and_isolates_cache(tmp_path, monkeypatch):
    import sys
    import types

    from bili_cli.asr import pipeline

    recorded: dict[str, object] = {}

    class FakeStream:
        def __init__(self):
            self.result = types.SimpleNamespace(text=" Hello Fourier ")

        def accept_waveform(self, rate, floats):
            recorded["rate"] = rate
            recorded["samples"] = len(floats)

    class FakeRecognizer:
        def create_stream(self):
            return FakeStream()

        def decode_stream(self, stream):
            recorded["decoded"] = True

    class FakeOfflineRecognizer:
        @staticmethod
        def from_qwen3_asr(**kwargs):
            recorded["kwargs"] = kwargs
            return FakeRecognizer()

    fake_so = types.ModuleType("sherpa_onnx")
    fake_so.OfflineRecognizer = FakeOfflineRecognizer
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake_so)
    # `[dev]` does not require numpy; fake Segment.floats so the test stays self-contained in CI.
    monkeypatch.setattr(Segment, "floats", lambda self: [0.0] * (len(self.pcm) // 2))

    model_dir = tmp_path / "oruk-fourier-2b-sherpa-int8"
    eng = engines.build_local({"model": "oruk-fourier-2b", "device": "cpu", "model_dir": str(model_dir), "threads": 3})
    assert isinstance(eng, engines.SherpaEngine)
    assert eng.id == "local-oruk-fourier-2b-cpu"
    out = asyncio.run(eng.transcribe([Segment(0, 1, SECOND)]))
    assert out == ["Hello Fourier"]
    assert recorded["kwargs"] == {
        "conv_frontend": str(model_dir / "conv_frontend.onnx"),
        "encoder": str(model_dir / "encoder.int8.onnx"),
        "decoder": str(model_dir / "decoder.int8.onnx"),
        "tokenizer": str(model_dir / "tokenizer"),
        "num_threads": 3,
    }
    assert recorded["rate"] == 16000 and recorded["decoded"] is True
    assert pipeline._cache_path("BV1test_p1", eng.id) != pipeline._cache_path("BV1test_p1", "local-qwen3-asr-cpu")


def test_setup_local_fourier_cli_dry_run_and_status(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from bili_cli.asr import setup as asr_setup
    from bili_cli.main import app

    runner = CliRunner()
    monkeypatch.setattr(asr_setup, "detect_gpu", lambda: None)

    res = runner.invoke(app, ["--dry-run", "asr", "setup", "local", "--model", "oruk-fourier-2b"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert data["ok"] is True
    assert data["data"]["dry_run"] is True
    assert data["data"]["plan"]["model"] == "oruk-fourier-2b"
    assert data["data"]["plan"]["device"] == "cpu"
    assert data["data"]["plan"]["language"] == "en"

    # Full setup_local saves English/CC BY-NC 4.0 metadata and routes to _fetch_hf_bundle.
    fetched: list[tuple[str, bool]] = []
    fake_dir = settings.models_dir() / "oruk-fourier-2b-sherpa-int8"
    fake_dir.mkdir(parents=True)
    monkeypatch.setattr(asr_setup, "_importable", lambda mod: True)
    monkeypatch.setattr(asr_setup, "_fetch_vad", lambda proxy: settings.models_dir() / asr_setup.VAD_FILE)
    monkeypatch.setattr(asr_setup, "_fetch_hf_bundle", lambda b, hf_mirror=False: (fetched.append((b["repo"], hf_mirror)), fake_dir)[1])
    monkeypatch.setattr(asr_setup, "self_test", lambda local: {"ok": True, "sample": "silence", "text": "", "seconds": 0.1})

    out = asr_setup.setup_local("auto", "oruk-fourier-2b", "https://ghfast.top", True)
    assert fetched == [("oruk/oruk-fourier-2b", True)]
    assert out["config"]["local"]["model"] == "oruk-fourier-2b"
    assert out["config"]["local"]["language"] == "en"
    assert out["config"]["local"]["license"] == "CC BY-NC 4.0"

    # status detects incomplete bundle files cleanly (missing ONNX / hf_config.json).
    status_res = runner.invoke(app, ["asr", "status"])
    assert status_res.exit_code == 0, status_res.output
    status_data = json.loads(status_res.stdout)["data"]
    assert status_data["local"]["model"] == "oruk-fourier-2b"
    assert status_data["checks"]["model_files"] is False

    # And reports ready when all bundle files match expected sizes.
    tiny_bundle = {
        "oruk-fourier-2b": {
            "files": (
                ("onnx/sherpa-int8/conv_frontend.onnx", "conv_frontend.onnx", 4, "a" * 64),
                ("config.json", "hf_config.json", 2, "b" * 64),
            ),
        },
    }
    monkeypatch.setattr(asr_setup, "HF_SHERPA_BUNDLES", tiny_bundle)
    (fake_dir / "conv_frontend.onnx").write_bytes(b"1234")
    (fake_dir / "hf_config.json").write_bytes(b"{}")
    assert asr_setup.model_files_ready(out["config"]["local"]) is True
    (fake_dir / "hf_config.json").unlink()
    assert asr_setup.model_files_ready(out["config"]["local"]) is False
