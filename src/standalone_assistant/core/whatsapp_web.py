from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from standalone_assistant.core.ai_response import AIResponseService
from standalone_assistant.core.connectors import ToolRegistry
from standalone_assistant.core.gemini_cli import GeminiCli, GeminiResult
from standalone_assistant.core.paths import (
    PROJECT_ROOT,
    SCRIPTS_DIR,
    WHATSAPP_BRIDGE_REQUEST,
    WHATSAPP_BRIDGE_RESPONSE,
    WHATSAPP_BRIDGE_STARTING,
    WHATSAPP_BRIDGE_STATUS,
    WHATSAPP_INCOMING_DIR,
    WHATSAPP_WEBJS_AUTH_DIR,
    ensure_runtime_dirs,
)
from standalone_assistant.core.process_utils import hidden_subprocess_kwargs
from standalone_assistant.core.storage import Storage, dumps, utc_now
from standalone_assistant.core.time_parser import now_local
from standalone_assistant.core.whatsapp_rules import load_whatsapp_rules, rule_actions, rule_audience, rule_triggers


@dataclass
class WhatsAppResult:
    ok: bool
    message: str
    data: dict[str, Any] | None = None
    error: str = ""


@dataclass
class WhatsAppRuleResult:
    matched: bool
    ok: bool
    reply: str = ""
    source: str = ""
    error: str = ""


@dataclass
class WhatsAppRuleContext:
    event_type: str = "message"
    event_subtype: str = ""
    chat_id: str = ""
    chat_label: str = ""
    now: datetime | None = None


