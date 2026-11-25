"""Recognizers. Each turns a list of Segments into one text per segment.

Local:  sherpa-onnx (CPU, int8)  /  qwen-asr on PyTorch (NVIDIA GPU, bf16)
Cloud:  OpenAI-compatible /audio/transcriptions  /  Aliyun Qwen3-ASR-Flash  /  Tencent SentenceRecognition
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from ..errors import BiliError
from ..runtime import http_client
from . import settings
from .segment import Segment

CLOUD_CONCURRENCY = 4


class Engine:
    id = "engine"
    kind = "local"

    async def transcribe(self, segments: list[Segment], context: str = "") -> list[str]:
        raise NotImplementedError


# ---------------------------------------------------------------- local


def expose_nvidia_dlls() -> None:
    """On Windows the CUDA/cuDNN runtime comes from NVIDIA's pip wheels; add their DLL folders to the search path."""
    if os.name != "nt":
        return
    import importlib.util

    spec = importlib.util.find_spec("nvidia")
    if spec is None or not spec.submodule_search_locations:
        return
    for bin_dir in Path(list(spec.submodule_search_locations)[0]).glob("*/bin"):
        os.add_dll_directory(str(bin_dir))
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


class SherpaEngine(Engine):
    """CPU inference with sherpa-onnx: no PyTorch, works everywhere."""

    def __init__(self, model: str, model_dir: Path, threads: int = 4) -> None:
        self.model, self.model_dir, self.threads = model, model_dir, threads
        self.id = f"local-{model}-cpu"
        self._rec = None

    def _build(self):
        import sherpa_onnx as so

        d = self.model_dir
        if self.model in ("qwen3-asr", "oruk-fourier-2b"):
            return so.OfflineRecognizer.from_qwen3_asr(
                conv_frontend=str(d / "conv_frontend.onnx"), encoder=str(d / "encoder.int8.onnx"),
                decoder=str(d / "decoder.int8.onnx"), tokenizer=str(d / "tokenizer"), num_threads=self.threads,
            )
        if self.model == "sensevoice":
            return so.OfflineRecognizer.from_sense_voice(
                model=str(d / "model.int8.onnx"), tokens=str(d / "tokens.txt"), num_threads=self.threads, use_itn=True
            )
        raise BiliError("invalid_argument", f"未知的本地模型 {self.model}")

    def _run(self, segments: list[Segment]) -> list[str]:
        if self._rec is None:
            self._rec = self._build()
        texts = []
        for seg in segments:
            stream = self._rec.create_stream()
            stream.accept_waveform(16000, seg.floats())
            self._rec.decode_stream(stream)
            texts.append(stream.result.text.strip())
        return texts

    async def transcribe(self, segments: list[Segment], context: str = "") -> list[str]:
        return await asyncio.to_thread(self._run, segments)


class TorchQwenEngine(Engine):
    """Official qwen-asr on an NVIDIA GPU in bf16: ~7x faster than the CPU path on an RTX 3060."""

    def __init__(self, model_path: str, batch_size: int = 16) -> None:
        self.model_path, self.batch_size = model_path, batch_size
        self.id = "local-qwen3-asr-cuda"

    def _run(self, segments: list[Segment], context: str) -> list[str]:
        import torch
        from qwen_asr import Qwen3ASRModel

        model = Qwen3ASRModel.from_pretrained(
            self.model_path, dtype=torch.bfloat16, device_map="cuda:0",
            max_inference_batch_size=self.batch_size, max_new_tokens=256,
        )
        # language=None lets the model detect it per segment (videos mix Chinese speech with English songs)
        results = model.transcribe(audio=[(s.floats(), 16000) for s in segments], language=None, context=context)
        return [r.text.strip() for r in results]

    async def transcribe(self, segments: list[Segment], context: str = "") -> list[str]:
        return await asyncio.to_thread(self._run, segments, context)


# ---------------------------------------------------------------- cloud


async def _gather_limited(segments: list[Segment], one) -> list[str]:
    sem = asyncio.Semaphore(CLOUD_CONCURRENCY)

    async def run(seg: Segment) -> str:
        async with sem:
            for attempt in range(3):
                try:
                    return (await one(seg)).strip()
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in (429, 500, 502, 503) and attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    raise BiliError("api_error", f"转写服务返回 HTTP {exc.response.status_code}：{exc.response.text[:200]}") from exc
            return ""

    return list(await asyncio.gather(*(run(s) for s in segments)))


class OpenAICompatEngine(Engine):
    """Any /audio/transcriptions endpoint: OpenAI, Groq, SiliconFlow, self-hosted servers..."""

    kind = "cloud"

    def __init__(self, base_url: str, api_key: str, model: str, language: str = "zh") -> None:
        self.base_url, self.api_key, self.model, self.language = base_url.rstrip("/"), api_key, model, language
        self.id = f"cloud-openai-{model}"

    async def transcribe(self, segments: list[Segment], context: str = "") -> list[str]:
        async with http_client(timeout=120, headers={"Authorization": f"Bearer {self.api_key}"}) as client:

            async def one(seg: Segment) -> str:
                data = {"model": self.model, "response_format": "json"}
                if self.language:
                    data["language"] = self.language
                if context:
                    data["prompt"] = context
                resp = await client.post(f"{self.base_url}/audio/transcriptions", data=data, files={"file": ("seg.wav", seg.wav_bytes(), "audio/wav")})
                resp.raise_for_status()
                return resp.json().get("text") or ""

            return await _gather_limited(segments, one)


