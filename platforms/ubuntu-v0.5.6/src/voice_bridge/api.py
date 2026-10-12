from __future__ import annotations

import io
import wave

import numpy as np

from .asr.base import ASRProvider
from .text_engine import TextEngine


def decode_pcm_wav(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data), "rb") as wav:
        if wav.getnchannels() != 1:
            raise ValueError("Only mono WAV is supported in MVP")
        sample_rate = wav.getframerate()
        width = wav.getsampwidth()
        frames = wav.readframes(wav.getnframes())
    if width == 2:
        samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        samples = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Unsupported WAV sample width: {width}")
    return samples, sample_rate


def create_api(asr: ASRProvider, text_engine: TextEngine, *, mode: str = "dictation", language: str | None = "Russian"):
    try:
        from fastapi import FastAPI, HTTPException, Request
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("API dependencies are missing. Install with pip install -e '.[api]'") from exc

    app = FastAPI(title="Voice Input Bridge API", version="0.2.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/transcribe")
    async def transcribe(request: Request) -> dict[str, str | None]:
        try:
            samples, sample_rate = decode_pcm_wav(await request.body())
            result = asr.transcribe(samples, sample_rate, language)
            final = text_engine.process(result.text, mode=mode).text
            return {"text": final, "raw_text": result.text, "language": result.language}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app
