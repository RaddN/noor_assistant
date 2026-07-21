from __future__ import annotations

import calendar
import hashlib
import html
import json
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from playwright.sync_api import sync_playwright

from standalone_assistant.core.paths import ASSETS_DIR, EMPLOYEE_REPORTS_CONFIG, PROJECT_ROOT, REPORT_TEMPLATES_DIR, REPORTS_DIR
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
    photo_link: str
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
    target_items: int = 0
    target_days: int = 0
    latest_keywords: list[str] = field(default_factory=list)

    @property
    def ready_rate(self) -> float:
        return self.ready / self.items if self.items else 0.0

    @property
    def translation_rate(self) -> float:
        return self.translation_ready / self.items if self.items else 0.0

    @property
    def target_rate(self) -> float:
        return self.translation_ready / self.target_items if self.target_items else (1.0 if self.translation_ready else 0.0)

    @property
    def target_gap(self) -> int:
        return max(0, self.target_items - self.translation_ready)

    @property
    def performance(self) -> str:
        if not self.items:
            return "No work logged"
        if self.target_items and self.target_rate < 0.7:
            return "Needs attention"
        if self.pending > max(2, self.items // 3):
            return "Needs attention"
        if self.target_items and self.target_rate >= 1.0 and self.pending == 0:
            return "Excellent"
        if self.items >= 9 and self.ready_rate >= 0.8 and self.translation_rate >= 0.8:
            return "Excellent"
        if self.target_items and self.target_rate >= 0.85:
            return "Good"
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
    def __init__(self, config_path: Path = EMPLOYEE_REPORTS_CONFIG) -> None:
        self.config_path = config_path
        self._sheets_service = None
        self._drive_service = None

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
            stats = self.build_stats(config, employees, items, in_period, start, end, now)
            recent_items = sorted(
                in_period,
                key=lambda item: (item.work_date or date.min, item.employee_name.casefold(), item.keyword.casefold()),
                reverse=True,
            )[:8]
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
                "recent_items": recent_items,
            }
            caption = self.caption(report_data)
            image_path = self.render_image(report_data)
            return ReportResult(True, caption=caption, image_path=str(image_path), data=report_data)
        except Exception as exc:
            return ReportResult(False, error=str(exc))

    def sheets(self):
        if self._sheets_service is not None:
            return self._sheets_service
        credentials = self.credentials()
        self._sheets_service = build("sheets", "v4", credentials=credentials)
        return self._sheets_service

    def drive(self):
        if self._drive_service is not None:
            return self._drive_service
        credentials = self.credentials()
        self._drive_service = build("drive", "v3", credentials=credentials)
        return self._drive_service

    def credentials(self) -> Credentials:
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
        return credentials

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
                photo_link=self.row_value(row, headers, "photo link"),
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
        period_start: date,
        period_end: date,
        now: datetime,
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
        effective_end = min(period_end, now.date())
        for stats in by_name.values():
            target_items, target_days = self.employee_target(config, stats.employee, stats.department, period_start, effective_end)
            stats.target_items = target_items
            stats.target_days = target_days
        return sorted(by_name.values(), key=lambda item: (item.items, item.words, item.name.casefold()), reverse=True)

    def employee_target(self, config: dict[str, Any], employee: Employee | None, department: str, start: date, end: date) -> tuple[int, int]:
        if end < start:
            return 0, 0
        target_configs = config.get("performance_targets", {})
        if not isinstance(target_configs, dict):
            return 0, 0
        employee_department = self.norm(employee.department if employee else department)
        for target in target_configs.values():
            if not isinstance(target, dict):
                continue
            if self.norm(target.get("team", "")) != employee_department:
                continue
            working_days = {self.norm(day)[:3] for day in target.get("working_days", []) if str(day).strip()}
            history = [item for item in target.get("history", []) if isinstance(item, dict)]
            history.sort(key=lambda item: str(item.get("start_date") or "0000-00-00"))
            total = 0
            days = 0
            current = start
            while current <= end:
                day_key = self.norm(current.strftime("%a"))[:3]
                if not working_days or day_key in working_days:
                    daily_items = self.target_for_day(history, current)
                    total += daily_items
                    days += int(daily_items > 0)
                current += timedelta(days=1)
            return total, days
        return 0, 0

    @classmethod
    def target_for_day(cls, history: list[dict[str, Any]], current: date) -> int:
        selected: dict[str, Any] | None = None
        for item in history:
            start = cls.parse_date(str(item.get("start_date") or ""))
            if start and start <= current:
                selected = item
        if not selected:
            return 0
        try:
            return int(selected.get("daily_items") or (int(selected.get("categories_per_day", 0)) * int(selected.get("rows_per_category", 0))))
        except (TypeError, ValueError):
            return 0

    def caption(self, data: dict[str, Any]) -> str:
        stats: list[EmployeeStats] = data["stats"]
        kind = str(data["kind"]).title()
        projects = ", ".join(data.get("projects") or ["No active project rows"])
        total_items = int(data["items"])
        total_words = int(data["words"])
        ready = sum(item.ready for item in stats)
        translated = sum(item.translation_ready for item in stats)
        target = sum(item.target_items for item in stats)
        pending = sum(item.pending for item in stats)
        top = next((item for item in stats if item.items), None)
        lines = [
            f"{kind} Work Progress",
            f"Period: {data['period_label']}",
            f"Projects: {projects}",
            f"Total: {total_items} items, {total_words:,} words, {ready} ready drafts, {translated}/{target or translated} translated target, {pending} pending checks.",
        ]
        if top:
            lines.append(f"Top output: {top.name} - {top.items} items, {top.words:,} words, {top.performance}.")
        return "\n".join(lines)[:1000]

    def render_image(self, data: dict[str, Any]) -> Path:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(f"{data['kind']}:{data['period_start']}:{data['period_end']}:{datetime.utcnow().isoformat()}".encode("utf-8")).hexdigest()[:10]
        base_name = f"employee_{data['kind']}_report_{data['period_start']}_{digest}"
        html_path = REPORTS_DIR / f"{base_name}.html"
        image_path = REPORTS_DIR / f"{base_name}.png"
        html_path.write_text(self.render_html(data), encoding="utf-8")
        self.capture_html_report(html_path, image_path)
        return image_path

    def render_html(self, data: dict[str, Any]) -> str:
        config = self.load_config()
        template_path = self.resolve_config_path(str((config.get("templates") or {}).get(str(data["kind"]), "")))
        if not template_path.exists():
            template_path = REPORT_TEMPLATES_DIR / f"employee_{data['kind']}_report.html"
        template = template_path.read_text(encoding="utf-8")
        context = self.template_context(data, config)
        return re.sub(r"\{\{([A-Za-z0-9_]+)\}\}", lambda item: context.get(item.group(1), ""), template)

    def capture_html_report(self, html_path: Path, image_path: Path) -> None:
        chrome_path = self.chrome_path()
        launch_options: dict[str, Any] = {"headless": True, "args": ["--disable-gpu", "--no-sandbox"]}
        if chrome_path:
            launch_options["executable_path"] = chrome_path
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**launch_options)
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 720}, device_scale_factor=1)
                page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
                page.locator(".report-page").screenshot(path=str(image_path), animations="disabled")
            finally:
                browser.close()

    @staticmethod
    def chrome_path() -> str:
        for candidate in [
            Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
            Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        ]:
            if candidate.exists():
                return str(candidate)
        return ""

    def template_context(self, data: dict[str, Any], config: dict[str, Any]) -> dict[str, str]:
        kind = str(data["kind"])
        stats: list[EmployeeStats] = data["stats"]
        projects = ", ".join(data.get("projects") or ["No active project rows"])
        active_people = sum(1 for item in stats if item.items)
        target = sum(item.target_items for item in stats)
        translated = sum(item.translation_ready for item in stats)
        pending = sum(item.pending for item in stats)
        ready = sum(item.ready for item in stats)
        metric_cards = self.metric_cards(
            [
                ("Items", self.format_int(int(data["items"])), "work rows logged", "#00a99d"),
                ("Words", self.format_int(int(data["words"])), "tracked output", "#2364aa"),
                ("Translated", f"{translated}/{target or translated}", "target-ready rows", "#178a56"),
                ("Pending", self.format_int(pending), "checks open", "#e29b16" if pending else "#178a56"),
                ("People", self.format_int(active_people), "active contributors", "#6b5bd6"),
            ]
        )
        top = next((item for item in stats if item.items), None)
        context = {
            "report_title": f"{kind.title()} Progress Report",
            "report_subtitle": f"Content Writing performance for {projects}. Targets reflect 3 categories per person per working day from July 21, 2026.",
            "period_label": str(data["period_label"]),
            "generated_at": self.display_generated_at(str(data["generated_at"])),
            "metric_cards": metric_cards,
            "team_rows": self.team_rows(kind, stats[:7]),
            "top_employee": self.escape(top.name if top else "No work logged"),
            "top_employee_note": self.escape(f"{top.items} items, {top.words:,} words, {top.translation_ready}/{top.target_items or top.translation_ready} translated target." if top else "No dated work rows were found for this report period."),
            "quality_pills": self.quality_pills(ready, translated, pending),
            "deliverable_rows": self.deliverable_rows(data.get("recent_items", [])),
            "source_label": self.escape(f"Sources: HR & Payroll + {projects}"),
            "privacy_note": "Private payroll, bank, NID, and personal-contact fields excluded",
            "stylesheet_uri": self.resolve_config_path(str((config.get("templates") or {}).get("stylesheet", "")), REPORT_TEMPLATES_DIR / "employee_report.css").resolve().as_uri(),
            "logo_uri": self.resolve_config_path(str((config.get("templates") or {}).get("logo_path", "")), ASSETS_DIR / "branding" / "eseo-logo.png").resolve().as_uri(),
        }
        return context

    @staticmethod
    def resolve_config_path(value: str, fallback: Path | None = None) -> Path:
        raw = str(value or "").strip()
        if raw:
            path = Path(raw)
            return path if path.is_absolute() else PROJECT_ROOT / path
        return fallback or PROJECT_ROOT

    def metric_cards(self, metrics: list[tuple[str, str, str, str]]) -> str:
        cards = []
        for label, value, note, color in metrics:
            cards.append(
                (
                    f'<div class="metric" style="--metric-color: {self.escape(color)}">'
                    f'<div class="metric-label">{self.escape(label)}</div>'
                    f'<div class="metric-value">{self.escape(value)}</div>'
                    f'<div class="metric-note">{self.escape(note)}</div>'
                    "</div>"
                )
            )
        return "".join(cards)

    def team_rows(self, kind: str, stats: list[EmployeeStats]) -> str:
        if not stats:
            column_count = 7
            return f'<tr><td class="empty-row" colspan="{column_count}">No employee work rows found for this period.</td></tr>'
        rows = []
        for item in stats:
            employee_cell = self.employee_cell(item)
            status = f'<span class="badge {self.performance_class(item.performance)}">{self.escape(item.performance)}</span>'
            if kind == "monthly":
                rows.append(
                    "<tr>"
                    f"<td>{employee_cell}</td>"
                    f'<td><span class="number">{self.format_int(item.items)}</span><span class="small-muted">Target {self.format_int(item.target_items)}</span></td>'
                    f'<td><span class="number">{self.format_int(item.words)}</span></td>'
                    f'<td><span class="number">{self.format_int(item.base_items)} / {self.format_int(item.variations)}</span></td>'
                    f'<td><span class="number">{self.format_int(item.proofread_done)}</span><span class="small-muted">{self.format_int(item.ready)} drafts ready</span></td>'
                    f'<td><span class="number">{self.format_int(item.translation_ready)}</span><span class="small-muted">Gap {self.format_int(item.target_gap)}</span></td>'
                    f"<td>{status}</td>"
                    "</tr>"
                )
                continue
            recent = "; ".join(item.latest_keywords[:2]) or "No recent keyword"
            rows.append(
                "<tr>"
                f"<td>{employee_cell}</td>"
                f'<td><span class="number">{self.format_int(item.items)}</span><span class="small-muted">Target {self.format_int(item.target_items)}</span></td>'
                f'<td><span class="number">{self.format_int(item.words)}</span></td>'
                f'<td><span class="number">{self.format_int(item.ready)}</span><span class="small-muted">{self.format_int(item.translation_ready)} translated</span></td>'
                f'<td><span class="number">{self.format_int(item.pending)}</span><span class="small-muted">Open checks</span></td>'
                f'<td><div class="keyword-list">{self.escape(recent)}</div></td>'
                f"<td>{status}</td>"
                "</tr>"
            )
        return "".join(rows)

    def employee_cell(self, item: EmployeeStats) -> str:
        photo_uri = self.employee_photo_uri(item.employee)
        if photo_uri:
            avatar = f'<span class="avatar photo"><img src="{self.escape(photo_uri)}" alt=""></span>'
        else:
            avatar = f'<span class="avatar">{self.escape(self.initials(item.name))}</span>'
        role = item.designation or item.department or "Team member"
        return (
            '<div class="employee-cell">'
            f"{avatar}"
            "<div>"
            f'<div class="employee-name">{self.escape(item.name)}</div>'
            f'<div class="employee-role">{self.escape(role)}</div>'
            "</div>"
            "</div>"
        )

    def employee_photo_uri(self, employee: Employee | None) -> str:
        if not employee or not employee.photo_link:
            return ""
        raw = employee.photo_link.strip()
        if raw.startswith(("file:/", "data:")):
            return raw
        file_id = self.drive_file_id(raw)
        if not file_id:
            return raw if raw.startswith(("http://", "https://")) else ""
        cache_dir = REPORTS_DIR / "employee-photos"
        cache_dir.mkdir(parents=True, exist_ok=True)
        extension = ".jpg"
        cache_path = cache_dir / f"{re.sub(r'[^A-Za-z0-9_-]', '_', file_id)}{extension}"
        if cache_path.exists() and cache_path.stat().st_size > 0:
            return cache_path.resolve().as_uri()
        try:
            request = self.drive().files().get_media(fileId=file_id)
            with cache_path.open("wb") as handle:
                downloader = MediaIoBaseDownload(handle, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
            if cache_path.exists() and cache_path.stat().st_size > 0:
                return cache_path.resolve().as_uri()
        except Exception:
            try:
                cache_path.unlink(missing_ok=True)
            except OSError:
                pass
        if self.download_public_drive_thumbnail(file_id, cache_path):
            return cache_path.resolve().as_uri()
        return ""

    @staticmethod
    def download_public_drive_thumbnail(file_id: str, cache_path: Path) -> bool:
        url = f"https://drive.google.com/thumbnail?id={file_id}&sz=w160"
        request = urllib.request.Request(url, headers={"User-Agent": "NoorEmployeeReport/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                content_type = response.headers.get("Content-Type", "")
                body = response.read()
            if len(body) < 512 or "text/html" in content_type.casefold():
                return False
            cache_path.write_bytes(body)
            return True
        except Exception:
            try:
                cache_path.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    @staticmethod
    def drive_file_id(value: str) -> str:
        patterns = [
            r"/file/d/([A-Za-z0-9_-]+)",
            r"[?&]id=([A-Za-z0-9_-]+)",
            r"/open\?id=([A-Za-z0-9_-]+)",
            r"/uc\?id=([A-Za-z0-9_-]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, value)
            if match:
                return match.group(1)
        if re.fullmatch(r"[A-Za-z0-9_-]{20,}", value.strip()):
            return value.strip()
        return ""

    def quality_pills(self, ready: int, translated: int, pending: int) -> str:
        pills = [
            (self.format_int(ready), "drafts ready"),
            (self.format_int(translated), "translated"),
            (self.format_int(pending), "pending"),
        ]
        return "".join(f"<div class=\"quality-pill\"><strong>{value}</strong><span>{self.escape(label)}</span></div>" for value, label in pills)

    def deliverable_rows(self, items: list[WorkItem]) -> str:
        if not items:
            return '<li><span>No recent work rows found</span><span class="deliverable-owner">-</span></li>'
        rows = []
        for item in items[:3]:
            date_label = item.work_date.strftime("%b %d") if item.work_date else "No date"
            title = item.keyword or item.item_type or "Untitled item"
            meta = f"{item.employee_name} - {date_label}"
            rows.append(
                "<li>"
                f"<span>{self.escape(title)}</span>"
                f'<span class="deliverable-owner">{self.escape(meta)}</span>'
                "</li>"
            )
        return "".join(rows)

    @staticmethod
    def performance_class(label: str) -> str:
        classes = {
            "Excellent": "perf-excellent",
            "Good": "perf-good",
            "Watch": "perf-watch",
            "Needs attention": "perf-attention",
            "No work logged": "perf-empty",
        }
        return classes.get(label, "perf-empty")

    @staticmethod
    def format_int(value: int) -> str:
        return f"{int(value):,}"

    @staticmethod
    def escape(value: Any) -> str:
        return html.escape(str(value or ""), quote=True)

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
