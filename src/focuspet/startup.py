"""Explicit opt-in login agent. No startup entries are written by default."""

from __future__ import annotations
import plistlib
import sys
from pathlib import Path


def set_startup(enabled: bool):
    if sys.platform != "darwin":
        raise RuntimeError("Login startup is currently implemented only on macOS")
    location = Path.home() / "Library" / "LaunchAgents" / "app.focuspet.companion.plist"
    if not enabled:
        location.unlink(missing_ok=True)
        return
    args = (
        [sys.executable, "run"]
        if getattr(sys, "frozen", False)
        else [sys.executable, "-m", "focuspet", "run"]
    )
    location.parent.mkdir(parents=True, exist_ok=True)
    with location.open("wb") as stream:
        plistlib.dump(
            {
                "Label": "app.focuspet.companion",
                "ProgramArguments": args,
                "RunAtLoad": True,
                "ProcessType": "Interactive",
                "KeepAlive": False,
            },
            stream,
        )
