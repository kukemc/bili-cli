"""bili asr: configure speech-to-text engines used by `bili video transcribe`."""

from __future__ import annotations

import asyncio
from typing import Optional

import typer

from .. import output
from ..asr import settings
from ..errors import BiliError
from ..runtime import run

app = typer.Typer(help="语音转写引擎：一键部署本地模型、配置云端服务、查看状态。", no_args_is_help=True)
setup_app = typer.Typer(help="部署 / 配置转写引擎。", no_args_is_help=True)
app.add_typer(setup_app, name="setup")

PROVIDERS = ("openai", "aliyun", "tencent")


@setup_app.command("local")
def setup_local(
    device: str = typer.Option("auto", "--device", help="auto（有 NVIDIA 显卡且显存 ≥4GB 用 GPU）/ cpu / gpu。"),
    model: str = typer.Option(
        "qwen3-asr",
        "--model",
        help="qwen3-asr（默认，质量最好）/ sensevoice（CPU 上快 10 倍，但无标点、错字多）/ oruk-fourier-2b（纯英语 2B INT8，CC BY-NC 4.0 非商用，CPU）。",
    ),
    github_proxy: str = typer.Option("", "--github-proxy", help="GitHub 下载加速前缀，国内网络慢时使用，如 https://ghfast.top/。"),
    hf_mirror: bool = typer.Option(False, "--hf-mirror", help="从 hf-mirror.com 下载 Hugging Face 模型（国内网络，适用于 GPU 版及 oruk-fourier-2b）。"),
) -> None:
    """一键部署本地转写：安装依赖、下载模型、自检。会把依赖装进当前 Python 环境。"""

    async def body():
        from ..asr import setup as asr_setup

        if device not in ("auto", "cpu", "gpu"):
            raise BiliError("invalid_argument", "--device 只能是 auto / cpu / gpu。")
        if model not in asr_setup.LOCAL_MODELS:
            raise BiliError("invalid_argument", f"--model 只能是 {' / '.join(asr_setup.LOCAL_MODELS)}。")
        dev = {"gpu": "cuda"}.get(device, device)
        if output.settings.dry_run:
            return {"dry_run": True, "action": "asr.setup.local", "plan": asr_setup.plan(dev, model)}
        # setup runs its own event loop for the self-test, so keep it off this one
        return await asyncio.to_thread(asr_setup.setup_local, dev, model, github_proxy, hf_mirror)

    run(body, deadline=None)  # downloads and installs a model


@setup_app.command("cloud")
def setup_cloud(
    provider: str = typer.Option(..., "--provider", help="openai（任何 OpenAI 兼容接口）/ aliyun（百炼 Qwen3-ASR-Flash）/ tencent（一句话识别）。"),
    api_key: Optional[str] = typer.Option(None, "--api-key", envvar="BILI_ASR_API_KEY", help="openai / aliyun 的 API Key。"),
    base_url: Optional[str] = typer.Option(None, "--base-url", help="openai：如 https://api.groq.com/openai/v1、https://api.siliconflow.cn/v1；aliyun 可选。"),
    model: Optional[str] = typer.Option(None, "--model", help="openai：如 whisper-large-v3、FunAudioLLM/SenseVoiceSmall；aliyun 默认 qwen3-asr-flash。"),
    secret_id: Optional[str] = typer.Option(None, "--secret-id", envvar="BILI_ASR_SECRET_ID", help="tencent 的 SecretId。"),
    secret_key: Optional[str] = typer.Option(None, "--secret-key", envvar="BILI_ASR_SECRET_KEY", help="tencent 的 SecretKey。"),
    engine_type: str = typer.Option("16k_zh", "--engine-type", help="tencent 引擎：16k_zh / 16k_zh_dialect 等。"),
    test: bool = typer.Option(True, "--test/--no-test", help="保存前用一段静音试调一次接口，确认密钥可用。"),
) -> None:
    """配置云端转写服务。密钥保存在本地配置目录（仅本人可读），不会出现在日志里。"""

    async def body():
        if provider not in PROVIDERS:
            raise BiliError("invalid_argument", f"--provider 只能是 {' / '.join(PROVIDERS)}。")
        if provider == "openai":
            if not (api_key and base_url and model):
                raise BiliError("invalid_argument", "openai 需要 --api-key、--base-url 和 --model。")
            cloud = {"provider": "openai", "api_key": api_key, "base_url": base_url, "model": model}
        elif provider == "aliyun":
            if not api_key:
                raise BiliError("invalid_argument", "aliyun 需要 --api-key（百炼 DashScope API Key）。")
            cloud = {"provider": "aliyun", "api_key": api_key, "model": model or "qwen3-asr-flash"}
            if base_url:
                cloud["base_url"] = base_url
        else:
            if not (secret_id and secret_key):
                raise BiliError("invalid_argument", "tencent 需要 --secret-id 和 --secret-key。")
            cloud = {"provider": "tencent", "secret_id": secret_id, "secret_key": secret_key, "engine_type": engine_type}
        conf = settings.load()
        if output.settings.dry_run:
            return {"dry_run": True, "action": "asr.setup.cloud", "params": settings.masked(cloud)}
        result = {"provider": provider}
        if test:
            from ..asr import engines
            from ..asr.segment import Segment

            probe = {**conf, "cloud": cloud, "default": "cloud"}
            settings.save(probe)
            try:
                await engines.build("cloud").transcribe([Segment(0, 1.0, b"\x00\x00" * 16000)])
            except BaseException:
                settings.save(conf)  # keep the previous working config
                raise
            result["test"] = "ok"
        conf["cloud"] = cloud
        settings.save(conf)
        return {**result, "config": settings.masked(conf)}

    run(body)


@app.command()
def use(engine: str = typer.Argument(..., help="默认引擎：cloud / local / auto（有云端用云端）。")) -> None:
    """设置 `video transcribe` 的默认引擎。"""

    async def body():
        if engine not in ("cloud", "local", "auto"):
            raise BiliError("invalid_argument", "只能是 cloud / local / auto。")
        conf = settings.load()
        if engine != "auto" and not conf.get(engine):
            raise BiliError("invalid_argument", f"{engine} 引擎尚未配置。", hint=f"先运行 `bili asr setup {engine}`。")
        conf.pop("default", None) if engine == "auto" else conf.update(default=engine)
        settings.save(conf)
        return {"default": engine}

    run(body)


@app.command()
def status() -> None:
    """查看已配置的转写引擎、默认引擎、依赖和模型文件是否就绪。"""

    async def body():
        from pathlib import Path

        from ..asr import setup as asr_setup
        from ..asr.audio import require_ffmpeg

        conf = settings.load()
        local = conf.get("local") or {}
        checks = {"ffmpeg": True, "sherpa_onnx": asr_setup._importable("sherpa_onnx"), "vad_model": bool(conf.get("vad_model") and Path(conf["vad_model"]).exists())}
        try:
            require_ffmpeg()
        except BiliError:
            checks["ffmpeg"] = False
        if local.get("device") == "cuda":
            checks["qwen_asr"] = asr_setup._importable("qwen_asr")
        elif local:
            checks["model_files"] = asr_setup.model_files_ready(local)
        default = conf.get("default") or ("cloud" if conf.get("cloud") else "local" if local else None)
        return {
            "default": default,
            "cloud": settings.masked(conf.get("cloud") or {}) or None,
            "local": local or None,
            "gpu": asr_setup.detect_gpu(),
            "checks": checks,
            "ready": bool(default) and checks["ffmpeg"],
        }

    run(body)
