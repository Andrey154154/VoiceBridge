from __future__ import annotations

from typing import Protocol


class TextInjector(Protocol):
    def insert(self, text: str) -> str | None:
        ...

    def undo(self) -> None:
        ...
