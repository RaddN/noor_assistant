from __future__ import annotations

import html
import json
import re
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QDate, QPoint, QProcess, QRectF, QSize, Qt, QTime, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPen, QRadialGradient, QTextCursor
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTimeEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from standalone_assistant.core.assistant_brain import AssistantBrain, AssistantReply
from standalone_assistant.core.connectors import ToolRegistry
from standalone_assistant.core.connections import connection_snapshot
from standalone_assistant.core.google_productivity import GoogleProductivityService
from standalone_assistant.core.paths import ICON_DIR, SESSION_DIR, ensure_runtime_dirs
from standalone_assistant.core.project_scanner import build_codex_prompt, codex_status, find_agents, preflight_project
from standalone_assistant.core.speech import SpeechService
from standalone_assistant.core.storage import Storage, dumps, loads, utc_now
from standalone_assistant.core.time_parser import format_local_timestamp, parse_when
from standalone_assistant.core.whatsapp_web import WhatsAppWebService
from standalone_assistant.core.whatsapp_rules import (
    action_summary as whatsapp_action_summary,
    audience_summary as whatsapp_audience_summary,
    contacts_from_text,
    contacts_to_text,
    load_whatsapp_rules,
    normalize_rule,
    trigger_summary as whatsapp_trigger_summary,
    write_whatsapp_rules,
)


SENSITIVE_WORDS = {
    "ceo",
    "client",
    "payment",
    "invoice",
    "complaint",
    "deadline",
    "legal",
    "confidential",
    "salary",
    "hiring",
    "firing",
    "urgent",
}


def make_button(text: str, callback: Any) -> QPushButton:
    button = QPushButton(text)
    button.clicked.connect(callback)
    return button


def configure_table(table: QTableWidget) -> None:
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setAlternatingRowColors(True)
    table.setWordWrap(False)
    table.setMinimumHeight(190)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(34)
    table.verticalHeader().setMinimumSectionSize(30)
    table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    table.horizontalHeader().setStretchLastSection(True)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)


def compact_text(value: Any, limit: int = 120) -> str:
    text = "" if value is None else str(value).replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def set_table_rows(table: QTableWidget, headers: list[str], rows: list[list[Any]]) -> None:
    table.setUpdatesEnabled(False)
    try:
        table.clear()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for col_index, value in enumerate(row):
                full_text = "" if value is None else str(value)
                item = QTableWidgetItem(compact_text(full_text, 140))
                item.setToolTip(full_text)
                item.setFlags(item.flags() ^ Qt.ItemIsEditable)
                table.setItem(row_index, col_index, item)
        table.resizeColumnsToContents()
        for column in range(table.columnCount()):
            width = table.columnWidth(column)
            table.setColumnWidth(column, min(max(width, 82), 360))
        table.horizontalHeader().setStretchLastSection(True)
    finally:
        table.setUpdatesEnabled(True)


def selected_value(table: QTableWidget, column: int = 0) -> str | None:
    row = table.currentRow()
    if row < 0:
        return None
    item = table.item(row, column)
    return item.text() if item else None


