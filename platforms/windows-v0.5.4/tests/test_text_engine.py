from pathlib import Path
import tempfile

from voice_bridge.text_engine import TextEngine


def test_dictation_commands_and_capitalization():
    engine = TextEngine()
    result = engine.process("привет запятая как дела вопросительный знак новая строка все хорошо точка")
    assert result.text == "Привет, как дела?\nВсе хорошо."


def test_vocabulary_replacement_is_case_insensitive():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "vocab.txt"
        path.write_text("комфи ю ай = ComfyUI\nдавинчи резолв = DaVinci Resolve\n", encoding="utf-8")
        engine = TextEngine(vocabulary_file=path)
        result = engine.process("открой Комфи Ю Ай и давинчи резолв")
        assert result.text == "Открой ComfyUI и DaVinci Resolve"


def test_raw_mode_keeps_voice_commands_literal():
    engine = TextEngine()
    result = engine.process("слово точка новая строка", mode="raw")
    assert result.text == "слово точка новая строка"
