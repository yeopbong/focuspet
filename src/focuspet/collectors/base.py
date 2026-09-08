from __future__ import annotations

from typing import Protocol
import re

from focuspet.domain import ActivityBucket, CATEGORIES


def validate_categories(value: dict) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("Application categories must be a bundle identifier mapping")
    result = {}
    for identifier, category in value.items():
        if (
            not isinstance(identifier, str)
            or len(identifier) > 255
            or re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", identifier) is None
            or category not in CATEGORIES
        ):
            raise ValueError("Use a bundle identifier and one of the documented categories")
        result[identifier] = category
    return result


class Collector(Protocol):
    def start(self, request_permission: bool = False) -> None: ...
    def stop(self) -> None: ...
    def pause(self, paused: bool = True) -> None: ...
    def sample(self) -> ActivityBucket | None: ...
    def capabilities(self) -> dict[str, str]: ...


class UnsupportedCollector:

    def __init__(self, **kwargs):
        self.paused = True

    def start(self, request_permission=False):
        self.paused = False

    def stop(self):
        self.paused = True

    def pause(self, paused=True):
        self.paused = paused

    def sample(self):
        return None

    def capabilities(self):
        return {
            name: "unavailable"
            for name in ("keyboard", "clicks", "scroll", "pointer", "idle", "application", "lock", "sleep")
        }
