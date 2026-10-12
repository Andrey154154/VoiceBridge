import io
import wave

import numpy as np

from voice_bridge.api import decode_pcm_wav


def test_decode_pcm16_wav():
    samples = np.array([0, 16384, -16384], dtype="<i2")
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(samples.tobytes())
    decoded, sr = decode_pcm_wav(out.getvalue())
    assert sr == 16000
    assert np.allclose(decoded, np.array([0.0, 0.5, -0.5], dtype=np.float32))
