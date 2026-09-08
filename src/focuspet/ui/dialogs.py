from __future__ import annotations

import re
from datetime import datetime

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog,
    QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox,
    QScrollArea, QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem, QToolButton,
    QVBoxLayout, QWidget,
)

from .assets import SpriteAtlas, character_manifests
from .strings import CATEGORIES, LABELS, PROFILES, tr
from .widgets import RhythmPlot, Timeline, WorkloadScale, button, card, date_text, label, legend, plot_legend, time_text


def _table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels([tr(h) for h in headers])
    widget.horizontalHeader().setStretchLastSection(True)
    widget.verticalHeader().hide()
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setAlternatingRowColors(False)
    return widget


def _scroll(content):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(content)
    return area


class CharacterChooser(QWidget):
    def __init__(self, selected="mira"):
        super().__init__()
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self._buttons = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        colors = {"mira": ("#f0ebfc", "#9d8bd5"), "jun": ("#e8f2ff", "#86b4e7"),
                  "ada": ("#eaf6ee", "#80bea0"), "sol": ("#fff1df", "#d9b17c")}
        for index, manifest in enumerate(character_manifests()):
            name = manifest["id"]
            item = QToolButton()
            item.setObjectName(f"character_{name}")
            item.setText(f"0{index + 1} · {manifest['name']}")
            item.setToolTip(manifest["tagline"])
            item.setCheckable(True)
            item.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            item.setIcon(QIcon(SpriteAtlas(name).thumbnail()))
            item.setIconSize(QSize(96, 120))
            item.setMinimumSize(112, 160)
            background, border = colors.get(name, ("#eef0fa", "#9da6c5"))
            item.setStyleSheet(f"QToolButton {{ background: {background}; border:1px solid {border}; color:#303959; }} "
                              f"QToolButton:checked {{ border:3px solid {border}; background:{background}; }} "
                              "QToolButton:hover { border-width:2px; }")
            item.setChecked(name == selected)
            self.group.addButton(item)
            self._buttons[name] = item
            layout.addWidget(item)
        if not self.group.checkedButton() and self._buttons:
            next(iter(self._buttons.values())).setChecked(True)

    def selected(self):
        return next((name for name, item in self._buttons.items() if item.isChecked()), "mira")


class Onboarding(QDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle(tr("app"))
        self.setObjectName("onboarding")
        self.resize(680, 690)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(26, 22, 26, 22)
        outer.addWidget(label(tr("app").upper(), "eyebrow"))
        outer.addWidget(label(tr("welcome"), "title", True))
        outer.addWidget(label(tr("welcome_note"), "subtitle", True))
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 10, 0, 4)
        layout.setSpacing(13)
        layout.addWidget(label(tr("choose_character"), "section"))
        self.characters = CharacterChooser(service.snapshot().get("settings", {}).get("character", "mira"))
        layout.addWidget(self.characters)
        layout.addWidget(label(tr("choose_profile"), "section"))
        self.profile = QComboBox()
        self.profile.addItems(PROFILES)
        self.profile.setCurrentText(service.snapshot().get("profile", "Mixed"))
        layout.addWidget(self.profile)
        layout.addWidget(label(tr("profile_note"), "subtitle", True))
        privacy, privacy_layout = card()
        privacy_layout.addWidget(label(tr("privacy_intro"), "section"))
        privacy_layout.addWidget(label(tr("privacy_text"), "subtitle", True))
        self.consent = QCheckBox(tr("consent"))
        self.consent.setObjectName("consentCheck")
        self.consent.setChecked(False)
        privacy_layout.addWidget(self.consent)
        layout.addWidget(privacy)
        layout.addWidget(label(tr("demo_note"), "subtitle", True))
        outer.addWidget(_scroll(content))
        actions = QHBoxLayout()
        self.demo_button = button("demo", self._demo)
        self.start_button = button("start", self._start, primary=True)
        self.start_button.setEnabled(False)
        self.consent.toggled.connect(self.start_button.setEnabled)
        actions.addWidget(self.demo_button)
        actions.addWidget(self.start_button)
        outer.addLayout(actions)

    def _start(self):
        if self.consent.isChecked():
            self.service.command("consent", allowed=True, profile=self.profile.currentText(),
                                 character=self.characters.selected())
            self.accept()

    def _demo(self):
        self.service.command("demo")
        self.service.command("settings", values={"character": self.characters.selected(),
                                                "profile": self.profile.currentText()})
        self.accept()


