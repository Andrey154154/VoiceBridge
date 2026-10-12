from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import logging

LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class TextEngineResult:
    text: str


VOICE_COMMANDS: tuple[tuple[str, str], ...] = (
    ("новый абзац", "\n\n"),
    ("новая строка", "\n"),
    ("вопросительный знак", "?"),
    ("восклицательный знак", "!"),
    ("двоеточие", ":"),
    ("точка с запятой", ";"),
    ("запятая", ","),
    ("точка", "."),
)


class TextEngine:
    def __init__(self, *, vocabulary_file: str | Path | None = None, capitalize_sentences: bool = True) -> None:
        self.capitalize_sentences = capitalize_sentences
        self.vocabulary_warning: str | None = None
        self.vocabulary = self._load_vocabulary(vocabulary_file)

    def _warn_vocabulary(self, path: Path, reason: str) -> list[tuple[str, str]]:
        self.vocabulary_warning = (
            f"Словарь замен не подключён: {reason}. Файл: {path}. "
            "Диктовка работает без словаря. Выберите текстовый файл в настройках "
            "или очистите поле «Файл словаря»."
        )
        LOG.warning("%s", self.vocabulary_warning)
        return []

    def _load_vocabulary(self, path: str | Path | None) -> list[tuple[str, str]]:
        if path is None or (isinstance(path, str) and not path.strip()):
            return []
        file_path = Path(path).expanduser()
        try:
            # On Windows read_text on a directory raises PermissionError rather
            # than IsADirectoryError. This resource is optional, not an ASR gate.
            if not file_path.is_file():
                return self._warn_vocabulary(file_path, "путь не является существующим файлом")
            # Windows editors commonly save UTF-8 with BOM. Consume it so the
            # first spoken phrase has no hidden U+FEFF character.
            contents = file_path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError, ValueError) as exc:
            return self._warn_vocabulary(file_path, f"не удалось прочитать UTF-8 ({exc})")
        pairs: list[tuple[str, str]] = []
        for raw in contents.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            left, right = line.split("=", 1)
            spoken, written = left.strip(), right.strip()
            if spoken and written:
                pairs.append((spoken, written))
        pairs.sort(key=lambda item: len(item[0]), reverse=True)
        return pairs

    @staticmethod
    def _replace_phrase(text: str, phrase: str, replacement: str) -> str:
        pattern = re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)", re.IGNORECASE)
        return pattern.sub(lambda _: replacement, text)

    def process(self, text: str, *, mode: str = "dictation") -> TextEngineResult:
        value = text.strip()
        if not value:
            return TextEngineResult("")

        for spoken, written in self.vocabulary:
            value = self._replace_phrase(value, spoken, written)

        if mode in {"dictation", "smart"}:
            for spoken, symbol in VOICE_COMMANDS:
                value = self._replace_phrase(value, spoken, symbol)
            value = self._cleanup_punctuation(value)
            if self.capitalize_sentences:
                value = self._capitalize(value)

        # SMART is intentionally conservative in v0.1: no LLM rewrite yet.
        return TextEngineResult(value.strip())

    @staticmethod
    def _cleanup_punctuation(text: str) -> str:
        text = re.sub(r"[ \t]+([,.;:!?])", r"\1", text)
        text = re.sub(r"([,;:])(?=\S)", r"\1 ", text)
        text = re.sub(r"([.!?])(?=[^\s\n])", r"\1 ", text)
        text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r" {2,}", " ", text)
        return text

    @staticmethod
    def _capitalize(text: str) -> str:
        chars = list(text)
        should_cap = True
        for idx, ch in enumerate(chars):
            if ch.isalpha() and should_cap:
                chars[idx] = ch.upper()
                should_cap = False
            elif ch in ".!?\n":
                should_cap = True
            elif not ch.isspace():
                should_cap = False
        return "".join(chars)
