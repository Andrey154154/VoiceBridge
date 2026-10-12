"""Compare actual engine inputs, not presentation/download preferences.

Kept Qt-free so the policy can be tested with the worker independently of UI.
"""
from __future__ import annotations
from ..config import Config


def change_kind(old: Config, new: Config) -> str:
    """none: preferences only; live: no ASR reload; model: release model process."""
    if old.asr != new.asr:
        return 'model'
    if (old.audio != new.audio or old.text != new.text or
            old.trigger != new.trigger or old.injector != new.injector or
            (old.app.mode, old.app.language, old.app.paste_target) !=
            (new.app.mode, new.app.language, new.app.paste_target)):
        return 'live'
    return 'none'