class FeedbackDialog(QDialog):
    def __init__(self, service, segment=None, revision=None, parent=None):
        super().__init__(parent)
        self.service, self.segment, self.revision = service, segment, revision
        self.setObjectName("feedbackDialog")
        self.setWindowTitle(tr("correction_title"))
        self.resize(490, 295)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(label(tr("correction_title"), "section"))
        layout.addWidget(label(tr("correction_note"), "subtitle", True))
        self.minutes = QComboBox()
        for duration in (1, 5, 15):
            self.minutes.addItem(f"{duration} {tr('minutes')}", duration)
        self.minutes.setCurrentIndex(1)
        if segment:
            layout.addWidget(label(f"{tr('target')}: {date_text(segment.get('start'))} – {time_text(segment.get('end'))}"))
        if not revision:
            row = QHBoxLayout()
            row.addWidget(label(tr("before_segment") if segment else tr("recent")))
            row.addWidget(self.minutes)
            layout.addLayout(row)
        self.labels = QComboBox()
        self.labels.setObjectName("feedbackLabel")
        self.labels.addItems(LABELS)
        if revision:
            self.labels.setCurrentText(revision.get("label", "Not sure"))
        else:
            self.labels.setCurrentText("Not sure")
        layout.addWidget(self.labels)
        layout.addWidget(label(tr("feedback_note"), "subtitle", True))
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(button("cancel", self.reject))
        actions.addWidget(button("save_feedback", self.save, primary=True))
        layout.addLayout(actions)

    def save(self):
        arguments = {"label": self.labels.currentText(), "minutes": self.minutes.currentData()}
        if self.segment:
            end = self.segment["end"]
            arguments.update(start=self.segment["start"] if self.revision else end - self.minutes.currentData() * 60,
                             end=end)
        if self.revision:
            arguments["revision_of"] = self.revision["id"]
        self.service.command("feedback", **arguments)
        self.accept()


