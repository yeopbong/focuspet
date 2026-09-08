"""Transparent native sprite window with input-safe desktop interactions."""

from __future__ import annotations

from typing import cast

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication, QMouseEvent, QPainter, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from .assets import SpriteAtlas


def clamp_position(position: QPoint, size, screens: list[QRect]) -> QPoint:
    """Keep the whole sprite on one available screen, including after unplugging."""
    if not screens:
        return position
    frame = QRect(position, size)
    intersecting = [screen for screen in screens if screen.intersects(frame)]
    candidates = intersecting or screens
    screen = min(candidates, key=lambda rect: (rect.center() - frame.center()).manhattanLength())
    x = min(max(position.x(), screen.left()), max(screen.left(), screen.right() - size.width() + 1))
    y = min(max(position.y(), screen.top()), max(screen.top(), screen.bottom() - size.height() + 1))
    return QPoint(x, y)


class PetWidget(QWidget):
    clicked = Signal()
    double_clicked = Signal()
    context_requested = Signal(QPoint)
    moved = Signal(QPoint)
    interaction = Signal()

    def __init__(self, character="mira", scale=3):
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        super().__init__(None, flags)
        self.setObjectName("desktopCompanion")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.atlas = SpriteAtlas(character)
        self.scale = max(1, min(int(scale), 5))
        self.action_name = "idle"
        self._state_action = "idle"
        self._frame_index = 0
        self._dragging = False
        self._double = False
        self._pressed = False
        self._press_global = QPoint()
        self._press_window = QPoint()
        self.click_through = False
        self._high_load = False
        self._scaled_frames = {}
        self._single_timer = QTimer(self)
        self._single_timer.setSingleShot(True)
        self._single_timer.timeout.connect(self._single_click)
        self._animation = QTimer(self)
        self._animation.setSingleShot(True)
        self._animation.timeout.connect(self._advance)
        self._response_timer = QTimer(self)
        self._response_timer.setSingleShot(True)
        self._response_timer.timeout.connect(lambda: self.set_action(self._state_action))
        self._display_frame()
        self._animation.start(self.atlas.duration(self.action_name, 0))
        app = cast(QGuiApplication, QGuiApplication.instance())
        if app:
            app.screenAdded.connect(self._connect_screen)
            app.screenRemoved.connect(self.clamp_to_screens)
            for screen in app.screens():
                self._connect_screen(screen)

    def _connect_screen(self, screen):
        screen.availableGeometryChanged.connect(self.clamp_to_screens)
        screen.logicalDotsPerInchChanged.connect(self.clamp_to_screens)
        self.clamp_to_screens()

    def set_character(self, character: str):
        self.atlas = SpriteAtlas(character)
        self._scaled_frames.clear()
        self._frame_index = 0
        self._display_frame()

    def set_scale(self, scale: int):
        bottom = self.geometry().bottomRight()
        self.scale = max(1, min(int(scale), 5))
        self._display_frame()
        self.move(bottom - QPoint(self.width() - 1, self.height() - 1))
        self.clamp_to_screens()

    def set_always_on_top(self, value: bool):
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, value)
        if visible:
            self.show()

    def set_click_through(self, enabled: bool):
        visible = self.isVisible()
        self.click_through = enabled
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, enabled)
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, enabled)
        if visible:
            self.show()
        self._display_frame()

    def set_state(self, state: str, workload: float = 0, paused=False):
        if workload >= 80:
            self._high_load = True
        elif workload < 75:
            self._high_load = False
        action = {"Focused": "focus", "Normal": "normal", "Distracted": "distracted",
                  "Rest": "rest", "Unknown": "unknown", "Paused": "unknown"}.get(state, "idle")
        if paused:
            action = "unknown"
        elif self._high_load and state in ("Focused", "Normal", "Distracted"):
            action = "stretch"
        previous = self._state_action
        self._state_action = action
        if previous == "rest" and action not in ("rest", "unknown"):
            self.respond()
        elif not self._response_timer.isActive() or action in ("rest", "unknown"):
            self._response_timer.stop()
            self.set_action(action)

    def set_action(self, action: str):
        if action != self.action_name:
            self.action_name = action
            self._frame_index = 0
            self._display_frame()
            self._animation.start(self.atlas.duration(action, 0))

    def respond(self):
        self.set_action("recovered")
        self._response_timer.start(2400)

    def _advance(self):
        action = self.atlas.action(self.action_name)
        next_index = self._frame_index + 1
        if next_index >= len(action["frames"]):
            next_index = 0 if action.get("loop", True) else len(action["frames"]) - 1
        self._frame_index = next_index
        self._display_frame()
        self._animation.start(self.atlas.duration(self.action_name, self._frame_index))

    def _display_frame(self):
        key = (self.action_name, self._frame_index, self.scale)
        if key not in self._scaled_frames:
            pixmap = self.atlas.frame(self.action_name, self._frame_index).scaled(
                self.atlas.width * self.scale, self.atlas.height * self.scale,
                Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
            self._scaled_frames[key] = (pixmap, QRegion(pixmap.mask()))
        self._pixmap, mask = self._scaled_frames[key]
        self.setFixedSize(self._pixmap.size())
        # A native alpha mask excludes transparent space from the input region.
        # Full input transparency additionally uses the OS window input flag.
        self.setMask(mask)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.drawPixmap(0, 0, self._pixmap)

    def mousePressEvent(self, event: QMouseEvent):
        self.interaction.emit()
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self._dragging = False
            self._double = False
            self._press_global = event.globalPosition().toPoint()
            self._press_window = self.pos()
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self._single_timer.stop()
            self.context_requested.emit(event.globalPosition().toPoint())
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._pressed and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self._press_global
            if delta.manhattanLength() >= QApplication.startDragDistance():
                self._dragging = True
                self._single_timer.stop()
            if self._dragging:
                self.move(self._press_window + delta)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        # A compositor may compress motion events; release displacement still
        # makes this a drag and must never become a click.
        delta = event.globalPosition().toPoint() - self._press_global
        if self._pressed and delta.manhattanLength() >= QApplication.startDragDistance():
            self._dragging = True
            self._single_timer.stop()
            self.move(self._press_window + delta)
        if self._dragging:
            self.clamp_to_screens()
            self.moved.emit(self.pos())
        elif not self._double:
            self._single_timer.start(QApplication.doubleClickInterval() + 30)
        self._pressed = False
        self._dragging = False
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and not self._dragging:
            self._single_timer.stop()
            self._double = True
            self.interaction.emit()
            self.double_clicked.emit()
            event.accept()

    def _single_click(self):
        self.respond()
        self.clicked.emit()

    @Slot()
    def clamp_to_screens(self):
        screens = [screen.availableGeometry() for screen in QGuiApplication.screens()]
        position = clamp_position(self.pos(), self.size(), screens)
        if position != self.pos():
            self.move(position)
            self.moved.emit(position)

    def restore(self):
        self.set_click_through(False)
        self.clamp_to_screens()
        self.show()
