from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from standalone_assistant.core.ai_response import AIResponseService
from standalone_assistant.core.connectors import ToolRegistry
from standalone_assistant.core.connections import connection_snapshot
from standalone_assistant.core.find_phone import FindPhoneService
from standalone_assistant.core.google_productivity import GoogleProductivityService
from standalone_assistant.core.paths import PROJECT_ROOT
from standalone_assistant.core.storage import Storage
from standalone_assistant.core.time_parser import format_local_datetime, parse_when
from standalone_assistant.core.whatsapp_web import WhatsAppWebService
from standalone_assistant.core.web_research import ResearchResult, answer_question, news, search_web, weather


@dataclass
class AssistantReply:
    text: str
    action: str | None = None
    speak: bool = True


class AssistantBrain:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def identity(self) -> dict[str, str]:
        identity = self.storage.get_setting("identity", {})
        return {
            "name": str(identity.get("name") or "Noor"),
            "nickname": str(identity.get("nickname") or "Noor"),
            "owner_name": str(identity.get("owner_name") or "Raihan Hossain"),
            "owner_role": str(identity.get("owner_role") or "boss and developer"),
        }

    def answer(self, message: str) -> AssistantReply:
        text = message.strip()
        lowered = text.lower()
        identity = self.identity()
        if not text:
            return AssistantReply(f"{identity['nickname']} is here. Ask me about tasks, tools, projects, Codex, Google, WhatsApp, weather, news, or research.")

        if lowered in {"hi", "hello", "hey", "assalamualaikum", "salam"}:
            return AssistantReply(f"Hello. I am {identity['nickname']}, Raihan Hossain's assistant. I can check tools, projects, Codex, approvals, tasks, WhatsApp, Find My Phone, weather, news, or research.")

        if lowered in {"what is your name", "what is your name?", "what's your name", "what's your name?", "who are you"}:
            return AssistantReply(f"My name is {identity['name']}. You can call me {identity['nickname']}. I work for {identity['owner_name']} and help with tools, projects, Codex, tasks, approvals, WhatsApp, Find My Phone, weather, news, and research.")

        if lowered in {"what can you do", "help", "commands", "what can you do?"}:
            return AssistantReply(
                "You can ask: open tools, read summary, create todo, remind me, schedule event, find my phone, calendar status, tool status, project status, Codex status, Google status, WhatsApp status, weather in Dhaka, latest news, or any research question."
            )

        setting_reply = self.apply_setting_command(lowered)
        if setting_reply:
            return AssistantReply(setting_reply)

        productivity_reply = self.handle_productivity_command(text, lowered)
        if productivity_reply:
            return AssistantReply(productivity_reply)

        if "employee" in lowered or "staff" in lowered or "team member" in lowered or "weekly report" in lowered or "monthly report" in lowered:
            return AssistantReply(self.employee_status(text, lowered))

        if "weather" in lowered:
            location = self._after_keywords(text, ["weather in", "weather for", "weather"])
            result = weather(location or "Dhaka")
            return self._from_research(result)

        if "news" in lowered or "headline" in lowered:
            topic = self._after_keywords(text, ["news about", "news on", "news in", "latest news about", "latest news", "news"])
            result = news(topic)
            return self._from_research(result, include_links=False)

        if lowered.startswith("research ") or lowered.startswith("search "):
            query = text.split(" ", 1)[1].strip() if " " in text else ""
            result = answer_question(query)
            return self._from_research(result)

        if "tool" in lowered:
            return AssistantReply(self.tool_status())

        if "project" in lowered:
            return AssistantReply(self.project_status())

        if "codex" in lowered:
            return AssistantReply(self.codex_status_text())

        if "google" in lowered or "sheet" in lowered or "docs" in lowered or "drive" in lowered:
            return AssistantReply(self.google_status())

        if "whatsapp" in lowered or "reply approval" in lowered:
            return AssistantReply(self.whatsapp_status())

        if "find my phone" in lowered or "ring my phone" in lowered or "find phone" in lowered or "locate my phone" in lowered:
            result = FindPhoneService(self.storage).open_find_hub()
            text = result.message if result.ok else f"{result.message} {result.error}".strip()
            return AssistantReply(text)

        entity = self.local_entity_answer(lowered)
        if entity:
            return AssistantReply(entity)

        knowledge = self.knowledge_answer(lowered)
        if knowledge:
            return AssistantReply(knowledge)

        if "connection" in lowered or "connected" in lowered or "status" in lowered:
            return AssistantReply(self.connection_status())

        if "approval" in lowered or "escalation" in lowered:
            return AssistantReply(self.approval_status())

        if lowered.startswith("open ") or lowered.startswith("add task ") or lowered.startswith("new task ") or "test connections" in lowered:
            return AssistantReply(f"I will handle: {text}", action=text)

        return self._fallback_answer(text)

    def handle_productivity_command(self, text: str, lowered: str) -> str:
        google = GoogleProductivityService(self.storage)
        if lowered in {"connect google productivity", "connect google calendar", "connect google tasks", "connect google todo"}:
            result = google.connect()
            return result.message if result.ok else f"{result.message} {result.error}".strip()

        if "google productivity status" in lowered or "google calendar status" in lowered or "google tasks status" in lowered:
            status = google.status()
            if status["authorization_connected"]:
                summary = google.productivity_summary(max_items=4)
                return summary.message
            token = "authorized" if status["token_present"] else "not authorized"
            return f"Google Tasks and Calendar are {token}. Credentials file present: {'yes' if status['credentials_present'] else 'no'}. {status.get('error', '')}".strip()

        if lowered.startswith("open "):
            return ""

        if "upcoming event" in lowered or "what is on my calendar" in lowered or "what's on my calendar" in lowered or lowered in {"calendar", "calendar status"}:
            result = google.upcoming_events()
            return result.message if result.ok else f"{result.message} {result.error}".strip()

        task_questions = [
            "my todo",
            "my todos",
            "my task",
            "my tasks",
            "todo list",
            "task list",
            "what is due",
            "what's due",
        ]
        if any(phrase in lowered for phrase in task_questions):
            result = google.list_tasks(max_results=10)
            if not result.ok:
                return f"{result.message} {result.error}".strip()
            tasks = result.data or []
            if not tasks:
                return "No open Google todos found."
            lines = [f"Open Google todos: {len(tasks)}."]
            for task in tasks[:10]:
                due = google.display_time(task.get("due", "")) or "no due date"
                lines.append(f"- {task['title']} ({due})")
            return "\n".join(lines)

        if "reminder" in lowered or "reminders" in lowered:
            result = google.list_events(max_results=20)
            if not result.ok:
                return f"{result.message} {result.error}".strip()
            reminders = [event for event in result.data or [] if event.get("kind") == "Reminder" or event.get("reminders")]
            if not reminders:
                return "No upcoming Google reminder-enabled events found."
            lines = [f"Upcoming Google reminders: {len(reminders)}."]
            for event in reminders[:10]:
                label = event.get("reminders") or "calendar reminder"
                lines.append(f"- {event['title']} ({event['start_display']}; {label})")
            return "\n".join(lines)

        productivity_questions = [
            "what do i have today",
            "what do i have tomorrow",
            "what is upcoming",
            "what's upcoming",
        ]
        if any(phrase in lowered for phrase in productivity_questions):
            result = google.productivity_summary(max_items=6)
            return result.message if result.ok else f"{result.message} {result.error}".strip()

        todo_prefixes = ["create todo", "add todo", "create task", "add task", "make todo", "new todo"]
        for prefix in todo_prefixes:
            if lowered.startswith(prefix):
                raw_title = text[len(prefix) :].strip(" :")
                parsed = parse_when(raw_title, default_hour=9, duration_minutes=30)
                title = parsed.cleaned_text or raw_title or "Untitled task"
                result = google.create_task(title, due=parsed.start, interactive=False)
                if result.ok:
                    self.add_local_task(title, parsed.start.isoformat(), "Google Task")
                    return f"{result.message}. Due {format_local_datetime(parsed.start)}."
                return f"{result.message} {result.error}".strip()

        reminder_prefixes = ["remind me to", "create reminder to", "add reminder to", "reminder to"]
        for prefix in reminder_prefixes:
            if lowered.startswith(prefix):
                raw_title = text[len(prefix) :].strip(" :")
                parsed = parse_when(raw_title, default_hour=9, duration_minutes=15)
                title = parsed.cleaned_text or raw_title or "Reminder"
                result = google.create_event(
                    f"Reminder: {title}",
                    start=parsed.start,
                    end=parsed.end,
                    reminders_minutes=[10, 0],
                    interactive=False,
                )
                if result.ok:
                    self.add_local_task(title, parsed.start.isoformat(), "Google Calendar reminder")
                    return f"{result.message}. Reminder time {format_local_datetime(parsed.start)}."
                return f"{result.message} {result.error}".strip()

        event_prefixes = ["create event", "add event", "schedule event", "schedule meeting", "create meeting", "add meeting"]
        for prefix in event_prefixes:
            if lowered.startswith(prefix):
                raw_title = text[len(prefix) :].strip(" :")
                parsed = parse_when(raw_title, default_hour=10, duration_minutes=60)
                title = parsed.cleaned_text or raw_title or "Calendar event"
                result = google.create_event(title, start=parsed.start, end=parsed.end, reminders_minutes=[30, 10], interactive=False)
                if result.ok:
                    return f"{result.message}. Starts {format_local_datetime(parsed.start)}."
                return f"{result.message} {result.error}".strip()

        return ""

    def employee_status(self, text: str, lowered: str) -> str:
        from standalone_assistant.core.employee_reports import EmployeeReportService

        service = EmployeeReportService()
        if "weekly report" in lowered or ("weekly" in lowered and "report" in lowered):
            result = service.generate_report("weekly")
            return f"{result.caption}\nImage: {result.image_path}" if result.ok else f"Weekly employee report failed. {result.error}".strip()
        if "monthly report" in lowered or ("monthly" in lowered and "report" in lowered):
            result = service.generate_report("monthly")
            return f"{result.caption}\nImage: {result.image_path}" if result.ok else f"Monthly employee report failed. {result.error}".strip()
        try:
            config = service.load_config()
            employees = service.load_employees(config)
        except Exception as exc:
            return f"Employee directory is configured but could not be read. {exc}".strip()
        rows = sorted(employees.values(), key=lambda item: (item.department.casefold(), item.name.casefold()))
        if not rows:
            return "No active employees were found in the configured employee directory."
        lines = [f"Active employees under Raihan Hossain: {len(rows)}."]
        for employee in rows[:12]:
            role = employee.designation or "Employee"
            department = employee.department or "General"
            manager = f"; reports to {employee.reporting_manager}" if employee.reporting_manager else ""
            lines.append(f"- {employee.name}: {role}, {department}{manager}")
        return "\n".join(lines)

    def add_local_task(self, title: str, due_at: str, note: str) -> None:
        from standalone_assistant.core.storage import utc_now

        now = utc_now()
        self.storage.execute(
            "INSERT INTO tasks (title, priority, due_at, status, notes, created_at, updated_at) VALUES (?, 'Normal', ?, 'Open', ?, ?, ?)",
            (title, due_at, note, now, now),
        )

    def apply_setting_command(self, lowered: str) -> str:
        voice = self.storage.get_setting("voice", {})
        changed = False
        if "use edge voice" in lowered or "use neural voice" in lowered or "make voice human" in lowered:
            voice["tts_provider"] = "edge"
            voice.setdefault("edge_voice", "en-US-JennyNeural")
            changed = True
        elif "use windows voice" in lowered or "use desktop voice" in lowered:
            voice["tts_provider"] = "windows"
            changed = True
        elif "make voice faster" in lowered or "speak faster" in lowered:
            voice["rate"] = min(10, int(voice.get("rate", 0)) + 1)
            changed = True
        elif "make voice slower" in lowered or "speak slower" in lowered:
            voice["rate"] = max(-10, int(voice.get("rate", 0)) - 1)
            changed = True
        elif "increase voice confidence" in lowered or "listen stricter" in lowered:
            voice["min_confidence"] = min(0.9, float(voice.get("min_confidence", 0.35)) + 0.1)
            changed = True
        elif "decrease voice confidence" in lowered or "listen easier" in lowered:
            voice["min_confidence"] = max(0.1, float(voice.get("min_confidence", 0.35)) - 0.1)
            changed = True
        elif "switch to dictation mode" in lowered or "use dictation mode" in lowered:
            voice["recognition_mode"] = "dictation"
            changed = True
        elif "switch to command mode" in lowered or "use command mode" in lowered:
            voice["recognition_mode"] = "command"
            changed = True
        elif "switch to hybrid mode" in lowered or "use hybrid mode" in lowered or "use productivity voice mode" in lowered:
            voice["recognition_mode"] = "hybrid"
            changed = True
        elif "turn voice off" in lowered or "disable voice" in lowered:
            voice["enabled"] = False
            changed = True
        elif "turn voice on" in lowered or "enable voice" in lowered:
            voice["enabled"] = True
            changed = True

        confidence = self._number_after(lowered, ["set voice confidence to", "set confidence to", "minimum confidence"])
        if confidence is None:
            confidence = self._word_number_after(lowered, ["set voice confidence to", "set confidence to", "minimum confidence"])
        if confidence is not None:
            voice["min_confidence"] = max(0.05, min(0.95, confidence / 100 if confidence > 1 else confidence))
            changed = True

        timeout = self._number_after(lowered, ["set listen timeout to", "listen timeout"])
        if timeout is None:
            timeout = self._word_number_after(lowered, ["set listen timeout to", "listen timeout"])
        if timeout is not None:
            voice["listen_timeout_seconds"] = int(max(3, min(30, timeout)))
            changed = True

        if changed:
            self.storage.set_setting("voice", voice)
            provider = voice.get("tts_provider", "windows")
            mode = voice.get("recognition_mode", "command")
            confidence_text = int(float(voice.get("min_confidence", 0.35)) * 100)
            return f"Voice settings updated. Provider: {provider}. Listening mode: {mode}. Minimum confidence: {confidence_text}%."
        return ""

    def _number_after(self, lowered: str, phrases: list[str]) -> float | None:
        for phrase in phrases:
            index = lowered.find(phrase)
            if index < 0:
                continue
            tail = lowered[index + len(phrase) :]
            match = re.search(r"(\d+(?:\.\d+)?)", tail)
            if match:
                return float(match.group(1))
        return None

    def _word_number_after(self, lowered: str, phrases: list[str]) -> float | None:
        values = {
            "five": 5,
            "eight": 8,
            "ten": 10,
            "fifteen": 15,
            "thirty": 30,
            "forty": 40,
            "fifty": 50,
            "sixty": 60,
        }
        for phrase in phrases:
            index = lowered.find(phrase)
            if index < 0:
                continue
            tail = lowered[index + len(phrase) :].strip()
            first = tail.split(" ", 1)[0] if tail else ""
            if first in values:
                return float(values[first])
        return None

    def local_entity_answer(self, lowered: str) -> str:
        registry = ToolRegistry(self.storage)
        for tool in registry.list_tools():
            name = tool["name"]
            compact_name = name.lower()
            tool_id = tool["id"].lower()
            if compact_name in lowered or tool_id in lowered:
                exists = Path(tool["path"]).exists()
                capabilities = ", ".join(tool.get("capabilities", [])[:5]) or "No capabilities listed"
                permissions = ", ".join(tool.get("permissions", [])[:4]) or "No special permissions listed"
                return (
                    f"{name} is a connected tool.\n"
                    f"Path: {tool['path']}\n"
                    f"Reachable: {'yes' if exists else 'no'}\n"
                    f"Last status: {tool['connection_status']}\n"
                    f"Capabilities: {capabilities}\n"
                    f"Permissions: {permissions}"
                )
        projects = self.storage.fetch_all("SELECT name, path, agents_path, notes, last_git_status FROM projects ORDER BY name")
        for project in projects:
            name = project["name"]
            if name.lower() in lowered or Path(project["path"]).name.lower() in lowered:
                return (
                    f"{name} is a registered project.\n"
                    f"Path: {project['path']}\n"
                    f"Reachable: {'yes' if Path(project['path']).exists() else 'no'}\n"
                    f"Git status: {project['last_git_status'] or 'unknown'}\n"
                    f"AGENTS.md: {project['agents_path'] or 'not selected'}\n"
                    f"Notes: {project['notes'] or 'none'}"
                )
        return ""

    def knowledge_answer(self, lowered: str) -> str:
        tokens = [token for token in re.findall(r"[a-z0-9]{4,}", lowered) if token not in {"what", "when", "where", "which", "about", "with", "from"}]
        if not tokens:
            return ""
        rows = self.storage.fetch_all("SELECT title, category, body FROM knowledge WHERE trusted = 1 ORDER BY updated_at DESC LIMIT 100")
        scored = []
        for row in rows:
            haystack = f"{row['title']} {row['category']} {row['body']}".lower()
            score = sum(1 for token in tokens if token in haystack)
            if score:
                scored.append((score, row))
        if not scored:
            return ""
        scored.sort(key=lambda item: item[0], reverse=True)
        row = scored[0][1]
        return f"{row['title']}\n{row['body'][:900]}"

    def _fallback_answer(self, text: str) -> AssistantReply:
        result = AIResponseService(self.storage, PROJECT_ROOT).answer(text, channel="assistant")
        if result.ok and result.text:
            prefix = "" if result.source.startswith(("research", "cache:research")) else f"({result.source}) "
            self.storage.log("info", "Assistant Brain", f"Fallback answered with {result.source}", {"prompt_hash": "hidden"})
            return AssistantReply(prefix + result.text)
        self.storage.log("info", "Assistant Brain", f"Unmatched message and fallback failed: {text}", {"error": result.error})
        return AssistantReply(f"I do not have a reliable answer yet. {result.error}".strip())

    def connection_status(self) -> str:
        snap = connection_snapshot(self.storage)
        google = "connected" if snap["google"]["connected"] else "setup needed"
        codex = "ready" if snap["codex"]["available"] else "missing"
        voice = "ready" if snap["voice"]["connected"] else "missing"
        tools = snap["tools"]
        projects = snap["projects"]
        return f"Connections: Google {google}, Codex {codex}, voice {voice}, tools {tools['connected']} of {tools['total']}, projects {projects['connected']} of {projects['total']}."

    def tool_status(self) -> str:
        snap = connection_snapshot(self.storage)
        lines = [f"Tools connected: {snap['tools']['connected']} of {snap['tools']['total']}."]
        for item in snap["tools"]["items"]:
            state = "available" if item["exists"] else "missing"
            lines.append(f"- {item['name']}: {state}, last status {item['status']}.")
        return "\n".join(lines)

    def project_status(self) -> str:
        snap = connection_snapshot(self.storage)
        lines = [f"Projects registered: {snap['projects']['connected']} of {snap['projects']['total']} folders reachable."]
        for item in snap["projects"]["items"]:
            state = "reachable" if item["exists"] else "missing"
            lines.append(f"- {item['name']}: {state}, git {item['git']}.")
        return "\n".join(lines)

    def codex_status_text(self) -> str:
        snap = connection_snapshot(self.storage)
        codex = snap["codex"]
        if codex["available"]:
            return f"Codex is ready. Version: {codex['version']}. Launcher: {codex['path']}."
        return "Codex is not available on PATH."

    def google_status(self) -> str:
        google = connection_snapshot(self.storage)["google"]
        token = "present" if google["token_present"] else "missing"
        creds = "present" if google["credentials_present"] else "missing"
        if google.get("productivity_authorized"):
            productivity = "authorized"
            next_step = "Ask `google productivity status` to read Google todos, reminders, and upcoming events."
        elif google.get("productivity_token_present"):
            productivity = "token present but not usable"
            next_step = google.get("productivity_error") or "Reconnect with `connect google productivity`."
        else:
            productivity = "not authorized yet"
            next_step = "Say `connect google productivity` to authorize Tasks and Calendar."
        return (
            f"Google Workspace is {'connected' if google['connected'] else 'not fully connected'} for Sheets, Docs, and Drive. "
            f"Credentials are {creds}; existing OAuth token is {token}. "
            f"Google Tasks and Calendar are {productivity}. {next_step}"
        )

    def whatsapp_status(self) -> str:
        whatsapp = WhatsAppWebService(self.storage)
        bridge = whatsapp.status()
        auto = whatsapp.auto_settings()
        sent = self.storage.fetch_one("SELECT COUNT(*) AS c FROM whatsapp_auto_replies WHERE status = 'Sent'")["c"]
        blocked = self.storage.fetch_one("SELECT COUNT(*) AS c FROM whatsapp_auto_replies WHERE status = 'Blocked'")["c"]
        profile = "ready" if whatsapp.profile_path().exists() else "not created"
        return (
            f"WhatsApp Web is {'connected' if bridge.ok else 'not connected'} in Noor's dedicated event bridge ({profile}). "
            f"Automatic direct-message replies are {'enabled' if auto.get('enabled') else 'disabled'}, with groups "
            f"{'skipped' if auto.get('skip_groups', True) else 'included'}. Audit: {sent} sent, {blocked} blocked."
        )

    def approval_status(self) -> str:
        approvals = self.storage.fetch_one(
            "SELECT COUNT(*) AS c FROM escalations WHERE status IN ('Detected', 'Waiting for acknowledgement')"
        )["c"]
        active = self.storage.fetch_one(
            "SELECT COUNT(*) AS c FROM escalations WHERE status NOT IN ('Acknowledged', 'Resolved', 'Cancelled', 'Failed', 'Expired')"
        )["c"]
        return f"Approvals waiting: {approvals}. Active escalation records: {active}."

    def _from_research(self, result: ResearchResult, include_links: bool = True) -> AssistantReply:
        if not result.ok:
            return AssistantReply(f"{result.title}: I could not complete that request. {result.error}")
        text = f"{result.title}\n{result.summary}"
        if include_links and result.links:
            text += "\n\nSources:\n" + "\n".join(result.links[:3])
        return AssistantReply(text)

    def _after_keywords(self, text: str, keywords: list[str]) -> str:
        lowered = text.lower()
        for keyword in keywords:
            index = lowered.find(keyword)
            if index >= 0:
                return text[index + len(keyword) :].strip(" ?:")
        return ""