class AssistantAvatar(QWidget):
    listen_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._pulse = 0
        self._listening = False
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Listen for a voice command")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        self.timer.start(70)
        self.setMinimumSize(260, 260)
        self.mic_icon = QSvgRenderer(str(ICON_DIR / "mic.svg"))

    def sizeHint(self) -> QSize:
        return QSize(320, 320)

    def animate(self) -> None:
        self._pulse = (self._pulse + 1) % (36 if self._listening else 80)
        self.update()

    def set_listening(self, listening: bool) -> None:
        self._listening = listening
        self.update()

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.LeftButton:
            self.listen_requested.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        center = rect.center()
        radius = min(rect.width(), rect.height()) * 0.38
        pulse = 1 + (self._pulse / (36 if self._listening else 80)) * (0.18 if self._listening else 0.09)

        glow = QRadialGradient(center, radius * 1.45)
        glow.setColorAt(0.0, QColor(32, 247, 219, 130))
        glow.setColorAt(0.48, QColor(15, 142, 150, 70))
        glow.setColorAt(1.0, QColor(7, 13, 33, 0))
        painter.setBrush(glow)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(center, radius * 1.4 * pulse, radius * 1.4 * pulse)

        painter.setPen(QPen(QColor(255, 203, 91, 230) if self._listening else QColor(44, 222, 216, 160), 3 if self._listening else 2))
        painter.setBrush(QColor(10, 43, 59, 210))
        painter.drawEllipse(center, radius * 1.03, radius * 1.03)

        painter.setPen(QPen(QColor(77, 255, 233), 8))
        painter.setBrush(QColor(28, 225, 204))
        painter.drawEllipse(center, radius * 0.58, radius * 0.58)

        face_rect = rect.adjusted(rect.width() // 3, rect.height() // 3, -rect.width() // 3, -rect.height() // 3)
        painter.setBrush(QColor(9, 80, 82))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(face_rect, 42, 42)

        eye_y = face_rect.center().y() - 10
        painter.setBrush(QColor(214, 255, 247))
        painter.drawEllipse(face_rect.center().x() - 52, eye_y, 16, 16)
        painter.drawEllipse(face_rect.center().x() + 36, eye_y, 16, 16)

        painter.setPen(QPen(QColor(218, 255, 247), 5, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(face_rect.center().x() - 28, face_rect.center().y() + 18, 56, 30, 205 * 16, 130 * 16)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(40, 232, 213))
        painter.drawRoundedRect(face_rect.left() - 18, face_rect.center().y() - 42, 26, 84, 12, 12)
        painter.drawRoundedRect(face_rect.right() - 8, face_rect.center().y() - 42, 26, 84, 12, 12)

        body_top = face_rect.bottom() + 20
        body = [
            center.x() - 72,
            body_top,
            144,
            max(45, rect.bottom() - body_top - 18),
        ]
        painter.setBrush(QColor(23, 200, 189, 185))
        painter.drawRoundedRect(*body, 28, 28)
        painter.setPen(QPen(QColor(166, 255, 245, 190), 2))
        painter.drawLine(center.x(), body_top + 8, center.x(), body_top + body[3] - 12)

        button_radius = max(23, int(radius * 0.22))
        painter.setPen(QPen(QColor(255, 203, 91) if self._listening else QColor(211, 255, 250), 2))
        painter.setBrush(QColor(239, 159, 52) if self._listening else QColor(8, 52, 65))
        painter.drawEllipse(center.x() - button_radius, center.y() - button_radius + 34, button_radius * 2, button_radius * 2)
        icon_rect = QRectF(center.x() - 11, center.y() + 23, 22, 22)
        if self.mic_icon.isValid():
            self.mic_icon.render(painter, icon_rect)
        else:
            painter.setPen(QPen(QColor(207, 255, 249), 2, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(center.x(), center.y() + 25, center.x(), center.y() + 39)
            painter.drawArc(center.x() - 8, center.y() + 24, 16, 20, 0, 180 * 16)


class AssistantCard(QFrame):
    def __init__(self, title: str, body: str = "", accent: str = "#25e0d0") -> None:
        super().__init__()
        self.setObjectName("assistantCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        self.setMinimumHeight(118)
        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        body_label = QLabel(body)
        body_label.setObjectName("cardBody")
        body_label.setWordWrap(True)
        line = QFrame()
        line.setFixedHeight(3)
        line.setStyleSheet(f"background: {accent}; border-radius: 1px;")
        layout.addWidget(title_label)
        layout.addWidget(body_label)
        layout.addStretch()
        layout.addWidget(line)
        self.body_label = body_label

    def set_body(self, body: str) -> None:
        self.body_label.setText(body)


class BasePage(QWidget):
    title = ""

    def __init__(self, storage: Storage) -> None:
        super().__init__()
        self.storage = storage

    def refresh(self) -> None:
        pass

    def notify(self, title: str, message: str) -> None:
        window = self.window()
        if hasattr(window, "show_toast"):
            window.show_toast(title, message)


class DashboardPage(BasePage):
    title = "Assistant"
    command_requested = Signal(str)
    listen_requested = Signal()

    def __init__(self, storage: Storage) -> None:
        super().__init__(storage)
        self.speech = SpeechService(storage)
        self.brain = AssistantBrain(storage)
        self.last_summary = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        header = QHBoxLayout()
        identity = self.storage.get_setting("identity", {})
        brand = QLabel(str(identity.get("name") or "Noor"))
        brand.setObjectName("heroBrand")
        self.connection_state = QLabel()
        self.connection_state.setObjectName("connectionState")
        header.addWidget(brand)
        header.addStretch()
        header.addWidget(self.connection_state)

        command_bar = QFrame()
        command_bar.setObjectName("commandBar")
        command_layout = QHBoxLayout(command_bar)
        command_layout.setContentsMargins(14, 10, 14, 10)
        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText("Ask or command: hi, weather in Dhaka, latest news, research WordPress security, tool status, open projects")
        self.command_input.returnPressed.connect(self.run_command)
        command_layout.addWidget(self.command_input, 1)
        command_layout.addWidget(make_button("Run", self.run_command))
        command_layout.addWidget(make_button("Speak", self.speak_summary))

        cockpit = QGridLayout()
        cockpit.setHorizontalSpacing(18)
        cockpit.setVerticalSpacing(18)
        self.cards = {
            "google": AssistantCard("Google", "Checking...", "#28e7cd"),
            "tools": AssistantCard("Tools", "Checking...", "#53d0ff"),
            "projects": AssistantCard("Projects", "Checking...", "#9ad66b"),
            "codex": AssistantCard("Codex", "Checking...", "#ffc861"),
            "voice": AssistantCard("Voice", "Checking...", "#d79dff"),
            "whatsapp": AssistantCard("WhatsApp", "Checking...", "#39dd88"),
            "gemini": AssistantCard("Gemini", "Checking...", "#ff92cb"),
            "attention": AssistantCard("Attention", "Checking...", "#ff6f8d"),
        }
        avatar_frame = QFrame()
        avatar_frame.setObjectName("avatarPanel")
        avatar_layout = QVBoxLayout(avatar_frame)
        avatar_layout.setAlignment(Qt.AlignCenter)
        self.avatar = AssistantAvatar()
        self.avatar.listen_requested.connect(self.listen_requested.emit)
        self.summary = QLabel()
        self.summary.setObjectName("assistantSummary")
        self.summary.setWordWrap(True)
        self.heard_label = QLabel("Voice and command results will appear here.")
        self.heard_label.setObjectName("heardLabel")
        self.heard_label.setWordWrap(True)
        avatar_layout.addWidget(self.avatar, alignment=Qt.AlignCenter)
        avatar_layout.addWidget(self.heard_label)
        avatar_layout.addWidget(self.summary)
        quick_actions = QHBoxLayout()
        quick_actions.addWidget(make_button("Open Tools", lambda: self.command_requested.emit("open tools")))
        quick_actions.addWidget(make_button("Projects", lambda: self.command_requested.emit("open projects")))
        quick_actions.addWidget(make_button("Codex", lambda: self.command_requested.emit("open codex")))
        quick_actions.addWidget(make_button("Approvals", lambda: self.command_requested.emit("show approvals")))
        avatar_layout.addLayout(quick_actions)

        cockpit.addWidget(self.cards["google"], 0, 0)
        cockpit.addWidget(self.cards["tools"], 1, 0)
        cockpit.addWidget(self.cards["projects"], 2, 0)
        cockpit.addWidget(self.cards["whatsapp"], 3, 0)
        cockpit.addWidget(avatar_frame, 0, 1, 4, 1)
        cockpit.addWidget(self.cards["codex"], 0, 2)
        cockpit.addWidget(self.cards["voice"], 1, 2)
        cockpit.addWidget(self.cards["gemini"], 2, 2)
        cockpit.addWidget(self.cards["attention"], 3, 2)
        cockpit.setColumnStretch(1, 2)
        cockpit.setColumnMinimumWidth(0, 250)
        cockpit.setColumnMinimumWidth(2, 250)

        self.activity = QTableWidget()
        configure_table(self.activity)
        self.activity.setObjectName("darkTable")
        self.activity.setMaximumHeight(190)
        layout.addLayout(header)
        layout.addWidget(command_bar)
        layout.addLayout(cockpit, 1)
        layout.addWidget(QLabel("Recent signal history"))
        layout.addWidget(self.activity, 1)
        layout.addWidget(make_button("Refresh Assistant", self.refresh), alignment=Qt.AlignRight)
        self.refresh()

    def run_command(self) -> None:
        text = self.command_input.text().strip()
        if not text:
            return
        reply = self.brain.answer(text)
        if reply.action:
            self.command_requested.emit(reply.action)
        else:
            self.storage.log("info", "Assistant", f"User: {text}")
            self.storage.log("info", "Assistant", f"Assistant: {reply.text[:600]}")
            self.last_summary = reply.text
            self.refresh()
            self.show_interaction(text, reply.text)
            if reply.speak:
                self.speech.speak(reply.text[:900])
        self.command_input.clear()

    def speak_summary(self) -> None:
        self.speech.speak(self.last_summary or "The assistant is ready.")

    def show_interaction(self, heard: str, answer: str, confidence: float | None = None) -> None:
        confidence_text = f" Confidence: {confidence:.2f}." if confidence is not None else ""
        self.heard_label.setText(f"Heard: {heard}.{confidence_text}")
        self.last_summary = answer
        self.summary.setText(answer)

    def refresh(self) -> None:
        open_tasks = self.storage.fetch_one("SELECT COUNT(*) AS c FROM tasks WHERE status <> 'Done'")["c"]
        projects = self.storage.fetch_one("SELECT COUNT(*) AS c FROM projects")["c"]
        tools = self.storage.fetch_one("SELECT COUNT(*) AS c FROM tools WHERE enabled = 1")["c"]
        active_escalations = self.storage.fetch_one(
            "SELECT COUNT(*) AS c FROM escalations WHERE status NOT IN ('Acknowledged', 'Resolved', 'Cancelled', 'Failed', 'Expired')"
        )["c"]
        approvals = self.storage.fetch_one(
            "SELECT COUNT(*) AS c FROM escalations WHERE status IN ('Detected', 'Waiting for acknowledgement')"
        )["c"]
        snapshot = connection_snapshot(self.storage)
        google = snapshot["google"]
        codex = snapshot["codex"]
        voice = snapshot["voice"]
        whatsapp = snapshot["whatsapp"]
        gemini = snapshot["gemini"]
        self.last_summary = (
            f"I see {open_tasks} open tasks, {approvals} approval items, "
            f"{active_escalations} active escalations, {projects} registered projects, and {tools} enabled tools."
        )
        self.summary.setText(self.last_summary)
        self.connection_state.setText(
            f"Google {'connected' if google['connected'] else 'setup needed'} | "
            f"Codex {'ready' if codex['available'] else 'missing'} | "
            f"WhatsApp {'connected' if whatsapp['connected'] else 'connecting'} | "
            f"Gemini {'available' if gemini['available'] else 'missing'} | "
            f"Voice {'ready' if voice['connected'] else 'missing'}"
        )
        self.cards["google"].set_body(
            f"{'Connected' if google['connected'] else 'Setup needed'} via {google['via']}. "
            f"Workspace token: {'yes' if google['token_present'] else 'no'}. "
            f"Tasks/Calendar: {'authorized' if google.get('productivity_authorized') else 'connect needed'}."
        )
        self.cards["tools"].set_body(f"{snapshot['tools']['connected']} of {snapshot['tools']['total']} tool paths are available.")
        self.cards["projects"].set_body(f"{snapshot['projects']['connected']} of {snapshot['projects']['total']} project folders are registered and reachable.")
        self.cards["codex"].set_body(f"{codex['version'] if codex['available'] else 'Codex CLI not found'}")
        voice_label = "Edge neural voice" if voice.get("provider") == "edge" else "Windows desktop voice"
        self.cards["voice"].set_body(f"{voice_label} ready. Selected: {voice['selected'] or 'system default'}.")
        self.cards["whatsapp"].set_body(whatsapp["message"])
        self.cards["gemini"].set_body(
            f"CLI {'available' if gemini['available'] else 'not found'}. "
            f"Unknown-message replies are {'enabled' if gemini['enabled'] else 'disabled'}.")
        self.cards["attention"].set_body(f"{approvals} approval items and {active_escalations} active incidents need review.")
        rows = self.storage.fetch_all("SELECT ts, level, source, message FROM activity ORDER BY id DESC LIMIT 12")
        set_table_rows(self.activity, ["Time", "Level", "Source", "Message"], [[format_local_timestamp(r["ts"]), r["level"], r["source"], r["message"]] for r in rows])


class ToolsPage(BasePage):
    title = "Connected Tools"

    def __init__(self, storage: Storage) -> None:
        super().__init__(storage)
        self.registry = ToolRegistry(storage)
        layout = QVBoxLayout(self)
        self.table = QTableWidget()
        configure_table(self.table)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        buttons = QHBoxLayout()
        buttons.addWidget(make_button("Refresh", self.refresh))
        buttons.addWidget(make_button("Test Connection", self.test_selected))
        buttons.addWidget(make_button("Test All", self.test_all))
        buttons.addWidget(make_button("Open Tool", self.open_selected))
        buttons.addWidget(make_button("Run Safe Command", self.run_safe_selected))
        buttons.addStretch()
        layout.addLayout(buttons)
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.table)
        splitter.addWidget(self.details)
        splitter.setSizes([420, 220])
        layout.addWidget(splitter)
        self.table.itemSelectionChanged.connect(self.show_details)
        self.refresh()

    def refresh(self) -> None:
        tools = self.registry.list_tools()
        rows = [
            [tool["id"], tool["name"], tool["kind"], tool["connection_status"], "yes" if tool["enabled"] else "no", tool["last_run"], tool["path"]]
            for tool in tools
        ]
        set_table_rows(self.table, ["ID", "Name", "Kind", "Status", "Enabled", "Last run", "Path"], rows)
        self.show_details()

    def current_tool_id(self) -> str | None:
        value = selected_value(self.table, 0)
        if not value:
            QMessageBox.information(self, "Select Tool", "Select a tool first.")
        return value

    def show_details(self) -> None:
        tool_id = selected_value(self.table, 0)
        if not tool_id:
            self.details.setPlainText("")
            return
        tool = self.registry.get_tool(tool_id)
        if not tool:
            return
        payload = {
            "name": tool["name"],
            "path": tool["path"],
            "path_exists": Path(tool["path"]).exists(),
            "capabilities": tool["capabilities"],
            "permissions": tool["permissions"],
            "sensitive_paths": tool["sensitive_paths"],
            "test_command": tool["test_command"],
            "last_error": tool["last_error"],
        }
        self.details.setPlainText(json.dumps(payload, indent=2))

    def test_selected(self) -> None:
        tool_id = self.current_tool_id()
        if not tool_id:
            return
        self.notify("Tools", f"Testing {tool_id}...")
        result = self.registry.test_tool(tool_id)
        self.refresh()
        self.details.setPlainText(result.combined_output or ("OK" if result.ok else "No output."))
        state = "OK" if result.ok else "needs attention"
        self.notify("Tools", f"{tool_id} test {state}.")

    def test_all(self) -> None:
        self.notify("Tools", "Testing all connected tools...")
        outputs = []
        ok_count = 0
        for tool in self.registry.list_tools():
            result = self.registry.test_tool(tool["id"])
            state = "OK" if result.ok else "NEEDS ATTENTION"
            if result.ok:
                ok_count += 1
            outputs.append(f"[{state}] {tool['name']}\n{result.combined_output or 'No output.'}")
            QApplication.processEvents()
        self.refresh()
        self.details.setPlainText("\n\n".join(outputs))
        total = len(outputs)
        self.notify("Tools", f"Checked {total} tools: {ok_count} OK, {total - ok_count} need attention.")

    def open_selected(self) -> None:
        tool_id = self.current_tool_id()
        if not tool_id:
            return
        result = self.registry.open_tool(tool_id)
        if not result.ok:
            QMessageBox.warning(self, "Open Tool Failed", result.combined_output)
        self.refresh()

    def run_safe_selected(self) -> None:
        tool_id = self.current_tool_id()
        if not tool_id:
            return
        tool = self.registry.get_tool(tool_id)
        commands = tool.get("safe_commands") if tool else []
        if not commands:
            QMessageBox.information(self, "No Safe Command", "This tool does not define a secondary safe command.")
            return
        labels = [command.get("label", f"Command {index + 1}") for index, command in enumerate(commands)]
        label, ok = QInputDialog.getItem(self, "Run Safe Command", "Command", labels, 0, False)
        if not ok:
            return
        index = labels.index(label)
        self.notify("Tools", f"Running {label}...")
        result = self.registry.run_safe_command(tool_id, index)
        self.refresh()
        self.details.setPlainText(result.combined_output or ("OK" if result.ok else "No output."))
        self.notify("Tools", f"{label} {'finished' if result.ok else 'needs attention'}.")


class ProjectsPage(BasePage):
    title = "Development Projects"

    def __init__(self, storage: Storage) -> None:
        super().__init__(storage)
        ensure_runtime_dirs()
        self.process: QProcess | None = None
        self.active_session_id: str | None = None
        self.active_transcript: Path | None = None

        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Vertical)

        top = QWidget()
        top_layout = QVBoxLayout(top)
        self.table = QTableWidget()
        configure_table(self.table)
        top_layout.addWidget(self.table)

        form_box = QGroupBox("Project registration")
        form = QGridLayout(form_box)
        self.name_input = QLineEdit()
        self.path_input = QLineEdit()
        self.agents_input = QLineEdit()
        self.notes_input = QLineEdit()
        form.addWidget(QLabel("Name"), 0, 0)
        form.addWidget(self.name_input, 0, 1)
        form.addWidget(QLabel("Path"), 1, 0)
        form.addWidget(self.path_input, 1, 1)
        form.addWidget(make_button("Browse", self.browse_project), 1, 2)
        form.addWidget(QLabel("AGENTS.md"), 2, 0)
        form.addWidget(self.agents_input, 2, 1)
        form.addWidget(make_button("Find", self.pick_agents), 2, 2)
        form.addWidget(QLabel("Notes"), 3, 0)
        form.addWidget(self.notes_input, 3, 1)
        form.addWidget(make_button("Save Project", self.save_project), 3, 2)
        top_layout.addWidget(form_box)

        codex_box = QGroupBox("Codex session")
        codex_layout = QVBoxLayout(codex_box)
        self.prompt_input = QPlainTextEdit()
        self.prompt_input.setPlaceholderText("Write the Codex task here.")
        self.allow_edits = QCheckBox("Allow file changes")
        self.codex_output = QPlainTextEdit()
        self.codex_output.setReadOnly(True)
        codex_buttons = QHBoxLayout()
        codex_buttons.addWidget(make_button("Preflight", self.preflight_selected))
        codex_buttons.addWidget(make_button("Start Codex", self.start_codex))
        codex_buttons.addWidget(make_button("Stop", self.stop_codex))
        codex_buttons.addWidget(make_button("Resume Last", self.resume_last))
        codex_buttons.addStretch()
        codex_layout.addWidget(self.prompt_input)
        codex_layout.addWidget(self.allow_edits)
        codex_layout.addLayout(codex_buttons)
        codex_layout.addWidget(self.codex_output)

        splitter.addWidget(top)
        splitter.addWidget(codex_box)
        splitter.setSizes([360, 420])
        layout.addWidget(splitter)
        self.table.itemSelectionChanged.connect(self.load_selected_into_form)
        self.refresh()

    def refresh(self) -> None:
        rows = self.storage.fetch_all("SELECT id, name, path, agents_path, last_git_status, updated_at FROM projects ORDER BY updated_at DESC")
        set_table_rows(self.table, ["ID", "Name", "Path", "AGENTS.md", "Git", "Updated"], [[r["id"], r["name"], r["path"], r["agents_path"], r["last_git_status"], format_local_timestamp(r["updated_at"])] for r in rows])

    def selected_project(self) -> dict[str, Any] | None:
        project_id = selected_value(self.table, 0)
        if not project_id:
            QMessageBox.information(self, "Select Project", "Select a project first.")
            return None
        return self.storage.fetch_one("SELECT * FROM projects WHERE id = ?", (project_id,))

    def load_selected_into_form(self) -> None:
        project_id = selected_value(self.table, 0)
        if not project_id:
            return
        row = self.storage.fetch_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        if not row:
            return
        self.name_input.setText(row["name"] or "")
        self.path_input.setText(row["path"] or "")
        self.agents_input.setText(row["agents_path"] or "")
        self.notes_input.setText(row["notes"] or "")

    def browse_project(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Project Folder", self.path_input.text() or "E:\\ESEO")
        if path:
            self.path_input.setText(path)
            if not self.name_input.text().strip():
                self.name_input.setText(Path(path).name)

    def pick_agents(self) -> None:
        root = self.path_input.text().strip()
        if root:
            agents = find_agents(root)
            if agents:
                if len(agents) == 1:
                    self.agents_input.setText(agents[0])
                    return
                picked, ok = QInputDialog.getItem(self, "Select AGENTS.md", "File", agents, 0, False)
                if ok:
                    self.agents_input.setText(picked)
                    return
        path, _ = QFileDialog.getOpenFileName(self, "Select AGENTS.md", root or "E:\\ESEO", "AGENTS.md (AGENTS.md);;Markdown (*.md)")
        if path:
            self.agents_input.setText(path)

    def save_project(self) -> None:
        name = self.name_input.text().strip()
        path = self.path_input.text().strip()
        agents = self.agents_input.text().strip() or None
        notes = self.notes_input.text().strip() or None
        if not name or not path:
            QMessageBox.information(self, "Missing Project", "Project name and path are required.")
            return
        info = preflight_project(path, agents)
        now = utc_now()
        self.storage.execute(
            """
            INSERT INTO projects (name, path, agents_path, notes, last_git_status, last_activity, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                name = excluded.name,
                agents_path = excluded.agents_path,
                notes = excluded.notes,
                last_git_status = excluded.last_git_status,
                last_activity = excluded.last_activity,
                updated_at = excluded.updated_at
            """,
            (name, path, agents, notes, info["git"]["status"], info["recent_activity"], now, now),
        )
        self.storage.log("info", "Projects", f"Registered project: {name}", {"path": path})
        self.refresh()
        self.codex_output.setPlainText(json.dumps(info, indent=2))

    def preflight_selected(self) -> None:
        project = self.selected_project()
        if not project:
            return
        info = preflight_project(project["path"], project["agents_path"])
        self.codex_output.setPlainText(json.dumps(info, indent=2))
        self.storage.execute(
            "UPDATE projects SET last_git_status = ?, last_activity = ?, updated_at = ? WHERE id = ?",
            (info["git"]["status"], info["recent_activity"], utc_now(), project["id"]),
        )
        self.storage.log("info", "Codex", f"Preflight checked {project['name']}", {"warnings": info["warnings"]})
        self.refresh()

    def start_codex(self) -> None:
        project = self.selected_project()
        if not project:
            return
        if self.process and self.process.state() != QProcess.NotRunning:
            QMessageBox.information(self, "Codex Running", "Stop the current Codex process before starting another one.")
            return
        user_prompt = self.prompt_input.toPlainText().strip()
        if not user_prompt:
            QMessageBox.information(self, "Missing Prompt", "Write a Codex task first.")
            return
        info = preflight_project(project["path"], project["agents_path"])
        if not info["path_exists"] or not info["codex"]["available"]:
            self.codex_output.setPlainText(json.dumps(info, indent=2))
            QMessageBox.warning(self, "Preflight Failed", "Project path or Codex CLI is not available.")
            return
        if self.allow_edits.isChecked() and info["git"]["status"] == "dirty":
            response = QMessageBox.question(
                self,
                "Dirty Worktree",
                "This project has uncommitted changes. Start Codex with file changes allowed?",
            )
            if response != QMessageBox.Yes:
                return

        prompt = build_codex_prompt(user_prompt, project["agents_path"], self.allow_edits.isChecked())
        session_id = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        transcript = SESSION_DIR / f"codex-{session_id}.log"
        self.active_session_id = session_id
        self.active_transcript = transcript
        self.storage.execute(
            """
            INSERT INTO codex_sessions (id, project_id, prompt, allow_edits, status, started_at, transcript_path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, project["id"], prompt, 1 if self.allow_edits.isChecked() else 0, "Running", utc_now(), str(transcript)),
        )

        codex_path = codex_status()["path"]
        sandbox = "workspace-write" if self.allow_edits.isChecked() else "read-only"
        args = [
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            codex_path,
            "exec",
            "-C",
            project["path"],
            "--sandbox",
            sandbox,
            "-",
        ]
        self.process = QProcess(self)
        self.process.setProgram("powershell")
        self.process.setArguments(args)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_codex_output)
        self.process.finished.connect(self.codex_finished)
        self.codex_output.setPlainText(f"Starting Codex session {session_id}...\n")
        self.process.start()
        if not self.process.waitForStarted(5000):
            self.codex_output.appendPlainText("Codex did not start.")
            self.storage.execute("UPDATE codex_sessions SET status = ?, ended_at = ?, last_error = ? WHERE id = ?", ("Failed", utc_now(), "QProcess failed to start", session_id))
            return
        self.process.write(prompt.encode("utf-8"))
        self.process.closeWriteChannel()
        self.storage.log("info", "Codex", f"Started Codex session for {project['name']}", {"session_id": session_id})
        self.notify("Codex", f"Started session {session_id} for {project['name']}.")

    def read_codex_output(self) -> None:
        if not self.process:
            return
        text = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if not text:
            return
        self.codex_output.moveCursor(QTextCursor.End)
        self.codex_output.insertPlainText(text)
        if self.active_transcript:
            with self.active_transcript.open("a", encoding="utf-8") as handle:
                handle.write(text)

    def codex_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        status = "Completed" if exit_code == 0 and exit_status == QProcess.NormalExit else "Failed"
        if self.active_session_id:
            self.storage.execute(
                "UPDATE codex_sessions SET status = ?, ended_at = ?, last_error = ? WHERE id = ?",
                (status, utc_now(), None if status == "Completed" else f"Exit code {exit_code}", self.active_session_id),
            )
            self.storage.log("info" if status == "Completed" else "warning", "Codex", f"Session {self.active_session_id} {status.lower()}")
        self.codex_output.appendPlainText(f"\nCodex session finished: {status} (exit {exit_code}).")
        self.notify("Codex", f"Session {self.active_session_id or ''} {status.lower()} (exit {exit_code}).")

    def stop_codex(self) -> None:
        if not self.process or self.process.state() == QProcess.NotRunning:
            QMessageBox.information(self, "Codex", "No Codex process is running.")
            return
        self.process.kill()
        if self.active_session_id:
            self.storage.execute("UPDATE codex_sessions SET status = ?, ended_at = ? WHERE id = ?", ("Stopped", utc_now(), self.active_session_id))
        self.storage.log("warning", "Codex", "Codex process stopped by user.")
        self.notify("Codex", "Codex session stopped.")

    def resume_last(self) -> None:
        project = self.selected_project()
        if not project:
            return
        codex = codex_status()
        if not codex["available"]:
            QMessageBox.warning(self, "Codex", "Codex CLI was not found on PATH.")
            return
        self.codex_output.setPlainText("Opening Codex resume picker in a terminal window...\n")
        QProcess.startDetached(
            "powershell",
            [
                "-NoExit",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                codex["path"],
                "resume",
                "--last",
                "-C",
                project["path"],
            ],
        )
        self.storage.log("info", "Codex", f"Opened Codex resume for {project['name']}")


class CodexSessionsPage(BasePage):
    title = "Codex Sessions"

    def __init__(self, storage: Storage) -> None:
        super().__init__(storage)
        layout = QVBoxLayout(self)
        self.table = QTableWidget()
        configure_table(self.table)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        buttons = QHBoxLayout()
        buttons.addWidget(make_button("Refresh", self.refresh))
        buttons.addWidget(make_button("Open Transcript", self.open_transcript))
        buttons.addStretch()
        layout.addLayout(buttons)
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.table)
        splitter.addWidget(self.output)
        layout.addWidget(splitter)
        self.table.itemSelectionChanged.connect(self.show_transcript)
        self.refresh()

    def refresh(self) -> None:
        rows = self.storage.fetch_all(
            """
            SELECT s.id, COALESCE(p.name, '') AS project, s.status, s.allow_edits, s.started_at, s.ended_at, s.transcript_path
              FROM codex_sessions s
              LEFT JOIN projects p ON p.id = s.project_id
             ORDER BY s.started_at DESC
            """
        )
        set_table_rows(self.table, ["ID", "Project", "Status", "Edits", "Started", "Ended", "Transcript"], [[r["id"], r["project"], r["status"], "yes" if r["allow_edits"] else "no", format_local_timestamp(r["started_at"]), format_local_timestamp(r["ended_at"]), r["transcript_path"]] for r in rows])

    def show_transcript(self) -> None:
        path = selected_value(self.table, 6)
        if path and Path(path).exists():
            self.output.setPlainText(Path(path).read_text(encoding="utf-8", errors="replace")[-12000:])
        else:
            self.output.setPlainText("")

    def open_transcript(self) -> None:
        path = selected_value(self.table, 6)
        if not path or not Path(path).exists():
            QMessageBox.information(self, "Transcript", "Select a session with a transcript first.")
            return
        QProcess.startDetached("notepad.exe", [path])


class TasksPage(BasePage):
    title = "Tasks"

    def __init__(self, storage: Storage) -> None:
        super().__init__(storage)
        self.google = GoogleProductivityService(storage)
        self.google_task_rows: list[dict[str, Any]] = []
        layout = QVBoxLayout(self)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        form = QGridLayout()
        self.title_input = QLineEdit()
        self.priority_input = QComboBox()
        self.priority_input.addItems(["Normal", "High", "Urgent", "Low"])
        self.due_input = QLineEdit()
        self.contact_input = QLineEdit()
        self.notes_input = QLineEdit()
        form.addWidget(QLabel("Task"), 0, 0)
        form.addWidget(self.title_input, 0, 1)
        form.addWidget(QLabel("Priority"), 0, 2)
        form.addWidget(self.priority_input, 0, 3)
        form.addWidget(QLabel("Due"), 1, 0)
        form.addWidget(self.due_input, 1, 1)
        form.addWidget(QLabel("Contact"), 1, 2)
        form.addWidget(self.contact_input, 1, 3)
        form.addWidget(QLabel("Notes"), 2, 0)
        form.addWidget(self.notes_input, 2, 1, 1, 3)
        actions = QHBoxLayout()
        self.connect_button = make_button("Connect Google Tasks", self.connect_google)
        actions.addWidget(self.connect_button)
        actions.addWidget(make_button("Open Google API Setup", self.open_api_setup))
        actions.addWidget(make_button("Add Task", self.add_task))
        actions.addWidget(make_button("Mark Done", self.mark_done))
        actions.addWidget(make_button("Refresh", self.refresh))
        actions.addStretch()
        self.table = QTableWidget()
        configure_table(self.table)
        layout.addLayout(form)
        layout.addLayout(actions)
        layout.addWidget(self.status_label)
        layout.addWidget(self.table)
        self.refresh()

    def refresh(self) -> None:
        status = self.google.status()
        self.connect_button.setText("Reconnect Google Tasks" if status["authorization_connected"] else "Connect Google Tasks")
        result = self.google.list_tasks(max_results=100)
        if result.ok:
            tasks = result.data or []
            self.google_task_rows = tasks
            self.status_label.setText(f"Google Tasks authorized. Showing {len(tasks)} open Google todos from Google.")
            rows = [
                [
                    task["title"],
                    self.google.display_time(task.get("due", "")) or "",
                    task.get("status", ""),
                    task.get("tasklist", ""),
                    self.google.display_time(task.get("updated", "")) or task.get("updated", ""),
                    "Google Tasks",
                ]
                for task in tasks
            ]
            set_table_rows(self.table, ["Todo", "Due", "Status", "List", "Updated", "Source"], rows)
            return
        self.google_task_rows = []
        status_text = result.message + ("\n" + result.error if result.error else "")
        if status["authorization_connected"]:
            self.status_label.setText(status_text)
        else:
            self.status_label.setText("Google Tasks is not authorized yet. Use Connect Google Tasks.")
        self.refresh_local_tasks()

    def refresh_local_tasks(self) -> None:
        self.google_task_rows = []
        rows = self.storage.fetch_all("SELECT id, title, priority, due_at, status, contact, updated_at FROM tasks ORDER BY status, due_at IS NULL, due_at, id DESC")
        set_table_rows(self.table, ["ID", "Task", "Priority", "Due", "Status", "Contact", "Updated", "Source"], [[r["id"], r["title"], r["priority"], format_local_timestamp(r["due_at"]), r["status"], r["contact"], format_local_timestamp(r["updated_at"]), "Local mirror"] for r in rows])

    def add_task(self) -> None:
        title = self.title_input.text().strip()
        if not title:
            QMessageBox.information(self, "Task", "Task title is required.")
            return
        due_text = self.due_input.text().strip()
        parsed = parse_when(f"{title} {due_text}".strip(), default_hour=9, duration_minutes=30) if due_text else None
        result = self.google.create_task(
            title,
            due=parsed.start if parsed else None,
            notes=self.notes_input.text().strip() or "Created by Noor for Raihan Hossain",
            interactive=False,
        )
        if not result.ok:
            QMessageBox.warning(self, "Google Tasks", result.message + ("\n" + result.error if result.error else ""))
            return
        now = utc_now()
        self.storage.execute(
            "INSERT INTO tasks (title, priority, due_at, contact, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                title,
                self.priority_input.currentText(),
                parsed.start.isoformat() if parsed else due_text or None,
                self.contact_input.text().strip() or None,
                (self.notes_input.text().strip() or "") + "\nGoogle Task",
                now,
                now,
            ),
        )
        self.storage.log("info", "Tasks", f"Added Google task: {title}")
        self.title_input.clear()
        self.notes_input.clear()
        self.refresh()

    def connect_google(self) -> None:
        result = self.google.connect()
        QMessageBox.information(self, "Google Tasks", result.message + ("\n" + result.error if result.error else ""))
        self.refresh()

    def open_api_setup(self) -> None:
        webbrowser.open("https://console.cloud.google.com/apis/library/tasks.googleapis.com")
        webbrowser.open("https://console.cloud.google.com/apis/library/calendar-json.googleapis.com")

    def mark_done(self) -> None:
        if self.google_task_rows:
            row = self.table.currentRow()
            if row < 0 or row >= len(self.google_task_rows):
                QMessageBox.information(self, "Task", "Select a Google task first.")
                return
            task = self.google_task_rows[row]
            result = self.google.complete_task(str(task.get("tasklist_id", "")), str(task.get("id", "")))
            if not result.ok:
                QMessageBox.warning(self, "Google Tasks", result.message + ("\n" + result.error if result.error else ""))
                return
            self.refresh()
            return
        task_id = selected_value(self.table, 0)
        if not task_id:
            QMessageBox.information(self, "Task", "Select a task first.")
            return
        self.storage.execute("UPDATE tasks SET status = 'Done', updated_at = ? WHERE id = ?", (utc_now(), task_id))
        self.storage.log("info", "Tasks", f"Marked task {task_id} done")
        self.refresh()


class CalendarPage(BasePage):
    title = "Calendar"

    def __init__(self, storage: Storage) -> None:
        super().__init__(storage)
        self.google = GoogleProductivityService(storage)
        layout = QVBoxLayout(self)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.table = QTableWidget()
        configure_table(self.table)
        buttons = QHBoxLayout()
        self.connect_button = make_button("Connect Google Calendar", self.connect_google)
        buttons.addWidget(self.connect_button)
        buttons.addWidget(make_button("Open Google API Setup", self.open_api_setup))
        buttons.addWidget(make_button("Refresh", self.refresh))
        buttons.addStretch()
        layout.addLayout(buttons)
        layout.addWidget(self.status_label)
        layout.addWidget(self.table)
        self.refresh()

    def refresh(self) -> None:
        status = self.google.status()
        self.connect_button.setText("Reconnect Google Calendar" if status["authorization_connected"] else "Connect Google Calendar")
        result = self.google.list_events(max_results=50)
        if result.ok:
            events = result.data or []
            reminders = [event for event in events if event.get("kind") == "Reminder" or event.get("reminders")]
            self.status_label.setText(
                f"Google Calendar authorized. Showing {len(events)} upcoming events and {len(reminders)} reminder-enabled items from Google."
            )
            rows = [
                [
                    event["title"],
                    event["start_display"],
                    event["end_display"],
                    event["kind"],
                    event.get("reminders", ""),
                    "Google Calendar",
                ]
                for event in events
            ]
            set_table_rows(self.table, ["Event", "Start", "End", "Type", "Reminders", "Source"], rows)
            return
        status_text = result.message + ("\n" + result.error if result.error else "")
        self.status_label.setText(status_text if status["authorization_connected"] else "Google Calendar is not authorized yet. Use Connect Google Calendar.")
        rows = self.storage.fetch_all("SELECT title, priority, due_at, status, contact FROM tasks WHERE due_at IS NOT NULL ORDER BY due_at")
        set_table_rows(self.table, ["Item", "Priority", "Due", "Status", "Contact", "Source"], [[r["title"], r["priority"], format_local_timestamp(r["due_at"]), r["status"], r["contact"], "Local mirror"] for r in rows])

    def connect_google(self) -> None:
        result = self.google.connect()
        QMessageBox.information(self, "Google Calendar", result.message + ("\n" + result.error if result.error else ""))
        self.refresh()

    def open_api_setup(self) -> None:
        webbrowser.open("https://console.cloud.google.com/apis/library/tasks.googleapis.com")
        webbrowser.open("https://console.cloud.google.com/apis/library/calendar-json.googleapis.com")


class RulesPage(BasePage):
    title = "Rules"

    def __init__(self, storage: Storage) -> None:
        super().__init__(storage)
        layout = QVBoxLayout(self)
        form = QGridLayout()
        self.name_input = QLineEdit()
        self.category_input = QComboBox()
        self.category_input.addItems(["Reply", "Contact", "Group", "Team Reminder", "Project", "Codex", "Tool Permission"])
        self.pattern_input = QLineEdit()
        self.action_input = QLineEdit()
        self.approval_input = QCheckBox("Require approval")
        self.approval_input.setChecked(True)
        form.addWidget(QLabel("Name"), 0, 0)
        form.addWidget(self.name_input, 0, 1)
        form.addWidget(QLabel("Category"), 0, 2)
        form.addWidget(self.category_input, 0, 3)
        form.addWidget(QLabel("Pattern"), 1, 0)
        form.addWidget(self.pattern_input, 1, 1)
        form.addWidget(QLabel("Action"), 1, 2)
        form.addWidget(self.action_input, 1, 3)
        form.addWidget(self.approval_input, 2, 1)
        buttons = QHBoxLayout()
        buttons.addWidget(make_button("Add Rule", self.add_rule))
        buttons.addWidget(make_button("Disable Rule", self.disable_rule))
        buttons.addWidget(make_button("Test Sample", self.test_sample))
        buttons.addWidget(make_button("Refresh", self.refresh))
        buttons.addStretch()
        self.table = QTableWidget()
        configure_table(self.table)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.table)
        layout.addWidget(self.output)
        self.refresh()

    def refresh(self) -> None:
        rows = self.storage.fetch_all("SELECT id, name, category, pattern, action, require_approval, enabled FROM rules ORDER BY category, name")
        set_table_rows(self.table, ["ID", "Name", "Category", "Pattern", "Action", "Approval", "Enabled"], [[r["id"], r["name"], r["category"], r["pattern"], r["action"], "yes" if r["require_approval"] else "no", "yes" if r["enabled"] else "no"] for r in rows])

    def add_rule(self) -> None:
        name = self.name_input.text().strip()
        pattern = self.pattern_input.text().strip()
        action = self.action_input.text().strip()
        if not name or not pattern or not action:
            QMessageBox.information(self, "Rule", "Name, pattern, and action are required.")
            return
        now = utc_now()
        self.storage.execute(
            """
            INSERT INTO rules (name, category, pattern, action, require_approval, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (name, self.category_input.currentText(), pattern, action, 1 if self.approval_input.isChecked() else 0, now, now),
        )
        self.storage.log("info", "Rules", f"Added rule: {name}")
        self.refresh()

    def disable_rule(self) -> None:
        rule_id = selected_value(self.table, 0)
        if not rule_id:
            QMessageBox.information(self, "Rule", "Select a rule first.")
            return
        self.storage.execute("UPDATE rules SET enabled = 0, updated_at = ? WHERE id = ?", (utc_now(), rule_id))
        self.storage.log("info", "Rules", f"Disabled rule {rule_id}")
        self.refresh()

    def test_sample(self) -> None:
        sample, ok = QInputDialog.getMultiLineText(self, "Rule Test", "Sample message or situation")
        if not ok:
            return
        rules = self.storage.fetch_all("SELECT * FROM rules WHERE enabled = 1")
        matches = []
        lowered = sample.lower()
        for rule in rules:
            if rule["pattern"].lower() in lowered:
                matches.append(
                    {
                        "rule": rule["name"],
                        "category": rule["category"],
                        "action": rule["action"],
                        "requires_approval": bool(rule["require_approval"]),
                    }
                )
        sensitive = sorted(word for word in SENSITIVE_WORDS if word in lowered)
        decision = "approval_required" if sensitive or any(match["requires_approval"] for match in matches) else "no_rule_action"
        self.output.setPlainText(json.dumps({"decision": decision, "sensitive_terms": sensitive, "matches": matches}, indent=2))


class KnowledgePage(BasePage):
    title = "Knowledge"

    def __init__(self, storage: Storage) -> None:
        super().__init__(storage)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title_input = QLineEdit()
        self.category_input = QLineEdit()
        self.body_input = QPlainTextEdit()
        self.trusted_input = QCheckBox("Trusted")
        self.trusted_input.setChecked(True)
        form.addRow("Title", self.title_input)
        form.addRow("Category", self.category_input)
        form.addRow("Body", self.body_input)
        form.addRow("", self.trusted_input)
        actions = QHBoxLayout()
        actions.addWidget(make_button("Add Knowledge", self.add_knowledge))
        actions.addWidget(make_button("Refresh", self.refresh))
        actions.addStretch()
        self.table = QTableWidget()
        configure_table(self.table)
        layout.addLayout(form)
        layout.addLayout(actions)
        layout.addWidget(self.table)
        self.refresh()

    def refresh(self) -> None:
        rows = self.storage.fetch_all("SELECT id, title, category, trusted, updated_at FROM knowledge ORDER BY updated_at DESC")
        set_table_rows(self.table, ["ID", "Title", "Category", "Trusted", "Updated"], [[r["id"], r["title"], r["category"], "yes" if r["trusted"] else "no", format_local_timestamp(r["updated_at"])] for r in rows])

    def add_knowledge(self) -> None:
        title = self.title_input.text().strip()
        body = self.body_input.toPlainText().strip()
        if not title or not body:
            QMessageBox.information(self, "Knowledge", "Title and body are required.")
            return
        now = utc_now()
        self.storage.execute(
            "INSERT INTO knowledge (title, category, body, trusted, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (title, self.category_input.text().strip() or "General", body, 1 if self.trusted_input.isChecked() else 0, now, now),
        )
        self.storage.log("info", "Knowledge", f"Added knowledge: {title}")
        self.title_input.clear()
        self.body_input.clear()
        self.refresh()


class EscalationsPage(BasePage):
    title = "Urgent Escalations"

    def __init__(self, storage: Storage) -> None:
        super().__init__(storage)
        layout = QVBoxLayout(self)
        self.table = QTableWidget()
        configure_table(self.table)
        actions = QHBoxLayout()
        actions.addWidget(make_button("Create Manual Incident", self.create_incident))
        actions.addWidget(make_button("Acknowledge", self.acknowledge))
        actions.addWidget(make_button("Cancel", self.cancel))
        actions.addWidget(make_button("Refresh", self.refresh))
        actions.addStretch()
        layout.addLayout(actions)
        layout.addWidget(self.table)
        self.refresh()

    def refresh(self) -> None:
        rows = self.storage.fetch_all("SELECT id, title, priority, status, source, summary, updated_at FROM escalations ORDER BY updated_at DESC")
        set_table_rows(self.table, ["ID", "Title", "Priority", "Status", "Source", "Summary", "Updated"], [[r["id"], r["title"], r["priority"], r["status"], r["source"], r["summary"], format_local_timestamp(r["updated_at"])] for r in rows])

    def create_incident(self) -> None:
        title, ok = QInputDialog.getText(self, "Manual Incident", "Title")
        if not ok or not title.strip():
            return
        summary, ok = QInputDialog.getMultiLineText(self, "Manual Incident", "Privacy-safe summary")
        if not ok:
            return
        incident_id = "INC-" + datetime.utcnow().strftime("%Y%m%d%H%M%S")
        now = utc_now()
        self.storage.execute(
            """
            INSERT INTO escalations (id, title, status, priority, source, summary, policy_json, created_at, updated_at)
            VALUES (?, ?, 'Detected', 'High', 'Manual', ?, ?, ?, ?)
            """,
            (incident_id, title.strip(), summary.strip(), dumps({"manual": True}), now, now),
        )
        self.storage.log("warning", "Escalation", f"Created manual incident {incident_id}")
        self.refresh()

    def update_status(self, status: str) -> None:
        incident_id = selected_value(self.table, 0)
        if not incident_id:
            QMessageBox.information(self, "Escalation", "Select an incident first.")
            return
        self.storage.execute("UPDATE escalations SET status = ?, updated_at = ? WHERE id = ?", (status, utc_now(), incident_id))
        self.storage.log("info", "Escalation", f"{incident_id} -> {status}")
        self.refresh()

    def acknowledge(self) -> None:
        self.update_status("Acknowledged")

    def cancel(self) -> None:
        self.update_status("Cancelled")


class WhatsAppPage(BasePage):
    title = "WhatsApp Inbox"

    def __init__(self, storage: Storage) -> None:
        super().__init__(storage)
        self.whatsapp = WhatsAppWebService(storage)
        layout = QVBoxLayout(self)
        self.status_label = QLabel("Noor watches unread direct chats in her dedicated WhatsApp profile. Only matching WhatsApp rules can reply; unmatched messages are ignored.")
        self.status_label.setWordWrap(True)
        actions = QHBoxLayout()
        actions.addWidget(make_button("Open Dedicated WhatsApp", self.open_dedicated_profile))
        actions.addWidget(make_button("Check Connection", self.check_connection))
        actions.addWidget(make_button("Refresh", self.refresh))
        actions.addStretch()
        self.table = QTableWidget()
        configure_table(self.table)
        layout.addWidget(self.status_label)
        layout.addLayout(actions)
        layout.addWidget(self.table)
        self.refresh()

    def open_dedicated_profile(self) -> None:
        result = self.whatsapp.launch_login()
        self.status_label.setText(result.message if result.ok else f"{result.message} {result.error}".strip())

    def check_connection(self) -> None:
        result = self.whatsapp.status()
        self.status_label.setText(result.message if result.ok else f"{result.message} {result.error}".strip())

    def process_unread_auto_replies(self) -> None:
        result = self.whatsapp.process_unread_auto_replies()
        if result:
            self.status_label.setText(result.message if result.ok else f"{result.message} {result.error}".strip())
            self.refresh()

    def refresh(self) -> None:
        rows = self.storage.fetch_all("SELECT chat_name, source, status, created_at FROM whatsapp_auto_replies ORDER BY id DESC LIMIT 100")
        set_table_rows(self.table, ["Chat", "Reply source", "Outcome", "Time"], [[r["chat_name"], r["source"], r["status"], format_local_timestamp(r["created_at"])] for r in rows])


class WhatsAppRulesPage(BasePage):
    title = "WhatsApp Rules"

    def __init__(self, storage: Storage) -> None:
        super().__init__(storage)
        self.current_triggers: list[dict[str, Any]] = []
        self.current_actions: list[dict[str, Any]] = []
        self.editor_rule_id = ""
        self.visible_rule_ids: list[str] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        self.toolbar = QFrame()
        toolbar = self.toolbar
        toolbar.setObjectName("rulesToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(10, 8, 10, 8)
        toolbar_layout.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search rules, triggers, contacts, or actions")
        self.search_input.addAction(QIcon(str(ICON_DIR / "search.svg")), QLineEdit.LeadingPosition)
        self.search_input.textChanged.connect(self.refresh)
        self.state_filter = QComboBox()
        self.state_filter.addItem("All states", "all")
        self.state_filter.addItem("Enabled", "enabled")
        self.state_filter.addItem("Disabled", "disabled")
        self.state_filter.currentIndexChanged.connect(self.refresh)
        self.trigger_filter = QComboBox()
        for label, value in [("All triggers", "all"), ("Message", "message"), ("Call", "call"), ("Time", "time"), ("Date", "date")]:
            self.trigger_filter.addItem(label, value)
        self.trigger_filter.currentIndexChanged.connect(self.refresh)
        self.action_filter = QComboBox()
        for label, value in [("All actions", "all"), ("Reply", "reply"), ("Noor brain", "assistant"), ("AI", "ai"), ("Tool", "tool"), ("Log", "log")]:
            self.action_filter.addItem(label, value)
        self.action_filter.currentIndexChanged.connect(self.refresh)
        toolbar_layout.addWidget(self.search_input, 2)
        toolbar_layout.addWidget(self.state_filter)
        toolbar_layout.addWidget(self.trigger_filter)
        toolbar_layout.addWidget(self.action_filter)
        toolbar_layout.addSpacing(8)
        toolbar_layout.addWidget(self._tool_button("Add", "plus.svg", self.show_new_rule_editor, "Add new WhatsApp rule", primary=True))
        toolbar_layout.addWidget(self._tool_button("Edit", "edit.svg", self.edit_selected_rule, "Edit selected rule"))
        toolbar_layout.addWidget(self._tool_button("Enable", "check.svg", lambda: self.bulk_set_enabled(True), "Enable selected rules"))
        toolbar_layout.addWidget(self._tool_button("Disable", "pause.svg", lambda: self.bulk_set_enabled(False), "Disable selected rules"))
        toolbar_layout.addWidget(self._tool_button("Delete", "trash.svg", self.delete_selected_rules, "Delete selected rules"))
        toolbar_layout.addWidget(self._tool_button("Refresh", "refresh.svg", self.refresh, "Refresh rules"))
        more_menu = QMenu(self)
        test_action = QAction(QIcon(str(ICON_DIR / "test.svg")), "Test Message", self)
        test_action.triggered.connect(self.test_rule)
        duplicate_action = QAction(QIcon(str(ICON_DIR / "plus.svg")), "Duplicate Selected", self)
        duplicate_action.triggered.connect(self.duplicate_selected_rule)
        more_menu.addAction(test_action)
        more_menu.addAction(duplicate_action)
        self.more_button = self._tool_button("More", "more.svg", None, "More rule actions")
        self.more_button.setMenu(more_menu)
        self.more_button.setPopupMode(QToolButton.InstantPopup)
        toolbar_layout.addWidget(self.more_button)

        self.summary_label = QLabel()
        self.summary_label.setObjectName("rulesMetric")

        self.table = QTableWidget()
        self.table.setObjectName("rulesTable")
        configure_table(self.table)
        self.table.setMinimumHeight(360)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setSortingEnabled(True)
        self.table.itemSelectionChanged.connect(self.update_bulk_status)
        self.table.itemDoubleClicked.connect(self._edit_from_table_item)

        self.editor_panel = QFrame()
        self.editor_panel.setObjectName("ruleEditorPanel")
        editor_layout = QVBoxLayout(self.editor_panel)
        editor_layout.setContentsMargins(12, 8, 12, 10)
        editor_layout.setSpacing(6)

        editor_header = QHBoxLayout()
        self.editor_title = QLabel("New WhatsApp Rule")
        self.editor_title.setObjectName("ruleEditorTitle")
        editor_header.addWidget(self.editor_title)
        editor_header.addStretch()
        editor_header.addWidget(self._tool_button("Save", "check.svg", self.save_rule, "Save rule", primary=True))
        editor_header.addWidget(self._tool_button("Test", "test.svg", self.test_rule, "Test message against rules"))
        editor_header.addWidget(self._tool_button("Close", "close.svg", self.close_editor, "Close editor"))
        editor_layout.addLayout(editor_header)

        top = QGridLayout()
        top.setHorizontalSpacing(8)
        top.setVerticalSpacing(4)
        self.id_input = QLineEdit()
        self.id_input.setPlaceholderText("project-status")
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Project status")
        self.enabled_check = QCheckBox("Enabled")
        self.enabled_check.setChecked(True)
        self.trigger_logic = QComboBox()
        self.trigger_logic.addItem("Any trigger can match", "any")
        self.trigger_logic.addItem("All triggers must match", "all")
        top.addWidget(QLabel("Rule ID"), 0, 0)
        top.addWidget(self.id_input, 0, 1)
        top.addWidget(QLabel("Name"), 0, 2)
        top.addWidget(self.name_input, 0, 3)
        top.addWidget(self.enabled_check, 0, 4)
        top.addWidget(QLabel("Trigger logic"), 1, 0)
        top.addWidget(self.trigger_logic, 1, 1, 1, 2)
        editor_layout.addLayout(top)

        audience_box = QGroupBox("Audience")
        audience_layout = QGridLayout(audience_box)
        self.audience_scope = QComboBox()
        self.audience_scope.addItem("Everyone", "everyone")
        self.audience_scope.addItem("Specific contacts", "contacts")
        self.audience_scope.addItem("Everyone except selected contacts", "except_contacts")
        self.contacts_input = QPlainTextEdit()
        self.contacts_input.setPlaceholderText("One contact, phone number, or WhatsApp chat id per line")
        self.contacts_input.setMaximumHeight(58)
        self.contacts_label = QLabel("Contacts")
        self.audience_scope.currentIndexChanged.connect(self.update_audience_fields)
        audience_layout.addWidget(QLabel("Send for"), 0, 0)
        audience_layout.addWidget(self.audience_scope, 0, 1)
        audience_layout.addWidget(self.contacts_label, 1, 0)
        audience_layout.addWidget(self.contacts_input, 1, 1)

        trigger_box = QGroupBox("Triggers")
        trigger_layout = QVBoxLayout(trigger_box)
        trigger_buttons = QHBoxLayout()
        trigger_buttons.addWidget(self._tool_button("Add Trigger", "plus.svg", self.add_trigger, "Add message, call, time, or date trigger", primary=True))
        trigger_buttons.addWidget(self._tool_button("Remove", "trash.svg", self.remove_trigger, "Remove selected trigger"))
        trigger_buttons.addStretch()
        self.triggers_table = QTableWidget()
        configure_table(self.triggers_table)
        self.triggers_table.setMinimumHeight(116)
        self.triggers_table.setMaximumHeight(140)
        trigger_layout.addLayout(trigger_buttons)
        trigger_layout.addWidget(self.triggers_table)

        action_box = QGroupBox("Actions")
        action_layout = QVBoxLayout(action_box)
        action_buttons = QHBoxLayout()
        action_buttons.addWidget(self._tool_button("Add Action", "plus.svg", self.add_action, "Add reply, AI, tool, or log action", primary=True))
        action_buttons.addWidget(self._tool_button("Remove", "trash.svg", self.remove_action, "Remove selected action"))
        action_buttons.addStretch()
        self.actions_table = QTableWidget()
        configure_table(self.actions_table)
        self.actions_table.setMinimumHeight(116)
        self.actions_table.setMaximumHeight(140)
        action_layout.addLayout(action_buttons)
        action_layout.addWidget(self.actions_table)

        self.output = QPlainTextEdit()
        self.output.setObjectName("rulesOutput")
        self.output.setReadOnly(True)
        self.output.setMaximumHeight(76)
        self.output.hide()
        editor_layout.addWidget(audience_box)
        editor_layout.addWidget(trigger_box)
        editor_layout.addWidget(action_box)

        layout.addWidget(toolbar)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.table)
        layout.addWidget(self.output)
        layout.addWidget(self.editor_panel)
        self.editor_panel.hide()
        self.refresh()
        self.reset_rule_editor()

    def _tool_button(self, text: str, icon_name: str, callback: Any | None = None, tooltip: str = "", *, primary: bool = False) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setIcon(QIcon(str(ICON_DIR / icon_name)))
        button.setIconSize(QSize(16, 16))
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip(tooltip or text)
        button.setObjectName("rulesPrimaryButton" if primary else "rulesToolButton")
        if callback:
            button.clicked.connect(callback)
        return button

    def set_editor_mode(self, editing: bool) -> None:
        self.toolbar.setVisible(not editing)
        self.summary_label.setVisible(not editing)
        self.table.setVisible(not editing)
        self.editor_panel.setVisible(editing)
        if editing:
            self.output.hide()

    def update_audience_fields(self) -> None:
        show_contacts = str(self.audience_scope.currentData() or "everyone") != "everyone"
        self.contacts_label.setVisible(show_contacts)
        self.contacts_input.setVisible(show_contacts)

    def apply_rules_table_layout(self) -> None:
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        for column in range(self.table.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.Interactive)
        widths = {0: 150, 1: 180, 2: 72, 3: 150, 5: 260}
        for column, width in widths.items():
            if column < self.table.columnCount():
                self.table.setColumnWidth(column, width)
        if self.table.columnCount() > 4:
            header.setSectionResizeMode(4, QHeaderView.Stretch)

    def apply_builder_table_layout(self, table: QTableWidget) -> None:
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        if table.columnCount() > 0:
            header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        if table.columnCount() > 1:
            header.setSectionResizeMode(1, QHeaderView.Stretch)

    def load_rules(self) -> list[dict[str, Any]]:
        return [normalize_rule(rule) for rule in load_whatsapp_rules()]

    def write_rules(self, rules: list[dict[str, Any]]) -> None:
        write_whatsapp_rules(rules)

    def refresh(self) -> None:
        rules = self.filtered_rules()
        self.visible_rule_ids = [str(rule.get("id") or "") for rule in rules]
        self.table.setSortingEnabled(False)
        rows = []
        full_summaries: list[tuple[str, str]] = []
        for rule in rules:
            trigger_summary = whatsapp_trigger_summary(rule)
            action_summary = whatsapp_action_summary(rule)
            full_summaries.append((trigger_summary, action_summary))
            rows.append(
                [
                    rule["id"],
                    rule.get("name") or rule["id"],
                    "On" if rule.get("enabled", True) else "Off",
                    whatsapp_audience_summary(rule),
                    compact_text(trigger_summary, 92),
                    compact_text(action_summary, 82),
                ]
            )
        set_table_rows(self.table, ["ID", "Name", "State", "Audience", "Triggers", "Actions"], rows)
        for row_index, (trigger_summary, action_summary) in enumerate(full_summaries):
            trigger_item = self.table.item(row_index, 4)
            action_item = self.table.item(row_index, 5)
            if trigger_item:
                trigger_item.setToolTip(trigger_summary)
            if action_item:
                action_item.setToolTip(action_summary)
        self.apply_rules_table_layout()
        self.table.setSortingEnabled(True)
        all_rules = self.load_rules()
        enabled = sum(1 for rule in all_rules if rule.get("enabled", True))
        self.summary_label.setText(f"{len(rules)} shown of {len(all_rules)} rules | {enabled} enabled | {len(all_rules) - enabled} disabled")
        self.update_bulk_status()
        self.refresh_builder_tables()

    def filtered_rules(self) -> list[dict[str, Any]]:
        query = self.search_input.text().strip().casefold()
        state = str(self.state_filter.currentData() or "all")
        trigger_filter = str(self.trigger_filter.currentData() or "all")
        action_filter = str(self.action_filter.currentData() or "all")
        rules = []
        for rule in self.load_rules():
            enabled = bool(rule.get("enabled", True))
            if state == "enabled" and not enabled:
                continue
            if state == "disabled" and enabled:
                continue
            trigger_types = {str(trigger.get("type") or "message").casefold() for trigger in rule.get("triggers", [])}
            if trigger_filter != "all" and trigger_filter not in trigger_types:
                continue
            action_types = {str(action.get("type") or "reply").casefold() for action in rule.get("actions", [])}
            if action_filter != "all":
                if action_filter == "ai":
                    if not action_types.intersection({"ai", "research", "gemini", "codex"}):
                        continue
                elif action_filter == "assistant":
                    if not action_types.intersection({"assistant", "brain"}):
                        continue
                elif action_filter == "tool":
                    if not action_types.intersection({"tool", "safe_tool"}):
                        continue
                elif action_filter not in action_types:
                    continue
            if query:
                haystack = " ".join(
                    [
                        str(rule.get("id") or ""),
                        str(rule.get("name") or ""),
                        whatsapp_audience_summary(rule),
                        whatsapp_trigger_summary(rule),
                        whatsapp_action_summary(rule),
                        contacts_to_text(rule.get("audience", {}).get("contacts", []) if isinstance(rule.get("audience"), dict) else []),
                    ]
                ).casefold()
                if query not in haystack:
                    continue
            rules.append(rule)
        return rules

    def load_selected(self) -> None:
        self.edit_selected_rule()

    def _edit_from_table_item(self, *_args: Any) -> None:
        self.edit_selected_rule()

    def selected_rule_ids(self) -> list[str]:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        if not rows and self.table.currentRow() >= 0:
            rows = [self.table.currentRow()]
        rule_ids: list[str] = []
        for row in rows:
            item = self.table.item(row, 0)
            if item and item.text():
                rule_ids.append(item.text())
        return list(dict.fromkeys(rule_ids))

    def update_bulk_status(self) -> None:
        selected = len(self.selected_rule_ids())
        base = re.sub(r" \| \d+ selected$", "", self.summary_label.text())
        suffix = f" | {selected} selected" if selected else ""
        if base:
            self.summary_label.setText(base + suffix)

    def show_new_rule_editor(self) -> None:
        self.reset_rule_editor()
        self.editor_title.setText("New WhatsApp Rule")
        self.set_editor_mode(True)
        self.id_input.setFocus()

    def edit_selected_rule(self) -> None:
        rule_ids = self.selected_rule_ids()
        if not rule_ids:
            QMessageBox.information(self, "WhatsApp Rule", "Select one rule to edit.")
            return
        if len(rule_ids) > 1:
            QMessageBox.information(self, "WhatsApp Rule", "Select only one rule to edit.")
            return
        self.load_rule_into_editor(rule_ids[0])

    def load_rule_into_editor(self, rule_id: str) -> None:
        rule = next((item for item in self.load_rules() if item.get("id") == rule_id), {})
        if not rule:
            QMessageBox.information(self, "WhatsApp Rule", "The selected rule was not found.")
            return
        self.editor_rule_id = rule_id
        self.editor_title.setText(f"Edit Rule: {rule_id}")
        self.set_editor_mode(True)
        self.populate_editor(rule)

    def populate_editor(self, rule: dict[str, Any]) -> None:
        rule_id = str(rule.get("id") or "")
        self.id_input.setText(rule_id)
        self.name_input.setText(str(rule.get("name") or rule_id))
        self.enabled_check.setChecked(bool(rule.get("enabled", True)))
        self.trigger_logic.setCurrentIndex(1 if str(rule.get("trigger_logic")) == "all" else 0)
        audience = rule.get("audience") if isinstance(rule.get("audience"), dict) else {"scope": "everyone", "contacts": []}
        scope = str(audience.get("scope") or "everyone")
        for index in range(self.audience_scope.count()):
            if self.audience_scope.itemData(index) == scope:
                self.audience_scope.setCurrentIndex(index)
                break
        self.contacts_input.setPlainText(contacts_to_text(audience.get("contacts", [])))
        self.update_audience_fields()
        self.current_triggers = [dict(trigger) for trigger in rule.get("triggers", []) if isinstance(trigger, dict)]
        self.current_actions = [dict(action) for action in rule.get("actions", []) if isinstance(action, dict)]
        self.refresh_builder_tables()

    def duplicate_selected_rule(self) -> None:
        rule_ids = self.selected_rule_ids()
        if len(rule_ids) != 1:
            QMessageBox.information(self, "WhatsApp Rule", "Select one rule to duplicate.")
            return
        source = next((item for item in self.load_rules() if item.get("id") == rule_ids[0]), None)
        if not source:
            return
        duplicate = dict(source)
        existing = {str(rule.get("id") or "") for rule in self.load_rules()}
        base = f"{rule_ids[0]}-copy"
        candidate = base
        counter = 2
        while candidate in existing:
            candidate = f"{base}-{counter}"
            counter += 1
        duplicate["id"] = candidate
        duplicate["name"] = f"{source.get('name') or rule_ids[0]} Copy"
        self.editor_rule_id = ""
        self.editor_title.setText(f"Duplicate Rule: {rule_ids[0]}")
        self.set_editor_mode(True)
        self.populate_editor(duplicate)

    def close_editor(self) -> None:
        self.set_editor_mode(False)
        self.output.hide()

    def reset_rule_editor(self) -> None:
        self.editor_rule_id = ""
        row = self.table.currentRow()
        if row >= 0:
            self.table.clearSelection()
        self.id_input.clear()
        self.name_input.clear()
        self.enabled_check.setChecked(True)
        self.trigger_logic.setCurrentIndex(0)
        self.audience_scope.setCurrentIndex(0)
        self.contacts_input.clear()
        self.update_audience_fields()
        self.current_triggers = []
        self.current_actions = []
        self.refresh_builder_tables()

    def add_trigger(self) -> None:
        self.open_trigger_dialog()

    def open_trigger_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Add Trigger")
        dialog.setObjectName("ruleBuilderDialog")
        dialog.setMinimumWidth(560)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        type_combo = QComboBox()
        for label, value in [("Message", "message"), ("Call", "call"), ("Time", "time"), ("Date", "date")]:
            type_combo.addItem(label, value)
        layout.addWidget(QLabel("Trigger"))
        layout.addWidget(type_combo)

        stack = QStackedWidget()

        message_page = QWidget()
        message_layout = QGridLayout(message_page)
        message_layout.setContentsMargins(0, 0, 0, 0)
        message_match = QComboBox()
        for label, value in [("Contains", "contains"), ("Regex", "regex"), ("Equals", "equals"), ("Starts with", "starts_with"), ("Ends with", "ends_with"), ("Any message", "any")]:
            message_match.addItem(label, value)
        message_value_label = QLabel("Text")
        message_value = QLineEdit()
        message_value.setPlaceholderText(r"project status or ^\s*hello\b")
        message_layout.addWidget(QLabel("Match"), 0, 0)
        message_layout.addWidget(message_match, 0, 1)
        message_layout.addWidget(message_value_label, 1, 0)
        message_layout.addWidget(message_value, 1, 1)

        def sync_message_fields() -> None:
            visible = str(message_match.currentData() or "contains") != "any"
            message_value_label.setVisible(visible)
            message_value.setVisible(visible)

        message_match.currentIndexChanged.connect(sync_message_fields)
        sync_message_fields()

        call_page = QWidget()
        call_layout = QGridLayout(call_page)
        call_layout.setContentsMargins(0, 0, 0, 0)
        call_type = QComboBox()
        call_type.addItem("Any direct call", "any")
        call_type.addItem("Incoming call", "incoming")
        call_layout.addWidget(QLabel("Call"), 0, 0)
        call_layout.addWidget(call_type, 0, 1)

        time_page = QWidget()
        time_layout = QGridLayout(time_page)
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_operator = QComboBox()
        for label, value in [("At", "at"), ("After", "after"), ("Before", "before"), ("Between", "between")]:
            time_operator.addItem(label, value)
        time_value = QTimeEdit()
        time_value.setDisplayFormat("h:mm AP")
        time_value.setTime(QTime.currentTime())
        time_end_label = QLabel("End")
        time_end = QTimeEdit()
        time_end.setDisplayFormat("h:mm AP")
        time_end.setTime(QTime.currentTime().addSecs(3600))
        time_days = QLineEdit()
        time_days.setPlaceholderText("Optional: Mon Tue Wed")
        time_layout.addWidget(QLabel("When"), 0, 0)
        time_layout.addWidget(time_operator, 0, 1)
        time_layout.addWidget(QLabel("Time"), 1, 0)
        time_layout.addWidget(time_value, 1, 1)
        time_layout.addWidget(time_end_label, 1, 2)
        time_layout.addWidget(time_end, 1, 3)
        time_layout.addWidget(QLabel("Days"), 2, 0)
        time_layout.addWidget(time_days, 2, 1, 1, 3)

        def sync_time_fields() -> None:
            visible = str(time_operator.currentData() or "at") == "between"
            time_end_label.setVisible(visible)
            time_end.setVisible(visible)

        time_operator.currentIndexChanged.connect(sync_time_fields)
        sync_time_fields()

        date_page = QWidget()
        date_layout = QGridLayout(date_page)
        date_layout.setContentsMargins(0, 0, 0, 0)
        date_operator = QComboBox()
        for label, value in [("On", "on"), ("After", "after"), ("Before", "before"), ("Between", "between")]:
            date_operator.addItem(label, value)
        date_value = QDateEdit()
        date_value.setDisplayFormat("yyyy-MM-dd")
        date_value.setCalendarPopup(True)
        date_value.setDate(QDate.currentDate())
        date_end_label = QLabel("End")
        date_end = QDateEdit()
        date_end.setDisplayFormat("yyyy-MM-dd")
        date_end.setCalendarPopup(True)
        date_end.setDate(QDate.currentDate())
        date_layout.addWidget(QLabel("When"), 0, 0)
        date_layout.addWidget(date_operator, 0, 1)
        date_layout.addWidget(QLabel("Date"), 1, 0)
        date_layout.addWidget(date_value, 1, 1)
        date_layout.addWidget(date_end_label, 1, 2)
        date_layout.addWidget(date_end, 1, 3)

        def sync_date_fields() -> None:
            visible = str(date_operator.currentData() or "on") == "between"
            date_end_label.setVisible(visible)
            date_end.setVisible(visible)

        date_operator.currentIndexChanged.connect(sync_date_fields)
        sync_date_fields()

        stack.addWidget(message_page)
        stack.addWidget(call_page)
        stack.addWidget(time_page)
        stack.addWidget(date_page)
        type_combo.currentIndexChanged.connect(stack.setCurrentIndex)
        layout.addWidget(stack)

        buttons = QHBoxLayout()
        buttons.addStretch()
        save_button = self._tool_button("Add", "check.svg", None, "Add trigger", primary=True)
        cancel_button = self._tool_button("Cancel", "close.svg", dialog.reject, "Cancel")
        buttons.addWidget(save_button)
        buttons.addWidget(cancel_button)
        layout.addLayout(buttons)

        def accept_trigger() -> None:
            trigger_type = str(type_combo.currentData() or "message")
            trigger: dict[str, Any]
            if trigger_type == "message":
                match_type = str(message_match.currentData() or "contains")
                value = message_value.text().strip()
                if match_type != "any" and not value:
                    QMessageBox.information(dialog, "WhatsApp Rule", "Message trigger text is required.")
                    return
                if match_type == "regex":
                    try:
                        re.compile(value)
                    except re.error as exc:
                        QMessageBox.warning(dialog, "WhatsApp Rule", f"Regex trigger is not valid: {exc}")
                        return
                trigger = {"type": "message", "match": match_type, "value": value}
            elif trigger_type == "call":
                trigger = {"type": "call", "call_type": str(call_type.currentData() or "any")}
            elif trigger_type == "time":
                operator = str(time_operator.currentData() or "at")
                trigger = {"type": "time", "operator": operator}
                if operator == "between":
                    trigger["start"] = time_value.time().toString("h:mm AP")
                    trigger["end"] = time_end.time().toString("h:mm AP")
                else:
                    trigger["time"] = time_value.time().toString("h:mm AP")
                days = [item.strip() for item in re.split(r"[,;\s]+", time_days.text()) if item.strip()]
                if days:
                    trigger["days"] = days
            else:
                operator = str(date_operator.currentData() or "on")
                trigger = {"type": "date", "operator": operator}
                if operator == "between":
                    trigger["start"] = date_value.date().toString("yyyy-MM-dd")
                    trigger["end"] = date_end.date().toString("yyyy-MM-dd")
                else:
                    trigger["date"] = date_value.date().toString("yyyy-MM-dd")
            self.current_triggers.append(trigger)
            self.refresh_builder_tables()
            dialog.accept()

        save_button.clicked.connect(accept_trigger)
        dialog.exec()

    def remove_trigger(self) -> None:
        row = self.triggers_table.currentRow()
        if 0 <= row < len(self.current_triggers):
            self.current_triggers.pop(row)
            self.refresh_builder_tables()

    def add_action(self) -> None:
        self.open_action_dialog()

    def open_action_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Add Action")
        dialog.setObjectName("ruleBuilderDialog")
        dialog.setMinimumWidth(600)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        type_combo = QComboBox()
        for label, value in [("Direct reply", "reply"), ("Noor brain", "assistant"), ("AI provider", "ai"), ("Safe tool command", "tool"), ("Log note", "log")]:
            type_combo.addItem(label, value)
        layout.addWidget(QLabel("Action"))
        layout.addWidget(type_combo)

        stack = QStackedWidget()

        reply_page = QWidget()
        reply_layout = QVBoxLayout(reply_page)
        reply_layout.setContentsMargins(0, 0, 0, 0)
        reply_text = QPlainTextEdit()
        reply_text.setPlaceholderText("WhatsApp reply text. You can use {message}, {chat}, {time}, and regex groups.")
        reply_text.setMaximumHeight(82)
        reply_layout.addWidget(reply_text)

        assistant_page = QWidget()
        assistant_layout = QVBoxLayout(assistant_page)
        assistant_layout.setContentsMargins(0, 0, 0, 0)
        assistant_prompt = QPlainTextEdit()
        assistant_prompt.setPlaceholderText("Prompt for Noor brain. Use {message} for incoming text.")
        assistant_prompt.setMaximumHeight(82)
        assistant_layout.addWidget(assistant_prompt)

        ai_page = QWidget()
        ai_layout = QGridLayout(ai_page)
        ai_layout.setContentsMargins(0, 0, 0, 0)
        ai_provider = QComboBox()
        for label, value in [("Auto brain", "auto"), ("Research", "research"), ("Gemini", "gemini"), ("Codex", "codex")]:
            ai_provider.addItem(label, value)
        ai_prompt = QPlainTextEdit()
        ai_prompt.setPlaceholderText("Prompt for AI. Use {message} for the incoming WhatsApp text.")
        ai_prompt.setMaximumHeight(82)
        ai_layout.addWidget(QLabel("Provider"), 0, 0)
        ai_layout.addWidget(ai_provider, 0, 1)
        ai_layout.addWidget(QLabel("Prompt"), 1, 0)
        ai_layout.addWidget(ai_prompt, 1, 1)

        tool_page = QWidget()
        tool_layout = QGridLayout(tool_page)
        tool_layout.setContentsMargins(0, 0, 0, 0)
        tool_combo = QComboBox()
        tools = ToolRegistry(self.storage).list_tools()
        if tools:
            for tool in tools:
                tool_combo.addItem(str(tool.get("name") or tool["id"]), tool["id"])
        else:
            tool_combo.addItem("No safe tools configured", "")
        tool_command_index = QSpinBox()
        tool_command_index.setRange(0, 20)
        tool_summarize = QComboBox()
        tool_summarize.addItem("No summary", "")
        tool_summarize.addItem("Auto brain summary", "auto")
        tool_summarize.addItem("Gemini summary", "gemini")
        tool_summarize.addItem("Codex summary", "codex")
        tool_summary_label = QLabel("Summary prompt")
        tool_summary_prompt = QPlainTextEdit()
        tool_summary_prompt.setPlaceholderText("Summarize this tool result for a concise WhatsApp reply.")
        tool_summary_prompt.setMaximumHeight(72)
        tool_layout.addWidget(QLabel("Tool"), 0, 0)
        tool_layout.addWidget(tool_combo, 0, 1)
        tool_layout.addWidget(QLabel("Command index"), 1, 0)
        tool_layout.addWidget(tool_command_index, 1, 1)
        tool_layout.addWidget(QLabel("Summarize"), 2, 0)
        tool_layout.addWidget(tool_summarize, 2, 1)
        tool_layout.addWidget(tool_summary_label, 3, 0)
        tool_layout.addWidget(tool_summary_prompt, 3, 1)

        def sync_tool_summary() -> None:
            visible = bool(tool_summarize.currentData())
            tool_summary_label.setVisible(visible)
            tool_summary_prompt.setVisible(visible)

        tool_summarize.currentIndexChanged.connect(sync_tool_summary)
        sync_tool_summary()

        log_page = QWidget()
        log_layout = QVBoxLayout(log_page)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_text = QPlainTextEdit()
        log_text.setPlaceholderText("Activity log note")
        log_text.setMaximumHeight(72)
        log_layout.addWidget(log_text)

        stack.addWidget(reply_page)
        stack.addWidget(assistant_page)
        stack.addWidget(ai_page)
        stack.addWidget(tool_page)
        stack.addWidget(log_page)
        type_combo.currentIndexChanged.connect(stack.setCurrentIndex)
        layout.addWidget(stack)

        buttons = QHBoxLayout()
        buttons.addStretch()
        save_button = self._tool_button("Add", "check.svg", None, "Add action", primary=True)
        cancel_button = self._tool_button("Cancel", "close.svg", dialog.reject, "Cancel")
        buttons.addWidget(save_button)
        buttons.addWidget(cancel_button)
        layout.addLayout(buttons)

        def accept_action() -> None:
            action_type = str(type_combo.currentData() or "reply")
            action: dict[str, Any]
            if action_type == "reply":
                text = reply_text.toPlainText().strip()
                if not text:
                    QMessageBox.information(dialog, "WhatsApp Rule", "Reply text is required.")
                    return
                action = {"type": "reply", "text": text}
            elif action_type == "assistant":
                prompt = assistant_prompt.toPlainText().strip() or "{message}"
                action = {"type": "assistant", "prompt": prompt}
            elif action_type == "ai":
                prompt = ai_prompt.toPlainText().strip() or "{message}"
                action = {"type": "ai", "provider": str(ai_provider.currentData() or "auto"), "prompt": prompt}
            elif action_type == "tool":
                tool_id = str(tool_combo.currentData() or "")
                if not tool_id:
                    QMessageBox.information(dialog, "WhatsApp Rule", "Select a safe tool first.")
                    return
                action = {"type": "tool", "tool_id": tool_id, "command_index": tool_command_index.value()}
                summarize_with = str(tool_summarize.currentData() or "")
                if summarize_with:
                    action["summarize_with"] = summarize_with
                    action["summary_prompt"] = tool_summary_prompt.toPlainText().strip() or "Summarize this tool result for a concise WhatsApp reply."
            else:
                action = {"type": "log", "prompt": log_text.toPlainText().strip() or "WhatsApp rule matched."}
            self.current_actions.append(action)
            self.refresh_builder_tables()
            dialog.accept()

        save_button.clicked.connect(accept_action)
        dialog.exec()

    def remove_action(self) -> None:
        row = self.actions_table.currentRow()
        if 0 <= row < len(self.current_actions):
            self.current_actions.pop(row)
            self.refresh_builder_tables()

    def refresh_builder_tables(self) -> None:
        trigger_rows = []
        for trigger in self.current_triggers:
            trigger_rows.append([self.trigger_type_label(trigger), self.trigger_detail(trigger)])
        set_table_rows(self.triggers_table, ["Type", "Condition"], trigger_rows)
        self.apply_builder_table_layout(self.triggers_table)
        action_rows = []
        for action in self.current_actions:
            action_rows.append([whatsapp_action_summary({"actions": [action]}), self.action_detail(action)])
        set_table_rows(self.actions_table, ["Action", "Details"], action_rows)
        self.apply_builder_table_layout(self.actions_table)

    @staticmethod
    def trigger_type_label(trigger: dict[str, Any]) -> str:
        labels = {"message": "Message", "call": "Call", "time": "Time", "date": "Date"}
        return labels.get(str(trigger.get("type") or "message"), "Trigger")

    @staticmethod
    def trigger_detail(trigger: dict[str, Any]) -> str:
        trigger_type = str(trigger.get("type") or "message")
        if trigger_type == "message":
            match_type = str(trigger.get("match") or "contains").replace("_", " ")
            value = str(trigger.get("value") or "")
            return f"{match_type}: {value}" if value else match_type
        if trigger_type == "call":
            return str(trigger.get("call_type") or "any")
        if trigger_type == "time":
            operator = str(trigger.get("operator") or "at")
            if operator == "between":
                value = f"{trigger.get('start') or ''} to {trigger.get('end') or ''}"
            else:
                value = str(trigger.get("time") or "")
            days = trigger.get("days")
            suffix = f" on {', '.join(days)}" if isinstance(days, list) and days else ""
            return f"{operator} {value}{suffix}".strip()
        if trigger_type == "date":
            operator = str(trigger.get("operator") or "on")
            if operator == "between":
                value = f"{trigger.get('start') or ''} to {trigger.get('end') or ''}"
            else:
                value = str(trigger.get("date") or "")
            return f"{operator} {value}".strip()
        return ""

    @staticmethod
    def action_detail(action: dict[str, Any]) -> str:
        action_type = str(action.get("type") or "reply")
        if action_type == "reply":
            return str(action.get("text") or "")[:160]
        if action_type in {"assistant", "brain", "ai", "research", "gemini", "codex"}:
            provider = f"{action.get('provider')}: " if action.get("provider") else ""
            return provider + str(action.get("prompt") or "{message}")[:160]
        if action_type in {"tool", "safe_tool"}:
            summary = f", summarize with {action.get('summarize_with')}" if action.get("summarize_with") else ""
            return f"{action.get('tool_id')}, command {action.get('command_index', 0)}{summary}"
        return str(action.get("prompt") or action.get("text") or "")[:160]

    def new_rule(self) -> None:
        self.show_new_rule_editor()

    def save_rule(self) -> None:
        rule_id = self.id_input.text().strip()
        name = self.name_input.text().strip() or rule_id
        if not rule_id or not re.fullmatch(r"[A-Za-z0-9_-]+", rule_id):
            QMessageBox.information(self, "WhatsApp Rule", "Use a Rule ID with letters, numbers, dash, or underscore.")
            return
        if not self.current_triggers:
            QMessageBox.information(self, "WhatsApp Rule", "Add at least one trigger.")
            return
        if not self.current_actions:
            QMessageBox.information(self, "WhatsApp Rule", "Add at least one action.")
            return
        scope = str(self.audience_scope.currentData() or "everyone")
        contacts = contacts_from_text(self.contacts_input.toPlainText())
        if scope in {"contacts", "except_contacts"} and not contacts:
            QMessageBox.information(self, "WhatsApp Rule", "Add at least one contact for this audience.")
            return
        trigger_types = {str(trigger.get("type") or "message") for trigger in self.current_triggers}
        exact_time = any(str(trigger.get("type")) == "time" and str(trigger.get("operator") or "at") == "at" for trigger in self.current_triggers)
        autonomous_schedule = "time" in trigger_types and "message" not in trigger_types and "call" not in trigger_types and exact_time
        if autonomous_schedule and scope != "contacts":
            QMessageBox.information(self, "WhatsApp Rule", "Scheduled time/date rules need specific contacts.")
            return
        for trigger in self.current_triggers:
            if trigger.get("type") == "message" and trigger.get("match") == "regex":
                try:
                    re.compile(str(trigger.get("value") or ""))
                except re.error as exc:
                    QMessageBox.warning(self, "WhatsApp Rule", f"Regex trigger is not valid: {exc}")
                    return
        rules = self.load_rules()
        if any(rule["id"] == rule_id and rule_id != self.editor_rule_id for rule in rules):
            QMessageBox.warning(self, "WhatsApp Rule", "Another rule already uses this Rule ID.")
            return
        preserved_aliases: list[Any] = []
        for rule in rules:
            if rule["id"] in {self.editor_rule_id, rule_id}:
                audience = rule.get("audience") if isinstance(rule.get("audience"), dict) else {}
                aliases = audience.get("aliases") or audience.get("contact_aliases") or []
                if isinstance(aliases, list):
                    preserved_aliases = aliases
                break
        audience_payload: dict[str, Any] = {"scope": scope, "contacts": contacts}
        if preserved_aliases:
            audience_payload["aliases"] = preserved_aliases
        new_rule: dict[str, Any] = {
            "id": rule_id,
            "name": name,
            "enabled": self.enabled_check.isChecked(),
            "trigger_logic": self.trigger_logic.currentData() or "any",
            "audience": audience_payload,
            "triggers": self.current_triggers,
            "actions": self.current_actions,
        }
        updated_rules: list[dict[str, Any]] = []
        replaced = False
        for rule in rules:
            if rule["id"] == self.editor_rule_id or rule["id"] == rule_id:
                if not replaced:
                    updated_rules.append(new_rule)
                    replaced = True
                continue
            updated_rules.append(rule)
        if not replaced:
            updated_rules.append(new_rule)
        self.write_rules(updated_rules)
        self.storage.log("info", "WhatsApp Rules", f"Saved WhatsApp rule: {rule_id}")
        self.editor_rule_id = rule_id
        self.refresh()
        self.set_editor_mode(False)
        self.output.setPlainText(f"Saved rule: {rule_id}")
        self.output.show()

    def delete_rule(self) -> None:
        self.delete_selected_rules()

    def bulk_set_enabled(self, enabled: bool) -> None:
        rule_ids = self.selected_rule_ids()
        if not rule_ids:
            QMessageBox.information(self, "WhatsApp Rule", "Select one or more rules first.")
            return
        selected = set(rule_ids)
        rules = self.load_rules()
        for rule in rules:
            if rule["id"] in selected:
                rule["enabled"] = enabled
        self.write_rules(rules)
        state = "enabled" if enabled else "disabled"
        self.storage.log("info", "WhatsApp Rules", f"Bulk {state} WhatsApp rules: {', '.join(rule_ids)}")
        self.refresh()
        self.output.setPlainText(f"{state.title()} {len(rule_ids)} rule(s).")
        self.output.show()

    def delete_selected_rules(self) -> None:
        rule_ids = self.selected_rule_ids()
        if not rule_ids:
            QMessageBox.information(self, "WhatsApp Rule", "Select one or more rules first.")
            return
        answer = QMessageBox.question(
            self,
            "Delete WhatsApp Rules",
            f"Delete {len(rule_ids)} selected rule(s)?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        selected = set(rule_ids)
        rules = [rule for rule in self.load_rules() if rule["id"] not in selected]
        self.write_rules(rules)
        self.storage.log("info", "WhatsApp Rules", f"Deleted WhatsApp rules: {', '.join(rule_ids)}")
        if self.editor_rule_id in selected:
            self.close_editor()
            self.reset_rule_editor()
        self.refresh()
        self.output.setPlainText(f"Deleted {len(rule_ids)} rule(s).")
        self.output.show()

    def test_rule(self) -> None:
        sample, ok = QInputDialog.getMultiLineText(self, "WhatsApp Rule Test", "Incoming message")
        if not ok:
            return
        matches = WhatsAppWebService(self.storage).preview_matches(sample)
        self.output.setPlainText("\n".join(matches) if matches else "No WhatsApp rule matched.")
        self.output.show()


class ReplyApprovalsPage(BasePage):
    title = "Reply Approvals"

    def __init__(self, storage: Storage) -> None:
        super().__init__(storage)
        layout = QVBoxLayout(self)
        self.table = QTableWidget()
        configure_table(self.table)
        actions = QHBoxLayout()
        actions.addWidget(make_button("Approve", self.approve))
        actions.addWidget(make_button("Reject", self.reject))
        actions.addWidget(make_button("Refresh", self.refresh))
        actions.addStretch()
        layout.addLayout(actions)
        layout.addWidget(self.table)
        self.refresh()

    def refresh(self) -> None:
        rows = self.storage.fetch_all(
            "SELECT id, title, priority, status, source, summary, updated_at FROM escalations WHERE status IN ('Detected', 'Waiting for acknowledgement') ORDER BY updated_at DESC"
        )
        set_table_rows(self.table, ["ID", "Title", "Priority", "Status", "Source", "Summary", "Updated"], [[r["id"], r["title"], r["priority"], r["status"], r["source"], r["summary"], format_local_timestamp(r["updated_at"])] for r in rows])

    def set_status(self, status: str) -> None:
        incident_id = selected_value(self.table, 0)
        if not incident_id:
            QMessageBox.information(self, "Approval", "Select an approval item first.")
            return
        self.storage.execute("UPDATE escalations SET status = ?, updated_at = ? WHERE id = ?", (status, utc_now(), incident_id))
        self.storage.log("info", "Approval", f"{incident_id} -> {status}")
        self.refresh()

    def approve(self) -> None:
        self.set_status("Acknowledged")

    def reject(self) -> None:
        self.set_status("Cancelled")


class AssistantChatPage(BasePage):
    title = "Assistant Chat"
    command_requested = Signal(str)

    def __init__(self, storage: Storage) -> None:
        super().__init__(storage)
        self.brain = AssistantBrain(storage)
        self.speech = SpeechService(storage)
        layout = QVBoxLayout(self)
        self.history = QTextEdit()
        self.history.setReadOnly(True)
        self.input = QPlainTextEdit()
        self.input.setFixedHeight(96)
        buttons = QHBoxLayout()
        buttons.addWidget(make_button("Send", self.send))
        buttons.addWidget(make_button("Speak Last", self.speak_last))
        buttons.addWidget(make_button("Clear", self.history.clear))
        buttons.addStretch()
        layout.addWidget(self.history)
        layout.addWidget(self.input)
        layout.addLayout(buttons)
        self.last_reply = ""

    def send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.history.append(f"You: {text}")
        lowered = text.lower()
        if lowered.startswith("task:"):
            reply = self.brain.answer("add task " + text.split(":", 1)[1].strip())
        elif lowered.startswith("note:"):
            body = text.split(":", 1)[1].strip()
            now = utc_now()
            self.storage.execute(
                "INSERT INTO knowledge (title, category, body, trusted, created_at, updated_at) VALUES (?, 'Quick Note', ?, 1, ?, ?)",
                (body[:60] or "Quick note", body, now, now),
            )
            reply = AssistantReply("Saved quick note.")
        else:
            reply = self.brain.answer(text)
        if reply.action:
            self.command_requested.emit(reply.action)
            response = f"I will handle: {reply.action}"
        else:
            response = reply.text
        self.last_reply = response
        self.storage.log("info", "Assistant Chat", f"Q: {text}")
        self.storage.log("info", "Assistant Chat", f"A: {response[:600]}")
        self.history.append(f"Assistant: {response}")
        self.input.clear()

    def speak_last(self) -> None:
        self.speech.speak(self.last_reply or "I do not have a reply yet.")


class TeamStatusPage(BasePage):
    title = "Team Status"

    def __init__(self, storage: Storage) -> None:
        super().__init__(storage)
        self.registry = ToolRegistry(storage)
        layout = QVBoxLayout(self)
        self.table = QTableWidget()
        configure_table(self.table)
        layout.addWidget(make_button("Refresh Sources", self.refresh_sources), alignment=Qt.AlignRight)
        layout.addWidget(self.table)
        self.refresh()

    def refresh_sources(self) -> None:
        for tool in self.registry.list_tools():
            status = "available" if Path(tool["path"]).exists() else "missing"
            self.storage.update_tool_status(tool["id"], status)
        self.storage.log("info", "Team Status", "Refreshed source availability.")
        self.refresh()

    def refresh(self) -> None:
        tools = self.registry.list_tools()
        rows = [[tool["name"], tool["connection_status"], format_local_timestamp(tool["last_run"]), ", ".join(tool["capabilities"][:3])] for tool in tools]
        set_table_rows(self.table, ["Source", "Status", "Last run", "Signals"], rows)


class ActivityPage(BasePage):
    title = "Activity History"

    def __init__(self, storage: Storage) -> None:
        super().__init__(storage)
        layout = QVBoxLayout(self)
        self.table = QTableWidget()
        configure_table(self.table)
        layout.addWidget(make_button("Refresh", self.refresh), alignment=Qt.AlignRight)
        layout.addWidget(self.table)
        self.refresh()

    def refresh(self) -> None:
        rows = self.storage.fetch_all("SELECT id, ts, level, source, message FROM activity ORDER BY id DESC LIMIT 500")
        set_table_rows(self.table, ["ID", "Time", "Level", "Source", "Message"], [[r["id"], format_local_timestamp(r["ts"]), r["level"], r["source"], r["message"]] for r in rows])


class SettingsPage(BasePage):
    title = "Settings"

    def __init__(self, storage: Storage) -> None:
        super().__init__(storage)
        self.speech = SpeechService(storage)
        layout = QVBoxLayout(self)
        self.approval_checks: dict[str, QCheckBox] = {}
        approvals_box = QGroupBox("Approvals")
        approvals_layout = QGridLayout(approvals_box)
        approvals = self.storage.get_setting("approvals", {})
        for index, (key, value) in enumerate(approvals.items()):
            check = QCheckBox(key.replace("_", " ").title())
            check.setChecked(bool(value))
            self.approval_checks[key] = check
            approvals_layout.addWidget(check, index // 2, index % 2)

        ui_box = QGroupBox("Interface")
        ui_layout = QFormLayout(ui_box)
        ui_settings = self.storage.get_setting("ui", {})
        self.toast_position = QComboBox()
        for label, value in [
            ("Top right", "top-right"),
            ("Top left", "top-left"),
            ("Bottom right", "bottom-right"),
            ("Bottom left", "bottom-left"),
        ]:
            self.toast_position.addItem(label, value)
        current_toast_position = str(ui_settings.get("toast_position", "top-right"))
        for index in range(self.toast_position.count()):
            if self.toast_position.itemData(index) == current_toast_position:
                self.toast_position.setCurrentIndex(index)
                break
        ui_layout.addRow("Floating messages", self.toast_position)

        escalation_box = QGroupBox("Escalation")
        escalation_layout = QFormLayout(escalation_box)
        self.escalation_enabled = QCheckBox("Enabled")
        self.teams_enabled = QCheckBox("Teams enabled")
        self.find_hub_enabled = QCheckBox("Find My Phone enabled")
        escalation = self.storage.get_setting("escalation", {})
        self.escalation_enabled.setChecked(bool(escalation.get("enabled")))
        self.teams_enabled.setChecked(bool(escalation.get("teams_enabled")))
        self.find_hub_enabled.setChecked(bool(escalation.get("find_hub_enabled", True)))
        self.quiet_start = QLineEdit(str(escalation.get("quiet_hours_start", "22:00")))
        self.quiet_end = QLineEdit(str(escalation.get("quiet_hours_end", "08:00")))
        escalation_layout.addRow("", self.escalation_enabled)
        escalation_layout.addRow("", self.teams_enabled)
        escalation_layout.addRow("", self.find_hub_enabled)
        escalation_layout.addRow("Quiet start", self.quiet_start)
        escalation_layout.addRow("Quiet end", self.quiet_end)

        whatsapp_box = QGroupBox("WhatsApp Web")
        whatsapp_layout = QFormLayout(whatsapp_box)
        whatsapp = self.storage.get_setting("whatsapp_web", {})
        self.whatsapp_enabled = QCheckBox("Enable dedicated browser bridge")
        self.whatsapp_enabled.setChecked(bool(whatsapp.get("enabled", True)))
        self.whatsapp_auto_start = QCheckBox("Start WhatsApp bridge when Noor opens")
        self.whatsapp_auto_start.setChecked(bool(whatsapp.get("auto_start", True)))
        self.whatsapp_store_private = QCheckBox("Store message previews")
        self.whatsapp_store_private.setChecked(bool(whatsapp.get("store_private_messages", False)))
        whatsapp_layout.addRow("Sending", QLabel("Active for matching direct-message rules"))
        whatsapp_layout.addRow("", self.whatsapp_enabled)
        whatsapp_layout.addRow("", self.whatsapp_auto_start)
        whatsapp_layout.addRow("", self.whatsapp_store_private)

        auto_reply_box = QGroupBox("WhatsApp Automatic Replies")
        auto_reply_layout = QFormLayout(auto_reply_box)
        auto_reply = self.storage.get_setting("whatsapp_auto_reply", {})
        self.whatsapp_auto_enabled = QCheckBox("Reply automatically to matching unread direct messages")
        self.whatsapp_auto_enabled.setChecked(bool(auto_reply.get("enabled", True)))
        self.whatsapp_auto_poll = QSpinBox()
        self.whatsapp_auto_poll.setRange(5, 60)
        self.whatsapp_auto_poll.setValue(int(auto_reply.get("poll_seconds", 12)))
        self.whatsapp_skip_groups = QCheckBox("Never reply in group chats")
        self.whatsapp_skip_groups.setChecked(bool(auto_reply.get("skip_groups", True)))
        auto_reply_layout.addRow("Mode", QLabel("Rules decide direct replies, actions, tool checks, research, Gemini, or Codex. Unmatched messages are ignored."))
        auto_reply_layout.addRow("", self.whatsapp_auto_enabled)
        auto_reply_layout.addRow("Check unread chats", self.whatsapp_auto_poll)
        auto_reply_layout.addRow("", self.whatsapp_skip_groups)

        ai_box = QGroupBox("AI Brain")
        ai_layout = QFormLayout(ai_box)
        ai_brain = self.storage.get_setting("ai_brain", {})
        self.ai_enabled = QCheckBox("Use fallback brain for unknown questions")
        self.ai_enabled.setChecked(bool(ai_brain.get("enabled", True)))
        self.ai_research_enabled = QCheckBox("Research before using AI")
        self.ai_research_enabled.setChecked(bool(ai_brain.get("research_enabled", True)))
        self.ai_gemini_enabled = QCheckBox("Use Gemini after weak research")
        self.ai_gemini_enabled.setChecked(bool(ai_brain.get("gemini_enabled", True)))
        self.ai_codex_enabled = QCheckBox("Use Codex if Gemini fails")
        self.ai_codex_enabled.setChecked(bool(ai_brain.get("codex_enabled", True)))
        self.ai_cache_hours = QSpinBox()
        self.ai_cache_hours.setRange(0, 168)
        self.ai_cache_hours.setValue(int(ai_brain.get("cache_hours", 24)))
        self.ai_research_pages = QSpinBox()
        self.ai_research_pages.setRange(1, 5)
        self.ai_research_pages.setValue(int(ai_brain.get("max_research_pages", 3)))
        ai_layout.addRow("", self.ai_enabled)
        ai_layout.addRow("", self.ai_research_enabled)
        ai_layout.addRow("", self.ai_gemini_enabled)
        ai_layout.addRow("", self.ai_codex_enabled)
        ai_layout.addRow("Cache hours", self.ai_cache_hours)
        ai_layout.addRow("Research pages", self.ai_research_pages)

        gemini_box = QGroupBox("Gemini CLI")
        gemini_layout = QFormLayout(gemini_box)
        gemini = self.storage.get_setting("gemini_cli", {})
        self.gemini_enabled = QCheckBox("Allow Gemini fallback answers")
        self.gemini_enabled.setChecked(bool(gemini.get("enabled", True)))
        self.gemini_model = QLineEdit(str(gemini.get("model", "gemini-2.5-flash")))
        self.gemini_timeout = QSpinBox()
        self.gemini_timeout.setRange(10, 120)
        self.gemini_timeout.setValue(int(gemini.get("timeout_seconds", 45)))
        gemini_layout.addRow("", self.gemini_enabled)
        gemini_layout.addRow("Model", self.gemini_model)
        gemini_layout.addRow("Timeout", self.gemini_timeout)
        gemini_layout.addRow("Mode", QLabel("Non-interactive JSON only; read-only approval mode; no sending"))

        codex_ai_box = QGroupBox("Codex AI Fallback")
        codex_ai_layout = QFormLayout(codex_ai_box)
        codex_ai = self.storage.get_setting("codex_ai", {})
        self.codex_ai_enabled = QCheckBox("Allow Codex final fallback")
        self.codex_ai_enabled.setChecked(bool(codex_ai.get("enabled", True)))
        codex_model = str(codex_ai.get("model", "gpt-5.4-mini"))
        if codex_model == "gpt-5-mini":
            codex_model = "gpt-5.4-mini"
        self.codex_ai_model = QLineEdit(codex_model)
        self.codex_ai_reasoning = QComboBox()
        for value in ["low", "medium", "high"]:
            self.codex_ai_reasoning.addItem(value, value)
        self.codex_ai_reasoning.setCurrentText(str(codex_ai.get("reasoning_effort", "low")))
        self.codex_ai_timeout = QSpinBox()
        self.codex_ai_timeout.setRange(20, 180)
        self.codex_ai_timeout.setValue(int(codex_ai.get("timeout_seconds", 60)))
        codex_ai_layout.addRow("", self.codex_ai_enabled)
        codex_ai_layout.addRow("Model", self.codex_ai_model)
        codex_ai_layout.addRow("Reasoning", self.codex_ai_reasoning)
        codex_ai_layout.addRow("Timeout", self.codex_ai_timeout)

        voice_box = QGroupBox("Voice")
        voice_layout = QFormLayout(voice_box)
        voice = self.speech.current_settings()
        self.voice_enabled = QCheckBox("Voice enabled")
        self.voice_enabled.setChecked(bool(voice.get("enabled", True)))
        self.voice_provider = QComboBox()
        self.voice_provider.addItem("Edge Neural voice (more human)", "edge")
        self.voice_provider.addItem("Windows desktop voice (offline fallback)", "windows")
        provider = str(voice.get("tts_provider", "edge"))
        self.voice_provider.setCurrentIndex(0 if provider == "edge" else 1)
        self.edge_voice = QComboBox()
        self.edge_voice.setEditable(True)
        edge_names = [
            "en-US-JennyNeural",
            "en-US-AriaNeural",
            "en-US-AvaNeural",
            "en-US-EmmaNeural",
            "en-GB-SoniaNeural",
        ]
        self.edge_voice.addItems(edge_names)
        edge_voice = str(voice.get("edge_voice", "en-US-JennyNeural"))
        if edge_voice not in edge_names:
            self.edge_voice.addItem(edge_voice)
        self.edge_voice.setCurrentText(edge_voice)
        self.voice_combo = QComboBox()
        self.voice_combo.setEditable(False)
        selected_voice = str(voice.get("voice_name", ""))
        self.voice_combo.addItem(selected_voice or "System default", selected_voice)
        self.voice_rate = QSpinBox()
        self.voice_rate.setRange(-10, 10)
        self.voice_rate.setValue(int(voice.get("rate", 0)))
        self.voice_volume = QSpinBox()
        self.voice_volume.setRange(0, 100)
        self.voice_volume.setValue(int(voice.get("volume", 100)))
        self.listen_timeout = QSpinBox()
        self.listen_timeout.setRange(3, 30)
        self.listen_timeout.setValue(int(voice.get("listen_timeout_seconds", 8)))
        self.voice_confidence = QSpinBox()
        self.voice_confidence.setRange(0, 100)
        self.voice_confidence.setSuffix("%")
        self.voice_confidence.setValue(int(float(voice.get("min_confidence", 0.35)) * 100))
        self.recognition_mode = QComboBox()
        self.recognition_mode.addItem("Hybrid productivity mode (recommended)", "hybrid")
        self.recognition_mode.addItem("Command mode (strict)", "command")
        self.recognition_mode.addItem("Dictation mode (less reliable)", "dictation")
        current_mode = str(voice.get("recognition_mode", "hybrid")).lower()
        self.recognition_mode.setCurrentIndex({"hybrid": 0, "command": 1, "dictation": 2}.get(current_mode, 0))
        voice_layout.addRow("", self.voice_enabled)
        voice_layout.addRow("Speech provider", self.voice_provider)
        voice_layout.addRow("Edge voice", self.edge_voice)
        voice_layout.addRow("Windows voice", self.voice_combo)
        voice_layout.addRow("Speed", self.voice_rate)
        voice_layout.addRow("Volume", self.voice_volume)
        voice_layout.addRow("Listen mode", self.recognition_mode)
        voice_layout.addRow("Listen timeout", self.listen_timeout)
        voice_layout.addRow("Minimum confidence", self.voice_confidence)
        voice_layout.addRow("", make_button("Load Windows Voices", self.load_windows_voices))
        voice_layout.addRow("", make_button("Test Voice", self.test_voice))

        layout.addWidget(approvals_box)
        layout.addWidget(ui_box)
        layout.addWidget(escalation_box)
        layout.addWidget(whatsapp_box)
        layout.addWidget(auto_reply_box)
        layout.addWidget(ai_box)
        layout.addWidget(gemini_box)
        layout.addWidget(codex_ai_box)
        layout.addWidget(voice_box)
        layout.addWidget(make_button("Save Settings", self.save), alignment=Qt.AlignRight)
        layout.addStretch()

    def load_windows_voices(self) -> None:
        current = str(self.voice_combo.currentData() or "")
        voices = self.speech.list_voices()
        voice_names = [str(item.get("name", "")) for item in voices if item.get("name")]
        self.voice_combo.clear()
        if not voice_names:
            self.voice_combo.addItem("System default", "")
            self.storage.log("warning", "Voice", "No Windows desktop voices were found.")
            return
        for voice_name in voice_names:
            self.voice_combo.addItem(voice_name, voice_name)
        selected = current if current in voice_names else self.speech.default_voice_name()
        if selected in voice_names:
            self.voice_combo.setCurrentText(selected)
        self.storage.log("info", "Voice", f"Loaded {len(voice_names)} Windows desktop voices.")

    def test_voice(self) -> None:
        self.save(show_message=False)
        self.speech.speak("Hello. I am ready to help with Google, tools, projects, and Codex.")

    def save(self, show_message: bool = True) -> None:  # type: ignore[override]
        approvals = {key: check.isChecked() for key, check in self.approval_checks.items()}
        escalation = self.storage.get_setting("escalation", {})
        escalation.update(
            {
                "enabled": self.escalation_enabled.isChecked(),
                "teams_enabled": self.teams_enabled.isChecked(),
                "find_hub_enabled": self.find_hub_enabled.isChecked(),
                "quiet_hours_start": self.quiet_start.text().strip(),
                "quiet_hours_end": self.quiet_end.text().strip(),
            }
        )
        find_phone = self.storage.get_setting("find_phone", {})
        find_phone.update(
            {
                "enabled": self.find_hub_enabled.isChecked(),
                "url": str(find_phone.get("url") or "https://www.google.com/android/find/"),
                "mode": "play_sound_only",
            }
        )
        voice = self.storage.get_setting("voice", {})
        voice.update(
            {
                "enabled": self.voice_enabled.isChecked(),
                "tts_provider": self.voice_provider.currentData() or "edge",
                "edge_voice": self.edge_voice.currentText().strip() or "en-US-JennyNeural",
                "voice_name": self.voice_combo.currentData() or "",
                "rate": self.voice_rate.value(),
                "volume": self.voice_volume.value(),
                "listen_timeout_seconds": self.listen_timeout.value(),
                "min_confidence": self.voice_confidence.value() / 100,
                "recognition_mode": self.recognition_mode.currentData() or "hybrid",
            }
        )
        whatsapp = {
            "enabled": self.whatsapp_enabled.isChecked(),
            "auto_start": self.whatsapp_auto_start.isChecked(),
            "send_mode": "active",
            "store_private_messages": self.whatsapp_store_private.isChecked(),
            "max_messages_per_read": int(self.storage.get_setting("whatsapp_web", {}).get("max_messages_per_read", 25)),
        }
        gemini = self.storage.get_setting("gemini_cli", {})
        gemini.update(
            {
                "enabled": self.gemini_enabled.isChecked(),
                "model": self.gemini_model.text().strip() or "gemini-2.5-flash",
                "timeout_seconds": self.gemini_timeout.value(),
                "max_context_characters": int(gemini.get("max_context_characters", 2200)),
            }
        )
        ai_brain = {
            "enabled": self.ai_enabled.isChecked(),
            "research_enabled": self.ai_research_enabled.isChecked(),
            "gemini_enabled": self.ai_gemini_enabled.isChecked(),
            "codex_enabled": self.ai_codex_enabled.isChecked(),
            "cache_hours": self.ai_cache_hours.value(),
            "max_research_pages": self.ai_research_pages.value(),
        }
        codex_ai = self.storage.get_setting("codex_ai", {})
        codex_ai.update(
            {
                "enabled": self.codex_ai_enabled.isChecked(),
                "model": self.codex_ai_model.text().strip() or "gpt-5.4-mini",
                "reasoning_effort": self.codex_ai_reasoning.currentData() or "low",
                "timeout_seconds": self.codex_ai_timeout.value(),
                "max_context_characters": int(codex_ai.get("max_context_characters", 2200)),
            }
        )
        ui_settings = {"toast_position": self.toast_position.currentData() or "top-right"}
        existing_auto_reply = self.storage.get_setting("whatsapp_auto_reply", {})
        auto_reply = {
            "enabled": self.whatsapp_auto_enabled.isChecked(),
            "poll_seconds": self.whatsapp_auto_poll.value(),
            "skip_groups": self.whatsapp_skip_groups.isChecked(),
            "fallback_scan_enabled": bool(existing_auto_reply.get("fallback_scan_enabled", True)),
            "activity_baseline_ready": bool(existing_auto_reply.get("activity_baseline_ready", False)),
            "activity_baseline_hashes": existing_auto_reply.get("activity_baseline_hashes", []),
        }
        self.storage.set_setting("approvals", approvals)
        self.storage.set_setting("ui", ui_settings)
        self.storage.set_setting("escalation", escalation)
        self.storage.set_setting("find_phone", find_phone)
        self.storage.set_setting("voice", voice)
        self.storage.set_setting("whatsapp_web", whatsapp)
        self.storage.set_setting("whatsapp_auto_reply", auto_reply)
        self.storage.set_setting("ai_brain", ai_brain)
        self.storage.set_setting("gemini_cli", gemini)
        self.storage.set_setting("codex_ai", codex_ai)
        self.storage.log("info", "Settings", "Settings updated.")
        if show_message:
            QMessageBox.information(self, "Settings", "Settings saved.")


class FloatingChatDialog(QDialog):
    command_requested = Signal(str)

    def __init__(self, storage: Storage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.storage = storage
        self.brain = AssistantBrain(storage)
        self.setWindowTitle("Chat with Noor")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        self.setMinimumSize(390, 480)
        self.resize(430, 560)
        self.setObjectName("floatingChat")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        header = QHBoxLayout()
        title = QLabel("Noor")
        title.setObjectName("floatingChatTitle")
        close_button = QToolButton()
        close_button.setText("x")
        close_button.setToolTip("Close chat")
        close_button.clicked.connect(self.hide)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_button)
        self.history = QTextEdit()
        self.history.setReadOnly(True)
        self.history.setObjectName("floatingChatHistory")
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Message Noor")
        self.input.setFixedHeight(78)
        send = make_button("Send", self.send_message)
        layout.addLayout(header)
        layout.addWidget(self.history, 1)
        layout.addWidget(self.input)
        layout.addWidget(send, alignment=Qt.AlignRight)
        self.history.append("<b>Noor</b><br>I am ready.")

    def send_message(self) -> None:
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        self.history.append(f"<p><b>You</b><br>{html.escape(text)}</p>")
        reply = self.brain.answer(text)
        self.history.append(f"<p><b>Noor</b><br>{html.escape(reply.text)}</p>")
        self.storage.log("info", "Floating Chat", f"Q: {text}")
        self.storage.log("info", "Floating Chat", f"A: {reply.text[:600]}")
        if reply.action:
            self.command_requested.emit(reply.action)
        if reply.speak:
            SpeechService(self.storage).speak(reply.text[:900])
        self.history.moveCursor(QTextCursor.End)


class MainWindow(QMainWindow):
    whatsapp_connection_finished = Signal(object)
    whatsapp_poll_finished = Signal(object, object)
    whatsapp_progress = Signal(str, str)

    def __init__(self, storage: Storage) -> None:
        super().__init__()
        self.storage = storage
        self.speech = SpeechService(storage)
        self.brain = AssistantBrain(storage)
        self.voice_process: QProcess | None = None
        self.setWindowTitle("ESEO Standalone Assistant")
        self.pages: list[BasePage] = []
        self.page_by_title: dict[str, int] = {}
        self.nav_item_by_index: dict[int, QTreeWidgetItem] = {}
        self.nav = QTreeWidget()
        self.stack = QStackedWidget()
        self.sidebar_collapsed = False
        self.last_whatsapp_incoming_hash = ""
        self.whatsapp_connection_running = False
        self.build_ui()
        self.apply_style()
        self.build_menu()
        self.statusBar().showMessage("Ready")
        self.whatsapp_connection_finished.connect(self.handle_whatsapp_connection_finished)
        self.whatsapp_poll_finished.connect(self.handle_whatsapp_poll_finished)
        self.whatsapp_progress.connect(self.show_toast)
        self.connection_timer = QTimer(self)
        self.connection_timer.timeout.connect(self.ensure_whatsapp_connection)
        self.connection_timer.start(30000)
        self.whatsapp_polling = False
        self.whatsapp_auto_timer = QTimer(self)
        self.whatsapp_auto_timer.timeout.connect(self.poll_whatsapp_auto_replies)
        self.whatsapp_auto_timer.start(max(5, min(int(WhatsAppWebService(self.storage).auto_settings().get("poll_seconds", 12)), 60)) * 1000)
        QTimer.singleShot(450, self.ensure_whatsapp_connection)
        QTimer.singleShot(2500, self.poll_whatsapp_auto_replies)

    def build_ui(self) -> None:
        container = QWidget()
        root = QHBoxLayout(container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(252)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(10, 12, 10, 10)
        sidebar_header = QHBoxLayout()
        self.sidebar_brand = QLabel("NOOR")
        self.sidebar_brand.setObjectName("sidebarBrand")
        self.sidebar_toggle = QToolButton()
        self.sidebar_toggle.setText("<")
        self.sidebar_toggle.setToolTip("Collapse navigation")
        self.sidebar_toggle.clicked.connect(self.toggle_sidebar)
        sidebar_header.addWidget(self.sidebar_brand)
        sidebar_header.addStretch()
        sidebar_header.addWidget(self.sidebar_toggle)
        self.nav.setHeaderHidden(True)
        self.nav.setRootIsDecorated(True)
        self.nav.setIndentation(16)
        self.nav.currentItemChanged.connect(self.change_page)
        sidebar_layout.addLayout(sidebar_header)
        sidebar_layout.addWidget(self.nav, 1)
        root.addWidget(self.sidebar)
        root.addWidget(self.stack, 1)
        self.setCentralWidget(container)

        whatsapp_parent = QTreeWidgetItem(["WhatsApp"])
        whatsapp_parent.setData(0, Qt.UserRole, -1)
        whatsapp_parent_added = False
        page_types: list[tuple[type[BasePage], QTreeWidgetItem | None]] = [
            (DashboardPage, None),
            (WhatsAppPage, whatsapp_parent),
            (WhatsAppRulesPage, whatsapp_parent),
            (ReplyApprovalsPage, whatsapp_parent),
            (TasksPage, None),
            (CalendarPage, None),
            (TeamStatusPage, None),
            (ProjectsPage, None),
            (CodexSessionsPage, None),
            (ToolsPage, None),
            (RulesPage, None),
            (KnowledgePage, None),
            (ActivityPage, None),
            (EscalationsPage, None),
            (SettingsPage, None),
        ]
        for page_type, parent_item in page_types:
            page = page_type(self.storage)
            if isinstance(page, DashboardPage):
                page.command_requested.connect(self.route_assistant_command)
                page.listen_requested.connect(self.start_voice_command)
            self.pages.append(page)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setObjectName("pageScroll")
            scroll.setWidget(page)
            self.stack.addWidget(scroll)
            index = self.stack.count() - 1
            item = QTreeWidgetItem([page.title])
            item.setData(0, Qt.UserRole, index)
            if parent_item:
                if not whatsapp_parent_added:
                    self.nav.addTopLevelItem(whatsapp_parent)
                    whatsapp_parent_added = True
                parent_item.addChild(item)
            else:
                self.nav.addTopLevelItem(item)
                if page_type is DashboardPage and not whatsapp_parent_added:
                    self.nav.addTopLevelItem(whatsapp_parent)
                    whatsapp_parent_added = True
            self.nav_item_by_index[index] = item
            self.page_by_title[page.title.lower()] = index
        whatsapp_parent.setExpanded(True)
        self.nav.setCurrentItem(self.nav_item_by_index[0])

        # A top-level tool window must not inherit the main window's initial show event.
        self.floating_chat = FloatingChatDialog(self.storage)
        self.floating_chat.command_requested.connect(self.route_assistant_command)
        self.floating_chat.hide()
        self.chat_button = QToolButton(self)
        self.chat_button.setObjectName("floatingChatButton")
        self.chat_button.setIcon(QIcon(str(ICON_DIR / "chat.svg")))
        self.chat_button.setIconSize(QSize(24, 24))
        self.chat_button.setToolTip("Open chat with Noor")
        self.chat_button.clicked.connect(self.show_floating_chat)
        self.chat_button.setFixedSize(56, 56)
        self.chat_button.raise_()
        self.toast_label = QLabel(self)
        self.toast_label.setObjectName("toastLabel")
        self.toast_label.setWordWrap(True)
        self.toast_label.setFixedWidth(360)
        self.toast_label.hide()

    def open_page(self, title: str) -> None:
        index = self.page_by_title.get(title.lower())
        if index is not None:
            self.nav.setCurrentItem(self.nav_item_by_index[index])

    def route_assistant_command(self, command: str) -> None:
        command = command.strip()
        if not command:
            return
        lowered = command.lower()
        response = ""
        if "open assistant" in lowered or "open dashboard" in lowered:
            self.open_page("Assistant")
            response = "Assistant dashboard is open."
        elif "open tools" in lowered or lowered == "tools":
            self.open_page("Connected Tools")
            response = "Connected tools are open."
        elif "open projects" in lowered or lowered == "projects":
            self.open_page("Development Projects")
            response = "Development projects are open."
        elif "open codex" in lowered or lowered == "codex":
            self.open_page("Codex Sessions")
            response = "Codex sessions are open."
        elif "open tasks" in lowered or lowered == "tasks":
            self.open_page("Tasks")
            response = "Tasks are open."
        elif "open settings" in lowered or lowered == "settings":
            self.open_page("Settings")
            response = "Settings are open."
        elif "check gemini" in lowered or "gemini status" in lowered:
            self.show_toast("Gemini", "Checking Gemini CLI...")
            snapshot = connection_snapshot(self.storage)
            gemini = snapshot["gemini"]
            response = (
                f"Gemini CLI is {'available' if gemini['available'] else 'not available'}"
                f"{' and enabled' if gemini['enabled'] else ' and disabled'}."
            )
            self.show_toast("Gemini", response)
        elif "check codex" in lowered or "codex status" in lowered:
            self.show_toast("Codex", "Checking Codex CLI...")
            codex = codex_status()
            response = f"Codex CLI is {'available' if codex['available'] else 'not available'} at {codex['path'] or 'no path'}."
            self.show_toast("Codex", response)
        elif "show approvals" in lowered or "open approvals" in lowered:
            self.open_page("Reply Approvals")
            response = "Reply approvals are open."
        elif "whatsapp rules" in lowered or "reply rules" in lowered:
            self.open_page("WhatsApp Rules")
            response = "WhatsApp rules are open."
        elif "read summary" in lowered or "say summary" in lowered:
            self.open_page("Assistant")
            page = self.pages[self.page_by_title["assistant"]]
            if isinstance(page, DashboardPage):
                page.refresh()
                response = page.last_summary
        elif lowered.startswith("add task ") or lowered.startswith("new task "):
            reply = self.brain.answer(command)
            response = reply.text
        elif "test connections" in lowered or "check connections" in lowered:
            self.open_page("Connected Tools")
            self.show_toast("Tools", "Checking connected tools...")
            page = self.pages[self.page_by_title["connected tools"]]
            if isinstance(page, ToolsPage):
                page.test_all()
            response = "I checked the connected tools. Review the results on the tools page."
        elif "pause escalations" in lowered:
            escalation = self.storage.get_setting("escalation", {})
            escalation["enabled"] = False
            self.storage.set_setting("escalation", escalation)
            response = "Escalations are paused."
        else:
            reply = self.brain.answer(command)
            if reply.action and reply.action.lower() != command.lower():
                self.route_assistant_command(reply.action)
                return
            response = reply.text
            self.storage.log("info", "Assistant Command", f"Q: {command}")
            self.storage.log("info", "Assistant Command", f"A: {response[:600]}")
        if response:
            self.statusBar().showMessage(response)
            self.refresh_current_page()
            self.show_assistant_response(command, response)
            voice = self.storage.get_setting("voice", {})
            if voice.get("speak_confirmations", True):
                self.speech.speak(response[:900])

    def show_assistant_response(self, heard: str, response: str, confidence: float | None = None) -> None:
        dashboard = self.pages[self.page_by_title.get("assistant", 0)]
        if isinstance(dashboard, DashboardPage):
            dashboard.show_interaction(heard, response, confidence)

    def add_task_from_command(self, title: str) -> str:
        title = title.strip()
        if not title:
            return "I need a task title."
        now = utc_now()
        priority = "Urgent" if "urgent" in title.lower() else "Normal"
        self.storage.execute(
            "INSERT INTO tasks (title, priority, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (title, priority, now, now),
        )
        self.storage.log("info", "Assistant Command", f"Added task by command: {title}")
        self.open_page("Tasks")
        return f"Added task: {title}"

    def start_voice_command(self) -> None:
        if self.voice_process and self.voice_process.state() != QProcess.NotRunning:
            self.statusBar().showMessage("Already listening.")
            return
        self.statusBar().showMessage("Listening...")
        dashboard = self.pages[self.page_by_title.get("assistant", 0)]
        if isinstance(dashboard, DashboardPage):
            dashboard.avatar.set_listening(True)
        self.show_assistant_response("voice input", "Listening now. Speak after pressing the button.")
        args = self.speech.listen_command_args()
        self.voice_process = QProcess(self)
        self.voice_process.setProgram(args[0])
        self.voice_process.setArguments(args[1:])
        self.voice_process.setProcessChannelMode(QProcess.MergedChannels)
        self.voice_process.finished.connect(self.finish_voice_command)
        self.voice_process.start()

    def finish_voice_command(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        dashboard = self.pages[self.page_by_title.get("assistant", 0)]
        if isinstance(dashboard, DashboardPage):
            dashboard.avatar.set_listening(False)
        if not self.voice_process:
            return
        raw = bytes(self.voice_process.readAllStandardOutput()).decode("utf-8", errors="replace").strip()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"ok": False, "error": raw}
        if payload.get("ok") and payload.get("text"):
            text = str(payload["text"])
            confidence = float(payload.get("confidence", 0) or 0)
            self.storage.log("info", "Voice", f"Recognized voice command: {text}", {"confidence": confidence})
            minimum = float(self.speech.current_settings().get("min_confidence", 0.35))
            if confidence < minimum:
                message = f"I heard something unclear: {text}. Please try again closer to the microphone."
                self.storage.log("warning", "Voice", f"Rejected low-confidence voice command: {text}", {"confidence": confidence, "minimum": minimum})
                self.show_assistant_response(text, message, confidence)
                self.statusBar().showMessage(message)
                self.speech.speak("I could not understand that clearly. Please try again.")
                return
            self.route_assistant_command(text)
            self.show_assistant_response(text, self.statusBar().currentMessage(), confidence)
        else:
            error = payload.get("error") or "No speech was recognized."
            self.storage.log("warning", "Voice", f"Voice command failed: {error}")
            self.statusBar().showMessage(str(error))
            self.show_assistant_response("voice input", str(error))
            self.speech.speak("I could not understand that.")

    def refresh_current_page(self) -> None:
        index = self.stack.currentIndex()
        if 0 <= index < len(self.pages):
            self.pages[index].refresh()

    def build_menu(self) -> None:
        refresh_action = QAction("Refresh Page", self)
        refresh_action.triggered.connect(lambda: self.pages[self.stack.currentIndex()].refresh())
        self.menuBar().addAction(refresh_action)

    def change_page(self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None = None) -> None:
        if not current:
            return
        index_value = current.data(0, Qt.UserRole)
        index = -1 if index_value is None else int(index_value)
        if index < 0:
            current.setExpanded(not current.isExpanded())
            return
        self.stack.setCurrentIndex(index)
        self.pages[index].refresh()
        self.statusBar().showMessage(self.pages[index].title)

    def toggle_sidebar(self) -> None:
        self.sidebar_collapsed = not self.sidebar_collapsed
        self.nav.setVisible(not self.sidebar_collapsed)
        self.sidebar_brand.setVisible(not self.sidebar_collapsed)
        self.sidebar.setFixedWidth(58 if self.sidebar_collapsed else 252)
        self.sidebar_toggle.setText(">" if self.sidebar_collapsed else "<")
        self.sidebar_toggle.setToolTip("Expand navigation" if self.sidebar_collapsed else "Collapse navigation")

    def show_floating_chat(self) -> None:
        self.floating_chat.show()
        self.position_floating_chat()
        self.floating_chat.raise_()
        self.floating_chat.activateWindow()

    def position_floating_chat(self) -> None:
        """Keep the conversation docked immediately above the lower-right launcher."""
        if not hasattr(self, "floating_chat") or not hasattr(self, "chat_button"):
            return
        button_top_left = self.chat_button.mapToGlobal(QPoint(0, 0))
        x = button_top_left.x() + self.chat_button.width() - self.floating_chat.width()
        y = button_top_left.y() - self.floating_chat.height() - 12
        screen = QApplication.screenAt(button_top_left) or QApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            x = max(area.left() + 12, min(x, area.right() - self.floating_chat.width() - 11))
            y = max(area.top() + 12, y)
        self.floating_chat.move(x, y)

    def ensure_whatsapp_connection(self) -> None:
        if self.whatsapp_connection_running:
            return
        self.whatsapp_connection_running = True
        threading.Thread(target=self._whatsapp_connection_worker, daemon=True).start()

    def _whatsapp_connection_worker(self) -> None:
        result = WhatsAppWebService(self.storage).ensure_running()
        self.whatsapp_connection_finished.emit(result)

    def handle_whatsapp_connection_finished(self, result: Any) -> None:
        self.whatsapp_connection_running = False
        self.update_whatsapp_notification_state(result)
        if result.ok:
            dashboard = self.pages[self.page_by_title.get("assistant", 0)]
            if isinstance(dashboard, DashboardPage):
                dashboard.refresh()

    def poll_whatsapp_auto_replies(self) -> None:
        if self.whatsapp_polling:
            return
        self.whatsapp_polling = True
        threading.Thread(target=self._whatsapp_poll_worker, daemon=True).start()

    def _whatsapp_poll_worker(self) -> None:
        status = None
        result = None
        try:
            service = WhatsAppWebService(self.storage, self.whatsapp_progress.emit)
            status = service.ensure_running()
            if status.ok:
                result = service.process_unread_auto_replies()
        finally:
            self.whatsapp_poll_finished.emit(status, result)

    def handle_whatsapp_poll_finished(self, status: Any, result: Any) -> None:
        self.whatsapp_polling = False
        if status:
            self.update_whatsapp_notification_state(status)
        if result:
            message = result.message if result.ok else f"{result.message} {result.error}".strip()
            self.statusBar().showMessage(message)
            self.show_toast("WhatsApp", message)
            dashboard = self.pages[self.page_by_title.get("assistant", 0)]
            if isinstance(dashboard, DashboardPage):
                dashboard.refresh()
            whatsapp_index = self.page_by_title.get("whatsapp inbox")
            if whatsapp_index is not None and self.stack.currentIndex() == whatsapp_index:
                self.pages[whatsapp_index].refresh()

    def update_whatsapp_notification_state(self, status: Any) -> None:
        data = getattr(status, "data", None) or {}
        diagnostics = data.get("diagnostics") if isinstance(data, dict) else None
        if not isinstance(diagnostics, dict):
            return
        incoming_hash = str(diagnostics.get("last_incoming_hash") or "")
        if incoming_hash and incoming_hash != self.last_whatsapp_incoming_hash:
            self.last_whatsapp_incoming_hash = incoming_hash
            incoming_type = str(diagnostics.get("last_incoming_type") or "message").strip().lower()
            event_name = "call" if incoming_type == "call" else "message"
            self.show_toast("WhatsApp", f"New direct WhatsApp {event_name} detected. Noor is checking the reply rules.")

    def show_toast(self, title: str, message: str) -> None:
        if not hasattr(self, "toast_label"):
            return
        self.toast_label.setText(f"<b>{html.escape(title)}</b><br>{html.escape(message[:240])}")
        self.toast_label.adjustSize()
        self.toast_label.setFixedWidth(360)
        self.position_toast()
        self.toast_label.show()
        self.toast_label.raise_()
        QTimer.singleShot(6500, self.toast_label.hide)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        if hasattr(self, "chat_button"):
            self.chat_button.move(self.width() - self.chat_button.width() - 24, self.height() - self.chat_button.height() - 42)
            if hasattr(self, "floating_chat") and self.floating_chat.isVisible():
                self.position_floating_chat()
            if hasattr(self, "toast_label") and self.toast_label.isVisible():
                self.position_toast()

    def position_toast(self) -> None:
        if not hasattr(self, "toast_label"):
            return
        margin = 18
        ui_settings = self.storage.get_setting("ui", {})
        position = str(ui_settings.get("toast_position", "top-right") or "top-right").lower()
        if position not in {"top-right", "top-left", "bottom-right", "bottom-left"}:
            position = "top-right"
        left_margin = margin
        if position.endswith("left") and hasattr(self, "sidebar") and self.sidebar.isVisible():
            left_margin = self.sidebar.width() + margin
        max_x = max(margin, self.width() - self.toast_label.width() - margin)
        x = max_x if position.endswith("right") else left_margin
        if position.startswith("top"):
            y = self.menuBar().height() + margin
        else:
            if hasattr(self, "chat_button"):
                y = self.chat_button.y() - self.toast_label.height() - margin
                if hasattr(self, "floating_chat") and self.floating_chat.isVisible():
                    y = min(y, self.chat_button.y() - self.floating_chat.height() - self.toast_label.height() - margin * 2)
            else:
                y = self.height() - self.toast_label.height() - margin
        max_y = max(self.menuBar().height() + margin, self.height() - self.toast_label.height() - margin)
        self.toast_label.move(max(margin, min(x, max_x)), max(self.menuBar().height() + 8, min(y, max_y)))

    def closeEvent(self, event: Any) -> None:
        if hasattr(self, "floating_chat"):
            self.floating_chat.close()
        super().closeEvent(event)

    def apply_style(self) -> None:
        QApplication.instance().setStyleSheet(
            """
            QMainWindow, QWidget {
                font-size: 13px;
                color: #edf7f8;
                background: #080d1d;
            }
            QLabel {
                background: transparent;
            }
            QScrollArea#pageScroll {
                background: #080d1d;
                border: 0;
            }
            QScrollArea#pageScroll > QWidget > QWidget {
                background: #080d1d;
            }
            QMenuBar, QStatusBar {
                background: #090f21;
                color: #cce5e9;
                border: 0;
            }
            QFrame#sidebar {
                background: #0d1429;
                border-right: 1px solid #172d47;
            }
            QLabel#sidebarBrand {
                color: #4df1dd;
                font-size: 14px;
                font-weight: 700;
                letter-spacing: 1px;
            }
            QTreeWidget {
                background: #0d1429;
                color: #dbe9ee;
                border: 0;
            }
            QTreeWidget::item {
                padding: 9px 8px;
                border-radius: 4px;
            }
            QTreeWidget::item:selected {
                background: #123d50;
                color: #ffffff;
            }
            QTreeWidget::branch:has-children:closed, QTreeWidget::branch:has-children:open {
                border-image: none;
                image: none;
            }
            QToolButton {
                min-width: 28px;
                min-height: 28px;
                border: 1px solid #28516a;
                border-radius: 4px;
                background: #10243a;
                color: #dffdf9;
            }
            QToolButton:hover {
                background: #163b53;
            }
            QPushButton {
                padding: 7px 12px;
                border: 1px solid #2cdccf;
                border-radius: 4px;
                background: #10243a;
                color: #efffff;
            }
            QPushButton:hover {
                background: #14344f;
            }
            QLineEdit, QPlainTextEdit, QTextEdit, QTableWidget, QComboBox, QSpinBox, QDateEdit, QTimeEdit {
                border: 1px solid #263d58;
                border-radius: 4px;
                padding: 4px;
                background: #0c1529;
                color: #edf7f8;
                selection-background-color: #1f8a91;
            }
            QComboBox::drop-down {
                border: 0;
                width: 22px;
            }
            QHeaderView::section {
                background: #111b32;
                color: #9bdfe4;
                border: 0;
                padding: 6px;
            }
            QTableWidget {
                gridline-color: #172844;
            }
            QGroupBox {
                border: 1px solid #263d58;
                border-radius: 5px;
                margin-top: 10px;
                padding: 10px;
                background: #0b1225;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
                color: #9bdfe4;
            }
            QFrame#rulesToolbar {
                border: 1px solid #1d4058;
                border-radius: 6px;
                background: #0a1327;
            }
            QLabel#rulesMetric {
                color: #9fc8d4;
                padding: 2px 4px;
            }
            QFrame#ruleEditorPanel {
                border: 1px solid #24566b;
                border-radius: 7px;
                background: #0a1429;
            }
            QDialog#ruleBuilderDialog {
                background: #0a1429;
                border: 1px solid #24566b;
                border-radius: 7px;
            }
            QLabel#ruleEditorTitle {
                color: #f4ffff;
                font-size: 16px;
                font-weight: 700;
            }
            QToolButton#rulesToolButton, QToolButton#rulesPrimaryButton {
                padding: 6px 9px;
                min-height: 28px;
                border-radius: 4px;
            }
            QToolButton#rulesToolButton {
                border: 1px solid #28495f;
                background: #0f2237;
                color: #e9fbfd;
            }
            QToolButton#rulesToolButton:hover {
                background: #16364d;
                border-color: #34708b;
            }
            QToolButton#rulesPrimaryButton {
                border: 1px solid #32eadb;
                background: #118478;
                color: #f4ffff;
                font-weight: 700;
            }
            QToolButton#rulesPrimaryButton:hover {
                background: #159b8e;
            }
            QTableWidget#rulesTable {
                background: #091326;
                alternate-background-color: #0c182d;
                border-color: #1c4058;
            }
            QPlainTextEdit#rulesOutput {
                background: #081a2b;
                border-color: #1f6670;
                color: #cdecef;
            }
            QLabel#heroBrand {
                font-size: 28px;
                font-weight: 700;
                color: #f5ffff;
            }
            QLabel#connectionState {
                color: #9bdfe4;
                padding: 8px 12px;
                border: 1px solid #214c63;
                border-radius: 4px;
                background: #0d1b31;
            }
            QFrame#commandBar {
                border: 1px solid #1c5365;
                border-radius: 6px;
                background: #0b152c;
            }
            QFrame#avatarPanel {
                border: 1px solid #1e5562;
                border-radius: 8px;
                background: #091329;
            }
            QFrame#assistantCard {
                border: 1px solid #1e5260;
                border-radius: 7px;
                background: #101833;
            }
            QLabel#cardTitle {
                color: #efffff;
                font-size: 15px;
                font-weight: 700;
            }
            QLabel#cardBody {
                color: #bcd5dd;
                line-height: 140%;
                background: transparent;
            }
            QLabel#assistantSummary {
                font-size: 18px;
                color: #efffff;
                padding: 12px;
                background: #0d2033;
                border: 1px solid #1e6670;
                border-radius: 6px;
            }
            QLabel#heardLabel {
                color: #9bdfe4;
                padding: 8px 10px;
                background: #07192a;
                border: 1px solid #174a5a;
                border-radius: 5px;
            }
            QToolButton#floatingChatButton {
                background: #20bcae;
                border: 1px solid #78fff0;
                color: #06151d;
                font-weight: 700;
                border-radius: 28px;
            }
            QDialog#floatingChat {
                background: #0a1328;
                border: 1px solid #2a6070;
                border-radius: 8px;
            }
            QLabel#floatingChatTitle {
                font-size: 16px;
                font-weight: 700;
                color: #f1ffff;
            }
            QLabel#toastLabel {
                color: #efffff;
                background: #0c2234;
                border: 1px solid #31d8cc;
                border-radius: 8px;
                padding: 12px;
            }
            QTextEdit#floatingChatHistory {
                background: #0c1930;
                border: 1px solid #1e4a62;
                border-radius: 6px;
                padding: 8px;
            }
            """
        )
