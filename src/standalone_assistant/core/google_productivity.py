from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from standalone_assistant.core.paths import GOOGLE_PRODUCTIVITY_TOKEN
from standalone_assistant.core.storage import Storage
from standalone_assistant.core.time_parser import google_datetime, google_task_due


SCOPES = [
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/calendar.events",
]
CONTENT_TOOL_ROOT = Path("E:/ESEO/content-review-manager")
CREDENTIALS_PATH = CONTENT_TOOL_ROOT / "credentials.json"


@dataclass
class GoogleResult:
    ok: bool
    message: str
    url: str = ""
    error: str = ""
    data: Any = None


class GoogleProductivityService:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def status(self) -> dict[str, Any]:
        connected = False
        error = ""
        if GOOGLE_PRODUCTIVITY_TOKEN.exists():
            try:
                connected = self.credentials(interactive=False) is not None
            except Exception as exc:
                error = self.google_error_message(exc, "Google Productivity")
        return {
            "credentials_present": CREDENTIALS_PATH.exists(),
            "token_present": GOOGLE_PRODUCTIVITY_TOKEN.exists(),
            "authorization_connected": connected,
            "token_path": str(GOOGLE_PRODUCTIVITY_TOKEN),
            "scopes": SCOPES,
            "error": error,
        }

    def connect(self) -> GoogleResult:
        try:
            creds = self.credentials(interactive=True)
            if not creds:
                return GoogleResult(False, "Google Tasks and Calendar are not connected.", error="OAuth did not return credentials.")
            self.storage.log("info", "Google Productivity", "Connected Google Tasks and Calendar scopes.")
            return GoogleResult(True, "Google Tasks and Calendar are connected.")
        except Exception as exc:
            self.storage.log("warning", "Google Productivity", f"Connection failed: {exc}")
            return GoogleResult(False, "Google connection failed.", error=self.google_error_message(exc, "Google Productivity"))

    def credentials(self, *, interactive: bool = False) -> Credentials | None:
        creds: Credentials | None = None
        if GOOGLE_PRODUCTIVITY_TOKEN.exists():
            creds = Credentials.from_authorized_user_file(str(GOOGLE_PRODUCTIVITY_TOKEN), SCOPES)
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            self.save_credentials(creds)
            return creds
        if not interactive:
            return None
        if not CREDENTIALS_PATH.exists():
            raise FileNotFoundError(f"Missing Google OAuth client file: {CREDENTIALS_PATH}")
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
        creds = flow.run_local_server(port=0)
        self.save_credentials(creds)
        return creds

    def save_credentials(self, creds: Credentials) -> None:
        GOOGLE_PRODUCTIVITY_TOKEN.parent.mkdir(parents=True, exist_ok=True)
        GOOGLE_PRODUCTIVITY_TOKEN.write_text(creds.to_json(), encoding="utf-8")

    def tasks_service(self, *, interactive: bool = False):
        creds = self.credentials(interactive=interactive)
        if not creds:
            return None
        return build("tasks", "v1", credentials=creds)

    def calendar_service(self, *, interactive: bool = False):
        creds = self.credentials(interactive=interactive)
        if not creds:
            return None
        return build("calendar", "v3", credentials=creds)

    def default_tasklist_id(self, service) -> str:
        lists = service.tasklists().list(maxResults=20).execute().get("items", [])
        for item in lists:
            if item.get("title", "").lower() in {"my tasks", "tasks"}:
                return item["id"]
        if lists:
            return lists[0]["id"]
        created = service.tasklists().insert(body={"title": "Noor Tasks"}).execute()
        return created["id"]

    def create_task(self, title: str, *, due=None, notes: str = "", interactive: bool = False) -> GoogleResult:
        service = self.tasks_service(interactive=interactive)
        if not service:
            return GoogleResult(False, "Google Tasks is not connected. Say `connect google productivity` first.")
        body: dict[str, Any] = {"title": title}
        if notes:
            body["notes"] = notes
        if due:
            body["due"] = google_task_due(due)
        try:
            tasklist = self.default_tasklist_id(service)
            task = service.tasks().insert(tasklist=tasklist, body=body).execute()
            self.storage.log("info", "Google Tasks", f"Created task: {title}", {"task_id": task.get("id")})
            return GoogleResult(True, f"Created Google task: {title}", task.get("selfLink", ""))
        except Exception as exc:
            return GoogleResult(False, "Could not create Google task.", error=self.google_error_message(exc, "Google Tasks API"))

    def create_event(self, title: str, *, start, end=None, notes: str = "", reminders_minutes: list[int] | None = None, interactive: bool = False) -> GoogleResult:
        service = self.calendar_service(interactive=interactive)
        if not service:
            return GoogleResult(False, "Google Calendar is not connected. Say `connect google productivity` first.")
        if end is None:
            from datetime import timedelta

            end = start + timedelta(minutes=60)
        body: dict[str, Any] = {
            "summary": title,
            "start": google_datetime(start),
            "end": google_datetime(end),
        }
        if notes:
            body["description"] = notes
        if reminders_minutes:
            body["reminders"] = {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": int(minutes)} for minutes in reminders_minutes],
            }
        try:
            event = service.events().insert(calendarId="primary", body=body).execute()
            self.storage.log("info", "Google Calendar", f"Created event: {title}", {"event_id": event.get("id")})
            return GoogleResult(True, f"Created Google Calendar event: {title}", event.get("htmlLink", ""))
        except Exception as exc:
            return GoogleResult(False, "Could not create Google Calendar event.", error=self.google_error_message(exc, "Google Calendar API"))

    def list_tasklists(self, max_results: int = 20) -> GoogleResult:
        service = self.tasks_service(interactive=False)
        if not service:
            return GoogleResult(False, "Google Tasks authorization is not connected. Say `connect google productivity` first.")
        try:
            tasklists = service.tasklists().list(maxResults=max_results).execute().get("items", [])
            return GoogleResult(True, f"{len(tasklists)} Google task lists found.", data=tasklists)
        except Exception as exc:
            return GoogleResult(False, "Could not read Google task lists.", error=self.google_error_message(exc, "Google Tasks API"))

    def list_tasks(self, max_results: int = 50, *, include_completed: bool = False) -> GoogleResult:
        service = self.tasks_service(interactive=False)
        if not service:
            return GoogleResult(False, "Google Tasks authorization is not connected. Say `connect google productivity` first.")
        try:
            tasklists = service.tasklists().list(maxResults=20).execute().get("items", [])
            rows: list[dict[str, Any]] = []
            per_list = max(10, min(100, max_results))
            for tasklist in tasklists:
                response = service.tasks().list(
                    tasklist=tasklist["id"],
                    showCompleted=include_completed,
                    showHidden=False,
                    maxResults=per_list,
                ).execute()
                for task in response.get("items", []):
                    if not include_completed and task.get("status") == "completed":
                        continue
                    rows.append(
                        {
                            "id": task.get("id", ""),
                            "title": task.get("title", "(untitled task)"),
                            "due": task.get("due", ""),
                            "status": task.get("status", "needsAction"),
                            "notes": task.get("notes", ""),
                            "updated": task.get("updated", ""),
                            "tasklist": tasklist.get("title", "Tasks"),
                            "tasklist_id": tasklist.get("id", ""),
                            "url": task.get("selfLink", ""),
                        }
                    )
                    if len(rows) >= max_results:
                        break
                if len(rows) >= max_results:
                    break
            rows.sort(key=lambda item: (item.get("due") or "9999", item.get("title") or ""))
            return GoogleResult(True, f"{len(rows)} open Google tasks found.", data=rows)
        except Exception as exc:
            return GoogleResult(False, "Could not read Google tasks.", error=self.google_error_message(exc, "Google Tasks API"))

    def complete_task(self, tasklist_id: str, task_id: str) -> GoogleResult:
        service = self.tasks_service(interactive=False)
        if not service:
            return GoogleResult(False, "Google Tasks authorization is not connected. Say `connect google productivity` first.")
        try:
            completed_at = datetime.now(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")
            service.tasks().patch(
                tasklist=tasklist_id,
                task=task_id,
                body={"status": "completed", "completed": completed_at},
            ).execute()
            self.storage.log("info", "Google Tasks", f"Marked Google task complete: {task_id}")
            return GoogleResult(True, "Marked Google task done.")
        except Exception as exc:
            return GoogleResult(False, "Could not mark Google task done.", error=self.google_error_message(exc, "Google Tasks API"))

    def list_events(self, max_results: int = 20, *, days: int = 45) -> GoogleResult:
        service = self.calendar_service(interactive=False)
        if not service:
            return GoogleResult(False, "Google Calendar authorization is not connected. Say `connect google productivity` first.")
        now = datetime.now(ZoneInfo("Asia/Dhaka"))
        try:
            events = service.events().list(
                calendarId="primary",
                timeMin=now.isoformat(),
                timeMax=(now + timedelta(days=days)).isoformat(),
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            ).execute().get("items", [])
            rows = [self.normalize_event(event) for event in events]
            return GoogleResult(True, f"{len(rows)} upcoming Google Calendar events found.", data=rows)
        except Exception as exc:
            return GoogleResult(False, "Could not read Google Calendar events.", error=self.google_error_message(exc, "Google Calendar API"))

    def upcoming_events(self, max_results: int = 10) -> GoogleResult:
        result = self.list_events(max_results=max_results)
        if not result.ok:
            return result
        events = result.data or []
        if not events:
            return GoogleResult(True, "No upcoming Google Calendar events found.", data=[])
        lines = [f"- {event['start_display']}: {event['title']}" for event in events]
        return GoogleResult(True, "Upcoming Google Calendar events:\n" + "\n".join(lines), data=events)

    def productivity_summary(self, max_items: int = 6) -> GoogleResult:
        status = self.status()
        if not status["authorization_connected"]:
            return GoogleResult(False, "Google Tasks and Calendar authorization is not connected. Say `connect google productivity` first.", error=status.get("error", ""))

        task_result = self.list_tasks(max_results=max_items)
        event_result = self.list_events(max_results=max_items)
        lines = ["Google productivity is authorized."]

        if task_result.ok:
            tasks = task_result.data or []
            lines.append(f"Open Google todos: {len(tasks)}.")
            for task in tasks[:max_items]:
                due = self.display_time(task.get("due", "")) or "no due date"
                lines.append(f"- Todo: {task['title']} ({due})")
        else:
            lines.append(f"Google todos could not be read: {task_result.error or task_result.message}")

        if event_result.ok:
            events = event_result.data or []
            reminders = [event for event in events if event.get("kind") == "Reminder" or event.get("reminders")]
            lines.append(f"Upcoming Google events: {len(events)}. Reminder-enabled items: {len(reminders)}.")
            for event in events[:max_items]:
                label = "Reminder" if event.get("kind") == "Reminder" else "Event"
                lines.append(f"- {label}: {event['title']} ({event['start_display']})")
        else:
            lines.append(f"Google Calendar could not be read: {event_result.error or event_result.message}")

        return GoogleResult(task_result.ok or event_result.ok, "\n".join(lines), data={"tasks": task_result.data or [], "events": event_result.data or []})

    def normalize_event(self, event: dict[str, Any]) -> dict[str, Any]:
        title = event.get("summary") or "(untitled event)"
        start = event.get("start", {})
        end = event.get("end", {})
        start_value = start.get("dateTime") or start.get("date") or ""
        end_value = end.get("dateTime") or end.get("date") or ""
        reminders = self.reminder_text(event.get("reminders", {}))
        kind = "Reminder" if title.lower().startswith("reminder:") else "Event"
        return {
            "id": event.get("id", ""),
            "title": title,
            "start": start_value,
            "end": end_value,
            "start_display": self.display_time(start_value),
            "end_display": self.display_time(end_value),
            "kind": kind,
            "reminders": reminders,
            "url": event.get("htmlLink", ""),
        }

    def reminder_text(self, reminders: dict[str, Any]) -> str:
        if not reminders:
            return ""
        if reminders.get("useDefault"):
            return "default"
        overrides = reminders.get("overrides") or []
        if not overrides:
            return ""
        return ", ".join(f"{item.get('method', 'popup')} {item.get('minutes', 0)}m" for item in overrides)

    def display_time(self, value: str) -> str:
        if not value:
            return ""
        try:
            if "T" in value:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return parsed.astimezone(ZoneInfo("Asia/Dhaka")).strftime("%Y-%m-%d %H:%M")
            return value
        except ValueError:
            return value

    def google_error_message(self, exc: Exception, api_name: str) -> str:
        if isinstance(exc, HttpError):
            message = str(exc)
            try:
                payload = json.loads(exc.content.decode("utf-8"))
                message = payload.get("error", {}).get("message", message)
            except Exception:
                pass
            status = getattr(exc.resp, "status", "")
            if status == 403 and ("has not been used" in message or "disabled" in message):
                return f"{api_name} is authorized but disabled in the Google Cloud project used by credentials.json. Enable it in Google Cloud, then refresh Noor."
            return f"{api_name} returned HTTP {status}: {message}"
        return str(exc)