class StatusCard(QDialog):
    def __init__(self, service, open_dashboard, open_settings, parent=None):
        super().__init__(parent)
        self.service = service
        self.setObjectName("statusCard")
        self.setWindowTitle(tr("app"))
        self.resize(520, 740)
        self._query_id = None
        self._portrait_character = None
        self._rest_pending = None
        self._load_pending = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 22)
        header = QHBoxLayout()
        header.addWidget(label(tr("app").upper(), "eyebrow"))
        header.addStretch()
        self.badge = label(tr("local"), "eyebrow")
        header.addWidget(self.badge)
        layout.addLayout(header)
        layout.addWidget(label(tr("status"), "title"))
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 6, 0, 4)
        body.setSpacing(14)
        self.query_card, query_layout = card()
        query_layout.addWidget(label(tr("audit_question"), "section"))
        self.query_note = label(tr("audit_note"), "subtitle", True)
        query_layout.addWidget(self.query_note)
        self.query_period = label("")
        query_layout.addWidget(self.query_period)
        self.query_label = QComboBox()
        self.query_label.addItems(LABELS)
        self.query_label.setCurrentText("Not sure")
        query_layout.addWidget(self.query_label)
        row = QHBoxLayout()
        row.addWidget(button("skip", lambda: self.answer_query(None)))
        row.addWidget(button("save_feedback", lambda: self.answer_query(self.query_label.currentText()), True))
        query_layout.addLayout(row)
        self.query_card.hide()
        body.addWidget(self.query_card)
        self.estimate, estimate_layout = card()
        state_row = QHBoxLayout()
        self.portrait = QLabel()
        self.portrait.setFixedSize(78, 96)
        state_row.addWidget(self.portrait)
        state_text = QVBoxLayout()
        self.state = label("Unknown", "title")
        self.observed = label("", "subtitle")
        self.source = label("", "subtitle", True)
        state_text.addWidget(self.state)
        state_text.addWidget(self.observed)
        state_text.addWidget(self.source)
        state_row.addLayout(state_text, 1)
        estimate_layout.addLayout(state_row)
        metric_row = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(label(tr("focus"), "section"))
        self.focus = label(tr("no_data"), "metric")
        left.addWidget(self.focus)
        left.addWidget(label(tr("focus_note"), "subtitle", True))
        right = QVBoxLayout()
        right.addWidget(label(tr("workload"), "section"))
        self.workload = label("0", "metric")
        right.addWidget(self.workload)
        right.addWidget(label(tr("workload_note"), "subtitle", True))
        metric_row.addLayout(left, 1)
        metric_row.addSpacing(22)
        metric_row.addLayout(right, 1)
        estimate_layout.addLayout(metric_row)
        self.load_scale = WorkloadScale()
        estimate_layout.addWidget(self.load_scale)
        self.quality = label("", "subtitle", True)
        estimate_layout.addWidget(self.quality)
        self.evidence = label("", "subtitle", True)
        estimate_layout.addWidget(self.evidence)
        body.addWidget(self.estimate)
        actions = QHBoxLayout()
        actions.addWidget(button("correct", self.correct))
        self.rest = button("start_rest", self.toggle_rest, primary=True)
        actions.addWidget(self.rest)
        body.addLayout(actions)
        load_frame, load_layout = card()
        load_layout.addWidget(label(tr("load_question"), "section"))
        load_layout.addWidget(label(tr("load_note"), "subtitle", True))
        row = QHBoxLayout()
        self.load_buttons = []
        for key, answer in (("yes", "Yes"), ("no", "No"), ("not_sure", "Not sure")):
            item = button(key, lambda checked=False, value=answer: self.submit_load_feedback(value))
            self.load_buttons.append(item)
            row.addWidget(item)
        load_layout.addLayout(row)
        self.load_receipt = label("", "subtitle", True)
        load_layout.addWidget(self.load_receipt)
        self._load_cooldown = QTimer(self)
        self._load_cooldown.setSingleShot(True)
        self._load_cooldown.timeout.connect(self._enable_load_buttons)
        body.addWidget(load_frame)
        self.learning = label("", "subtitle", True)
        body.addWidget(self.learning)
        self.status = label("", "subtitle", True)
        body.addWidget(self.status)
        body.addStretch()
        layout.addWidget(_scroll(content))
        footer = QHBoxLayout()
        footer.addWidget(button("quiet", lambda: self.service.command("settings", values={"quiet": True})))
        footer.addStretch()
        footer.addWidget(button("dashboard", open_dashboard))
        footer.addWidget(button("settings", open_settings))
        layout.addLayout(footer)

    def correct(self):
        FeedbackDialog(self.service, parent=self).exec()

    def toggle_rest(self):
        if self._rest_pending is not None:
            return
        active = bool(self.service.snapshot().get("rest_active"))
        accepted = self.service.command("end_rest") if active else self.service.command("rest", minutes=5)
        if accepted is not False:
            self._rest_pending = not active
            self.rest.setEnabled(False)

    def submit_load_feedback(self, answer):
        if self._load_cooldown.isActive():
            return
        accepted = self.service.command("load_feedback", answer=answer)
        if accepted is False:
            return
        self._load_pending = True
        self.load_receipt.setText(f"{tr('feedback_submitted')} · {answer}")
        for item in self.load_buttons:
            item.setEnabled(False)
        self._load_cooldown.start(2000)

    def _enable_load_buttons(self):
        for item in self.load_buttons:
            item.setEnabled(True)

    def answer_query(self, answer):
        if self._query_id:
            args = {"query_id": self._query_id}
            if answer is not None:
                args["label"] = answer
            self.service.command("query_answer" if answer is not None else "query_skip", **args)

    def refresh(self, snapshot):
        demo = snapshot.get("mode") == "synthetic-demo"
        self.badge.setText(tr("demo_badge" if demo else "real_badge"))
        query = snapshot.get("query")
        self._query_id = query.get("id") if query else None
        self.query_card.setVisible(bool(query))
        self.estimate.setVisible(not bool(query))
        if query:
            self.query_period.setText(f"{time_text(query.get('target_start'))} – {time_text(query.get('target_end'))}")
        self.state.setText(snapshot.get("state", "Unknown"))
        self.observed.setText(f"{tr('observed')}: {snapshot.get('observed_state', 'Missing')}")
        self.source.setText(f"{tr('source')}: {snapshot.get('source', tr('data-insufficient'))}")
        focus = snapshot.get("focus")
        self.focus.setText(tr("no_data") if focus is None else f"{focus:.0f}")
        workload = float(snapshot.get("workload") or 0)
        self.workload.setText("120+" if workload > 120 else f"{workload:.1f}")
        stale = snapshot.get("workload_stale", False)
        self.load_scale.set_value(workload, stale)
        coverage = snapshot.get("coverage")
        uncertainty = snapshot.get("uncertainty")
        cov = tr("no_data") if coverage is None else f"{coverage:.0%}"
        unc = tr("no_data") if uncertainty is None else f"{uncertainty:.0%}"
        self.quality.setText(f"{tr('coverage')}: {cov}  ·  {tr('uncertainty')}: {unc}" + (f"\n{tr('stale')}" if stale else ""))
        self.evidence.setText(tr("evidence") + "\n" + "\n".join(snapshot.get("evidence") or [tr("no_evidence")]))
        character = snapshot.get("settings", {}).get("character", "mira")
        if character != self._portrait_character:
            atlas = SpriteAtlas(character)
            self.portrait.setPixmap(atlas.thumbnail().scaled(78, 96, Qt.AspectRatioMode.KeepAspectRatio,
                                                           Qt.TransformationMode.FastTransformation))
            self._portrait_character = character
        self.rest.setText(tr("end_rest" if snapshot.get("rest_active") else "start_rest"))
        if self._rest_pending == bool(snapshot.get("rest_active")) or str(snapshot.get("status", "")).startswith("Action failed"):
            self._rest_pending = None
        self.rest.setEnabled(self._rest_pending is None)
        if self._load_pending and snapshot.get("status") == "Rest preference saved separately from work-state labels.":
            self.load_receipt.setText(tr("load_feedback_saved"))
            self._load_pending = False
        info = snapshot.get("learning", {})
        self.learning.setText(f"{tr('episodes')}: {info.get('episodes', 0)} · {tr('days_covered')}: {info.get('days', 0)}\n"
                              f"{tr('version')}: {info.get('version', 'generic-v1')} · {info.get('validation', tr('missing_feedback'))}")
        self.status.setText(str(snapshot.get("status", "")))


