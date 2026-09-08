"""Manifest-driven sprite loading with safe action and character fallbacks."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QRect
from PySide6.QtGui import QPixmap

ASSET_ROOT = Path(__file__).resolve().parents[1] / "assets" / "characters"
ACTIONS = ("idle", "focus", "normal", "distracted", "stretch", "rest", "recovered", "unknown")


def character_manifests() -> list[dict]:
    result = []
    for path in sorted(ASSET_ROOT.glob("*/manifest.json")):
        try:
            item = json.loads(path.read_text())
            item["directory"] = str(path.parent)
            result.append(item)
        except (OSError, ValueError):
            continue
    return result


class SpriteAtlas:
    def __init__(self, character: str = "mira"):
        manifests = character_manifests()
        if not manifests:
            raise RuntimeError("The packaged companion sprite assets are missing.")
        self.manifest = next((m for m in manifests if m["id"] == character), manifests[0])
        self.character = self.manifest["id"]
        self.root = Path(self.manifest["directory"])
        self.sheet = QPixmap(str(self.root / self.manifest["sheet"]))
        self.width, self.height = self.manifest["frame_size"]
        self.columns = self.manifest.get("columns", self.sheet.width() // self.width)
        self._frames: dict[int, QPixmap] = {}

    def action(self, name: str) -> dict:
        actions = self.manifest["actions"]
        return actions.get(name, actions.get("idle", next(iter(actions.values()))))

    def frame(self, action: str = "idle", index: int = 0) -> QPixmap:
        frames = self.action(action)["frames"]
        number = frames[index % len(frames)]
        if number not in self._frames:
            self._frames[number] = self.sheet.copy(QRect(number % self.columns * self.width,
                                                       number // self.columns * self.height,
                                                       self.width, self.height))
        return self._frames[number]

    def duration(self, action: str, index: int) -> int:
        durations = self.action(action).get("durations_ms", [400])
        return max(50, int(durations[index % len(durations)]))

    def thumbnail(self) -> QPixmap:
        thumb = QPixmap(str(self.root / self.manifest["thumbnail"]))
        return thumb if not thumb.isNull() else self.frame()
