from __future__ import annotations

import numpy as np

from .base import Transcription


class FakeASRProvider:
    def __init__(self, text: str = "тестовая диктовка") -> None:
        self.text = text
        self.calls = 0

    def transcribe(self, samples: np.ndarray, sample_rate: int, language: str | None = None) -> Transcription:
        del samples, sample_rate
        self.calls += 1
        return Transcription(text=self.text, language=language)
