from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QImage, QPainter, QPen

from standalone_assistant.core.paths import EMPLOYEE_REPORTS_CONFIG, PROJECT_ROOT, REPORTS_DIR
from standalone_assistant.core.time_parser import now_local


SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
]


@dataclass
class Employee:
    employee_id: str
    name: str
    designation: str
    department: str
    status: str
    reporting_manager: str
    skill_level: str
    aliases: set[str] = field(default_factory=set)


@dataclass
class WorkItem:
    employee_name: str
    writer_label: str
    project: str
    team: str
    keyword: str
    item_type: str
    words: int
    work_date: date | None
    draft_status: str
    proofread: str
    translation: str
    publication: str
    review_status: str


@dataclass
class EmployeeStats:
    employee: Employee | None
    name: str
    designation: str = ""
    department: str = ""
    items: int = 0
    base_items: int = 0
    variations: int = 0
    words: int = 0
    ready: int = 0
    proofread_done: int = 0
    translation_ready: int = 0
    approved: int = 0
    pending: int = 0
    backlog: int = 0
    latest_keywords: list[str] = field(default_factory=list)

    @property
    def ready_rate(self) -> float:
        return self.ready / self.items if self.items else 0.0

    @property
    def performance(self) -> str:
        if not self.items:
            return "No work logged"
        if self.pending > max(2, self.items // 3):
            return "Needs attention"
        if self.items >= 9 and self.ready_rate >= 0.8:
            return "Excellent"
        if self.items >= 4 and self.ready_rate >= 0.6:
            return "Good"
        return "Watch"


@dataclass
class ReportResult:
    ok: bool
    caption: str = ""
    image_path: str = ""
    error: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class EmployeeReportService:
    _qt_app: QGuiApplication | None = None
    _font_family = "Arial"

    def __init__(self, config_path: Path = EMPLOYEE_REPORTS_CONFIG) -> None:
        self.config_path = config_path
        self._sheets_service = None

    def load_config(self) -> dict[str, Any]:
        try:
            config = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            config = {}
        return config if isinstance(config, dict) else {}

    def generate_report(self, kind: str = "weekly", *, now: datetime | None = None) -> ReportResult:
        kind = kind.strip().casefold()
        if kind not in {"weekly", "monthly"}:
            return ReportResult(False, error=f"Unsupported employee report type: {kind}")
        now = now or now_local()
        try:
            config = self.load_config()
            employees = self.load_employees(config)
            start, end, period_label = self.period(kind, now)
            items = self.load_work_items(config, employees)
            in_period = [item for item in items if item.work_date and start <= item.work_date <= end]
            stats = self.build_stats(config, employees, items, in_period)
            report_data = {
                "kind": kind,
                "period_start": start.isoformat(),
                "period_end": end.isoformat(),
                "period_label": period_label,
                "generated_at": now.isoformat(),
                "employees": len(employees),
                "items": len(in_period),
                "words": sum(item.words for item in in_period),
                "projects": sorted({item.project for item in in_period}),
                "stats": stats,
            }
            caption = self.caption(report_data)
            image_path = self.render_image(report_data)
            return ReportResult(True, caption=caption, image_path=str(image_path), data=report_data)
        except Exception as exc:
            return ReportResult(False, error=str(exc))

    def sheets(self):
        if self._sheets_service is not None:
            return self._sheets_service
        config = self.load_config().get("google", {})
        credentials_path = Path(str(config.get("credentials_path") or PROJECT_ROOT / "credentials.json"))
        token_path = Path(str(config.get("token_path") or PROJECT_ROOT / ".secrets" / "token.json"))
        credentials: Credentials | None = None
        if token_path.exists():
            credentials = Credentials.from_authorized_user_file(str(token_path), SHEETS_SCOPES)
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(credentials.to_json(), encoding="utf-8")
        if not credentials or not credentials.valid:
            if credentials_path.exists():
                raise RuntimeError(f"Google Sheets token is not connected for Noor reports. Reconnect with {credentials_path}.")
            raise RuntimeError("Google Sheets credentials are missing for Noor reports.")
        self._sheets_service = build("sheets", "v4", credentials=credentials)
        return self._sheets_service

    def read_values(self, spreadsheet_url: str, sheet_name: str, a1_range: str) -> list[list[str]]:
        spreadsheet_id = self.spreadsheet_id(spreadsheet_url)
        full_range = f"{self.quote_sheet(sheet_name)}!{a1_range}"
        response = self.sheets().spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=full_range).execute()
        rows = response.get("values", [])
        return [[self.clean_cell(cell) for cell in row] for row in rows if isinstance(row, list)]

    def load_employees(self, config: dict[str, Any]) -> dict[str, Employee]:
        directory = config.get("employee_directory", {})
        rows = self.read_values(
            str(directory.get("spreadsheet_url") or ""),
            str(directory.get("sheet_name") or "Employees"),
            str(directory.get("range") or "A1:AH1000"),
        )
        header_index = self.find_header_row(rows, {"Employee ID", "Full Name"})
        if header_index is None:
            raise RuntimeError("Employee sheet header was not found.")
        headers = self.header_map(rows[header_index])
        active_statuses = {self.norm(item) for item in directory.get("active_statuses", ["Active"])}
        owner_names = {self.norm(item) for item in config.get("owner_names", [])}
        employees: dict[str, Employee] = {}
        for row in rows[header_index + 1 :]:
            name = self.row_value(row, headers, "full name")
            if not name or self.norm(name) in owner_names:
                continue
            status = self.row_value(row, headers, "status")
            if active_statuses and self.norm(status) not in active_statuses:
                continue
            employee = Employee(
                employee_id=self.row_value(row, headers, "employee id"),
                name=name,
                designation=self.row_value(row, headers, "designation"),
                department=self.row_value(row, headers, "department"),
                status=status,
                reporting_manager=self.row_value(row, headers, "reporting manager"),
                skill_level=self.row_value(row, headers, "skill level"),
            )
            employee.aliases = self.employee_aliases(employee)
            employees[self.norm(employee.name)] = employee
        return employees

    def load_work_items(self, config: dict[str, Any], employees: dict[str, Employee]) -> list[WorkItem]:
        items: list[WorkItem] = []
        for workbook in config.get("workbooks", []):
            if not isinstance(workbook, dict):
                continue
            rows = self.read_values(
                str(workbook.get("spreadsheet_url") or ""),
                str(workbook.get("sheet_name") or ""),
                str(workbook.get("range") or "A1:Z1000"),
            )
            if not rows:
                continue
            headers = self.header_map(rows[0])
            columns = {self.norm(key): self.norm(value) for key, value in dict(workbook.get("columns", {})).items()}
            aliases = {self.norm(key): str(value).strip() for key, value in dict(workbook.get("employee_aliases", {})).items()}
            for row in rows[1:]:
                writer = self.row_value(row, headers, columns.get("writer", "writer"))
                keyword = self.row_value(row, headers, columns.get("keyword", "main keyword"))
                item_type = self.row_value(row, headers, columns.get("type", "type"))
                if not writer or not (keyword or item_type):
                    continue
                employee_name = self.resolve_employee_name(writer, aliases, employees)
                if self.norm(employee_name) not in employees and self.norm(employee_name) == self.norm(writer):
                    employee_name = writer
                items.append(
                    WorkItem(
                        employee_name=employee_name,
                        writer_label=writer,
                        project=str(workbook.get("project") or "Project"),
                        team=str(workbook.get("team") or "Team"),
                        keyword=keyword or item_type,
                        item_type=item_type,
                        words=self.parse_words(self.row_value(row, headers, columns.get("word_count", "word count"))),
                        work_date=self.parse_date(self.row_value(row, headers, columns.get("date", "date"))),
                        draft_status=self.row_value(row, headers, columns.get("draft_status", "draft status")),
                        proofread=self.row_value(row, headers, columns.get("proofread", "proofread")),
                        translation=self.row_value(row, headers, columns.get("translation", "translation")),
                        publication=self.row_value(row, headers, columns.get("publication", "publications")),
                        review_status=self.row_value(row, headers, columns.get("review_status", "review status")),
                    )
                )
        return items

    def build_stats(
        self,
        config: dict[str, Any],
        employees: dict[str, Employee],
        all_items: list[WorkItem],
        period_items: list[WorkItem],
    ) -> list[EmployeeStats]:
        by_name: dict[str, EmployeeStats] = {}
        period_employee_names = {self.norm(item.employee_name) for item in period_items}
        workbook_teams = {self.norm(workbook.get("team", "")) for workbook in config.get("workbooks", []) if isinstance(workbook, dict)}
        for employee in employees.values():
            if self.norm(employee.department) in workbook_teams or self.norm(employee.name) in period_employee_names:
                by_name[self.norm(employee.name)] = EmployeeStats(
                    employee=employee,
                    name=employee.name,
                    designation=employee.designation,
                    department=employee.department,
                )
        for item in period_items:
            key = self.norm(item.employee_name)
            employee = employees.get(key)
            if key not in by_name:
                by_name[key] = EmployeeStats(
                    employee=employee,
                    name=employee.name if employee else item.employee_name,
                    designation=employee.designation if employee else "",
                    department=employee.department if employee else item.team,
                )
            stats = by_name[key]
            stats.items += 1
            stats.words += item.words
            stats.base_items += int("base" in self.norm(item.item_type))
            stats.variations += int("variation" in self.norm(item.item_type))
            stats.ready += int(self.is_ready(item.draft_status))
            stats.proofread_done += int("done" in self.norm(item.proofread))
            stats.translation_ready += int(self.is_ready(item.translation) or "translated" in self.norm(item.translation))
            stats.approved += int("approved" in self.norm(item.review_status))
            stats.pending += int(self.is_pending(item))
            if item.keyword and len(stats.latest_keywords) < 3:
                stats.latest_keywords.append(item.keyword)
        for item in all_items:
            if item.work_date is None and item.employee_name:
                key = self.norm(item.employee_name)
                if key in by_name:
                    by_name[key].backlog += 1
        return sorted(by_name.values(), key=lambda item: (item.items, item.words, item.name.casefold()), reverse=True)

    def caption(self, data: dict[str, Any]) -> str:
        stats: list[EmployeeStats] = data["stats"]
        kind = str(data["kind"]).title()
        projects = ", ".join(data.get("projects") or ["No active project rows"])
        total_items = int(data["items"])
        total_words = int(data["words"])
        ready = sum(item.ready for item in stats)
        pending = sum(item.pending for item in stats)
        top = next((item for item in stats if item.items), None)
        lines = [
            f"{kind} Work Progress",
            f"Period: {data['period_label']}",
            f"Projects: {projects}",
            f"Total: {total_items} items, {total_words:,} words, {ready} ready drafts, {pending} pending checks.",
        ]
        if top:
            lines.append(f"Top output: {top.name} - {top.items} items, {top.words:,} words, {top.performance}.")
        lines.append("Visual report attached.")
        return "\n".join(lines)[:1000]

    def render_image(self, data: dict[str, Any]) -> Path:
        self.ensure_qt_app()
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        stats: list[EmployeeStats] = data["stats"]
        visible_stats = stats[:8]
        width = 1280
        height = max(760, 515 + len(visible_stats) * 74)
        image = QImage(width, height, QImage.Format_ARGB32)
        image.fill(QColor("#f5f8fb"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        try:
            self.draw_report(painter, width, height, data, visible_stats)
        finally:
            painter.end()
        digest = hashlib.sha1(f"{data['kind']}:{data['period_start']}:{data['period_end']}:{datetime.utcnow().isoformat()}".encode("utf-8")).hexdigest()[:10]
        path = REPORTS_DIR / f"employee_{data['kind']}_report_{data['period_start']}_{digest}.png"
        image.save(str(path), "PNG")
        return path

    @classmethod
    def ensure_qt_app(cls) -> None:
        existing = QGuiApplication.instance()
        if existing is None:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            cls._qt_app = QGuiApplication(sys.argv[:1])
        for font_path in (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/calibri.ttf")):
            if font_path.exists():
                font_id = QFontDatabase.addApplicationFont(str(font_path))
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    cls._font_family = families[0]
                    break

    def draw_report(self, painter: QPainter, width: int, height: int, data: dict[str, Any], stats: list[EmployeeStats]) -> None:
        navy = QColor("#102033")
        muted = QColor("#617085")
        teal = QColor("#00a99d")
        blue = QColor("#2364aa")
        amber = QColor("#f0a202")
        red = QColor("#d64550")
        green = QColor("#168a55")
        white = QColor("#ffffff")
        line = QColor("#d8e1ec")

        self.rounded_rect(painter, 0, 0, width, 176, QColor("#11243a"), 0)
        painter.setPen(white)
        self.set_font(painter, 30, True)
        painter.drawText(QRectF(44, 34, width - 88, 42), Qt.AlignLeft | Qt.AlignVCenter, f"ESEO {str(data['kind']).title()} Work Progress")
        self.set_font(painter, 15, False)
        painter.setPen(QColor("#b6c7d8"))
        painter.drawText(QRectF(46, 78, width - 92, 28), Qt.AlignLeft | Qt.AlignVCenter, str(data["period_label"]))
        painter.drawText(QRectF(46, 110, width - 92, 28), Qt.AlignLeft | Qt.AlignVCenter, f"Generated {self.display_generated_at(str(data['generated_at']))}")

        total_items = int(data["items"])
        total_words = int(data["words"])
        ready = sum(item.ready for item in stats)
        approved = sum(item.approved for item in stats)
        pending = sum(item.pending for item in stats)
        active_people = sum(1 for item in stats if item.items)
        cards = [
            ("Items", f"{total_items}", "dated work rows", teal),
            ("Words", f"{total_words:,}", "tracked output", blue),
            ("Ready", f"{ready}", "drafts ready", green),
            ("Pending", f"{pending}", "checks open", amber if pending else green),
            ("People", f"{active_people}", "active this period", QColor("#6d5bd0")),
        ]
        card_w = (width - 88 - 4 * 18) / 5
        x = 44.0
        for title, value, subtitle, color in cards:
            self.rounded_rect(painter, x, 128, card_w, 108, white, 14)
            self.rounded_rect(painter, x, 128, 6, 108, color, 3)
            painter.setPen(muted)
            self.set_font(painter, 12, True)
            painter.drawText(QRectF(x + 22, 146, card_w - 34, 22), Qt.AlignLeft | Qt.AlignVCenter, title.upper())
            painter.setPen(navy)
            self.set_font(painter, 25, True)
            painter.drawText(QRectF(x + 22, 169, card_w - 34, 34), Qt.AlignLeft | Qt.AlignVCenter, value)
            painter.setPen(muted)
            self.set_font(painter, 12, False)
            painter.drawText(QRectF(x + 22, 205, card_w - 34, 22), Qt.AlignLeft | Qt.AlignVCenter, subtitle)
            x += card_w + 18

        y = 272
        self.rounded_rect(painter, 44, y, width - 88, height - y - 34, white, 18)
        painter.setPen(navy)
        self.set_font(painter, 20, True)
        painter.drawText(QRectF(72, y + 20, width - 144, 30), Qt.AlignLeft | Qt.AlignVCenter, "Employee Performance")
        painter.setPen(muted)
        self.set_font(painter, 12, False)
        projects = ", ".join(data.get("projects") or ["No active project rows"])
        painter.drawText(QRectF(72, y + 52, width - 144, 24), Qt.AlignLeft | Qt.AlignVCenter, f"Sources: HR & Payroll, {projects}")

        table_y = y + 92
        headers = ["Employee", "Items", "Words", "Ready", "Approved", "Pending", "Performance"]
        positions = [72, 505, 610, 760, 875, 1000, 1110]
        painter.setPen(QColor("#8290a4"))
        self.set_font(painter, 11, True)
        for left, header in zip(positions, headers):
            painter.drawText(QRectF(left, table_y, 150, 22), Qt.AlignLeft | Qt.AlignVCenter, header.upper())
        painter.setPen(QPen(line, 1))
        painter.drawLine(72, table_y + 31, width - 72, table_y + 31)

        row_y = table_y + 46
        max_words = max([item.words for item in stats] + [1])
        for index, stat in enumerate(stats):
            bg = QColor("#f7fafc") if index % 2 == 0 else QColor("#ffffff")
            self.rounded_rect(painter, 64, row_y - 8, width - 128, 60, bg, 10)
            initials = self.initials(stat.name)
            avatar_color = teal if stat.performance == "Excellent" else blue if stat.performance == "Good" else amber if stat.performance == "Watch" else red
            self.rounded_rect(painter, 74, row_y, 42, 42, avatar_color, 21)
            painter.setPen(white)
            self.set_font(painter, 13, True)
            painter.drawText(QRectF(74, row_y, 42, 42), Qt.AlignCenter, initials)

            painter.setPen(navy)
            self.set_font(painter, 14, True)
            painter.drawText(QRectF(128, row_y - 1, 350, 22), Qt.AlignLeft | Qt.AlignVCenter, stat.name)
            painter.setPen(muted)
            self.set_font(painter, 11, False)
            subtitle = stat.designation or stat.department or "Employee"
            painter.drawText(QRectF(128, row_y + 21, 350, 19), Qt.AlignLeft | Qt.AlignVCenter, subtitle[:54])

            painter.setPen(navy)
            self.set_font(painter, 14, True)
            painter.drawText(QRectF(505, row_y + 6, 80, 28), Qt.AlignLeft | Qt.AlignVCenter, str(stat.items))
            painter.drawText(QRectF(610, row_y + 6, 130, 28), Qt.AlignLeft | Qt.AlignVCenter, f"{stat.words:,}")
            self.progress_bar(painter, 610, row_y + 38, 115, 5, stat.words / max_words, blue)
            painter.drawText(QRectF(760, row_y + 6, 80, 28), Qt.AlignLeft | Qt.AlignVCenter, str(stat.ready))
            painter.drawText(QRectF(875, row_y + 6, 80, 28), Qt.AlignLeft | Qt.AlignVCenter, str(stat.approved))
            painter.setPen(red if stat.pending else green)
            painter.drawText(QRectF(1000, row_y + 6, 80, 28), Qt.AlignLeft | Qt.AlignVCenter, str(stat.pending))
            self.badge(painter, 1110, row_y + 7, stat.performance)
            row_y += 74

        footer_y = height - 50
        painter.setPen(QColor("#91a0b4"))
        self.set_font(painter, 11, False)
        painter.drawText(QRectF(72, footer_y, width - 144, 20), Qt.AlignLeft | Qt.AlignVCenter, "Private payroll, bank, NID, and personal-contact fields are excluded from this WhatsApp report.")

    def period(self, kind: str, now: datetime) -> tuple[date, date, str]:
        today = now.date()
        if kind == "monthly":
            end_day = calendar.monthrange(today.year, today.month)[1]
            start = date(today.year, today.month, 1)
            end = date(today.year, today.month, end_day)
            label = f"{start:%b 1} - {end:%b %d, %Y}"
            return start, end, label
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=4)
        label = f"{start:%b %d} - {end:%b %d, %Y}"
        return start, end, label

    @staticmethod
    def spreadsheet_id(url: str) -> str:
        match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
        if not match:
            if re.fullmatch(r"[A-Za-z0-9_-]{20,}", url.strip()):
                return url.strip()
            raise ValueError("Invalid Google Sheets URL in employee report config.")
        return match.group(1)

    @staticmethod
    def quote_sheet(sheet_name: str) -> str:
        escaped = sheet_name.replace("'", "''")
        return f"'{escaped}'"

    @staticmethod
    def clean_cell(value: Any) -> str:
        return str(value or "").replace("\r", "\n").strip()

    @classmethod
    def norm(cls, value: Any) -> str:
        return re.sub(r"\s+", " ", cls.clean_cell(value)).strip().casefold()

    @classmethod
    def header_map(cls, row: list[str]) -> dict[str, int]:
        return {cls.norm(value): index for index, value in enumerate(row) if cls.norm(value)}

    @classmethod
    def find_header_row(cls, rows: list[list[str]], required: set[str]) -> int | None:
        required_norm = {cls.norm(item) for item in required}
        for index, row in enumerate(rows[:12]):
            headers = set(cls.header_map(row))
            if required_norm.issubset(headers):
                return index
        return None

    @staticmethod
    def row_value(row: list[str], headers: dict[str, int], header: str) -> str:
        index = headers.get(header)
        if index is None or index >= len(row):
            return ""
        return str(row[index] or "").strip()

    @classmethod
    def employee_aliases(cls, employee: Employee) -> set[str]:
        parts = [part for part in re.split(r"\s+", employee.name) if part]
        aliases = {cls.norm(employee.name)}
        aliases.update(cls.norm(part) for part in parts if len(part) >= 3)
        if employee.employee_id:
            aliases.add(cls.norm(employee.employee_id))
        return aliases

    @classmethod
    def resolve_employee_name(cls, writer: str, aliases: dict[str, str], employees: dict[str, Employee]) -> str:
        key = cls.norm(writer)
        if key in aliases:
            return aliases[key]
        for employee in employees.values():
            if key in employee.aliases:
                return employee.name
        return writer.strip()

    @staticmethod
    def parse_words(value: str) -> int:
        match = re.search(r"\d[\d,]*", value or "")
        if not match:
            return 0
        return int(match.group(0).replace(",", ""))

    @staticmethod
    def parse_date(value: str) -> date | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d-%b-%Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        return None

    @classmethod
    def is_ready(cls, value: str) -> bool:
        normalized = cls.norm(value)
        return any(token in normalized for token in ("ready", "completed", "done", "approved"))

    @classmethod
    def is_pending(cls, item: WorkItem) -> bool:
        return any("pending" in cls.norm(value) or "wip" in cls.norm(value) for value in [item.draft_status, item.proofread, item.translation, item.publication, item.review_status])

    @staticmethod
    def set_font(painter: QPainter, size: int, bold: bool = False) -> None:
        font = QFont(EmployeeReportService._font_family, size)
        font.setBold(bold)
        painter.setFont(font)

    @staticmethod
    def rounded_rect(painter: QPainter, x: float, y: float, w: float, h: float, color: QColor, radius: float) -> None:
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(x, y, w, h), radius, radius)

    @classmethod
    def badge(cls, painter: QPainter, x: float, y: float, label: str) -> None:
        colors = {
            "Excellent": (QColor("#dff7ed"), QColor("#127a4c")),
            "Good": (QColor("#e2edff"), QColor("#245aa8")),
            "Watch": (QColor("#fff4dc"), QColor("#956000")),
            "Needs attention": (QColor("#ffe5e8"), QColor("#b42030")),
            "No work logged": (QColor("#eef2f6"), QColor("#637083")),
        }
        bg, fg = colors.get(label, colors["No work logged"])
        cls.rounded_rect(painter, x, y, 132, 28, bg, 14)
        painter.setPen(fg)
        cls.set_font(painter, 10, True)
        painter.drawText(QRectF(x + 8, y, 116, 28), Qt.AlignCenter, label)

    @classmethod
    def progress_bar(cls, painter: QPainter, x: float, y: float, w: float, h: float, ratio: float, color: QColor) -> None:
        cls.rounded_rect(painter, x, y, w, h, QColor("#dfe7f1"), h / 2)
        cls.rounded_rect(painter, x, y, max(4, w * max(0.0, min(1.0, ratio))), h, color, h / 2)

    @staticmethod
    def initials(name: str) -> str:
        parts = [part for part in re.split(r"\s+", name.strip()) if part]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][:1] + parts[-1][:1]).upper()

    @staticmethod
    def display_generated_at(value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return value
        return parsed.strftime("%b %d, %Y %I:%M %p").replace(" 0", " ")
