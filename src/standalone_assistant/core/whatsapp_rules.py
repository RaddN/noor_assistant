from __future__ import annotations

import json
from typing import Any

from standalone_assistant.core.paths import WHATSAPP_REPLY_RULES


def load_whatsapp_rules() -> list[dict[str, Any]]:
    try:
        rules = json.loads(WHATSAPP_REPLY_RULES.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(rules, list):
        return []
    return [dict(rule) for rule in rules if isinstance(rule, dict)]


def write_whatsapp_rules(rules: list[dict[str, Any]]) -> None:
    WHATSAPP_REPLY_RULES.write_text(json.dumps(rules, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def rule_triggers(rule: dict[str, Any]) -> list[dict[str, Any]]:
    triggers = rule.get("triggers")
    if isinstance(triggers, list):
        return [dict(trigger) for trigger in triggers if isinstance(trigger, dict)]
    pattern = str(rule.get("pattern") or "").strip()
    if not pattern:
        return []
    return [{"type": "message", "match": "regex", "value": pattern}]


def rule_actions(rule: dict[str, Any]) -> list[dict[str, Any]]:
    actions = rule.get("actions")
    if isinstance(actions, list):
        clean = [dict(action) for action in actions if isinstance(action, dict)]
        if clean:
            return clean

    action = rule.get("action")
    if isinstance(action, dict):
        return [dict(action)]
    if isinstance(action, str) and action.strip():
        return [{"type": action.strip()}]

    reply = str(rule.get("reply") or "").strip()
    if reply:
        return [{"type": "reply", "text": reply}]
    return []


def rule_audience(rule: dict[str, Any]) -> dict[str, Any]:
    audience = rule.get("audience")
    if not isinstance(audience, dict):
        return {"scope": "everyone", "contacts": []}
    scope = str(audience.get("scope") or "everyone").strip().casefold()
    if scope not in {"everyone", "contacts", "except_contacts"}:
        scope = "everyone"
    contacts = audience.get("contacts")
    if not isinstance(contacts, list):
        contacts = []
    aliases = audience.get("aliases") or audience.get("contact_aliases") or []
    clean_aliases: list[dict[str, str]] = []
    if isinstance(aliases, list):
        for alias in aliases:
            if isinstance(alias, dict):
                label = str(alias.get("label") or alias.get("name") or alias.get("chat_label") or "").strip()
                contact = str(alias.get("contact") or alias.get("phone") or alias.get("chat_id") or "").strip()
            else:
                raw = str(alias or "").strip()
                if "=" in raw:
                    label, contact = [item.strip() for item in raw.split("=", 1)]
                else:
                    label, contact = raw, ""
            if label:
                clean_aliases.append({"label": label, "contact": contact})
    return {"scope": scope, "contacts": [str(item).strip() for item in contacts if str(item).strip()], "aliases": clean_aliases}


def normalize_rule(rule: dict[str, Any]) -> dict[str, Any]:
    rule_id = str(rule.get("id") or "").strip()
    normalized: dict[str, Any] = {
        "id": rule_id,
        "name": str(rule.get("name") or rule_id or "New rule").strip(),
        "enabled": bool(rule.get("enabled", True)),
        "trigger_logic": str(rule.get("trigger_logic") or "any").strip().casefold() if str(rule.get("trigger_logic") or "").strip().casefold() in {"any", "all"} else "any",
        "audience": rule_audience(rule),
        "triggers": rule_triggers(rule),
        "actions": rule_actions(rule),
    }
    return normalized


def contacts_from_text(value: str) -> list[str]:
    contacts: list[str] = []
    for line in value.replace(",", "\n").splitlines():
        item = line.strip()
        if item:
            contacts.append(item)
    return contacts


def contacts_to_text(contacts: list[Any]) -> str:
    return "\n".join(str(item).strip() for item in contacts if str(item).strip())


def audience_summary(rule: dict[str, Any]) -> str:
    audience = rule_audience(rule)
    contacts = audience["contacts"]
    if audience["scope"] == "contacts":
        return f"Specific contacts ({len(contacts)})"
    if audience["scope"] == "except_contacts":
        return f"Everyone except {len(contacts)}"
    return "Everyone"


def trigger_summary(rule: dict[str, Any]) -> str:
    pieces = []
    for trigger in rule_triggers(rule):
        trigger_type = str(trigger.get("type") or "message").strip().casefold()
        if trigger_type == "message":
            pieces.append(f"Message {trigger.get('match') or 'contains'}: {trigger.get('value') or ''}".strip())
        elif trigger_type == "call":
            pieces.append(f"Call: {trigger.get('call_type') or trigger.get('value') or 'any'}")
        elif trigger_type == "time":
            operator = str(trigger.get("operator") or "at")
            value = trigger.get("time") or trigger.get("value") or ""
            if operator == "between":
                value = f"{trigger.get('start') or ''}-{trigger.get('end') or ''}"
            pieces.append(f"Time {operator}: {value}".strip())
        elif trigger_type == "date":
            operator = str(trigger.get("operator") or "on")
            value = trigger.get("date") or trigger.get("value") or ""
            if operator == "between":
                value = f"{trigger.get('start') or ''}-{trigger.get('end') or ''}"
            pieces.append(f"Date {operator}: {value}".strip())
        else:
            pieces.append(trigger_type.title())
    return "; ".join(piece for piece in pieces if piece) or "No triggers"


def action_summary(rule: dict[str, Any]) -> str:
    pieces = []
    for action in rule_actions(rule):
        action_type = str(action.get("type") or "reply").strip().casefold()
        if action_type == "reply":
            pieces.append("Reply")
        elif action_type in {"assistant", "brain"}:
            pieces.append("Noor brain")
        elif action_type in {"ai", "research", "gemini", "codex"}:
            provider = str(action.get("provider") or ("auto" if action_type == "ai" else action_type))
            pieces.append(f"AI: {provider}")
        elif action_type in {"tool", "safe_tool"}:
            pieces.append(f"Tool: {action.get('tool_id') or 'select'}")
        elif action_type in {"employee_report", "weekly_report", "monthly_report"}:
            report_type = action.get("report") or action.get("kind") or action_type.replace("_report", "")
            pieces.append(f"Report: {str(report_type).title()}")
        elif action_type in {"note", "log"}:
            pieces.append("Log")
        else:
            pieces.append(action_type.title())
    return " + ".join(pieces) or "No actions"
