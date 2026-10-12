from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(slots=True)
class Transcription:
    text: str
    language: str | None = None


class ASRProvider(Protocol):
    def transcribe(self, samples: np.ndarray, sample_rate: int, language: str | None = None) -> Transcription:
        ...
