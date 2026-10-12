from __future__ import annotations

import threading
import platform
import time
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(slots=True)
class AudioClip:
    samples: np.ndarray
    sample_rate: int

    @property
    def duration_seconds(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return float(len(self.samples)) / float(self.sample_rate)


class AudioRecorder:
    """Push-to-talk recorder backed by sounddevice.InputStream."""

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        device: str | int | None = None,
        dtype: str = "float32",
        max_seconds: float = 120.0,
    ) -> None:
        self.sample_rate = sample_rate
        self.device = device
        self.dtype = dtype
        self.max_seconds = max_seconds
        self._stream: Any | None = None
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._started_at: float | None = None
        self._recording = False

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(self) -> None:
        if self._recording:
            return
        try:
            import sounddevice as sd
        except ImportError as exc:  # pragma: no cover - dependency path
            raise RuntimeError("sounddevice is not installed") from exc

        with self._lock:
            self._chunks.clear()
        self._started_at = time.monotonic()
        self._recording = True

        def callback(indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
            del frames, time_info
            if status:
                # PortAudio status is intentionally non-fatal for dictation.
                pass
            if not self._recording:
                return
            if self._started_at is not None and time.monotonic() - self._started_at > self.max_seconds:
                # Keep the push-to-talk state active until release, but stop buffering
                # to bound memory use and transcription length.
                return
            with self._lock:
                self._chunks.append(indata[:, 0].copy())

        try:
            extra = {}
            device = self.device
            if platform.system() == "Windows":
                if device is None:
                    for host in sd.query_hostapis():
                        if 'WASAPI' in host['name'].upper() and host.get('default_input_device', -1) >= 0:
                            device = host['default_input_device']
                            break
                info = sd.query_devices(device, 'input')
                host = sd.query_hostapis(info['hostapi'])
                if 'WASAPI' in host['name'].upper():
                    # WASAPI shared-mode conversion handles 44.1/48 kHz devices
                    # while ASR receives a genuine 16 kHz stream.
                    extra['extra_settings'] = sd.WasapiSettings(auto_convert=True)
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype=self.dtype,
                device=device,
                callback=callback,
                **extra,
            )
            self._stream.start()
        except Exception:
            self._recording = False
            stream, self._stream = self._stream, None
            if stream is not None:
                stream.close()
            raise

    def stop(self) -> AudioClip:
        if not self._recording and self._stream is None:
            return AudioClip(np.empty((0,), dtype=np.float32), self.sample_rate)
        self._recording = False
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()
        with self._lock:
            chunks, self._chunks = self._chunks, []
        if not chunks:
            data = np.empty((0,), dtype=np.float32)
        else:
            data = np.concatenate(chunks).astype(np.float32, copy=False)
        return AudioClip(data, self.sample_rate)


def list_audio_devices() -> list[dict[str, Any]]:
    try:
        import sounddevice as sd
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("sounddevice is not installed") from exc
    devices = sd.query_devices()
    out: list[dict[str, Any]] = []
    hostapis = sd.query_hostapis()
    for idx, item in enumerate(devices):
        if int(item.get("max_input_channels", 0)) > 0:
            out.append(
                {
                    "index": idx,
                    "name": str(item.get("name", "")),
                    "hostapi_name": str(hostapis[int(item.get("hostapi", 0))]["name"]),
                    "max_input_channels": int(item.get("max_input_channels", 0)),
                    "default_samplerate": float(item.get("default_samplerate", 0.0)),
                }
            )
    return out
