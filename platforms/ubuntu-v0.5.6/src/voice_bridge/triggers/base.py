from __future__ import annotations

from typing import Callable, Protocol


class HoldTrigger(Protocol):
    def run(self, on_press: Callable[[], None], on_release: Callable[[], None]) -> None:
        ...
