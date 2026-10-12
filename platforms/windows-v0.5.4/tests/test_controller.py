import numpy as np

from voice_bridge.asr.fake import FakeASRProvider
from voice_bridge.audio import AudioClip
from voice_bridge.controller import VoiceBridgeController
from voice_bridge.text_engine import TextEngine


class FakeRecorder:
    def __init__(self):
        self.is_recording = False
        self.clip = AudioClip(np.ones(16000, dtype=np.float32), 16000)

    def start(self):
        self.is_recording = True

    def stop(self):
        self.is_recording = False
        return self.clip


class FakeInjector:
    def __init__(self):
        self.values = []

    def insert(self, text):
        self.values.append(text)

    def undo(self):
        pass


def test_controller_pipeline():
    recorder = FakeRecorder()
    asr = FakeASRProvider("привет запятая мир точка")
    injector = FakeInjector()
    controller = VoiceBridgeController(
        recorder=recorder,
        asr=asr,
        text_engine=TextEngine(),
        injector=injector,
    )
    result = controller._process_clip(recorder.clip)
    assert result is not None
    assert result.final_text == "Привет, мир."
    assert injector.values == ["Привет, мир."]


def test_release_sync_processes_before_return():
    recorder = FakeRecorder()
    asr = FakeASRProvider("готово точка")
    injector = FakeInjector()
    controller = VoiceBridgeController(
        recorder=recorder,
        asr=asr,
        text_engine=TextEngine(),
        injector=injector,
    )
    controller.press()
    result = controller.release_sync()
    assert result is not None
    assert result.final_text == "Готово."
    assert injector.values == ["Готово."]
