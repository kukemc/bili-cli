"""audio -> segments -> engine -> subtitle-style items, cached per (video page, engine)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .. import config, output
from . import engines, settings
from .audio import fetch_wav
from .segment import silence_segments, vad_segments


def _cache_path(key: str, engine_id: str) -> Path:
    d = config.home() / "transcripts"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}__{engine_id}.json"


async def transcribe(playurl_fn, key: str, engine: engines.Engine, context: str = "", use_cache: bool = True) -> dict[str, Any]:
    cache = _cache_path(key, engine.id)
    if use_cache and cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        return {**data, "cached": True}

    output.info("下载音频…")
    wav = await fetch_wav(await playurl_fn(), key)
    vad_model = settings.load().get("vad_model")
    try:
        segments = vad_segments(wav, Path(vad_model)) if vad_model and Path(vad_model).exists() else silence_segments(wav)
        segmenter = "silero-vad" if vad_model and Path(vad_model).exists() else "ffmpeg-silencedetect"
    except ImportError:
        segments, segmenter = silence_segments(wav), "ffmpeg-silencedetect"
    output.info(f"{len(segments)} 个语音片段，使用 {engine.id} 识别…")
    t0 = time.time()
    texts = await engine.transcribe(segments, context=context)
    items = [{"from": s.start, "to": s.end, "content": t} for s, t in zip(segments, texts) if t]
    data = {
        "engine": engine.id,
        "segmenter": segmenter,
        "seconds": round(time.time() - t0, 1),
        "items": items,
    }
    cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    wav.unlink(missing_ok=True)  # audio is only needed once; the transcript is cached
    return {**data, "cached": False}
