"""Elapsed time and UTC time are intentionally separate clock readings."""

from __future__ import annotations
import time
from typing import Protocol


class Clock(Protocol):
    def utc(self) -> float: ...
    def monotonic(self) -> float: ...


class SystemClock:
    def utc(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()


class FakeClock:
    def __init__(self, utc: float = 0, monotonic: float = 0):
        self.wall = utc
        self.elapsed = monotonic

    def utc(self) -> float:
        return self.wall

    def monotonic(self) -> float:
        return self.elapsed

    def advance(self, seconds: float, wall_seconds: float | None = None) -> None:
        if seconds < 0:
            raise ValueError("Monotonic time cannot run backwards")
        self.elapsed += seconds
        self.wall += seconds if wall_seconds is None else wall_seconds
