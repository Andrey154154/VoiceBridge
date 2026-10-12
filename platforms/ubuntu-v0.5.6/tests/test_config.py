from pathlib import Path
import tempfile

from voice_bridge.config import load_config


def test_load_minimal_config():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "config.toml"
        path.write_text("[app]\nmode='raw'\n[audio]\ninput_device=''\n", encoding="utf-8")
        cfg = load_config(path)
        assert cfg.app.mode == "raw"
        assert cfg.audio.input_device is None
        assert cfg.asr.model.startswith("Qwen/")