class AliyunEngine(Engine):
    """Aliyun Model Studio Qwen3-ASR-Flash through its OpenAI-style chat endpoint (base64 audio, <= 5 min per call)."""

    kind = "cloud"
    DEFAULT_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def __init__(self, api_key: str, model: str = "qwen3-asr-flash", base_url: str = DEFAULT_BASE) -> None:
        self.api_key, self.model, self.base_url = api_key, model, base_url.rstrip("/")
        self.id = f"cloud-aliyun-{model}"

    async def transcribe(self, segments: list[Segment], context: str = "") -> list[str]:
        async with http_client(timeout=120, headers={"Authorization": f"Bearer {self.api_key}"}) as client:

            async def one(seg: Segment) -> str:
                audio = "data:audio/wav;base64," + base64.b64encode(seg.wav_bytes()).decode()
                messages: list[dict[str, Any]] = []
                if context:
                    messages.append({"role": "system", "content": [{"type": "text", "text": context}]})
                messages.append({"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": audio}}]})
                body = {"model": self.model, "messages": messages, "stream": False, "asr_options": {"enable_itn": True, "language": "zh"}}
                resp = await client.post(f"{self.base_url}/chat/completions", json=body)
                resp.raise_for_status()
                return ((resp.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""

            return await _gather_limited(segments, one)


class TencentEngine(Engine):
    """Tencent Cloud 一句话识别 (SentenceRecognition): <= 60 s per call, TC3-HMAC-SHA256 signed."""

    kind = "cloud"
    HOST = "asr.tencentcloudapi.com"

    def __init__(self, secret_id: str, secret_key: str, engine_type: str = "16k_zh") -> None:
        self.secret_id, self.secret_key, self.engine_type = secret_id, secret_key, engine_type
        self.id = f"cloud-tencent-{engine_type}"

    def _headers(self, payload: str) -> dict[str, str]:
        action, service, ts = "SentenceRecognition", "asr", int(time.time())
        date = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
        ctype = "application/json; charset=utf-8"
        canonical = "\n".join([
            "POST", "/", "",
            f"content-type:{ctype}\nhost:{self.HOST}\nx-tc-action:{action.lower()}\n",
            "content-type;host;x-tc-action",
            hashlib.sha256(payload.encode()).hexdigest(),
        ])
        scope = f"{date}/{service}/tc3_request"
        to_sign = f"TC3-HMAC-SHA256\n{ts}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"

        def sign(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode(), hashlib.sha256).digest()

        signing_key = sign(sign(sign(("TC3" + self.secret_key).encode(), date), service), "tc3_request")
        signature = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).hexdigest()
        return {
            "Authorization": f"TC3-HMAC-SHA256 Credential={self.secret_id}/{scope}, SignedHeaders=content-type;host;x-tc-action, Signature={signature}",
            "Content-Type": ctype,
            "Host": self.HOST,
            "X-TC-Action": action,
            "X-TC-Timestamp": str(ts),
            "X-TC-Version": "2019-06-14",
        }

    async def transcribe(self, segments: list[Segment], context: str = "") -> list[str]:
        async with http_client(timeout=60) as client:

            async def one(seg: Segment) -> str:
                raw = seg.wav_bytes()
                payload = json.dumps({
                    "EngSerViceType": self.engine_type, "SourceType": 1, "VoiceFormat": "wav",
                    "Data": base64.b64encode(raw).decode(), "DataLen": len(raw),
                })
                resp = await client.post(f"https://{self.HOST}", content=payload, headers=self._headers(payload))
                resp.raise_for_status()
                body = resp.json().get("Response") or {}
                if body.get("Error"):
                    err = body["Error"]
                    raise BiliError("api_error", f"腾讯云 ASR 错误 {err.get('Code')}：{err.get('Message')}")
                return body.get("Result") or ""

            return await _gather_limited(segments, one)


# ---------------------------------------------------------------- selection


def build_local(local: dict[str, Any]) -> Engine:
    if local.get("device") == "cuda":
        expose_nvidia_dlls()
        return TorchQwenEngine(local["model_path"], local.get("batch_size", 16))
    return SherpaEngine(local["model"], Path(local["model_dir"]), local.get("threads", 4))


def build(which: str = "auto") -> Engine:
    """auto = cloud if configured, else local."""
    conf = settings.load()
    cloud, local = conf.get("cloud"), conf.get("local")
    if which == "auto":
        which = conf.get("default") or ("cloud" if cloud else "local" if local else "")
    if which == "cloud":
        if not cloud:
            raise BiliError("invalid_argument", "尚未配置云端转写。", hint="运行 `bili asr setup cloud --provider openai|aliyun|tencent ...`。")
        p = cloud.get("provider")
        if p == "openai":
            return OpenAICompatEngine(cloud["base_url"], cloud["api_key"], cloud["model"], cloud.get("language", "zh"))
        if p == "aliyun":
            return AliyunEngine(cloud["api_key"], cloud.get("model", "qwen3-asr-flash"), cloud.get("base_url") or AliyunEngine.DEFAULT_BASE)
        if p == "tencent":
            return TencentEngine(cloud["secret_id"], cloud["secret_key"], cloud.get("engine_type", "16k_zh"))
        raise BiliError("invalid_argument", f"未知的云端服务 {p}")
    if which == "local":
        if not local:
            raise BiliError("invalid_argument", "尚未部署本地转写模型。", hint="运行 `bili asr setup local`（自动识别 NVIDIA 显卡）。")
        return build_local(local)
    raise BiliError("invalid_argument", "还没有可用的转写引擎。", hint="运行 `bili asr setup local` 一键部署本地模型，或 `bili asr setup cloud` 配置云端服务。")
