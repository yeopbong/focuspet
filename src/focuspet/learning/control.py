from __future__ import annotations

import threading


class TrainingCancelled(Exception):
    pass


def check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise TrainingCancelled("Operation cancelled")
