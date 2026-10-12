from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable

from .asr.base import ASRProvider
from .audio import AudioRecorder
from .injectors.base import TextInjector
from .injectors.errors import DeliveryDeferred
from .text_engine import TextEngine

LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class DictationResult:
    raw_text: str
    final_text: str
    language: str | None
    duration_seconds: float
    delivery: str = "not-requested"


class VoiceBridgeController:
    def __init__(self, *, recorder: AudioRecorder, asr: ASRProvider,
                 text_engine: TextEngine, injector: TextInjector,
                 mode: str = "dictation", language: str | None = "Russian",
                 min_audio_seconds: float = 0.12,
                 state_callback: Callable[[str, str], None] | None = None) -> None:
        self.recorder = recorder
        self.asr = asr
        self.text_engine = text_engine
        self.injector = injector
        self.mode = mode
        self.language = language
        self.min_audio_seconds = min_audio_seconds
        self._work_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._phase = "idle"
        self._cancelled = threading.Event()
        self._state_callback = state_callback
        self.last_result: DictationResult | None = None

    @property
    def busy(self) -> bool:
        with self._state_lock:
            return self._phase != "idle"

    def _emit_state(self, state: str, detail: str = "") -> None:
        if self._state_callback:
            try:
                self._state_callback(state, detail)
            except Exception:
                LOG.exception("State callback failed")

    def press(self) -> None:
        with self._state_lock:
            if self._phase != "idle" or self._work_lock.locked():
                LOG.warning("Ignoring trigger: dictation is %s", self._phase)
                return
            self._cancelled.clear()
            self._phase = "recording"
            try:
                self.recorder.start()
            except Exception:
                self._phase = "idle"
                raise
        LOG.info("Recording started")
        self._emit_state("recording")

    def _stop_capture(self):
        with self._state_lock:
            if self._phase != "recording":
                return None
            # Reserve processing *before* creating the worker thread. A second
            # press cannot start capture in the gap between release and ASR.
            self._phase = "processing"
            try:
                clip = self.recorder.stop()
            except Exception:
                self._phase = "idle"
                raise
        LOG.info("Recording stopped: %.2fs", clip.duration_seconds)
        self._emit_state("processing", f"{clip.duration_seconds:.2f}s")
        return clip

    def release(self) -> None:
        clip = self._stop_capture()
        if clip is not None:
            threading.Thread(target=self._process_clip, args=(clip,),
                             name="voice-bridge-transcribe", daemon=True).start()

    def release_sync(self) -> DictationResult | None:
        clip = self._stop_capture()
        return self._process_clip(clip) if clip is not None else None

    def process_once(self) -> DictationResult | None:
        return self._process_clip(self.recorder.stop())

    def cancel(self) -> None:
        """Cancel insertion, without pretending to interrupt a running model call."""
        self._cancelled.set()
        with self._state_lock:
            if self._phase == "recording":
                try:
                    self.recorder.stop()
                finally:
                    self._phase = "idle"
            self._emit_state("cancelled", "Dictation cancelled; pending recognition will not be pasted")
            if self._phase == "idle":
                self._emit_state("operation_idle")

    def _process_clip(self, clip) -> DictationResult | None:
        if not self._work_lock.acquire(blocking=False):
            LOG.warning("Dropping clip because ASR is busy")
            return None
        with self._state_lock:
            if self._phase == "idle":
                self._phase = "processing"
        try:
            if self._cancelled.is_set():
                self._emit_state("cancelled")
                return None
            if clip.duration_seconds < self.min_audio_seconds:
                self._emit_state("ready", "Audio clip too short")
                return None
            transcript = self.asr.transcribe(clip.samples, clip.sample_rate, self.language)
            LOG.info("Recognized %d characters", len(transcript.text))
            final = self.text_engine.process(transcript.text, mode=self.mode).text
            result = DictationResult(transcript.text, final, transcript.language, clip.duration_seconds)
            self.last_result = result
            # Preserve text in the UI even when clipboard/key delivery fails.
            self._emit_state("transcript", final)
            if self._cancelled.is_set():
                result.delivery = "cancelled"
                self._emit_state("cancelled", "Recognition finished; paste cancelled")
                return result
            if final:
                self._emit_state("injecting", str(len(final)))
                LOG.info("Injecting %d characters into the active application", len(final))
                result.delivery = self.injector.insert(final) or "application-unverified"
                self._emit_state("paste_requested", result.delivery)
            else:
                self._emit_state("ready", "No speech recognized")
            return result
        except DeliveryDeferred as exc:
            # The ASR result is valid. A withheld shortcut is not a model crash,
            # and it must not lock out the next recording or reload the model.
            if self.last_result and self.last_result.delivery == "not-requested":
                self.last_result.delivery = "deferred"
            LOG.info("Paste deferred: %s", exc)
            self._emit_state("delivery_deferred", str(exc))
            return self.last_result
        except Exception as exc:
            LOG.exception("Dictation failed")
            if self.last_result and self.last_result.delivery == "not-requested":
                self.last_result.delivery = "failed"
            self._emit_state("error", str(exc))
            return None
        finally:
            with self._state_lock:
                self._phase = "idle"
                self._work_lock.release()
                # Publish completion before another trigger may publish recording.
                # Cancelled ASR stays busy until the actual call has returned.
                self._emit_state("operation_idle")
