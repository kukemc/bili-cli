"""`bili asr setup local`: install dependencies into the current Python, fetch models, self-test.

CPU: sherpa-onnx (bundles ONNX Runtime, no PyTorch) + Qwen3-ASR-0.6B int8 (~840 MB).
GPU: PyTorch CUDA + qwen-asr + Qwen3-ASR-0.6B bf16 (~4 GB total); no CUDA toolkit needed.
Both also get the 0.6 MB silero VAD used for segmentation.
Benchmarks behind these choices (i7-11800H / RTX 3060, 3 min of speech): CPU int8 77 s, GPU bf16 10.5 s.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

import httpx

from .. import output
from ..errors import BiliError
from . import settings

GITHUB_RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
SHERPA_PACKAGES = {
    "qwen3-asr": "sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25",
    "sensevoice": "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09",
}
FOURIER_MODEL = "oruk-fourier-2b"
FOURIER_HF_REPO = "oruk/oruk-fourier-2b"
FOURIER_HF_REVISION = "3fda023e157ce7986f137f76fcf326ed6e08fa73"
HF_BASE_URL = "https://huggingface.co"
HF_MIRROR_URL = "https://hf-mirror.com"
# Direct Hugging Face sherpa-onnx bundles: (remote_path, local_relpath, size, sha256).
# Repo-root config.json is fetched LAST (as hf_config.json so it never collides with
# tokenizer/config.json) so interrupted downloads do not count toward Hub downloads.
HF_SHERPA_BUNDLES: dict[str, dict[str, Any]] = {
    FOURIER_MODEL: {
        "dir_name": "oruk-fourier-2b-sherpa-int8",
        "repo": FOURIER_HF_REPO,
        "revision": FOURIER_HF_REVISION,
        "size_label": "~2.41 GB",
        "language": "en",
        "license": "CC BY-NC 4.0",
        "files": (
            ("onnx/sherpa-int8/conv_frontend.onnx", "conv_frontend.onnx", 48080440, "19ddcb711237bba84f7aa385ce7c63320bcb736ce8888299064036d57a774bd9"),
            ("onnx/sherpa-int8/encoder.int8.onnx", "encoder.int8.onnx", 1670820, "61255ca9e30a52c0d1da59ade9327792f08e32849cb1570da19be9ed6760cef9"),
            ("onnx/sherpa-int8/encoder.int8.onnx.data", "encoder.int8.onnx.data", 306487296, "a11a260308504627190fbf662478eec9ff95cb36b3387f257892f6fb8886e8a1"),
            ("onnx/sherpa-int8/decoder.int8.onnx", "decoder.int8.onnx", 5393503, "d252adc96e55aa858876896e20783a357fda4c38e216d5be1c7b42bf73c86e8b"),
            ("onnx/sherpa-int8/decoder.int8.onnx.data", "decoder.int8.onnx.data", 2032082944, "4625b41d68653f974af294b6ef5539c36e32d0477d0a2262a9157e275a09433f"),
            ("onnx/sherpa-int8/tokenizer/config.json", "tokenizer/config.json", 2072, "9306077f32e1989f6128d12a0e471a3bd612389dbd5bff81305b8f558fee9901"),
            ("onnx/sherpa-int8/tokenizer/merges.txt", "tokenizer/merges.txt", 1671853, "8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5"),
            ("onnx/sherpa-int8/tokenizer/tokenizer.json", "tokenizer/tokenizer.json", 11429377, "bd2a97b55c8f7f9c328c73ee9b9178771037e9f566dfca8e238a063d41cbac92"),
            ("onnx/sherpa-int8/tokenizer/tokenizer_config.json", "tokenizer/tokenizer_config.json", 10386, "e8f0641c46b7c259b8d4a34097471dab636bd5d5062f480ed7c97a3d3b3981ee"),
            ("onnx/sherpa-int8/tokenizer/vocab.json", "tokenizer/vocab.json", 3080118, "97dfe4fe392c23cda68fe01c60babe50a57c894fc0cf44d02c4048ad283499c3"),
            ("config.json", "hf_config.json", 2479, "5bc1d99c141fc866b61efca97dcbc6412a38db25ff0934c38bf7b46fa09485a2"),
        ),
    },
}
LOCAL_MODELS = (*SHERPA_PACKAGES, *HF_SHERPA_BUNDLES)
VAD_FILE = "silero_vad.onnx"
HF_MODEL = "Qwen/Qwen3-ASR-0.6B"
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"
MIN_VRAM_MB = 4000


def detect_gpu() -> dict[str, Any] | None:
    if not shutil.which("nvidia-smi"):
        return None
    proc = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    name, mem = [x.strip() for x in proc.stdout.strip().splitlines()[0].split(",")]
    return {"name": name, "vram_mb": int(float(mem))}


def _importable(module: str) -> bool:
    try:
        importlib.import_module(module)
        return True
    except Exception:  # noqa: BLE001 - a broken install counts as missing
        return False


def _pip(*args: str) -> None:
    output.info(f"pip install {' '.join(args)}")
    proc = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *args], stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if proc.returncode != 0:
        raise BiliError("internal_error", "安装依赖失败。", hint="检查网络，或手动执行上面的 pip 命令。", detail={"pip": proc.stderr[-800:]})
    importlib.invalidate_caches()


def _download(url: str, dest: Path, expected_sha256: str | None = None) -> None:
    output.info(f"下载 {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    digest = hashlib.sha256() if expected_sha256 else None
    try:
        with httpx.Client(follow_redirects=True, timeout=60) as client, client.stream("GET", url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            done = 0
            with open(tmp, "wb") as f:
                for chunk in resp.iter_bytes(1 << 20):
                    f.write(chunk)
                    if digest is not None:
                        digest.update(chunk)
                    done += len(chunk)
                    if total and sys.stderr.isatty():
                        sys.stderr.write(f"\r  {done >> 20}/{total >> 20} MB")
                        sys.stderr.flush()
        if sys.stderr.isatty():
            sys.stderr.write("\n")
        if digest is not None and digest.hexdigest() != expected_sha256:
            raise BiliError(
                "network_error",
                f"文件校验失败：{dest.name}。",
                hint="请重试下载；如使用镜像请确认镜像与上游同步。",
                detail={"url": url, "expected_sha256": expected_sha256, "actual_sha256": digest.hexdigest()},
            )
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _hf_endpoint(hf_mirror: bool) -> str:
    if hf_mirror:
        return HF_MIRROR_URL
    return (os.environ.get("HF_ENDPOINT") or HF_BASE_URL).rstrip("/")


def _fetch_hf_bundle(bundle: dict[str, Any], hf_mirror: bool = False) -> Path:
    target = settings.models_dir() / bundle["dir_name"]
    target.mkdir(parents=True, exist_ok=True)
    base = _hf_endpoint(hf_mirror)
    for remote_path, local_rel, expected_size, expected_sha256 in bundle["files"]:
        dest = target / local_rel
        if dest.is_file() and dest.stat().st_size == expected_size:
            continue
        url = f"{base}/{bundle['repo']}/resolve/{bundle['revision']}/{remote_path}"
        _download(url, dest, expected_sha256=expected_sha256)
    return target


def model_files_ready(local: dict[str, Any]) -> bool:
    model_dir = local.get("model_dir")
    if not model_dir:
        return False
    d = Path(model_dir)
    bundle = HF_SHERPA_BUNDLES.get(local.get("model", ""))
    if bundle:
        return all(
            (d / local_rel).is_file() and (d / local_rel).stat().st_size == expected_size
            for _, local_rel, expected_size, _ in bundle["files"]
        )
    return d.exists()


def _fetch_release(name: str, proxy: str) -> Path:
    target = settings.models_dir() / name
    if target.exists():
        return target
    archive = settings.models_dir() / f"{name}.tar.bz2"
    _download(proxy + GITHUB_RELEASE + f"{name}.tar.bz2", archive)
    output.info("解压中…")
    with tarfile.open(archive, "r:bz2") as tar:
        try:
            tar.extractall(settings.models_dir(), filter="data")
        except TypeError:  # Python < 3.12 without extraction filters
            tar.extractall(settings.models_dir())
    archive.unlink()
    return target


def _fetch_vad(proxy: str) -> Path:
    path = settings.models_dir() / VAD_FILE
    if not path.exists():
        _download(proxy + GITHUB_RELEASE + VAD_FILE, path)
    return path


def plan(device: str, model: str) -> dict[str, Any]:
    if model not in LOCAL_MODELS:
        raise BiliError("invalid_argument", f"--model 只能是 {' / '.join(LOCAL_MODELS)}。")
    gpu = detect_gpu()
    if device == "auto":
        device = "cuda" if gpu and gpu["vram_mb"] >= MIN_VRAM_MB and model == "qwen3-asr" else "cpu"
    if device == "cuda" and not gpu:
        raise BiliError("invalid_argument", "没有检测到 NVIDIA 显卡。", hint="去掉 --device gpu，使用 CPU 版本。")
    if device == "cuda" and model != "qwen3-asr":
        raise BiliError("invalid_argument", "GPU 版本只支持 qwen3-asr 模型。", hint="去掉 --device gpu，使用 sherpa-onnx CPU 版本。")
    if model in HF_SHERPA_BUNDLES:
        bundle = HF_SHERPA_BUNDLES[model]
        return {
            "device": device,
            "gpu": gpu,
            "model": model,
            "language": bundle["language"],
            "license": f"{bundle['license']} (noncommercial)",
            "packages": ["sherpa-onnx", "numpy"],
            "downloads": [
                f"{bundle['repo']}@{bundle['revision'][:8]} onnx/sherpa-int8 ({bundle['size_label']}, HuggingFace, English-only, {bundle['license']})",
                VAD_FILE,
            ],
            "expected_speed": "纯英语 2B INT8 模型（CC BY-NC 4.0 非商用，sherpa-onnx CPU）；英语准确率高于 Qwen3-ASR，CPU 推理较 0.6B 更慢",
        }
    return {
        "device": device,
        "gpu": gpu,
        "model": model,
        "packages": ["torch (CUDA 12.8)", "qwen-asr", "sherpa-onnx", "numpy"] if device == "cuda" else ["sherpa-onnx", "numpy"],
        "downloads": (
            [f"{HF_MODEL} (~1.8 GB, HuggingFace)", "PyTorch CUDA (~2.5 GB)", VAD_FILE]
            if device == "cuda"
            else [f"{SHERPA_PACKAGES[model]} ({'~840 MB' if model == 'qwen3-asr' else '~160 MB'}, GitHub)", VAD_FILE]
        ),
        "expected_speed": "约 0.06 倍实时（30 分钟视频约 2 分钟，RTX 3060 实测）" if device == "cuda"
        else ("约 0.45 倍实时（30 分钟视频约 13 分钟，i7-11800H 实测）" if model == "qwen3-asr" else "约 0.04 倍实时，但准确率较低、无标点"),
    }


def setup_local(device: str, model: str, github_proxy: str, hf_mirror: bool) -> dict[str, Any]:
    p = plan(device, model)
    proxy = github_proxy.rstrip("/") + "/" if github_proxy else ""
    if not _importable("sherpa_onnx") or not _importable("numpy"):
        _pip("sherpa-onnx", "numpy")
    vad = _fetch_vad(proxy)
    conf = settings.load()

    if p["device"] == "cuda":
        if not (_importable("torch") and importlib.import_module("torch").cuda.is_available()):
            _pip("torch", "--index-url", TORCH_INDEX)
        if not _importable("qwen_asr"):
            _pip("qwen-asr")
        if hf_mirror:
            os.environ["HF_ENDPOINT"] = HF_MIRROR_URL
        from huggingface_hub import snapshot_download

        output.info(f"下载模型 {HF_MODEL}…")
        model_path = snapshot_download(HF_MODEL)
        local = {"model": "qwen3-asr", "device": "cuda", "model_path": model_path,
                 "batch_size": 16 if (p["gpu"] or {}).get("vram_mb", 0) >= 6000 else 8}
    elif model in HF_SHERPA_BUNDLES:
        bundle = HF_SHERPA_BUNDLES[model]
        model_dir = _fetch_hf_bundle(bundle, hf_mirror=hf_mirror)
        local = {
            "model": model,
            "device": "cpu",
            "model_dir": str(model_dir),
            "threads": min(4, os.cpu_count() or 4),
            "language": bundle["language"],
            "license": bundle["license"],
        }
    else:
        model_dir = _fetch_release(SHERPA_PACKAGES[model], proxy)
        local = {"model": model, "device": "cpu", "model_dir": str(model_dir), "threads": min(4, os.cpu_count() or 4)}

    conf["local"] = local
    conf["vad_model"] = str(vad)
    settings.save(conf)
    return {**p, "self_test": self_test(local), "config": settings.masked(conf)}


def self_test(local: dict[str, Any]) -> dict[str, Any]:
    """Run the engine on a short sample so setup only reports success when recognition really works."""
    import asyncio
    import time

    from . import engines
    from .segment import Segment, read_pcm

    sample = None
    if local.get("model_dir"):
        wavs = sorted(Path(local["model_dir"]).glob("test_wavs/*.wav"), key=lambda p: ("zh" not in p.name and "raokouling" not in p.name, p.name))
        sample = wavs[0] if wavs else None
    if sample is not None:
        # sample files come at various rates (raokouling.wav is 44.1 kHz); normalise like real audio
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "sample.wav"
            subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(sample), "-t", "15", "-ac", "1", "-ar", "16000", str(wav)],
                           check=True, stdin=subprocess.DEVNULL, capture_output=True)
            pcm = read_pcm(wav)
        seg = Segment(0, len(pcm) / 32000, pcm)
    else:
        seg = Segment(0, 1.0, b"\x00\x00" * 16000)  # GPU path: one second of silence proves the model loads on CUDA
    engine = engines.build_local(local)
    t0 = time.time()
    text = asyncio.run(engine.transcribe([seg]))[0]
    return {"ok": True, "sample": sample.name if sample else "silence", "text": text, "seconds": round(time.time() - t0, 1)}
