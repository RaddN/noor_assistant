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
from typing import Any

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
    WHATSAPP_REPLY_RULES,
    WHATSAPP_WEBJS_AUTH_DIR,
    ensure_runtime_dirs,
)
from standalone_assistant.core.process_utils import hidden_subprocess_kwargs
from standalone_assistant.core.storage import Storage, dumps, utc_now


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


class WhatsAppWebService:
    """Event-driven WhatsApp Web bridge with an isolated local authentication session."""

    def __init__(self, storage: Storage) -> None:
        self.storage = storage

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
        event_result = self._process_webjs_event(auto)
        if event_result is not None:
            return event_result
        state = self.status().data or {}
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
        self.capture_fingerprint(chat, message_hash, body)
        rule_result = self._reply_for(body)
        if not rule_result.matched:
            self._record_auto_reply(chat, message_hash, "", "no-rule", "Ignored")
            self.storage.log("info", "WhatsApp", "Unread direct message ignored because no WhatsApp rule matched.", {"chat": chat, "message_hash": message_hash})
            return WhatsAppResult(True, "No WhatsApp rule matched; no reply sent.", {"chat": chat, "source": "no-rule"})
        if not rule_result.ok or not rule_result.reply:
            self._record_auto_reply(chat, message_hash, "", rule_result.source or "rule-error", "Blocked")
            self.storage.log("warning", "WhatsApp", "Matched WhatsApp rule could not produce a reply.", {"chat": chat, "message_hash": message_hash, "source": rule_result.source, "error": rule_result.error})
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

    def _reply_for(self, message: str) -> WhatsAppRuleResult:
        try:
            rules = json.loads(WHATSAPP_REPLY_RULES.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return WhatsAppRuleResult(False, False, error="WhatsApp rules could not be read.")
        if not isinstance(rules, list):
            return WhatsAppRuleResult(False, False, error="WhatsApp rules file is not a list.")
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            pattern = str(rule.get("pattern") or "")
            try:
                match = re.search(pattern, message, flags=re.IGNORECASE) if pattern else None
            except re.error:
                continue
            if match:
                return self._execute_rule(rule, message, match)
        return WhatsAppRuleResult(False, True)

    def _execute_rule(self, rule: dict[str, Any], message: str, match: re.Match[str]) -> WhatsAppRuleResult:
        rule_id = str(rule.get("id") or "custom")
        action = rule.get("action")
        action_type = "reply"
        action_payload: dict[str, Any] = {}
        if isinstance(action, str) and action.strip():
            action_type = action.strip().casefold()
        elif isinstance(action, dict):
            action_payload = action
            action_type = str(action.get("type") or "reply").strip().casefold()

        reply_template = str(action_payload.get("reply") or rule.get("reply") or "").strip()
        source = f"rule:{rule_id}:{action_type}"
        if action_type == "reply":
            if not reply_template:
                return WhatsAppRuleResult(True, False, source=source, error="Reply rule has no reply text.")
            return WhatsAppRuleResult(True, True, self._render_rule_template(reply_template, message, match), source)

        prompt = self._render_rule_template(str(action_payload.get("prompt") or "{message}"), message, match)
        if action_type in {"assistant", "brain"}:
            from standalone_assistant.core.assistant_brain import AssistantBrain

            reply = AssistantBrain(self.storage).answer(prompt).text.strip()
            return WhatsAppRuleResult(True, bool(reply), reply[:1200], source, "" if reply else "Assistant returned an empty answer.")

        if action_type in {"ai", "research", "gemini", "codex"}:
            provider = str(action_payload.get("provider") or ("auto" if action_type == "ai" else action_type))
            result = AIResponseService(self.storage, PROJECT_ROOT).answer_with_provider(provider, prompt, channel="whatsapp")
            return WhatsAppRuleResult(True, result.ok, result.text, f"{source}:{result.source or provider}", result.error)

        if action_type in {"tool", "safe_tool"}:
            return self._execute_tool_rule(rule_id, action_payload, message, match)

        return WhatsAppRuleResult(True, False, source=source, error=f"Unknown WhatsApp rule action: {action_type}")

    def _execute_tool_rule(self, rule_id: str, action: dict[str, Any], message: str, match: re.Match[str]) -> WhatsAppRuleResult:
        tool_id = self._render_rule_template(str(action.get("tool_id") or ""), message, match).strip()
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
            )
            ai = AIResponseService(self.storage, PROJECT_ROOT).answer_with_provider(str(action.get("summarize_with")), prompt, channel="whatsapp", context=output)
            if ai.ok:
                return WhatsAppRuleResult(True, True, ai.text, f"rule:{rule_id}:tool:{ai.source}")
        if action.get("reply_template"):
            reply = self._render_rule_template(str(action.get("reply_template")), message, match, extra={"output": output})
        else:
            prefix = "Tool command completed." if result.ok else "Tool command failed."
            reply = f"{prefix} {output}"
        return WhatsAppRuleResult(True, bool(reply.strip()), reply[:1200].strip(), f"rule:{rule_id}:tool", "" if result.ok else result.stderr)

    @staticmethod
    def _render_rule_template(template: str, message: str, match: re.Match[str], extra: dict[str, str] | None = None) -> str:
        values = {"message": message}
        values.update({str(index): value or "" for index, value in enumerate(match.groups(), start=1)})
        values.update({key: value or "" for key, value in match.groupdict().items()})
        if extra:
            values.update(extra)
        return re.sub(r"\{([A-Za-z0-9_]+)\}", lambda item: values.get(item.group(1), ""), template).strip()

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
        body = str(event.get("body") or "").strip()
        if len(event_id) != 64 or not chat_id or not body:
            event_path.unlink(missing_ok=True)
            return WhatsAppResult(False, "Invalid WhatsApp event was discarded.")
        chat_key = hashlib.sha256(chat_id.encode("utf-8")).hexdigest()
        if self.storage.fetch_one("SELECT 1 FROM whatsapp_auto_replies WHERE message_hash = ?", (event_id,)):
            event_path.unlink(missing_ok=True)
            return None
        self.capture_fingerprint(chat_key, event_id, body)
        rule_result = self._reply_for(body)
        if not rule_result.matched:
            self._record_auto_reply(chat_key, event_id, "", "no-rule", "Ignored")
            event_path.unlink(missing_ok=True)
            self.storage.log("info", "WhatsApp", "WhatsApp event ignored because no rule matched.", {"message_hash": event_id})
            return WhatsAppResult(True, "No WhatsApp rule matched; no reply sent.", {"source": "no-rule"})
        if not rule_result.ok or not rule_result.reply:
            self._record_auto_reply(chat_key, event_id, "", rule_result.source or "rule-error", "Blocked")
            event_path.unlink(missing_ok=True)
            return WhatsAppResult(False, "Matched WhatsApp rule could not produce a reply.", error=rule_result.error)
        reply, source = rule_result.reply, rule_result.source
        sent = self._request("send-reply", {"chat_id": chat_id, "chat_label": "Direct contact", "reply": reply})
        if not sent.ok:
            return sent
        self._record_auto_reply(chat_key, event_id, reply, source, "Sent")
        event_path.unlink(missing_ok=True)
        self.storage.log("warning", "WhatsApp", "Auto reply sent through whatsapp-web.js.", {"message_hash": event_id, "source": source})
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
