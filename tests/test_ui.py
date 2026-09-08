from __future__ import annotations

import copy
import time

import pytest
from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from focuspet.ui.assets import ACTIONS, SpriteAtlas, character_manifests
from focuspet.ui.dialogs import Dashboard, FeedbackDialog, Onboarding, Settings, StatusCard
from focuspet.ui.pet import PetWidget, clamp_position
from focuspet.ui.theme import STYLE


class MemoryService:
    def __init__(self):
        now = time.time()
        self.calls = []
        self.value = {"mode": "synthetic-demo", "consent": False, "state": "Normal", "observed_state": "Active",
                      "focus": 57., "workload": 81.4, "workload_stale": False, "source": "Generic rule prior",
                      "evidence": ["Stable application category with modest switching.", "Input is a weak activity signal."],
                      "coverage": .85, "uncertainty": .31, "query": None, "status": "Ready",
                      "settings": {"character": "mira", "profile": "Mixed", "scale": 2},
                      "permissions": {"keyboard": "denied", "application_category": "granted"},
                      "history": [{"id": "past", "start": now-60, "end": now-30, "state": "Normal", "focus": 57.,
                                   "workload": 81.4, "source": "Generic rule prior"}],
                      "feedback": [], "learning": {"episodes": 0, "days": 0, "sessions": 0,
                                   "version": "generic-v1", "parameter_version": "defaults-v1", "last_training": None,
                                   "validation": "Evidence insufficient", "missing": ["30 valid independent feedback episodes"]}}

    def snapshot(self):
        return copy.deepcopy(self.value)

    def command(self, name, **kwargs):
        self.calls.append((name, kwargs))

    def drain_events(self):
        return []

    def close(self):
        self.calls.append(("close", {}))


@pytest.fixture
def service(qapp):
    qapp.setStyleSheet(STYLE)
    return MemoryService()


def test_four_original_atlases_have_real_frames_and_alpha(qapp):
    manifests = character_manifests()
    assert len(manifests) == 4
    for manifest in manifests:
        atlas = SpriteAtlas(manifest["id"])
        assert atlas.sheet.size() == QSize(256, 640)
        assert set(ACTIONS) <= set(manifest["actions"])
        assert atlas.thumbnail().hasAlphaChannel()
        for action in ACTIONS:
            frames = [atlas.frame(action, i).toImage() for i in range(4)]
            assert len({frame.cacheKey() for frame in frames}) >= 2
            assert all(not frame.isNull() for frame in frames)
        assert atlas.frame("missing_action").size() == QSize(64, 80)


def test_clamp_handles_removed_display_and_negative_origins():
    screens = [QRect(-1920, 0, 1920, 1080), QRect(0, 0, 1440, 900)]
    assert clamp_position(QPoint(-3000, 1500), QSize(192, 240), screens) == QPoint(-1920, 840)
    assert clamp_position(QPoint(1400, 880), QSize(192, 240), screens) == QPoint(1248, 660)
    assert clamp_position(QPoint(200, 200), QSize(192, 240), []) == QPoint(200, 200)


def test_pet_is_nonactivating_and_transparent_space_not_hit(qtbot):
    pet = PetWidget(scale=2)
    qtbot.addWidget(pet)
    pet.show()
    assert pet.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert pet.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert pet.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert not pet.mask().contains(QPoint(0, 0))
    assert not pet.mask().isEmpty()
    pet.set_click_through(True)
    assert pet.windowFlags() & Qt.WindowType.WindowTransparentForInput
    pet.restore()
    assert not pet.click_through
    assert not pet.windowFlags() & Qt.WindowType.WindowTransparentForInput