class WhatsAppWebService:
    """Event-driven WhatsApp Web bridge with an isolated local authentication session."""

    def __init__(self, storage: Storage, progress_callback: Callable[[str, str], None] | None = None) -> None:
        self.storage = storage
        self.progress_callback = progress_callback

    def _progress(self, title: str, message: str) -> None:
        if self.progress_callback:
            self.progress_callback(title, message)

    def settings(self) -> dict[str, Any]:
        defaults = {
            "enabled": True,
            "auto_start": True,
            "send_mode": "active",
            "store_private_messages": False,
            "max_messages_per_read": 25,
        }
        defaults.update(self.storage.get_setting("whatsapp_web", {}))
        return defaults

    def profile_path(self) -> Path:
        return WHATSAPP_WEBJS_AUTH_DIR

    def launch_login(self) -> WhatsAppResult:
        if not self.settings().get("enabled", True):
            return WhatsAppResult(False, "WhatsApp Web is disabled in Settings.")
        state = self._read_json(WHATSAPP_BRIDGE_STATUS)
        if state and self._bridge_is_live(state):
            self._clear_launch_marker()
            if bool(state.get("connected")):
                return WhatsAppResult(True, "Noor's dedicated WhatsApp profile is already open and connected.", state)
            return WhatsAppResult(True, "Noor's dedicated WhatsApp profile is already open and completing its connection.", state)
        starting = self._read_json(WHATSAPP_BRIDGE_STARTING)
        if starting and self._launch_marker_is_fresh(starting):
            return WhatsAppResult(True, "Noor's dedicated WhatsApp bridge is starting.", starting)
        profile_pids = self._profile_browser_pids()
        if profile_pids:
            self._clear_launch_marker()
            return WhatsAppResult(
                False,
                "Noor's dedicated WhatsApp browser is already open, but the bridge is not attached. Close that WhatsApp window once, then open WhatsApp from Noor again.",
                {"profile_browser_pids": profile_pids},
            )
        ensure_runtime_dirs()
        script = SCRIPTS_DIR / "whatsapp_webjs_bridge.js"
        node_path = shutil.which("node") or "node"
        command = [
            node_path,
            str(script),
            "--state-dir",
            str(WHATSAPP_BRIDGE_STATUS.parent),
            "--auth-dir",
            str(self.profile_path()),
        ]
        try:
            self._write_json(WHATSAPP_BRIDGE_STARTING, {"started_at": time.time(), "command": "whatsapp-web.js"})
            try:
                WHATSAPP_BRIDGE_RESPONSE.unlink(missing_ok=True)
            except OSError:
                pass
            stdout = (WHATSAPP_BRIDGE_STATUS.parent / "webjs.stdout.log").open("ab")
            stderr = (WHATSAPP_BRIDGE_STATUS.parent / "webjs.stderr.log").open("ab")
            subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                **hidden_subprocess_kwargs(),
                close_fds=False,
            )
        except OSError as exc:
            return WhatsAppResult(False, "Could not open the dedicated WhatsApp profile.", error=str(exc))
        self.storage.log("info", "WhatsApp", "Opened dedicated whatsapp-web.js bridge.")
        return WhatsAppResult(True, "Noor's dedicated WhatsApp Web window opened. Scan its QR code once; your normal Chrome profile is not used.")

    def ensure_running(self) -> WhatsAppResult:
        state = self._read_json(WHATSAPP_BRIDGE_STATUS)
        if state and self._bridge_is_live(state):
            return self.status()
        if not self.settings().get("auto_start", False):
            return WhatsAppResult(False, "WhatsApp bridge is stopped. Open it from the WhatsApp page when you want auto-replies.")
        return self.launch_login()

    def status(self) -> WhatsAppResult:
        state = self._read_json(WHATSAPP_BRIDGE_STATUS)
        if not state or not self._bridge_is_live(state):
            return WhatsAppResult(False, "No dedicated WhatsApp bridge is running. Open the dedicated profile first.")
        self._clear_launch_marker()
        if bool(state.get("connected")):
            return WhatsAppResult(True, "WhatsApp Web is connected in Noor's dedicated event bridge.", state)
        if bool(state.get("authenticated")):
            return WhatsAppResult(False, "WhatsApp authentication succeeded; Noor's event bridge is finishing its connection.", state)
        return WhatsAppResult(False, "WhatsApp Web is waiting for QR login in Noor's dedicated event bridge.", state)

    def read_selected_chat(self) -> WhatsAppResult:
        result = self._request("read-selected")
        if not result.ok or not result.data:
            return result
        chat = str(result.data.get("chat") or "Unknown chat")
        fingerprints = result.data.get("message_hashes")
        if not isinstance(fingerprints, list):
            return WhatsAppResult(False, "WhatsApp returned an invalid selected-chat response.")
        captured = 0
        duplicate = 0
        for fingerprint in fingerprints[: int(self.settings().get("max_messages_per_read", 25))]:
            value = str(fingerprint).strip()
            if len(value) != 64:
                continue
            outcome = self.capture_fingerprint(chat, value)
            captured += int(outcome == "captured")
            duplicate += int(outcome == "duplicate")
        self.storage.log("info", "WhatsApp", "Read selected WhatsApp chat.", {"chat": chat, "captured": captured, "duplicates": duplicate})
        return WhatsAppResult(True, f"Read the selected chat safely: {captured} new messages, {duplicate} duplicates ignored.", {"chat": chat, "captured": captured, "duplicates": duplicate})

    def process_unread_auto_replies(self) -> WhatsAppResult | None:
        """Process at most one current unread conversation per poll.

        The browser bridge keeps navigation and WhatsApp-specific selectors isolated.
        This service only receives the currently selected unread chat and its bounded,
        transient message payload; message text is never written to the local database.
        """
        auto = self.auto_settings()
        if not bool(auto.get("enabled")):
            return None
        scheduled_result = self._process_scheduled_rules(auto)
        if scheduled_result is not None:
            return scheduled_result
        event_result = self._process_webjs_event(auto)
        if event_result is not None:
            return event_result
        state = self.status().data or {}
        if state.get("backend") == "whatsapp-web.js" and not bool(auto.get("fallback_scan_enabled", False)):
            return None
        result = self._request("next-unread")
        if not result.ok or not result.data:
            if state.get("backend") == "whatsapp-web.js" and self._is_unread_scan_failure(result):
                self._log_unread_scan_failure(result)
                return None
            return result
        if not bool(result.data.get("has_unread")):
            if state.get("backend") == "whatsapp-web.js":
                return None
            activity = self._request("scan-activity", {"known_hashes": auto.get("activity_baseline_hashes", [])})
            if not activity.ok or not activity.data:
                return activity
            observed = [str(value) for value in activity.data.get("observed_hashes", []) if len(str(value)) == 64]
            if not bool(auto.get("activity_baseline_ready", False)):
                auto["activity_baseline_ready"] = True
                auto["activity_baseline_hashes"] = observed[-100:]
                self.storage.set_setting("whatsapp_auto_reply", auto)
                return None
            auto["activity_baseline_hashes"] = list(dict.fromkeys([*auto.get("activity_baseline_hashes", []), *observed]))[-100:]
            self.storage.set_setting("whatsapp_auto_reply", auto)
            if not bool(activity.data.get("has_unread")):
                return None
            result = activity
        chat = str(result.data.get("chat") or "")
        if not chat:
            return WhatsAppResult(False, "WhatsApp returned an unread chat without a verified chat name.")
        if bool(auto.get("skip_groups", True)) and bool(result.data.get("is_group")):
            self.storage.log("info", "WhatsApp", "Skipped unread group conversation.", {"chat": chat})
            return WhatsAppResult(True, "Skipped an unread group conversation by policy.", {"chat": chat, "skipped": "group"})
        incoming = [item for item in result.data.get("incoming_messages", []) if isinstance(item, dict)]
        candidates = [item for item in incoming if len(str(item.get("hash") or "")) == 64 and str(item.get("text") or "").strip()]
        if not candidates:
            return None
        candidate = candidates[-1]
        message_hash = str(candidate.get("hash"))
        body = str(candidate.get("text") or "").strip()
        if self.storage.fetch_one("SELECT 1 FROM whatsapp_auto_replies WHERE message_hash = ?", (message_hash,)):
            return None
        self._progress("WhatsApp", f"New direct message from {chat}. Checking reply rules...")
        self.capture_fingerprint(chat, message_hash, body)
        rule_result = self._reply_for(body, WhatsAppRuleContext(event_type="message", chat_id=str(result.data.get("chat_id") or ""), chat_label=chat, now=now_local()))
        if not rule_result.matched:
            self._record_auto_reply(chat, message_hash, "", "no-rule", "Ignored")
            self.storage.log("info", "WhatsApp", "Unread direct message ignored because no WhatsApp rule matched.", {"chat": chat, "message_hash": message_hash})
            self._progress("WhatsApp", "No WhatsApp rule matched; no reply sent.")
            return WhatsAppResult(True, "No WhatsApp rule matched; no reply sent.", {"chat": chat, "source": "no-rule"})
        if not rule_result.ok or not rule_result.reply:
            self._record_auto_reply(chat, message_hash, "", rule_result.source or "rule-error", "Blocked")
            self.storage.log("warning", "WhatsApp", "Matched WhatsApp rule could not produce a reply.", {"chat": chat, "message_hash": message_hash, "source": rule_result.source, "error": rule_result.error})
            self._progress("WhatsApp", f"Matched rule failed: {rule_result.error[:160]}")
            return WhatsAppResult(False, "Matched WhatsApp rule could not produce a reply.", error=rule_result.error)
        reply, source = rule_result.reply, rule_result.source
        send_payload = {"reply": reply}
        if result.data.get("chat_id"):
            send_payload["chat_id"] = str(result.data.get("chat_id") or "")
            send_payload["chat_label"] = chat
        else:
            send_payload.update({"expected_chat": chat, "expected_message_hash": message_hash})
        sent = self._request("send-reply", send_payload)
        if not sent.ok:
            return sent
        self._record_auto_reply(chat, message_hash, reply, source, "Sent")
        self.storage.log("warning", "WhatsApp", "Auto reply sent to unread direct chat.", {"chat": chat, "message_hash": message_hash, "source": source})
        self._progress("WhatsApp", f"Auto reply sent using {source}.")
        return WhatsAppResult(True, f"Auto reply sent using {source}.", {"chat": chat, "source": source})

    def capture_incoming(self, chat: str, body: str) -> str:
        fingerprint = hashlib.sha256(f"{chat}\n{body}".encode("utf-8")).hexdigest()
        return self.capture_fingerprint(chat, fingerprint, body)

    def capture_fingerprint(self, chat: str, fingerprint: str, body: str = "") -> str:
        preview = body[:220] if body and self.settings().get("store_private_messages", False) else "Private message captured; content not stored."
        try:
            self.storage.execute(
                """
                INSERT INTO whatsapp_messages (chat_name, message_hash, preview, captured_at, source, status)
                VALUES (?, ?, ?, ?, 'WhatsApp Web selected chat', 'Captured')
                """,
                (chat[:180], fingerprint, preview, utc_now()),
            )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE constraint failed" in str(exc):
                return "duplicate"
            raise
        return "captured"

    def create_approval_draft(self, chat: str, draft: str, *, origin: str = "manual") -> WhatsAppResult:
        allowed, reason = self._draft_allowed(chat)
        if not allowed:
            return WhatsAppResult(False, reason)
        now = utc_now()
        draft_id = "WA-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        self.storage.execute(
            """
            INSERT INTO escalations (id, title, status, priority, source, summary, policy_json, created_at, updated_at)
            VALUES (?, ?, 'Waiting for acknowledgement', 'Normal', 'WhatsApp approval draft', ?, ?, ?, ?)
            """,
            (draft_id, f"Reply approval: {chat[:120] or 'Unknown chat'}", draft[:220], dumps({"chat": chat[:180], "draft_only": True, "send_mode": "dry-run", "origin": origin}), now, now),
        )
        self.storage.log("info", "WhatsApp", "Created a WhatsApp reply approval draft.", {"draft_id": draft_id, "chat": chat[:180], "origin": origin})
        return WhatsAppResult(True, "Reply draft saved for approval.", {"draft_id": draft_id})

    def gemini_draft(self, incoming_message: str) -> GeminiResult:
        return GeminiCli(self.storage.get_setting("gemini_cli", {}), PROJECT_ROOT).draft_reply(incoming_message)

    def smart_reply(self, incoming_message: str):
        return AIResponseService(self.storage, PROJECT_ROOT).answer(incoming_message, channel="whatsapp")

    def auto_settings(self) -> dict[str, Any]:
        defaults = {
            "enabled": True,
            "poll_seconds": 12,
            "skip_groups": True,
            "fallback_scan_enabled": True,
            "activity_baseline_ready": False,
            "activity_baseline_hashes": [],
        }
        configured = self.storage.get_setting("whatsapp_auto_reply", {})
        if isinstance(configured, dict):
            defaults.update({key: value for key, value in configured.items() if key in defaults})
        return defaults

    def _request(self, action: str, extra: dict[str, Any] | None = None) -> WhatsAppResult:
        if not self.status().ok:
            return WhatsAppResult(False, "No connected dedicated WhatsApp bridge is available. Open the dedicated profile first.")
        request_id = uuid.uuid4().hex
        self._write_json(WHATSAPP_BRIDGE_REQUEST, {"id": request_id, "action": action, **(extra or {})})
        deadline = time.monotonic() + (30 if action in {"scan-activity", "next-unread", "send-reply"} else 10)
        while time.monotonic() < deadline:
            response = self._read_json(WHATSAPP_BRIDGE_RESPONSE)
            if response and response.get("id") == request_id:
                try:
                    WHATSAPP_BRIDGE_RESPONSE.unlink(missing_ok=True)
                except OSError:
                    pass
                return WhatsAppResult(
                    bool(response.get("ok")),
                    str(response.get("message") or "WhatsApp request finished."),
                    response.get("data") if isinstance(response.get("data"), dict) else None,
                    str(response.get("error") or ""),
                )
            time.sleep(0.2)
        return WhatsAppResult(False, "The dedicated WhatsApp bridge did not answer in time.")

    def preview_matches(self, message: str, *, event_type: str = "message", chat_label: str = "", chat_id: str = "") -> list[str]:
        context = WhatsAppRuleContext(event_type=event_type, chat_id=chat_id, chat_label=chat_label, now=now_local())
        matches: list[str] = []
        for rule in load_whatsapp_rules():
            if not bool(rule.get("enabled", True)):
                continue
            if not self._audience_matches(rule, context):
                continue
            matched, _match = self._rule_matches(rule, message, context)
            if matched:
                rule_id = str(rule.get("id") or "custom")
                action_names = " + ".join(str(action.get("type") or "reply") for action in rule_actions(rule))
                matches.append(f"{rule_id}: {action_names or 'no actions'}")
        return matches

    def _reply_for(self, message: str, context: WhatsAppRuleContext | None = None) -> WhatsAppRuleResult:
        context = context or WhatsAppRuleContext(event_type="message", now=now_local())
        if context.now is None:
            context.now = now_local()
        rules = load_whatsapp_rules()
        for rule in rules:
            if not bool(rule.get("enabled", True)):
                continue
            if not self._audience_matches(rule, context):
                continue
            matched, match = self._rule_matches(rule, message, context)
            if matched:
                return self._execute_rule(rule, message, match, context)
        return WhatsAppRuleResult(False, True)

    def _rule_matches(self, rule: dict[str, Any], message: str, context: WhatsAppRuleContext) -> tuple[bool, re.Match[str] | None]:
        triggers = rule_triggers(rule)
        if not triggers:
            return False, None
        logic = str(rule.get("trigger_logic") or "any").strip().casefold()
        require_all = logic == "all"
        outcomes: list[bool] = []
        first_match: re.Match[str] | None = None
        for trigger in triggers:
            ok, match = self._trigger_matches(trigger, message, context)
            outcomes.append(ok)
            if match and first_match is None:
                first_match = match
        if not outcomes:
            return False, None
        return (all(outcomes) if require_all else any(outcomes)), first_match

    def _trigger_matches(self, trigger: dict[str, Any], message: str, context: WhatsAppRuleContext) -> tuple[bool, re.Match[str] | None]:
        trigger_type = str(trigger.get("type") or "message").strip().casefold()
        if trigger_type == "message":
            return self._message_trigger_matches(trigger, message, context)
        if trigger_type == "call":
            return self._call_trigger_matches(trigger, context), None
        if trigger_type == "time":
            return self._time_trigger_matches(trigger, context), None
        if trigger_type == "date":
            return self._date_trigger_matches(trigger, context), None
        return False, None

    def _message_trigger_matches(self, trigger: dict[str, Any], message: str, context: WhatsAppRuleContext) -> tuple[bool, re.Match[str] | None]:
        if context.event_type != "message":
            return False, None
        value = str(trigger.get("value") or trigger.get("pattern") or "").strip()
        match_type = str(trigger.get("match") or "contains").strip().casefold()
        text = message.strip()
        lowered = text.casefold()
        needle = value.casefold()
        if match_type == "any":
            return bool(text), None
        if not value:
            return False, None
        if match_type == "regex":
            try:
                match = re.search(value, text, flags=re.IGNORECASE)
            except re.error:
                return False, None
            return bool(match), match
        if match_type == "equals":
            return lowered == needle, None
        if match_type == "starts_with":
            return lowered.startswith(needle), None
        if match_type == "ends_with":
            return lowered.endswith(needle), None
        return needle in lowered, None

    @staticmethod
    def _call_trigger_matches(trigger: dict[str, Any], context: WhatsAppRuleContext) -> bool:
        if context.event_type != "call":
            return False
        call_type = str(trigger.get("call_type") or trigger.get("value") or "any").strip().casefold()
        if call_type in {"", "any"}:
            return True
        return call_type == (context.event_subtype or "incoming").casefold()

    def _time_trigger_matches(self, trigger: dict[str, Any], context: WhatsAppRuleContext) -> bool:
        now = context.now or now_local()
        if not self._days_match(trigger, now):
            return False
        operator = str(trigger.get("operator") or "at").strip().casefold()
        current_minutes = now.hour * 60 + now.minute
        if operator == "between":
            start = self._parse_time_minutes(str(trigger.get("start") or ""))
            end = self._parse_time_minutes(str(trigger.get("end") or ""))
            if start is None or end is None:
                return False
            if start <= end:
                return start <= current_minutes <= end
            return current_minutes >= start or current_minutes <= end
        target = self._parse_time_minutes(str(trigger.get("time") or trigger.get("value") or ""))
        if target is None:
            return False
        if operator == "after":
            return current_minutes >= target
        if operator == "before":
            return current_minutes <= target
        return current_minutes == target

    def _date_trigger_matches(self, trigger: dict[str, Any], context: WhatsAppRuleContext) -> bool:
        now = context.now or now_local()
        operator = str(trigger.get("operator") or "on").strip().casefold()
        current = now.date()
        if operator == "between":
            start = self._parse_date_value(str(trigger.get("start") or ""))
            end = self._parse_date_value(str(trigger.get("end") or ""))
            return bool(start and end and start <= current <= end)
        target = self._parse_date_value(str(trigger.get("date") or trigger.get("value") or ""))
        if target is None:
            return False
        if operator == "after":
            return current >= target
        if operator == "before":
            return current <= target
        return current == target

    @staticmethod
    def _parse_time_minutes(value: str) -> int | None:
        raw = value.strip().lower().replace(".", "")
        match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", raw)
        if not match:
            return None
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        ampm = match.group(3)
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            return None
        return hour * 60 + minute

    @staticmethod
    def _parse_date_value(value: str):
        raw = value.strip().casefold()
        today = now_local().date()
        if raw == "today":
            return today
        if raw == "tomorrow":
            return today + timedelta(days=1)
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _days_match(trigger: dict[str, Any], now: datetime) -> bool:
        raw_days = trigger.get("days") or []
        if isinstance(raw_days, str):
            days = [item.strip().casefold() for item in re.split(r"[,;\s]+", raw_days) if item.strip()]
        elif isinstance(raw_days, list):
            days = [str(item).strip().casefold() for item in raw_days if str(item).strip()]
        else:
            days = []
        if not days:
            return True
        weekday = now.strftime("%a").casefold()
        full = now.strftime("%A").casefold()
        return weekday in days or full in days

    def _audience_matches(self, rule: dict[str, Any], context: WhatsAppRuleContext) -> bool:
        audience = rule_audience(rule)
        scope = audience["scope"]
        contacts = audience["contacts"]
        aliases = audience.get("aliases") or []
        if scope == "everyone":
            return True
        matched = any(self._contact_matches(contact, context) for contact in contacts) or any(self._alias_matches(alias, context) for alias in aliases)
        if scope == "contacts":
            return matched
        if scope == "except_contacts":
            return not matched
        return True

    @staticmethod
    def _alias_matches(alias: Any, context: WhatsAppRuleContext) -> bool:
        if not isinstance(alias, dict):
            return False
        label = str(alias.get("label") or "").strip().casefold()
        contact = str(alias.get("contact") or "").strip()
        chat_id = context.chat_id.strip().casefold()
        chat_label = context.chat_label.strip().casefold()
        if contact and WhatsAppWebService._contact_matches(contact, context):
            return True
        if not label:
            return False
        return label in {chat_id, chat_label} or bool(len(label) >= 3 and chat_label and (label in chat_label or chat_label in label))

    @staticmethod
    def _contact_matches(contact: str, context: WhatsAppRuleContext) -> bool:
        needle = contact.strip().casefold()
        if not needle:
            return False
        chat_id = context.chat_id.strip().casefold()
        chat_label = context.chat_label.strip().casefold()
        if needle in {chat_id, chat_label}:
            return True
        contact_numbers = WhatsAppWebService._phone_variants(needle)
        chat_numbers = WhatsAppWebService._phone_variants(chat_id) | WhatsAppWebService._phone_variants(chat_label)
        if contact_numbers and chat_numbers and contact_numbers.intersection(chat_numbers):
            return True
        return bool(len(needle) >= 3 and chat_label and (needle in chat_label or chat_label in needle))

    @staticmethod
    def _phone_variants(value: str) -> set[str]:
        digits = re.sub(r"\D+", "", value)
        if not digits:
            return set()
        variants = {digits}
        if digits.startswith("00") and len(digits) > 2:
            variants.add(digits[2:])
        for candidate in list(variants):
            if candidate.startswith("880") and len(candidate) > 3:
                variants.add("0" + candidate[3:])
            if candidate.startswith("0") and len(candidate) > 1:
                variants.add("880" + candidate[1:])
            if len(candidate) >= 10:
                variants.add(candidate[-10:])
            if len(candidate) >= 11:
                variants.add(candidate[-11:])
        return {item for item in variants if len(item) >= 7}

    def _execute_rule(
        self,
        rule: dict[str, Any],
        message: str,
        match: re.Match[str] | None,
        context: WhatsAppRuleContext,
    ) -> WhatsAppRuleResult:
        rule_id = str(rule.get("id") or "custom")
        actions = rule_actions(rule)
        if not actions:
            return WhatsAppRuleResult(True, False, source=f"rule:{rule_id}", error="Matched rule has no actions.")
        replies: list[str] = []
        sources: list[str] = []
        for action in actions:
            result = self._execute_action(rule_id, action, message, match, context)
            if result.source:
                sources.append(result.source)
            if not result.ok:
                return WhatsAppRuleResult(True, False, source=result.source or f"rule:{rule_id}", error=result.error)
            if result.reply.strip():
                replies.append(result.reply.strip())
        reply = "\n\n".join(replies).strip()
        source = "+".join(sources)[:120] if sources else f"rule:{rule_id}"
        if not reply:
            return WhatsAppRuleResult(True, False, source=source, error="Matched rule actions produced no reply text.")
        return WhatsAppRuleResult(True, True, reply[:1200], source)

    def _execute_action(
        self,
        rule_id: str,
        action: dict[str, Any],
        message: str,
        match: re.Match[str] | None,
        context: WhatsAppRuleContext,
    ) -> WhatsAppRuleResult:
        action_type = str(action.get("type") or "reply").strip().casefold()
        source = f"rule:{rule_id}:{action_type}"
        if action_type == "reply":
            reply_template = str(action.get("text") or action.get("reply") or "").strip()
            if not reply_template:
                return WhatsAppRuleResult(True, False, source=source, error="Reply action has no text.")
            return WhatsAppRuleResult(True, True, self._render_rule_template(reply_template, message, match, context), source)

        prompt = self._render_rule_template(str(action.get("prompt") or action.get("text") or "{message}"), message, match, context)
        if action_type in {"assistant", "brain"}:
            from standalone_assistant.core.assistant_brain import AssistantBrain

            reply = AssistantBrain(self.storage).answer(prompt).text.strip()
            return WhatsAppRuleResult(True, bool(reply), reply[:1200], source, "" if reply else "Assistant returned an empty answer.")

        if action_type in {"ai", "research", "gemini", "codex"}:
            provider = str(action.get("provider") or ("auto" if action_type == "ai" else action_type))
            label = {"auto": "Noor AI", "research": "Research", "gemini": "Gemini", "codex": "Codex"}.get(provider, provider.title())
            self._progress(label, f"Preparing WhatsApp reply for {context.chat_label or 'direct chat'}...")
            result = AIResponseService(self.storage, PROJECT_ROOT).answer_with_provider(provider, prompt, channel="whatsapp")
            if result.ok:
                self._progress(label, f"AI reply ready via {result.source or provider}.")
            else:
                self._progress(label, f"AI reply failed: {result.error[:160]}")
            return WhatsAppRuleResult(True, result.ok, result.text, f"{source}:{result.source or provider}", result.error)

        if action_type in {"tool", "safe_tool"}:
            self._progress("Tools", f"Running WhatsApp rule tool action for {context.chat_label or 'direct chat'}...")
            return self._execute_tool_rule(rule_id, action, message, match, context)

        if action_type in {"note", "log"}:
            note = prompt or "WhatsApp rule matched."
            self.storage.log("info", "WhatsApp Rules", note[:600], {"rule_id": rule_id, "chat": context.chat_label})
            return WhatsAppRuleResult(True, True, "", source)

        return WhatsAppRuleResult(True, False, source=source, error=f"Unknown WhatsApp rule action: {action_type}")

    def _execute_tool_rule(
        self,
        rule_id: str,
        action: dict[str, Any],
        message: str,
        match: re.Match[str] | None,
        context: WhatsAppRuleContext,
    ) -> WhatsAppRuleResult:
        tool_id = self._render_rule_template(str(action.get("tool_id") or ""), message, match, context).strip()
        if not tool_id:
            return WhatsAppRuleResult(True, False, source=f"rule:{rule_id}:tool", error="Tool rule has no tool_id.")
        try:
            command_index = int(action.get("command_index", 0))
        except (TypeError, ValueError):
            command_index = 0
        result = ToolRegistry(self.storage).run_safe_command(tool_id, command_index)
        output = result.combined_output[:2500] or ("Command completed." if result.ok else "Command returned no output.")
        if action.get("summarize_with"):
            prompt = self._render_rule_template(
                str(action.get("summary_prompt") or "Summarize this tool result for a concise WhatsApp reply."),
                message,
                match,
                context,
            )
            ai = AIResponseService(self.storage, PROJECT_ROOT).answer_with_provider(str(action.get("summarize_with")), prompt, channel="whatsapp", context=output)
            if ai.ok:
                return WhatsAppRuleResult(True, True, ai.text, f"rule:{rule_id}:tool:{ai.source}")
        if action.get("reply_template"):
            reply = self._render_rule_template(str(action.get("reply_template")), message, match, context, extra={"output": output})
        else:
            prefix = "Tool command completed." if result.ok else "Tool command failed."
            reply = f"{prefix} {output}"
        return WhatsAppRuleResult(True, bool(reply.strip()), reply[:1200].strip(), f"rule:{rule_id}:tool", "" if result.ok else result.stderr)

    @staticmethod
    def _render_rule_template(
        template: str,
        message: str,
        match: re.Match[str] | None,
        context: WhatsAppRuleContext,
        extra: dict[str, str] | None = None,
    ) -> str:
        now = context.now or now_local()
        values = {
            "message": message,
            "chat": context.chat_label,
            "chat_id": context.chat_id,
            "event_type": context.event_type,
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%I:%M %p").lstrip("0"),
        }
        if match:
            values.update({str(index): value or "" for index, value in enumerate(match.groups(), start=1)})
            values.update({key: value or "" for key, value in match.groupdict().items()})
        if extra:
            values.update(extra)
        return re.sub(r"\{([A-Za-z0-9_]+)\}", lambda item: values.get(item.group(1), ""), template).strip()

    def _process_scheduled_rules(self, auto: dict[str, Any]) -> WhatsAppResult | None:
        now = now_local()
        for rule in load_whatsapp_rules():
            if not bool(rule.get("enabled", True)) or not self._autonomous_schedule_rule(rule):
                continue
            audience = rule_audience(rule)
            for contact in audience["contacts"]:
                context = WhatsAppRuleContext(event_type="time", chat_label=contact, now=now)
                matched, match = self._rule_matches(rule, "", context)
                if not matched:
                    continue
                rule_id = str(rule.get("id") or "custom")
                due_key = now.strftime("%Y%m%d%H%M")
                message_hash = hashlib.sha256(f"scheduled\n{rule_id}\n{contact}\n{due_key}".encode("utf-8")).hexdigest()
                if self.storage.fetch_one("SELECT 1 FROM whatsapp_auto_replies WHERE message_hash = ?", (message_hash,)):
                    continue
                rule_result = self._execute_rule(rule, "", match, context)
                if not rule_result.ok or not rule_result.reply:
                    self._record_auto_reply(contact, message_hash, "", rule_result.source or "scheduled-rule-error", "Blocked")
                    self.storage.log("warning", "WhatsApp", "Scheduled WhatsApp rule could not produce a reply.", {"rule_id": rule_id, "contact": contact, "error": rule_result.error})
                    return WhatsAppResult(False, "Scheduled WhatsApp rule could not produce a reply.", {"rule_id": rule_id, "contact": contact}, rule_result.error)
                sent = self._request("send-reply", {"contact": contact, "chat_label": contact, "reply": rule_result.reply})
                if not sent.ok:
                    self._record_auto_reply(contact, message_hash, "", rule_result.source or "scheduled-send-error", "Blocked")
                    return sent
                self._record_auto_reply(contact, message_hash, rule_result.reply, rule_result.source, "Sent")
                self.storage.log("warning", "WhatsApp", "Scheduled WhatsApp rule sent.", {"rule_id": rule_id, "contact": contact, "source": rule_result.source})
                return WhatsAppResult(True, f"Scheduled WhatsApp rule sent using {rule_result.source}.", {"rule_id": rule_id, "contact": contact, "source": rule_result.source})
        return None

    @staticmethod
    def _autonomous_schedule_rule(rule: dict[str, Any]) -> bool:
        triggers = rule_triggers(rule)
        if not triggers:
            return False
        types = {str(trigger.get("type") or "message").strip().casefold() for trigger in triggers}
        if "message" in types or "call" in types or "time" not in types:
            return False
        has_exact_time = any(str(trigger.get("type") or "").strip().casefold() == "time" and str(trigger.get("operator") or "at").strip().casefold() == "at" for trigger in triggers)
        audience = rule_audience(rule)
        return bool(has_exact_time and audience["scope"] == "contacts" and audience["contacts"] and rule_actions(rule))

    def _record_auto_reply(self, chat: str, message_hash: str, reply: str, source: str, status: str) -> None:
        self.storage.execute(
            """
            INSERT INTO whatsapp_auto_replies (chat_name, message_hash, reply_hash, source, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (chat[:180], message_hash, hashlib.sha256(reply.encode("utf-8")).hexdigest(), source[:60], status[:60], utc_now()),
        )

    def _process_webjs_event(self, auto: dict[str, Any]) -> WhatsAppResult | None:
        try:
            event_path = next(iter(sorted(WHATSAPP_INCOMING_DIR.glob("*.json"))), None)
        except OSError:
            return None
        if not event_path:
            return None
        event = self._read_json(event_path)
        if not event:
            event_path.unlink(missing_ok=True)
            return None
        event_id = str(event.get("event_id") or "")
        chat_id = str(event.get("chat_id") or "")
        chat_label = str(event.get("chat_label") or "Direct contact")
        event_type = str(event.get("event_type") or "message").strip().casefold()
        event_subtype = str(event.get("event_subtype") or event.get("call_type") or ("incoming" if event_type == "call" else "")).strip().casefold()
        body = str(event.get("body") or "").strip()
        if event_type == "call" and not body:
            body = "Incoming WhatsApp call"
        if len(event_id) != 64 or not chat_id or event_type not in {"message", "call"} or not body:
            event_path.unlink(missing_ok=True)
            return WhatsAppResult(False, "Invalid WhatsApp event was discarded.")
        chat_key = hashlib.sha256(chat_id.encode("utf-8")).hexdigest()
        if self.storage.fetch_one("SELECT 1 FROM whatsapp_auto_replies WHERE message_hash = ?", (event_id,)):
            event_path.unlink(missing_ok=True)
            return None
        self._progress("WhatsApp", f"New {event_type} from {chat_label}. Checking reply rules...")
        self.capture_fingerprint(chat_key, event_id, body)
        context = WhatsAppRuleContext(event_type=event_type, event_subtype=event_subtype, chat_id=chat_id, chat_label=chat_label, now=now_local())
        rule_result = self._reply_for(body, context)
        if not rule_result.matched:
            self._record_auto_reply(chat_key, event_id, "", "no-rule", "Ignored")
            event_path.unlink(missing_ok=True)
            self.storage.log("info", "WhatsApp", "WhatsApp event ignored because no rule matched.", {"message_hash": event_id})
            self._progress("WhatsApp", "No WhatsApp rule matched; no reply sent.")
            return WhatsAppResult(True, "No WhatsApp rule matched; no reply sent.", {"source": "no-rule"})
        if not rule_result.ok or not rule_result.reply:
            self._record_auto_reply(chat_key, event_id, "", rule_result.source or "rule-error", "Blocked")
            event_path.unlink(missing_ok=True)
            self.storage.log("warning", "WhatsApp", "Matched WhatsApp event rule could not produce a reply.", {"message_hash": event_id, "source": rule_result.source, "error": rule_result.error})
            self._progress("WhatsApp", f"Matched rule failed: {rule_result.error[:160]}")
            return WhatsAppResult(False, "Matched WhatsApp rule could not produce a reply.", error=rule_result.error)
        reply, source = rule_result.reply, rule_result.source
        sent = self._request("send-reply", {"chat_id": chat_id, "chat_label": chat_label, "reply": reply})
        if not sent.ok:
            return sent
        self._record_auto_reply(chat_key, event_id, reply, source, "Sent")
        event_path.unlink(missing_ok=True)
        self.storage.log("warning", "WhatsApp", "Auto reply sent through whatsapp-web.js.", {"message_hash": event_id, "source": source})
        self._progress("WhatsApp", f"Auto reply sent using {source}.")
        return WhatsAppResult(True, f"Auto reply sent using {source}.", {"source": source})

    @staticmethod
    def _is_unread_scan_failure(result: WhatsAppResult) -> bool:
        message = result.message.casefold()
        error = result.error.casefold()
        return (
            "unread whatsapp scan" in message
            or "unread whatsapp chat scan" in message
            or error == "timeout"
            or "getchats" in error
        )

    def _log_unread_scan_failure(self, result: WhatsAppResult) -> None:
        latest = self.storage.fetch_one(
            """
            SELECT ts FROM activity
             WHERE source = 'WhatsApp'
               AND message = 'Unread fallback scan failed; event-based auto replies remain active.'
             ORDER BY id DESC
             LIMIT 1
            """
        )
        if latest:
            try:
                prior = datetime.fromisoformat(latest["ts"].replace("Z", "+00:00"))
                if datetime.now(timezone.utc) - prior < timedelta(minutes=10):
                    return
            except (TypeError, ValueError):
                pass
        self.storage.log(
            "warning",
            "WhatsApp",
            "Unread fallback scan failed; event-based auto replies remain active.",
            {"message": result.message, "error": result.error[:240]},
        )

    @staticmethod
    def _same_chat(left: str, right: str) -> bool:
        return bool(left.strip() and right.strip() and left.strip().casefold() == right.strip().casefold())

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        for attempt in range(4):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                if attempt < 3:
                    time.sleep(0.03)
                    continue
                return None
            return value if isinstance(value, dict) else None
        return None

    @staticmethod
    def _bridge_is_live(state: dict[str, Any]) -> bool:
        try:
            process_id = int(state.get("process_id"))
            updated_at = float(state.get("updated_at"))
            # Browser actions may take several seconds while WhatsApp animates or filters chats.
            if time.time() - updated_at > 60:
                return False
            if os.name == "nt":
                check = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {process_id}", "/FO", "CSV", "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                    **hidden_subprocess_kwargs(),
                )
                if str(process_id) not in check.stdout:
                    return False
            else:
                os.kill(process_id, 0)
        except (OSError, subprocess.TimeoutExpired, TypeError, ValueError):
            return False
        return True

    @staticmethod
    def _launch_marker_is_fresh(state: dict[str, Any]) -> bool:
        try:
            return time.time() - float(state.get("started_at", 0)) < 45
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _clear_launch_marker() -> None:
        try:
            WHATSAPP_BRIDGE_STARTING.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _profile_browser_pids() -> list[int]:
        if os.name != "nt":
            return []
        try:
            command = [
                "powershell",
                "-NoProfile",
                "-WindowStyle",
                "Hidden",
                "-Command",
                "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'chrome.exe' -and $_.CommandLine -like '*whatsapp-webjs-auth*' } | Select-Object -ExpandProperty ProcessId",
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                **hidden_subprocess_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        pids = []
        for line in completed.stdout.splitlines():
            try:
                pids.append(int(line.strip()))
            except ValueError:
                continue
        return pids

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=True), encoding="utf-8")
        for attempt in range(10):
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.05)

    def _draft_allowed(self, chat: str) -> tuple[bool, str]:
        return True, ""
