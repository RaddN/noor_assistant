from __future__ import annotations

import html
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from standalone_assistant.core.find_phone import FindPhoneService
from standalone_assistant.core.paths import DATA_DIR, PROJECT_ROOT, SCRIPTS_DIR
from standalone_assistant.core.process_utils import hidden_subprocess_kwargs
from standalone_assistant.core.storage import Storage, dumps, utc_now


TERMINAL_URGENCY_STATUSES = {"acknowledged", "resolved", "cancelled"}


@dataclass
class TeamsAlertResult:
    ok: bool
    message: str
    error: str = ""
    data: dict[str, Any] | None = None


class TeamsAlertService:
    """One-way Microsoft Teams alert sender for WhatsApp fallback incidents."""

    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def settings(self) -> dict[str, Any]:
        defaults: dict[str, Any] = {
            "enabled": False,
            "mode": "graph",
            "graph_chat_id": "",
            "graph_chat_id_env": "NOOR_TEAMS_CHAT_ID",
            "graph_token_env": "NOOR_TEAMS_GRAPH_TOKEN",
            "graph_token_path": "data/teams_graph_token.txt",
            "graph_sender_user_id": "",
            "graph_sender_user_id_env": "NOOR_TEAMS_SENDER_USER_ID",
            "webhook_url": "",
            "webhook_url_env": "NOOR_TEAMS_WEBHOOK_URL",
            "local_window_title": "Microsoft Teams",
            "local_prefer_chrome": False,
            "local_enter_to_send": True,
            "reply_detection_enabled": True,
            "include_message_preview": False,
            "repeat_interval_minutes": 30,
            "max_alerts_per_urgency": 5,
            "ack_silence_minutes": 240,
            "ring_phone_after_alerts": 5,
            "ring_phone_once": True,
            "phone_fallback_devices": ["Symphony innova30", "Redmi 10"],
            "timeout_seconds": 12,
        }
        configured = self.storage.get_setting("teams_alerts", {})
        if isinstance(configured, dict):
            defaults.update({key: configured[key] for key in defaults if key in configured})
        escalation = self.storage.get_setting("escalation", {})
        if isinstance(escalation, dict) and bool(escalation.get("teams_enabled")):
            defaults["enabled"] = True
        return defaults

    def status(self) -> dict[str, Any]:
        settings = self.settings()
        mode = str(settings.get("mode") or "graph").strip().casefold()
        if mode not in {"graph", "webhook", "local_ui"}:
            mode = "graph"
        if not bool(settings.get("enabled")):
            return {
                "enabled": False,
                "configured": False,
                "mode": mode,
                "message": "Teams fallback is disabled.",
            }
        if mode == "webhook":
            configured = bool(self._webhook_url(settings))
            message = "Teams webhook is configured." if configured else "Teams webhook URL is missing."
        elif mode == "local_ui":
            configured = self._local_sender_script().exists() and bool(str(settings.get("local_window_title") or "").strip())
            message = "Teams local window sender is configured." if configured else "Teams local window title or sender script is missing."
        else:
            configured = bool(self._graph_chat_id(settings) and self._graph_token(settings))
            message = "Teams Graph direct chat is configured." if configured else "Teams Graph chat ID or delegated token is missing."
        return {
            "enabled": True,
            "configured": configured,
            "mode": mode,
            "message": message,
            "reply_detection_enabled": bool(settings.get("reply_detection_enabled", True)),
            "reply_detection_configured": bool(self._graph_chat_id(settings) and self._graph_token(settings)),
            "active_urgency": self.active_urgency(),
        }

    def active_urgency(self) -> dict[str, Any] | None:
        self._ensure_state_table()
        row = self.storage.fetch_one(
            """
            SELECT urgency_key, chat_label, event_type, reason, status, alert_count, last_alert_at,
                   acknowledged_at, phone_ring_count, last_phone_ring_at, phone_device,
                   last_teams_message_id, last_teams_incident_id, last_teams_reply_check_at, reply_detected_at,
                   reply_message_id, reply_source, updated_at
              FROM teams_alert_state
             WHERE lower(status) NOT IN ('acknowledged', 'resolved', 'cancelled')
             ORDER BY updated_at DESC
             LIMIT 1
            """
        )
        return row or None

    def acknowledge_current_urgency(self, source: str = "manual") -> TeamsAlertResult:
        self._ensure_state_table()
        now = utc_now()
        rows = self.storage.fetch_all(
            """
            SELECT urgency_key
              FROM teams_alert_state
             WHERE lower(status) NOT IN ('acknowledged', 'resolved', 'cancelled')
            """
        )
        escalation_rows = self.storage.fetch_all(
            """
            SELECT id
              FROM escalations
             WHERE source = 'WhatsApp Teams fallback'
               AND lower(status) IN ('detected', 'teams alert sent', 'teams alert failed')
            """
        )
        for row in rows:
            self.storage.execute(
                """
                UPDATE teams_alert_state
                   SET status = 'Acknowledged',
                       acknowledged_at = ?,
                       updated_at = ?
                 WHERE urgency_key = ?
                """,
                (now, now, row["urgency_key"]),
            )
        self.storage.execute(
            """
            UPDATE escalations
               SET status = 'Acknowledged',
                   updated_at = ?
             WHERE source = 'WhatsApp Teams fallback'
               AND lower(status) IN ('detected', 'teams alert sent', 'teams alert failed')
            """,
            (now,),
        )
        total = len(rows) + len(escalation_rows)
        if total:
            self.storage.log(
                "info",
                "Teams",
                "Teams urgency acknowledged; further Teams alerts are silenced for the current urgency.",
                {"source": source, "urgency_count": len(rows), "escalation_count": len(escalation_rows)},
            )
            return TeamsAlertResult(
                True,
                "Acknowledged the current Teams urgency.",
                data={"urgency_count": len(rows), "escalation_count": len(escalation_rows)},
            )
        self.storage.log("info", "Teams", "Teams acknowledgement requested but no active urgency was found.", {"source": source})
        return TeamsAlertResult(True, "No active Teams urgency was found.")

    def send_alert(self, title: str, message: str, metadata: dict[str, Any] | None = None) -> TeamsAlertResult:
        settings = self.settings()
        if not bool(settings.get("enabled")):
            return TeamsAlertResult(False, "Teams fallback is disabled.")
        mode = str(settings.get("mode") or "graph").strip().casefold()
        timeout = max(3, min(int(settings.get("timeout_seconds", 12) or 12), 30))
        if mode == "webhook":
            result = self._send_webhook(settings, title, message, timeout)
        elif mode == "local_ui":
            result = self._send_local_ui(settings, title, message, timeout)
        else:
            result = self._send_graph(settings, title, message, timeout)
        if result.ok:
            self.storage.log("warning", "Teams", "Teams alert sent.", {**(metadata or {}), "mode": mode})
        else:
            self.storage.log("warning", "Teams", "Teams alert failed.", {**(metadata or {}), "mode": mode, "error": result.error[:300]})
        return result

    def escalate_whatsapp_gap(
        self,
        *,
        chat_label: str,
        event_type: str,
        message_hash: str,
        reason: str,
        body: str = "",
        error: str = "",
    ) -> TeamsAlertResult:
        settings = self.settings()
        if not bool(settings.get("enabled")):
            return TeamsAlertResult(False, "Teams fallback is disabled.")
        incident_id = f"WA-{message_hash[:16].upper()}"
        urgency_key = self._urgency_key(chat_label, event_type, reason)
        urgency_state = self._record_urgency_event(
            urgency_key=urgency_key,
            chat_label=chat_label,
            event_type=event_type,
            reason=reason,
            message_hash=message_hash,
            settings=settings,
        )
        reply_ack = self._acknowledge_if_teams_reply_seen(urgency_state, settings)
        if reply_ack:
            reply_ack.data = {**(reply_ack.data or {}), "duplicate": True, "incident_id": incident_id, "urgency_key": urgency_key}
            return reply_ack
        suppress_reason = self._suppression_reason(urgency_state, settings)
        if suppress_reason:
            self.storage.log(
                "info",
                "Teams",
                "Teams alert suppressed for active urgency.",
                {
                    "urgency_key": urgency_key,
                    "incident_id": incident_id,
                    "message_hash": message_hash,
                    "reason": suppress_reason,
                    "alert_count": urgency_state.get("alert_count", 0),
                },
            )
            return TeamsAlertResult(
                True,
                suppress_reason,
                data={"duplicate": True, "suppressed": True, "incident_id": incident_id, "urgency_key": urgency_key},
            )
        existing = self.storage.fetch_one("SELECT id, status FROM escalations WHERE id = ?", (incident_id,))
        if existing and str(existing.get("status") or "").casefold() in {"teams alert sent", "acknowledged", "resolved", "cancelled"}:
            return TeamsAlertResult(True, "Teams escalation was already recorded.", data={"duplicate": True, "incident_id": incident_id, "urgency_key": urgency_key})

        title = f"WhatsApp {event_type or 'message'} needs attention"
        summary = self._whatsapp_summary(chat_label, event_type, reason, body, error, settings)
        now = utc_now()
        policy = {
            "channel": "whatsapp",
            "teams_fallback": True,
            "urgency_key": urgency_key,
            "event_type": event_type,
            "message_hash": message_hash,
            "reason": reason,
            "privacy": "preview" if bool(settings.get("include_message_preview")) else "privacy-safe",
        }
        if existing:
            self.storage.execute(
                "UPDATE escalations SET title = ?, status = 'Detected', source = 'WhatsApp Teams fallback', summary = ?, policy_json = ?, updated_at = ? WHERE id = ?",
                (title, summary[:900], dumps(policy), now, incident_id),
            )
        else:
            self.storage.execute(
                """
                INSERT INTO escalations (id, title, status, priority, source, summary, policy_json, created_at, updated_at)
                VALUES (?, ?, 'Detected', 'High', 'WhatsApp Teams fallback', ?, ?, ?, ?)
                """,
                (incident_id, title, summary[:900], dumps(policy), now, now),
            )
        result = self.send_alert(
            title,
            f"{summary}\nIncident: {incident_id}",
            {"incident_id": incident_id, "message_hash": message_hash, "reason": reason},
        )
        status = "Teams alert sent" if result.ok else "Teams alert failed"
        self.storage.execute("UPDATE escalations SET status = ?, updated_at = ? WHERE id = ?", (status, utc_now(), incident_id))
        self._mark_urgency_alert_result(urgency_key, status, result.ok, settings, {**(result.data or {}), "incident_id": incident_id})
        result.data = {**(result.data or {}), "incident_id": incident_id, "urgency_key": urgency_key}
        return result

    def _send_graph(self, settings: dict[str, Any], title: str, message: str, timeout: int) -> TeamsAlertResult:
        chat_id = self._graph_chat_id(settings)
        token = self._graph_token(settings)
        if not chat_id or not token:
            return TeamsAlertResult(False, "Teams Graph chat ID or delegated token is missing.")
        endpoint = f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages"
        content = self._html_message(title, message)
        payload = {"body": {"contentType": "html", "content": content}}
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        result = self._post_json(endpoint, payload, headers, timeout, expected={200, 201, 202})
        if result.ok and isinstance(result.data, dict):
            parsed = result.data.get("json")
            if isinstance(parsed, dict) and parsed.get("id"):
                result.data["message_id"] = str(parsed.get("id"))
        return result

    def _send_webhook(self, settings: dict[str, Any], title: str, message: str, timeout: int) -> TeamsAlertResult:
        url = self._webhook_url(settings)
        if not url:
            return TeamsAlertResult(False, "Teams webhook URL is missing.")
        payload = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "summary": title[:120],
            "themeColor": "D13438",
            "title": title[:120],
            "text": message[:24000],
        }
        return self._post_json(url, payload, {"Content-Type": "application/json"}, timeout, expected={200, 201, 202})

    def _send_local_ui(self, settings: dict[str, Any], title: str, message: str, timeout: int) -> TeamsAlertResult:
        script = self._local_sender_script()
        if not script.exists():
            return TeamsAlertResult(False, "Teams local sender script is missing.", error=str(script))
        window_title = str(settings.get("local_window_title") or "Microsoft Teams").strip()
        if not window_title:
            return TeamsAlertResult(False, "Teams local window title is missing.")
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        body = f"{title}\n\n{message}".strip()
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".txt", dir=DATA_DIR) as handle:
            handle.write(body[:24000])
            message_path = Path(handle.name)
        args = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-MessageFile",
            str(message_path),
            "-WindowTitleContains",
            window_title,
        ]
        if bool(settings.get("local_prefer_chrome", False)):
            args.append("-PreferChrome")
        if not bool(settings.get("local_enter_to_send", True)):
            args.append("-NoEnter")
        try:
            completed = subprocess.run(
                args,
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=timeout,
                **hidden_subprocess_kwargs(),
            )
        except subprocess.TimeoutExpired:
            return TeamsAlertResult(False, "Teams local sender timed out.", error=f"Timed out after {timeout}s.")
        except OSError as exc:
            return TeamsAlertResult(False, "Could not start Teams local sender.", error=str(exc))
        finally:
            try:
                message_path.unlink(missing_ok=True)
            except OSError:
                pass
        output = (completed.stdout or completed.stderr or "").strip()
        data = self._loads_json(output)
        if completed.returncode == 0:
            details = data if isinstance(data, dict) else {"output": output[:500]}
            return TeamsAlertResult(True, "Teams local alert sent." if bool(settings.get("local_enter_to_send", True)) else "Teams local alert pasted.", data=details)
        error = ""
        if isinstance(data, dict):
            error = str(data.get("error") or data.get("message") or "")
        if not error:
            error = (completed.stderr or completed.stdout or "").strip()
        return TeamsAlertResult(False, "Teams local sender failed.", error=error[:500])

    def _record_urgency_event(
        self,
        *,
        urgency_key: str,
        chat_label: str,
        event_type: str,
        reason: str,
        message_hash: str,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        self._ensure_state_table()
        now = utc_now()
        existing = self.storage.fetch_one("SELECT * FROM teams_alert_state WHERE urgency_key = ?", (urgency_key,))
        if existing:
            existing = self._maybe_reset_acknowledged_urgency(existing, settings)
            self.storage.execute(
                """
                UPDATE teams_alert_state
                   SET last_message_hash = ?,
                       updated_at = ?
                 WHERE urgency_key = ?
                """,
                (message_hash, now, urgency_key),
            )
            updated = self.storage.fetch_one("SELECT * FROM teams_alert_state WHERE urgency_key = ?", (urgency_key,))
            return updated or existing
        self.storage.execute(
            """
            INSERT INTO teams_alert_state (
                urgency_key, chat_label, event_type, reason, status,
                first_message_hash, last_message_hash, alert_count,
                last_alert_at, acknowledged_at, phone_ring_count,
                last_phone_ring_at, phone_device, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'Detected', ?, ?, 0, NULL, NULL, 0, NULL, NULL, ?, ?)
            """,
            (urgency_key, chat_label[:180], event_type[:40], reason[:160], message_hash, message_hash, now, now),
        )
        return self.storage.fetch_one("SELECT * FROM teams_alert_state WHERE urgency_key = ?", (urgency_key,)) or {}

    def _maybe_reset_acknowledged_urgency(self, state: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
        status = str(state.get("status") or "").casefold()
        if status not in TERMINAL_URGENCY_STATUSES:
            return state
        acknowledged_at = self._parse_utc(str(state.get("acknowledged_at") or ""))
        silence_minutes = max(1, int(settings.get("ack_silence_minutes", 240) or 240))
        if acknowledged_at and datetime.now(timezone.utc) - acknowledged_at < timedelta(minutes=silence_minutes):
            return state
        now = utc_now()
        self.storage.execute(
            """
            UPDATE teams_alert_state
               SET status = 'Detected',
                   alert_count = 0,
                   last_alert_at = NULL,
                   acknowledged_at = NULL,
                   phone_ring_count = 0,
                   last_phone_ring_at = NULL,
                   phone_device = NULL,
                   updated_at = ?
             WHERE urgency_key = ?
            """,
            (now, state["urgency_key"]),
        )
        return self.storage.fetch_one("SELECT * FROM teams_alert_state WHERE urgency_key = ?", (state["urgency_key"],)) or state

    def _suppression_reason(self, state: dict[str, Any], settings: dict[str, Any]) -> str:
        status = str(state.get("status") or "").casefold()
        if status in TERMINAL_URGENCY_STATUSES:
            return "Teams urgency was already acknowledged."
        max_alerts = max(1, int(settings.get("max_alerts_per_urgency", 5) or 5))
        try:
            alert_count = int(state.get("alert_count") or 0)
        except (TypeError, ValueError):
            alert_count = 0
        if alert_count >= max_alerts:
            return "Teams alert already sent for this active urgency."
        last_alert_at = self._parse_utc(str(state.get("last_alert_at") or ""))
        interval = max(1, int(settings.get("repeat_interval_minutes", 30) or 30))
        if last_alert_at and datetime.now(timezone.utc) - last_alert_at < timedelta(minutes=interval):
            return "Teams alert throttled for this active urgency."
        return ""

    def _mark_urgency_alert_result(self, urgency_key: str, status: str, ok: bool, settings: dict[str, Any], alert_data: dict[str, Any] | None = None) -> None:
        now = utc_now()
        message_id = str((alert_data or {}).get("message_id") or "").strip()
        incident_id = str((alert_data or {}).get("incident_id") or "").strip()
        self.storage.execute(
            """
            UPDATE teams_alert_state
               SET status = ?,
                   alert_count = alert_count + 1,
                   last_alert_at = ?,
                   last_teams_message_id = COALESCE(NULLIF(?, ''), last_teams_message_id),
                   last_teams_incident_id = COALESCE(NULLIF(?, ''), last_teams_incident_id),
                   updated_at = ?
             WHERE urgency_key = ?
            """,
            (status, now, message_id, incident_id, now, urgency_key),
        )
        state = self.storage.fetch_one("SELECT * FROM teams_alert_state WHERE urgency_key = ?", (urgency_key,))
        if state:
            self._maybe_ring_phone_after_alerts(state, settings)

    def _acknowledge_if_teams_reply_seen(self, state: dict[str, Any], settings: dict[str, Any]) -> TeamsAlertResult | None:
        if not bool(settings.get("reply_detection_enabled", True)):
            return None
        if str(state.get("status") or "").casefold() in TERMINAL_URGENCY_STATUSES:
            return None
        last_alert_at = self._parse_utc(str(state.get("last_alert_at") or ""))
        if not last_alert_at:
            return None
        if not self._graph_chat_id(settings) or not self._graph_token(settings):
            return None
        try:
            reply = self._latest_graph_reply_after(settings, state, last_alert_at)
        except Exception as exc:
            self.storage.log(
                "warning",
                "Teams",
                "Could not check Teams replies for active urgency.",
                {"urgency_key": state.get("urgency_key"), "error": str(exc)[:300]},
            )
            return None
        now = utc_now()
        self.storage.execute(
            """
            UPDATE teams_alert_state
               SET last_teams_reply_check_at = ?,
                   updated_at = ?
             WHERE urgency_key = ?
            """,
            (now, now, state["urgency_key"]),
        )
        if not reply:
            return None
        self.storage.execute(
            """
            UPDATE teams_alert_state
               SET status = 'Acknowledged',
                   acknowledged_at = ?,
                   reply_detected_at = ?,
                   reply_message_id = ?,
                   reply_source = 'graph',
                   updated_at = ?
             WHERE urgency_key = ?
            """,
            (now, now, reply.get("id", ""), now, state["urgency_key"]),
        )
        self.storage.execute(
            """
            UPDATE escalations
               SET status = 'Acknowledged',
                   updated_at = ?
             WHERE source = 'WhatsApp Teams fallback'
               AND lower(status) IN ('detected', 'teams alert sent', 'teams alert failed')
            """,
            (now,),
        )
        self.storage.log(
            "info",
            "Teams",
            "Teams reply detected; active WhatsApp urgency acknowledged.",
            {
                "urgency_key": state.get("urgency_key"),
                "reply_message_id": reply.get("id", ""),
                "created_at": reply.get("createdDateTime", ""),
            },
        )
        return TeamsAlertResult(True, "Teams reply detected; current urgency acknowledged.", data={"acknowledged": True, "reply_message_id": reply.get("id", "")})

    def _latest_graph_reply_after(self, settings: dict[str, Any], state: dict[str, Any], cutoff: datetime) -> dict[str, Any] | None:
        result = self._get_graph_chat_messages(settings, timeout=max(3, min(int(settings.get("timeout_seconds", 12) or 12), 30)), top=12)
        if not result.ok:
            raise RuntimeError(result.error or result.message)
        messages = ((result.data or {}).get("messages") or []) if isinstance(result.data, dict) else []
        sender_user_id = self._graph_sender_user_id(settings).casefold()
        latest_reply: dict[str, Any] | None = None
        for item in messages:
            if not isinstance(item, dict):
                continue
            created = self._parse_utc(str(item.get("createdDateTime") or ""))
            if not created or created <= cutoff:
                continue
            last_teams_message_id = str(state.get("last_teams_message_id") or "")
            if last_teams_message_id and str(item.get("id") or "") == last_teams_message_id:
                continue
            content = self._graph_message_text(item)
            if self._looks_like_noor_alert(content, state):
                continue
            from_user = item.get("from", {}).get("user", {}) if isinstance(item.get("from"), dict) else {}
            from_user_id = str(from_user.get("id") or "").casefold() if isinstance(from_user, dict) else ""
            if sender_user_id and from_user_id and from_user_id == sender_user_id:
                continue
            if not content.strip():
                continue
            if latest_reply is None or created > (self._parse_utc(str(latest_reply.get("createdDateTime") or "")) or cutoff):
                latest_reply = item
        return latest_reply

    def _get_graph_chat_messages(self, settings: dict[str, Any], *, timeout: int, top: int = 10) -> TeamsAlertResult:
        chat_id = self._graph_chat_id(settings)
        token = self._graph_token(settings)
        if not chat_id or not token:
            return TeamsAlertResult(False, "Teams Graph chat ID or delegated token is missing.")
        endpoint = f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages?$top={max(1, min(top, 50))}"
        request = urllib.request.Request(endpoint, headers={"Authorization": f"Bearer {token}"}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read(100000).decode("utf-8", errors="replace")
                if response.status != 200:
                    return TeamsAlertResult(False, "Teams returned an unexpected status while reading replies.", error=f"HTTP {response.status}: {body[:300]}")
                parsed = self._loads_json(body)
                if not isinstance(parsed, dict):
                    return TeamsAlertResult(False, "Teams returned unreadable reply data.")
                messages = parsed.get("value")
                return TeamsAlertResult(True, "Teams replies read.", data={"messages": messages if isinstance(messages, list) else []})
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read(1000).decode("utf-8", errors="replace")
            except OSError:
                body = ""
            return TeamsAlertResult(False, "Teams rejected the reply-read request.", error=f"HTTP {exc.code}: {body[:300]}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return TeamsAlertResult(False, "Could not read Teams replies.", error=str(exc))

    def _maybe_ring_phone_after_alerts(self, state: dict[str, Any], settings: dict[str, Any]) -> None:
        if str(state.get("status") or "").casefold() in TERMINAL_URGENCY_STATUSES:
            return
        threshold = max(1, int(settings.get("ring_phone_after_alerts", 5) or 5))
        try:
            alert_count = int(state.get("alert_count") or 0)
        except (TypeError, ValueError):
            alert_count = 0
        if alert_count < threshold:
            return
        try:
            phone_ring_count = int(state.get("phone_ring_count") or 0)
        except (TypeError, ValueError):
            phone_ring_count = 0
        if bool(settings.get("ring_phone_once", True)) and phone_ring_count >= 1:
            return
        devices = settings.get("phone_fallback_devices")
        if not isinstance(devices, list) or not [str(item).strip() for item in devices if str(item).strip()]:
            devices = ["Symphony innova30", "Redmi 10"]
        try:
            result = FindPhoneService(self.storage).ring_first_available([str(item).strip() for item in devices if str(item).strip()])
        except Exception as exc:
            self.storage.log(
                "error",
                "Teams",
                "Phone ring escalation failed before Find Hub returned a result.",
                {"urgency_key": state.get("urgency_key"), "error": str(exc)[:300]},
            )
            return
        now = utc_now()
        self.storage.execute(
            """
            UPDATE teams_alert_state
               SET phone_ring_count = phone_ring_count + 1,
                   last_phone_ring_at = ?,
                   phone_device = ?,
                   updated_at = ?
             WHERE urgency_key = ?
            """,
            (now, (result.data or {}).get("device") if result.data else "", now, state["urgency_key"]),
        )
        self.storage.log(
            "warning" if result.ok else "error",
            "Teams",
            "Phone ring attempted after Teams no-response threshold.",
            {
                "urgency_key": state.get("urgency_key"),
                "alert_count": alert_count,
                "ok": result.ok,
                "message": result.message[:300],
                "error": result.error[:300],
            },
        )

    def _post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout: int,
        *,
        expected: set[int],
    ) -> TeamsAlertResult:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=True).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read(4000).decode("utf-8", errors="replace")
                parsed = self._loads_json(body)
                if response.status in expected:
                    data: dict[str, Any] = {"status": response.status, "response": body[:300]}
                    if isinstance(parsed, dict):
                        data["json"] = parsed
                    return TeamsAlertResult(True, "Teams alert sent.", data=data)
                return TeamsAlertResult(False, "Teams returned an unexpected status.", error=f"HTTP {response.status}: {body[:300]}")
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read(1000).decode("utf-8", errors="replace")
            except OSError:
                body = ""
            return TeamsAlertResult(False, "Teams rejected the alert.", error=f"HTTP {exc.code}: {body[:300]}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return TeamsAlertResult(False, "Could not send Teams alert.", error=str(exc))

    def _graph_chat_id(self, settings: dict[str, Any]) -> str:
        direct = str(settings.get("graph_chat_id") or "").strip()
        if direct:
            return direct
        env_name = str(settings.get("graph_chat_id_env") or "NOOR_TEAMS_CHAT_ID").strip()
        return os.environ.get(env_name, "").strip() if env_name else ""

    def _graph_token(self, settings: dict[str, Any]) -> str:
        env_name = str(settings.get("graph_token_env") or "NOOR_TEAMS_GRAPH_TOKEN").strip()
        if env_name:
            token = os.environ.get(env_name, "").strip()
            if token:
                return token
        token_path = self._resolve_project_path(str(settings.get("graph_token_path") or "data/teams_graph_token.txt"))
        try:
            return token_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _graph_sender_user_id(self, settings: dict[str, Any]) -> str:
        direct = str(settings.get("graph_sender_user_id") or "").strip()
        if direct:
            return direct
        env_name = str(settings.get("graph_sender_user_id_env") or "NOOR_TEAMS_SENDER_USER_ID").strip()
        return os.environ.get(env_name, "").strip() if env_name else ""

    @staticmethod
    def _graph_message_text(message: dict[str, Any]) -> str:
        body = message.get("body", {})
        content = str(body.get("content") or "") if isinstance(body, dict) else ""
        without_tags = re.sub(r"<[^>]+>", " ", content)
        return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()

    @staticmethod
    def _looks_like_noor_alert(content: str, state: dict[str, Any]) -> bool:
        lowered = content.casefold()
        message_hashes = [
            str(state.get("first_message_hash") or ""),
            str(state.get("last_message_hash") or ""),
        ]
        incident_ids = {f"wa-{value[:16].casefold()}" for value in message_hashes if len(value) >= 16}
        last_incident_id = str(state.get("last_teams_incident_id") or "").strip().casefold()
        if last_incident_id:
            incident_ids.add(last_incident_id)
        if any(incident_id and incident_id in lowered for incident_id in incident_ids):
            return True
        return ("whatsapp" in lowered and "needs attention" in lowered and "incident:" in lowered) or "noor teams test" in lowered

    def _webhook_url(self, settings: dict[str, Any]) -> str:
        direct = str(settings.get("webhook_url") or "").strip()
        if direct:
            return direct
        env_name = str(settings.get("webhook_url_env") or "NOOR_TEAMS_WEBHOOK_URL").strip()
        return os.environ.get(env_name, "").strip() if env_name else ""

    @staticmethod
    def _local_sender_script() -> Path:
        return SCRIPTS_DIR / "teams_send_ui.ps1"

    def _ensure_state_table(self) -> None:
        self.storage.execute(
            """
            CREATE TABLE IF NOT EXISTS teams_alert_state (
                urgency_key TEXT PRIMARY KEY,
                chat_label TEXT NOT NULL,
                event_type TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL,
                first_message_hash TEXT NOT NULL,
                last_message_hash TEXT NOT NULL,
                alert_count INTEGER NOT NULL DEFAULT 0,
                last_alert_at TEXT,
                acknowledged_at TEXT,
                phone_ring_count INTEGER NOT NULL DEFAULT 0,
                last_phone_ring_at TEXT,
                phone_device TEXT,
                last_teams_message_id TEXT,
                last_teams_incident_id TEXT,
                last_teams_reply_check_at TEXT,
                reply_detected_at TEXT,
                reply_message_id TEXT,
                reply_source TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._ensure_state_columns()

    def _ensure_state_columns(self) -> None:
        rows = self.storage.fetch_all("PRAGMA table_info(teams_alert_state)")
        columns = {str(row.get("name") or "") for row in rows}
        additions = {
            "phone_ring_count": "ALTER TABLE teams_alert_state ADD COLUMN phone_ring_count INTEGER NOT NULL DEFAULT 0",
            "last_phone_ring_at": "ALTER TABLE teams_alert_state ADD COLUMN last_phone_ring_at TEXT",
            "phone_device": "ALTER TABLE teams_alert_state ADD COLUMN phone_device TEXT",
            "last_teams_message_id": "ALTER TABLE teams_alert_state ADD COLUMN last_teams_message_id TEXT",
            "last_teams_incident_id": "ALTER TABLE teams_alert_state ADD COLUMN last_teams_incident_id TEXT",
            "last_teams_reply_check_at": "ALTER TABLE teams_alert_state ADD COLUMN last_teams_reply_check_at TEXT",
            "reply_detected_at": "ALTER TABLE teams_alert_state ADD COLUMN reply_detected_at TEXT",
            "reply_message_id": "ALTER TABLE teams_alert_state ADD COLUMN reply_message_id TEXT",
            "reply_source": "ALTER TABLE teams_alert_state ADD COLUMN reply_source TEXT",
        }
        for column, statement in additions.items():
            if column not in columns:
                self.storage.execute(statement)

    @staticmethod
    def _urgency_key(chat_label: str, event_type: str, reason: str) -> str:
        normalized = "\n".join(
            [
                "whatsapp",
                (chat_label or "direct contact").strip().casefold(),
                (event_type or "message").strip().casefold(),
                (reason or "needs attention").strip().casefold(),
            ]
        )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _parse_utc(value: str) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _loads_json(value: str) -> Any:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _resolve_project_path(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @staticmethod
    def _html_message(title: str, message: str) -> str:
        safe_title = html.escape(title[:200])
        safe_message = html.escape(message[:24000]).replace("\n", "<br>")
        return f"<b>{safe_title}</b><br>{safe_message}"

    @staticmethod
    def _whatsapp_summary(chat_label: str, event_type: str, reason: str, body: str, error: str, settings: dict[str, Any]) -> str:
        timestamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %I:%M %p")
        lines = [
            "Noor could not answer a WhatsApp event.",
            f"Chat: {chat_label or 'Direct contact'}",
            f"Event: {event_type or 'message'}",
            f"Reason: {reason}",
            f"Time: {timestamp}",
            "No WhatsApp reply was sent.",
        ]
        if error:
            lines.append(f"Error: {error[:240]}")
        if bool(settings.get("include_message_preview")) and body:
            lines.append(f"Preview: {body[:240]}")
        lines.append("After handling this, tell Noor: teams ack.")
        return "\n".join(lines)