def test_drag_does_not_emit_click_or_doubleclick(qtbot):
    pet = PetWidget(scale=2)
    qtbot.addWidget(pet)
    pet.show()
    emitted = []
    pet.clicked.connect(lambda: emitted.append("click"))
    pet.double_clicked.connect(lambda: emitted.append("double"))
    local = QPointF(64, 60)
    global_start = QPointF(pet.mapToGlobal(local.toPoint()))
    press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, local, global_start,
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(pet, press)
    move = QMouseEvent(QMouseEvent.Type.MouseMove, local + QPointF(55, 10), global_start + QPointF(55, 10),
                       Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(pet, move)
    release = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, local, global_start + QPointF(55, 10),
                          Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(pet, release)
    qtbot.wait(QApplication.doubleClickInterval() + 80)
    assert emitted == []


def test_rest_and_pause_override_animation_immediately(qtbot):
    pet = PetWidget()
    qtbot.addWidget(pet)
    pet.set_state("Focused", 82)
    assert pet.action_name == "stretch"
    pet.set_state("Normal", 77)
    assert pet.action_name == "stretch"
    pet.set_state("Normal", 74)
    assert pet.action_name == "normal"
    pet.respond()
    pet.set_state("Rest", 70)
    assert pet.action_name == "rest"
    pet.set_state("Focused", 90, paused=True)
    assert pet.action_name == "unknown"


def test_onboarding_never_collects_without_explicit_consent(qtbot, service):
    window = Onboarding(service)
    qtbot.addWidget(window)
    assert not window.consent.isChecked()
    assert not window.start_button.isEnabled()
    window._start()
    assert service.calls == []
    window._demo()
    assert [name for name, kwargs in service.calls] == ["demo", "settings"]
    assert not any(name == "consent" for name, _ in service.calls)


def test_explicit_consent_carries_profile_and_character(qtbot, service):
    window = Onboarding(service)
    qtbot.addWidget(window)
    window.profile.setCurrentText("Research / Reading")
    window.characters._buttons["ada"].setChecked(True)
    window.consent.setChecked(True)
    window._start()
    assert service.calls[-1] == ("consent", {"allowed": True, "profile": "Research / Reading", "character": "ada"})


def test_feedback_uses_target_interval_and_revision(qtbot, service):
    item = {"id": "feedback-1", "start": 123., "end": 423., "label": "Normal"}
    window = FeedbackDialog(service, segment=item, revision=item)
    qtbot.addWidget(window)
    window.labels.setCurrentText("Focused")
    window.save()
    name, args = service.calls[-1]
    assert name == "feedback"
    assert args["start"] == 123 and args["end"] == 423
    assert args["revision_of"] == "feedback-1" and args["label"] == "Focused"


def test_timeline_correction_anchors_ended_segment_and_covers_features(qtbot, service):
    window = FeedbackDialog(service, segment={"start": 470., "end": 500.})
    qtbot.addWidget(window)
    window.labels.setCurrentText("Normal")
    window.save()
    assert service.calls[-1][1]["start"] == 200
    assert service.calls[-1][1]["end"] == 500


def test_status_separates_uncertainty_and_hides_estimate_for_audit(qtbot, service):
    window = StatusCard(service, lambda: None, lambda: None)
    qtbot.addWidget(window)
    window.show()
    window.refresh(service.snapshot())
    assert window.focus.text() == "57"
    assert "85%" in window.quality.text() and "31%" in window.quality.text()
    service.value["query"] = {"id": "audit-1", "kind": "audit", "target_start": 100, "target_end": 200}
    window.refresh(service.snapshot())
    assert window.estimate.isHidden()
    assert not window.query_card.isHidden()
    window.answer_query("Normal")
    assert service.calls[-1] == ("query_answer", {"query_id": "audit-1", "label": "Normal"})
    service.value["query"] = None
    service.value["focus"] = None
    service.value["workload"] = 132.4
    window.refresh(service.snapshot())
    assert window.focus.text() == "—" and window.workload.text() == "120+"


def test_dashboard_real_feedback_counts_and_timeline(qtbot, service):
    window = Dashboard(service)
    qtbot.addWidget(window)
    window.refresh(service.snapshot())
    assert window.table.rowCount() == 1
    assert window.learning_fields["episodes"].text() == "0"
    assert window.learning_fields["days_covered"].text() == "0"
    assert "30 valid" in window.learning_fields["missing"].text()


def test_feedback_history_distinguishes_revision_and_withdrawal(qtbot, service):
    window = Dashboard(service)
    qtbot.addWidget(window)
    service.value["feedback"] = [
        {"id": "old", "start": 10, "end": 100, "label": "Normal"},
        {"id": "new", "start": 10, "end": 100, "label": "Focused", "revises": "old"},
        {"id": "withdrawn", "start": 100, "end": 200, "label": "Rest", "withdrawn": True},
    ]
    window.refresh(service.snapshot())
    assert [window.feedback_table.item(row, 3).text() for row in range(3)] == ["Revised", "Active", "Withdrawn"]


def test_settings_save_consent_unchanged_and_apply_categories(qtbot, service):
    window = Settings(service)
    qtbot.addWidget(window)
    assert not window.startup.isChecked()
    window.app_id.setText("org.example.reader")
    window.category.setCurrentText("Reader")
    window.add_mapping()
    window.quiet_start.setText("22:00")
    window.quiet_end.setText("08:00")
    window.save()
    name, kwargs = service.calls[-1]
    assert name == "settings"
    assert "consent" not in kwargs["values"]
    assert kwargs["values"]["categories"] == {"org.example.reader": "Reader"}
    assert kwargs["values"]["quiet_hours"] == [("22:00", "08:00")]


def test_load_feedback_has_inline_receipt_and_rejects_instant_doubleclick(qtbot, service):
    window = StatusCard(service, lambda: None, lambda: None)
    qtbot.addWidget(window)
    window.submit_load_feedback('Yes')
    window.submit_load_feedback('Yes')
    assert service.calls == [('load_feedback', {'answer': 'Yes'})]
    assert 'submitted' in window.load_receipt.text()
    assert all(not item.isEnabled() for item in window.load_buttons)
    service.value['status'] = 'Rest preference saved separately from work-state labels.'
    window.refresh(service.snapshot())
    assert 'saved locally' in window.load_receipt.text()
    qtbot.waitUntil(lambda: all(item.isEnabled() for item in window.load_buttons), timeout=3000)


def test_rest_button_does_not_create_duplicate_pending_declarations(qtbot, service):
    window = StatusCard(service, lambda: None, lambda: None)
    qtbot.addWidget(window)
    window.toggle_rest()
    window.toggle_rest()
    assert service.calls == [('rest', {'minutes': 5})]
    assert not window.rest.isEnabled()
    service.value['rest_active'] = True
    window.refresh(service.snapshot())
    assert window.rest.isEnabled()
    assert window.rest.text() == 'Finish my break'


def test_first_use_through_demo_persists_feedback_settings_and_restart(qtbot, tmp_path, qapp):
    from focuspet.service import AppService
    from focuspet.ui.app import AppController
    from focuspet.ui.strings import tr
    from PySide6.QtWidgets import QPushButton

    backend = AppService(mode='test', data_dir=tmp_path)
    assert backend.wait_ready()
    controller = AppController(backend, qapp)
    controller.open_onboarding()
    welcome = controller._onboarding
    assert not welcome.consent.isChecked()
    assert not welcome.start_button.isEnabled()
    welcome.profile.setCurrentText('Research / Reading')
    qtbot.mouseClick(welcome.characters._buttons['ada'], Qt.MouseButton.LeftButton)
    qtbot.mouseClick(welcome.demo_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: backend.snapshot()['mode'] == 'synthetic-demo')
    qtbot.waitUntil(lambda: backend.snapshot()['settings']['character'] == 'ada')
    assert not backend.snapshot()['consent']
    qtbot.waitUntil(lambda: len(backend.snapshot()['history']) >= 3, timeout=5000)
    controller.open_status()
    controller.refresh()
    assert controller.status.badge.text() == tr('demo_badge')
    assert controller.pet.atlas.character == 'ada'
    correction = FeedbackDialog(backend, parent=controller.status)
    qtbot.addWidget(correction)
    correction.show()
    correction.labels.setCurrentText('Normal')
    qtbot.mouseClick(correction.findChild(QPushButton, 'save_feedback'), Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: bool(backend.snapshot()['feedback']))
    assert backend.snapshot()['feedback'][0]['label'] == 'Normal'
    assert backend.snapshot()['learning']['version'] == 'generic-prior-v1'
    qtbot.mouseClick(controller.status.rest, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: backend.snapshot()['rest_active'])
    controller.refresh()
    assert controller.pet.action_name == 'rest'
    assert controller.status.focus.text() == '—'
    qtbot.mouseClick(controller.status.rest, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not backend.snapshot()['rest_active'])
    controller.open_dashboard()
    controller.refresh()
    assert controller.dashboard.feedback_table.rowCount() == 1
    controller.open_settings()
    settings = controller._settings_window
    settings.scale.setValue(2)
    settings.profile.setCurrentText('Mixed')
    qtbot.mouseClick(settings.findChild(QPushButton, 'save'), Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: backend.snapshot()['settings']['scale'] == 2)
    pause_action = next(action for action in controller.menu.actions() if action.text() == tr('pause'))
    resume_action = next(action for action in controller.menu.actions() if action.text() == tr('resume'))
    pause_action.trigger()
    qtbot.waitUntil(lambda: backend.snapshot()['observed_state'] == 'Paused')
    controller.refresh()
    assert controller.pet.action_name == 'unknown'
    resume_action.trigger()
    qtbot.waitUntil(lambda: backend.snapshot()['observed_state'] != 'Paused')
    backend.command('settings', values={'quiet': True})
    qtbot.waitUntil(lambda: backend.snapshot()['settings']['quiet'])
    controller.refresh()
    quiet_action = next(action for action in controller.menu.actions() if action.objectName() == 'quiet_action')
    assert quiet_action.isChecked()
    quiet_action.trigger()
    qtbot.waitUntil(lambda: not backend.snapshot()['settings']['quiet'])
    controller.shutdown()
    restored = AppService(mode='synthetic-demo', data_dir=tmp_path)
    try:
        assert restored.wait_ready()
        assert restored.snapshot()['settings']['character'] == 'ada'
        assert restored.snapshot()['settings']['scale'] == 2
        assert restored.snapshot()['feedback'][0]['label'] == 'Normal'
        assert not restored.snapshot()['consent']
    finally:
        restored.close()