class Dashboard(QDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.setObjectName("dashboard")
        self.setWindowTitle(f"{tr('dashboard')} · {tr('app')}")
        self.resize(920, 760)
        self._rows, self._feedback = [], []
        self._history_signature = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        header = QHBoxLayout()
        header.addWidget(label(tr("dashboard"), "title"))
        header.addStretch()
        self.badge = label(tr("local"), "eyebrow")
        header.addWidget(self.badge)
        layout.addLayout(header)
        self.tabs = QTabWidget()
        self.tabs.tabBar().setElideMode(Qt.TextElideMode.ElideNone)
        layout.addWidget(self.tabs)
        day = QWidget()
        day_layout = QVBoxLayout(day)
        day_layout.setContentsMargins(0, 16, 0, 0)
        day_layout.addWidget(label(tr("timeline_note"), "subtitle", True))
        self.timeline = Timeline()
        self.timeline.segment_selected.connect(self.correct_segment)
        day_layout.addWidget(self.timeline)
        day_layout.addWidget(legend())
        day_layout.addWidget(label(tr("curves"), "section"))
        self.plot = RhythmPlot()
        day_layout.addWidget(self.plot)
        day_layout.addWidget(plot_legend())
        self.distribution = label("", "subtitle", True)
        day_layout.addWidget(self.distribution)
        self.table = _table(["time", "state", "focus", "workload", "source"])
        self.table.doubleClicked.connect(lambda index: self.correct_selected())
        day_layout.addWidget(self.table, 1)
        day_layout.addWidget(button("correct", self.correct_selected))
        self.tabs.addTab(day, tr("timeline"))
        feedback = QWidget()
        feedback_layout = QVBoxLayout(feedback)
        feedback_layout.addWidget(label(tr("feedback_note"), "subtitle", True))
        self.feedback_table = _table(["period", "label", "source", "status_col"])
        feedback_layout.addWidget(self.feedback_table)
        actions = QHBoxLayout()
        actions.addWidget(button("revise", self.revise_feedback))
        actions.addWidget(button("withdraw", self.withdraw_feedback))
        actions.addStretch()
        feedback_layout.addLayout(actions)
        self.tabs.addTab(feedback, tr("feedback"))
        learning = QWidget()
        learning_layout = QVBoxLayout(learning)
        learning_layout.setContentsMargins(8, 22, 8, 10)
        learning_layout.addWidget(label(tr("learning"), "section"))
        learning_layout.addWidget(label(tr("learning_note"), "subtitle", True))
        self.learning_fields = {}
        form = QFormLayout()
        form.setVerticalSpacing(17)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for key in ("episodes", "days_covered", "sessions", "version", "parameter_version", "last_training", "validation", "missing"):
            item = label(tr("no_data"), wrap=True)
            if key == "validation":
                item.setMinimumHeight(52)
            elif key == "missing":
                item.setMinimumHeight(104)
            form.addRow(tr(key), item)
            self.learning_fields[key] = item
        learning_layout.addLayout(form)
        self.job = label("", "subtitle", True)
        learning_layout.addWidget(self.job)
        actions = QHBoxLayout()
        for key in ("train", "calibrate", "cancel_job"):
            actions.addWidget(button(key, lambda checked=False, name=key: self.service.command(name)))
        learning_layout.addLayout(actions)
        actions2 = QHBoxLayout()
        for key in ("rollback_model", "rollback_parameters"):
            actions2.addWidget(button(key, lambda checked=False, name=key: self.service.command(name)))
        learning_layout.addLayout(actions2)
        learning_layout.addStretch()
        self.tabs.addTab(_scroll(learning), tr("learning_tab"))

    def correct_segment(self, segment):
        FeedbackDialog(self.service, segment=segment, parent=self).exec()

    def correct_selected(self):
        row = self.table.currentRow()
        if 0 <= row < len(self._rows):
            self.correct_segment(self._rows[row])

    def revise_feedback(self):
        row = self.feedback_table.currentRow()
        if 0 <= row < len(self._feedback):
            feedback = self._feedback[row]
            FeedbackDialog(self.service, segment=feedback, revision=feedback, parent=self).exec()

    def withdraw_feedback(self):
        row = self.feedback_table.currentRow()
        if 0 <= row < len(self._feedback):
            self.service.command("retract_feedback", id=self._feedback[row]["id"])

    def refresh(self, snapshot):
        self.badge.setText(tr("demo_badge" if snapshot.get("mode") == "synthetic-demo" else "local"))
        history = snapshot.get("history", [])
        today = datetime.now().astimezone().date()
        if snapshot.get("mode") == "synthetic-demo" and history:
            today = datetime.fromtimestamp(history[-1]["end"]).astimezone().date()
        rows = [row for row in history if row.get("end") and datetime.fromtimestamp(row["end"]).astimezone().date() == today]
        signature = (len(rows), rows[-1].get("id") if rows else None)
        if signature != self._history_signature:
            self._history_signature = signature
            self._rows = list(reversed(rows))
            self.timeline.set_rows(rows)
            self.plot.set_rows(rows)
            self.table.setRowCount(len(self._rows))
            distribution: dict[str, float] = {}
            for i, row in enumerate(self._rows):
                state = row.get("state", "Unknown")
                elapsed = row.get("duration_s")
                distribution[state] = distribution.get(state, 0) + (max(0, elapsed) if elapsed is not None else max(0, row.get("end", 0) - row.get("start", 0)))
                values = [f"{time_text(row.get('start'), True)}–{time_text(row.get('end'), True)}", state,
                          tr("no_data") if row.get("focus") is None else f"{row['focus']:.0f}",
                          tr("no_data") if row.get("workload_stale") else f"{float(row.get('workload') or 0):.1f}",
                          row.get("source", tr("no_data"))]
                for col, value in enumerate(values):
                    self.table.setItem(i, col, QTableWidgetItem(str(value)))
            self.table.resizeColumnsToContents()
            self.distribution.setText(tr("distribution") + ": " + " · ".join(f"{state} {seconds/60:.1f} min" for state, seconds in distribution.items()))
        feedback = snapshot.get("feedback", [])
        if feedback != self._feedback:
            self._feedback = feedback
            self.feedback_table.setRowCount(len(feedback))
            superseded = {item.get("revises") for item in feedback if item.get("revises")}
            for i, item in enumerate(feedback):
                status = "withdrawn" if item.get("withdrawn", item.get("retracted")) else "superseded" if item.get("id") in superseded else "active"
                values = [f"{date_text(item.get('start'))}–{time_text(item.get('end'))}", item.get("label", ""),
                          item.get("source", "user"), tr(status)]
                for col, value in enumerate(values):
                    self.feedback_table.setItem(i, col, QTableWidgetItem(str(value)))
            self.feedback_table.resizeColumnsToContents()
        learning = snapshot.get("learning", {})
        mapping = {"episodes": "episodes", "days_covered": "days", "sessions": "sessions", "version": "version",
                   "parameter_version": "parameter_version", "last_training": "last_training", "validation": "validation", "missing": "missing"}
        for key, source in mapping.items():
            value = learning.get(source, 0 if key in ("episodes", "days_covered", "sessions") else tr("no_data"))
            if key == "last_training":
                value = date_text(value)
            if isinstance(value, (list, tuple)):
                value = "\n".join(str(part) for part in value)
            self.learning_fields[key].setText(str(value))
        self.job.setText(f"{tr('training_status')}: {snapshot.get('job_status', snapshot.get('status', ''))}")


class Settings(QDialog):
    def __init__(self, service, on_applied=None, parent=None):
        super().__init__(parent)
        self.service, self.on_applied = service, on_applied
        snapshot = service.snapshot()
        values = snapshot.get("settings", {})
        self.setObjectName("settingsDialog")
        self.setWindowTitle(f"{tr('settings')} · {tr('app')}")
        self.resize(780, 720)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(23, 20, 23, 20)
        layout.addWidget(label(tr("settings"), "title"))
        tabs = QTabWidget()
        tabs.tabBar().setElideMode(Qt.TextElideMode.ElideNone)
        layout.addWidget(tabs, 1)
        general = QWidget()
        general_layout = QVBoxLayout(general)
        self.characters = CharacterChooser(values.get("character", "mira"))
        general_layout.addWidget(self.characters)
        form = QFormLayout()
        self.profile = QComboBox()
        self.profile.addItems(PROFILES)
        self.profile.setCurrentText(values.get("profile", "Mixed"))
        form.addRow(tr("choose_profile"), self.profile)
        self.scale = QSpinBox()
        self.scale.setRange(1, 5)
        self.scale.setValue(int(values.get("scale", 3)))
        self.scale.setSuffix(" ×")
        form.addRow(tr("size"), self.scale)
        self.spacing = QSpinBox()
        self.spacing.setRange(15, 240)
        self.spacing.setValue(int(values.get("reminder_cooldown", 30)))
        self.spacing.setSuffix(" min")
        form.addRow(tr("reminder_frequency"), self.spacing)
        hours = values.get("quiet_hours", [])
        self.quiet_start = QLineEdit(hours[0][0] if hours else "")
        self.quiet_end = QLineEdit(hours[0][1] if hours else "")
        self.quiet_start.setPlaceholderText("22:00")
        self.quiet_end.setPlaceholderText("08:00")
        hour_layout = QHBoxLayout()
        hour_layout.addWidget(self.quiet_start)
        hour_layout.addWidget(label("–"))
        hour_layout.addWidget(self.quiet_end)
        form.addRow(tr("quiet_hours"), hour_layout)
        general_layout.addLayout(form)
        general_layout.addWidget(label(tr("quiet_hours_hint"), "subtitle", True))
        self.reminders = QCheckBox(tr("reminders"))
        self.reminders.setChecked(bool(values.get("reminders_enabled", True)))
        general_layout.addWidget(self.reminders)
        self.top = QCheckBox(tr("always_on_top"))
        self.top.setChecked(bool(values.get("always_on_top", True)))
        general_layout.addWidget(self.top)
        self.through = QCheckBox(tr("through"))
        self.through.setChecked(bool(values.get("click_through", False)))
        general_layout.addWidget(self.through)
        general_layout.addWidget(label(tr("through_note"), "subtitle", True))
        self.startup = QCheckBox(tr("startup"))
        self.startup.setChecked(bool(values.get("startup", False)))
        general_layout.addWidget(self.startup)
        general_layout.addWidget(label(tr("startup_note"), "subtitle", True))
        general_layout.addWidget(label(tr("profile_note"), "subtitle", True))
        general_layout.addStretch()
        tabs.addTab(_scroll(general), tr("general"))
        privacy = QWidget()
        privacy_layout = QVBoxLayout(privacy)
        privacy_layout.addWidget(label(tr("privacy_intro"), "section"))
        privacy_layout.addWidget(label(tr("privacy_text"), "subtitle", True))
        privacy_layout.addWidget(label(tr("permissions"), "section"))
        self.permissions = label("", wrap=True)
        privacy_layout.addWidget(self.permissions)
        privacy_layout.addWidget(label(tr("permissions_note"), "subtitle", True))
        privacy_actions = QHBoxLayout()
        privacy_actions.addWidget(button("grant", self.grant))
        privacy_actions.addWidget(button("revoke", lambda: service.command("consent", allowed=False)))
        privacy_layout.addLayout(privacy_actions)
        retention = QFormLayout()
        self.bucket_days = QSpinBox()
        self.bucket_days.setRange(1, 365)
        self.bucket_days.setValue(int(values.get("bucket_days", 7)))
        self.bucket_days.setSuffix(" " + tr("days"))
        retention.addRow(tr("retention_buckets"), self.bucket_days)
        self.feature_days = QSpinBox()
        self.feature_days.setRange(1, 730)
        self.feature_days.setValue(int(values.get("feature_days", 90)))
        self.feature_days.setSuffix(" " + tr("days"))
        retention.addRow(tr("retention_features"), self.feature_days)
        self.feedback_days = QSpinBox()
        self.feedback_days.setRange(1, 730)
        self.feedback_days.setValue(int(values.get("feedback_days", 365)))
        self.feedback_days.setSuffix(" " + tr("days"))
        retention.addRow(tr("retention_feedback"), self.feedback_days)
        self.model_days = QSpinBox()
        self.model_days.setRange(1, 730)
        self.model_days.setValue(int(values.get("model_days", 365)))
        self.model_days.setSuffix(" " + tr("days"))
        retention.addRow(tr("retention_models"), self.model_days)
        privacy_layout.addLayout(retention)
        privacy_layout.addWidget(button("export", self.export))
        privacy_layout.addWidget(button("delete_range", self.delete_range))
        danger = button("delete", self.delete)
        danger.setProperty("danger", True)
        privacy_layout.addWidget(danger)
        privacy_layout.addStretch()
        tabs.addTab(_scroll(privacy), tr("privacy"))
        categories = QWidget()
        categories_layout = QVBoxLayout(categories)
        categories_layout.addWidget(label(tr("mapping_note"), "subtitle", True))
        self.mapping = dict(values.get("categories", {}))
        self.mapping_table = _table(["app_id", "category"])
        categories_layout.addWidget(self.mapping_table)
        self.app_id = QLineEdit()
        self.app_id.setPlaceholderText("com.example.application")
        self.category = QComboBox()
        self.category.addItems(CATEGORIES)
        category_form = QFormLayout()
        category_form.addRow(tr("app_id"), self.app_id)
        category_form.addRow(tr("category"), self.category)
        categories_layout.addLayout(category_form)
        categories_layout.addWidget(button("add_mapping", self.add_mapping))
        tabs.addTab(categories, tr("applications"))
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(button("cancel", self.reject))
        actions.addWidget(button("save", self.save, True))
        layout.addLayout(actions)
        self.refresh(snapshot)
        self._update_mapping()

    def refresh(self, snapshot):
        permissions = snapshot.get("permissions", {})
        self.permissions.setText("\n".join(f"{key}: {value.get('status', value) if isinstance(value, dict) else value}" for key, value in permissions.items()) or tr("permission_required"))

    def grant(self):
        Onboarding(self.service, self).exec()

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, tr("export_title"), "focus-pet-export.json", tr("export_filter"))
        if path:
            self.service.command("export", path=path)

    def delete(self):
        answer, accepted = QInputDialog.getText(self, tr("delete_title"), tr("delete_note"))
        if accepted and answer == "DELETE":
            self.service.command("delete_data", confirm=True)

    def delete_range(self):
        answer, accepted = QInputDialog.getText(self, tr("delete_range_title"), tr("delete_range_note"))
        if not accepted:
            return
        try:
            begin, finish = answer.split("/")
            start = datetime.strptime(begin.strip(), "%Y-%m-%d %H:%M").astimezone().timestamp()
            end = datetime.strptime(finish.strip(), "%Y-%m-%d %H:%M").astimezone().timestamp()
            if start >= end:
                raise ValueError("Invalid interval")
        except ValueError:
            QMessageBox.information(self, tr("notice"), tr("invalid_range"))
            return
        self.service.command("delete_range", confirm=True, start=start, end=end)

    def add_mapping(self):
        app_id = self.app_id.text().strip()
        if app_id and len(app_id) <= 200 and not any(char in app_id for char in ("\n", "\r", "\t")):
            self.mapping[app_id] = self.category.currentText()
            self._update_mapping()
            self.app_id.clear()

    def _update_mapping(self):
        self.mapping_table.setRowCount(len(self.mapping))
        for row, (app_id, category) in enumerate(sorted(self.mapping.items())):
            self.mapping_table.setItem(row, 0, QTableWidgetItem(app_id))
            self.mapping_table.setItem(row, 1, QTableWidgetItem(category))
        self.mapping_table.resizeColumnsToContents()

    def save(self):
        start, end = self.quiet_start.text().strip(), self.quiet_end.text().strip()
        pattern = r"(?:[01]\d|2[0-3]):[0-5]\d"
        if (start or end) and not (re.fullmatch(pattern, start) and re.fullmatch(pattern, end)):
            QMessageBox.information(self, tr("notice"), tr("invalid_time"))
            return
        values = {"character": self.characters.selected(), "profile": self.profile.currentText(),
                  "scale": self.scale.value(), "quiet_hours": [(start, end)] if start else [],
                  "reminder_cooldown": self.spacing.value(), "reminders_enabled": self.reminders.isChecked(),
                  "always_on_top": self.top.isChecked(), "click_through": self.through.isChecked(),
                  "startup": self.startup.isChecked(), "bucket_days": self.bucket_days.value(),
                  "feature_days": self.feature_days.value(), "feedback_days": self.feedback_days.value(),
                  "model_days": self.model_days.value(), "categories": self.mapping}
        self.service.command("settings", values=values)
        if self.on_applied:
            self.on_applied(values)
        self.accept()
