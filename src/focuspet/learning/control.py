"""Lightweight cancellation primitives shared by training and optimization workers."""
from __future__ import annotations

import threading


class TrainingCancelled(Exception):
    """User cancellation; no partial candidate may be activated."""


def check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise TrainingCancelled("Operation cancelled")
