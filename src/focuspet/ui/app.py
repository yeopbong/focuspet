from __future__ import annotations

from pathlib import Path
from typing import cast

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox, QSystemTrayIcon, QVBoxLayout

from .assets import SpriteAtlas
from .dialogs import Dashboard, Onboarding, Settings, StatusCard
from .pet import PetWidget
from .strings import tr
from .theme import STYLE
from .widgets import button, label


class PassiveBubble(QLabel):

    def __init__(self):
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWordWrap(True)
        self.setMaximumWidth(250)
        self.setStyleSheet("QLabel { background:#fffef9; color:#293d35; border:1px solid #d9ddd3; border-radius:9px; padding:12px; }")
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)

    def present(self, text, pet):
        self.setText(str(text))
        self.adjustSize()
        available = pet.screen().availableGeometry()
        x = max(available.left(), min(pet.x() - self.width() + 35, available.right() - self.width()))
        y = max(available.top(), min(pet.y() - self.height(), available.bottom() - self.height()))
        self.move(x, y)
        self.show()
        self.timer.start(6000)


class AppController(QObject):
    def __init__(self, service, app=None):
        super().__init__()
        self.service = service
        self.app = cast(QApplication, app or QApplication.instance())
        self.app.setQuitOnLastWindowClosed(False)
        self._closed = False
        self._startup_checked = False
        self._applied = {}
        self._last_status = None
        self._settings_window = None
        self._onboarding = None
        self._gap_dialog = None
        self.pet = PetWidget()
        self.bubble = PassiveBubble()
        self.status = StatusCard(service, self.open_dashboard, self.open_settings)
        self.dashboard = Dashboard(service)
        self.tray = QSystemTrayIcon(QIcon(SpriteAtlas().thumbnail()), self)
        self.tray.setToolTip(tr("app"))
        self.menu = self._build_menu(tray=True)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()
        self.pet.clicked.connect(lambda: self.bubble.present(tr("reply"), self.pet))
        self.pet.double_clicked.connect(self.open_status)
        self.pet.context_requested.connect(self._show_pet_menu)
        self.pet.interaction.connect(lambda: self.service.command("interaction"))
        self.pet.moved.connect(lambda point: self.service.command("settings", values={"position": [point.x(), point.y()]}))
        self.app.installEventFilter(self)
        self.app.aboutToQuit.connect(self.shutdown)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()
        QTimer.singleShot(250, self._startup)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress, QEvent.Type.Wheel):
            self.service.command("interaction")
        return False

    def _startup(self):
        if self._closed or self._startup_checked:
            return
        snapshot = self.service.snapshot()
        if snapshot.get("status") == "Starting":
            QTimer.singleShot(250, self._startup)
            return
        self._startup_checked = True
        self.apply_settings(snapshot.get("settings", {}))
        self.pet.show()
        self.pet.clamp_to_screens()
        if snapshot.get("mode") == "real" and not snapshot.get("consent"):
            self.open_onboarding()
        elif snapshot.get("restart_gap"):
            self._show_restart_gap()

    def _build_menu(self, tray=False):
        menu = QMenu()
        menu.addAction(tr("status"), self.open_status)
        menu.addAction(tr("dashboard"), self.open_dashboard)
        menu.addSeparator()
        menu.addAction(tr("start_rest"), lambda: self.service.command("rest", minutes=5))
        menu.addAction(tr("end_rest"), lambda: self.service.command("end_rest"))
        snooze = menu.addMenu(tr("snooze"))
        for minutes in (5, 15, 30):
            snooze.addAction(f"{minutes} {tr('minutes')}",
                             lambda checked=False, value=minutes: self.service.command("snooze", minutes=value))
        quiet = menu.addAction(tr("quiet"))
        quiet.setObjectName("quiet_action")
        quiet.setCheckable(True)
        quiet.setChecked(bool(self.service.snapshot().get("settings", {}).get("quiet")))
        quiet.triggered.connect(lambda checked: self.service.command("settings", values={"quiet": checked}))
        menu.addSeparator()
        menu.addAction(tr("pause"), lambda: self.service.command("pause"))
        menu.addAction(tr("resume"), lambda: self.service.command("resume"))
        menu.addAction(tr("reset_cycle"), self._reset_cycle)
        menu.addAction(tr("choose"), self.open_settings)
        menu.addAction(tr("settings"), self.open_settings)
        menu.addSeparator()
        menu.addAction(tr("restore"), self.restore_pet)
        menu.addAction(tr("disable_through"), self.disable_click_through)
        if not tray:
            hide = menu.addAction(tr("hide"), self.pet.hide)
            hide.setEnabled(QSystemTrayIcon.isSystemTrayAvailable())
        menu.addSeparator()
        menu.addAction(tr("quit"), self.app.quit)
        return menu

    def _show_pet_menu(self, point):
        menu = self._build_menu()
        menu.exec(point)
        menu.deleteLater()

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.open_status()

    def _show(self, window):
        window.show()
        window.raise_()
        window.activateWindow()

    def open_status(self):
        self.status.refresh(self.service.snapshot())
        self._show(self.status)

    def open_dashboard(self):
        self.dashboard.refresh(self.service.snapshot())
        self._show(self.dashboard)

    def open_settings(self):
        if self._settings_window is not None:
            self._settings_window.close()
            self._settings_window.deleteLater()
        self._settings_window = Settings(self.service, self.apply_settings)
        self._show(self._settings_window)

    def open_onboarding(self):
        if self._onboarding is None:
            self._onboarding = Onboarding(self.service)
        self._show(self._onboarding)

    def restore_pet(self):
        self.disable_click_through()
        self.pet.restore()

    def disable_click_through(self):
        self.pet.set_click_through(False)
        self._applied["click_through"] = False
        self.service.command("settings", values={"click_through": False})

    def apply_settings(self, settings):
        character = settings.get("character", "mira")
        if character != self._applied.get("character"):
            self.pet.set_character(character)
            self.tray.setIcon(QIcon(self.pet.atlas.thumbnail()))
        scale = int(settings.get("scale", 3))
        if scale != self._applied.get("scale"):
            self.pet.set_scale(scale)
        top = bool(settings.get("always_on_top", True))
        if top != self._applied.get("always_on_top"):
            self.pet.set_always_on_top(top)
        through = bool(settings.get("click_through", False))
        if through != self._applied.get("click_through"):
            if through and not QSystemTrayIcon.isSystemTrayAvailable():
                through = False
                self.service.command("settings", values={"click_through": False})
                self.bubble.present(tr("through_unavailable"), self.pet)
            self.pet.set_click_through(through)
        if "position" not in self._applied:
            position = settings.get("position")
            if position and len(position) == 2:
                self.pet.move(int(position[0]), int(position[1]))
            else:
                rect = QGuiApplication.primaryScreen().availableGeometry()
                self.pet.move(rect.right() - self.pet.width() - 38, rect.bottom() - self.pet.height() - 22)
            self.pet.clamp_to_screens()
        self._applied.update(settings)
        self._applied["click_through"] = through

    def _reset_cycle(self):
        answer = QMessageBox.question(self.status,
                                      tr("reset_cycle"), tr("reset_note"))
        if answer == QMessageBox.StandardButton.Yes:
            self.service.command("reset_cycle")

    def _show_restart_gap(self):
        dialog = QDialog()
        dialog.setWindowTitle(tr("app"))
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(tr("restart_title"), "section"))
        layout.addWidget(label(tr("restart_note"), "subtitle", True))
        actions = QHBoxLayout()
        def choose(command):
            self.service.command(command)
            dialog.accept()
        actions.addWidget(button("continue_cycle", lambda: choose("continue_cycle")))
        def confirm_rest():
            minutes, accepted = QInputDialog.getInt(dialog, tr("confirm_recent_rest"), tr("confirm_recent_rest_note"),
                                                    5, 1, 120)
            if accepted:
                self.service.command("confirm_recent_rest", minutes=minutes)
                dialog.accept()
        actions.addWidget(button("confirm_recent_rest", confirm_rest))
        actions.addWidget(button("reset_cycle", lambda: choose("reset_cycle")))
        layout.addLayout(actions)
        self._gap_dialog = dialog
        self._show(dialog)

    def refresh(self):
        snapshot = self.service.snapshot()
        self.apply_settings(snapshot.get("settings", {}))
        for action in self.menu.actions():
            if action.objectName() == "quiet_action":
                action.setChecked(bool(snapshot.get("settings", {}).get("quiet")))
        self.pet.set_state(snapshot.get("state", "Unknown"), float(snapshot.get("workload") or 0),
                           snapshot.get("observed_state") == "Paused")
        if self.status.isVisible():
            self.status.refresh(snapshot)
        if self.dashboard.isVisible():
            self.dashboard.refresh(snapshot)
        if self._settings_window and self._settings_window.isVisible():
            self._settings_window.refresh(snapshot)
        for event in self.service.drain_events():
            text = event.get("text") or event.get("message")
            if text:
                self.bubble.present(text, self.pet)

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self.timer.stop()
        self.pet._animation.stop()
        self.bubble.close()
        self.pet.close()
        self.status.close()
        self.dashboard.close()
        if self._settings_window:
            self._settings_window.close()
        if self._onboarding:
            self._onboarding.close()
        self.tray.hide()
        self.app.removeEventFilter(self)
        self.service.close()


def run_app(mode="real", data_dir=None, service=None, scenario="workday", quit_after=None, screenshot=None):
    from focuspet.service import AppService

    app = cast(QApplication, QApplication.instance() or QApplication([]))
    app.setApplicationName(tr("app"))
    app.setOrganizationName("Focus Pet")
    app.setStyleSheet(STYLE)
    coordinator = service or AppService(mode=mode, data_dir=data_dir, scenario=scenario)
    controller = AppController(coordinator, app)
    if screenshot:
        path = Path(screenshot)
        def capture():
            path.parent.mkdir(parents=True, exist_ok=True)
            controller.open_status()
            controller.status.grab().save(str(path))
            controller.open_dashboard()
            controller.dashboard.grab().save(str(path.with_name(path.stem + "-dashboard.png")))
        delay = max(500, int((float(quit_after) - 1) * 1000)) if quit_after else 4500
        QTimer.singleShot(delay, capture)
    if quit_after:
        QTimer.singleShot(max(100, int(float(quit_after) * 1000)), app.quit)
    result = app.exec()
    controller.shutdown()
    return result
