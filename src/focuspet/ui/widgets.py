"""Small native painted history views. All supplied values remain factual."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .strings import tr
from .theme import AMBER, MOSS, MUTED, STATE_COLORS


def label(text: str, role=None, wrap=False) -> QLabel:
    result = QLabel(text)
    if role:
        result.setProperty("role", role)
    result.setWordWrap(wrap)
    return result


def button(key, slot=None, primary=False) -> QPushButton:
    result = QPushButton(tr(key).replace("&", "&&"))
    result.setObjectName(key)
    if primary:
        result.setProperty("primary", True)
    if slot:
        result.clicked.connect(slot)
    return result


def card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setProperty("card", True)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(9)
    return frame, layout


def time_text(value, seconds=False):
    try:
        return datetime.fromtimestamp(float(value)).astimezone().strftime("%H:%M:%S" if seconds else "%H:%M")
    except (TypeError, ValueError, OSError):
        return tr("no_data")


def date_text(value):
    if not value:
        return tr("no_data")
    try:
        return datetime.fromtimestamp(float(value)).astimezone().strftime("%b %d, %H:%M")
    except (TypeError, ValueError, OSError):
        return str(value)


class WorkloadScale(QWidget):
    def __init__(self):
        super().__init__()
        self.value = 0.0
        self.stale = False
        self.setMinimumHeight(52)

    def set_value(self, value, stale=False):
        self.value, self.stale = float(value or 0), stale
        self.setToolTip(f"{tr('workload')}: {self.value:.2f}" + (f" · {tr('stale')}" if stale else ""))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, right, top = 13, self.width() - 20, 12
        width = right - left
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#e9ece2"))
        p.drawRoundedRect(QRectF(left, top, width, 7), 3, 3)
        ratio = max(0, min(1, (self.value + 10) / 140))
        p.setBrush(QColor(MUTED if self.stale else MOSS if self.value < 80 else AMBER))
        p.drawRoundedRect(QRectF(left, top, width * ratio, 7), 3, 3)
        p.setPen(QPen(QColor("#748177"), 1, Qt.PenStyle.DashLine))
        ref = left + 110 / 140 * width
        p.drawLine(int(ref), top - 5, int(ref), top + 13)
        p.setPen(QColor(MUTED))
        f = p.font()
        f.setPixelSize(9)
        p.setFont(f)
        for value in (-10, 0, 20, 40, 60, 80, 100, 120):
            x = left + (value + 10) / 140 * width
            p.drawText(QRectF(x - 14, top + 16, 28, 15), Qt.AlignmentFlag.AlignCenter, str(value))
        p.drawText(QRectF(right - 16, top + 16, 40, 15), Qt.AlignmentFlag.AlignCenter, "120+")


class Timeline(QWidget):
    segment_selected = Signal(dict)

    def __init__(self):
        super().__init__()
        self.rows = []
        self._rects = []
        self.setMinimumHeight(72)
        self.setMouseTracking(True)

    def set_rows(self, rows):
        self.rows = sorted(rows, key=lambda row: row.get("start", 0))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        self._rects = []
        if not self.rows:
            p.setPen(QColor(MUTED))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, tr("empty_history"))
            return
        start = self.rows[0].get("start", 0)
        end = max(row.get("end", start + 1) for row in self.rows)
        duration = max(1, end - start)
        for row in self.rows:
            x = 4 + (row.get("start", start) - start) / duration * (self.width() - 8)
            width = max(2, (row.get("end", start) - row.get("start", start)) / duration * (self.width() - 8))
            rect = QRectF(x, 9, width, 27)
            p.fillRect(rect, QColor(STATE_COLORS.get(row.get("state"), "#d4d8d0")))
            self._rects.append((rect, row))
        p.setPen(QColor(MUTED))
        for i in range(5):
            x = 4 + i * max(0, self.width() - 78) / 4
            p.drawText(QRectF(x, 45, 70, 20), Qt.AlignmentFlag.AlignLeft,
                       time_text(start + duration * i / 4, seconds=duration < 300))

    def mousePressEvent(self, event):
        for rect, row in self._rects:
            if rect.contains(event.position()):
                self.segment_selected.emit(row)
                return

    def mouseMoveEvent(self, event):
        for rect, row in self._rects:
            if rect.contains(event.position()):
                self.setToolTip(f"{time_text(row.get('start'), True)}–{time_text(row.get('end'), True)} · {row.get('state', 'Unknown')}")
                return
        self.setToolTip("")


class RhythmPlot(QWidget):
    """Two independent scales, real elapsed-time x positions, explicit data gaps."""

    def __init__(self):
        super().__init__()
        self.rows = []
        self.setMinimumHeight(200)

    def set_rows(self, rows):
        self.rows = sorted(rows, key=lambda row: row.get("end", 0))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(37, 17, max(1, self.width() - 84), self.height() - 51)
        p.setPen(QPen(QColor("#e2e6dd"), 1))
        for i in range(5):
            y = area.top() + area.height() * i / 4
            p.drawLine(int(area.left()), int(y), int(area.right()), int(y))
            p.setPen(QColor(MUTED))
            p.drawText(QRectF(0, y - 8, 29, 16), Qt.AlignmentFlag.AlignRight, str(100 - i * 25))
            p.setPen(QPen(QColor("#e2e6dd"), 1))
        if not self.rows:
            p.setPen(QColor(MUTED))
            p.drawText(area, Qt.AlignmentFlag.AlignCenter, tr("empty_history"))
            return
        start = self.rows[0].get("start", 0)
        end = self.rows[-1].get("end", start + 1)
        duration = max(1, end - start)
        max_load = max(130, max(float(row.get("workload") or 0) for row in self.rows))
        for i in range(5):
            x = area.left() + i / 4 * area.width()
            p.setPen(QColor(MUTED))
            p.drawText(QRectF(x - 35, area.bottom() + 8, 70, 20), Qt.AlignmentFlag.AlignCenter,
                       time_text(start + i / 4 * duration, seconds=duration < 300))
            value = max_load - (max_load + 10) * i / 4
            p.drawText(QRectF(area.right() + 6, area.top() + area.height() * i / 4 - 8, 42, 16),
                       Qt.AlignmentFlag.AlignLeft, str(round(value)))
        # The 100 Work Load reference is distinct from the left Focus scale.
        ref_y = area.bottom() - 110 / (max_load + 10) * area.height()
        p.setPen(QPen(QColor("#d0b58c"), 1, Qt.PenStyle.DashLine))
        p.drawLine(int(area.left()), int(ref_y), int(area.right()), int(ref_y))
        for key, color in (("focus", MOSS), ("workload", AMBER)):
            path = QPainterPath()
            previous_end = None
            open_path = False
            for row in self.rows:
                value = row.get(key)
                timestamp = row.get("end", start)
                missing = value is None or (key == "workload" and row.get("workload_stale", False))
                if missing:
                    open_path = False
                    previous_end = timestamp
                    continue
                x = area.left() + (timestamp - start) / duration * area.width()
                ratio = float(value) / 100 if key == "focus" else (float(value) + 10) / (max_load + 10)
                y = area.bottom() - max(0, min(1, ratio)) * area.height()
                gap = previous_end is not None and row.get("start", timestamp) - previous_end > 45
                if not open_path or gap:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(color))
                p.drawEllipse(QRectF(x - 1.5, y - 1.5, 3, 3))
                open_path = True
                previous_end = timestamp
            p.setPen(QPen(QColor(color), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)


def legend() -> QWidget:
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    for state, color in STATE_COLORS.items():
        item = label(f"● {state}", "subtitle")
        item.setStyleSheet(f"color: {color}")
        layout.addWidget(item)
    layout.addStretch()
    return widget


def plot_legend() -> QWidget:
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(18)
    for text, color in zip(tr("plot_legend").split("      "), (MOSS, AMBER, MUTED)):
        item = label(text, "subtitle")
        item.setStyleSheet(f"color: {color}")
        layout.addWidget(item)
    layout.addStretch()
    return widget
