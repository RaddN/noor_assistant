from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from standalone_assistant.core.storage import Storage, loads


def usage_snapshot(storage: Storage) -> dict[str, Any]:
    return {
        "codex": codex_usage_snapshot(),
        "gemini": gemini_usage_snapshot(storage),
    }


def codex_usage_snapshot() -> dict[str, Any]:
    from_sessions = _codex_rate_limits_from_sessions()
    if from_sessions:
        return _codex_from_rate_limits(from_sessions["rate_limits"], from_sessions["source"], from_sessions.get("timestamp", ""))

    from_manager = _codex_rate_limits_from_accounts_manager()
    if from_manager:
        return from_manager

    return {
        "available": False,
        "summary": "Usage: no local Codex quota data yet.",
        "windows": [],
        "source": "",
    }


def gemini_usage_snapshot(storage: Storage) -> dict[str, Any]:
    settings = storage.get_setting("gemini_cli", {})
    daily_limit = _safe_int(settings.get("daily_request_limit"), 1000)
    used_today = _count_gemini_requests_today(storage)
    if daily_limit > 0:
        remaining = max(0, daily_limit - used_today)
        summary = f"Usage: {remaining} of {daily_limit} daily requests remaining ({used_today} used by Noor today)."
    else:
        remaining = None
        summary = f"Usage: {used_today} Gemini calls by Noor today; daily limit is not configured."

    return {
        "available": True,
        "daily_limit": daily_limit,
        "used_today": used_today,
        "remaining_today": remaining,
        "auth_type": _gemini_auth_type(),
        "summary": summary,
        "source": "Noor activity log plus configured Gemini CLI daily limit",
    }


def _codex_rate_limits_from_sessions() -> dict[str, Any] | None:
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    root = codex_home / "sessions"
    if not root.exists():
        return None
    try:
        files = sorted(root.rglob("*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True)[:20]
    except OSError:
        return None
    for path in files:
        for line in reversed(_tail_lines(path)):
            try:
                event = json.loads(line)
                payload = event.get("payload", {})
            except (json.JSONDecodeError, AttributeError):
                continue
            if not isinstance(payload, dict):
                continue
            rate_limits = payload.get("rate_limits")
            if isinstance(rate_limits, dict):
                return {
                    "rate_limits": rate_limits,
                    "source": str(path),
                    "timestamp": event.get("timestamp") or "",
                }
    return None


def _tail_lines(path: Path, max_bytes: int = 524288) -> list[str]:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            start = max(0, size - max_bytes)
            handle.seek(start)
            raw = handle.read()
    except OSError:
        return []
    text = raw.decode("utf-8", errors="ignore")
    lines = text.splitlines()
    if start > 0 and lines:
        lines = lines[1:]
    return [line for line in lines if line.strip()]


def _codex_rate_limits_from_accounts_manager() -> dict[str, Any] | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    path = Path(appdata) / "Code" / "User" / "globalStorage" / "wannanbigpig.codex-accounts-manager" / "accounts-index.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    account_id = str(payload.get("currentAccountId") or "")
    accounts = payload.get("accounts")
    if not isinstance(accounts, list):
        return None
    account = next((item for item in accounts if isinstance(item, dict) and str(item.get("id") or "") == account_id), None)
    if not account:
        return None
    summary = account.get("quotaSummary")
    if not isinstance(summary, dict):
        return None
    raw = summary.get("rawData")
    rate_limit = raw.get("rate_limit") if isinstance(raw, dict) else None
    if isinstance(rate_limit, dict):
        windows = []
        primary = rate_limit.get("primary_window")
        secondary = rate_limit.get("secondary_window")
        if isinstance(primary, dict):
            windows.append(_window_from_raw("7d", primary))
        if isinstance(secondary, dict):
            windows.append(_window_from_raw("5h", secondary))
        return _codex_from_windows(windows, "Codex Accounts Manager")

    windows = []
    if bool(summary.get("weeklyWindowPresent")):
        remaining = _safe_float(summary.get("weeklyPercentage"), 0)
        windows.append(
            {
                "label": _window_label(_safe_int(summary.get("weeklyWindowMinutes"), 10080), "7d"),
                "used_percent": max(0, 100 - remaining),
                "remaining_percent": max(0, remaining),
                "resets_at": summary.get("weeklyResetTime"),
                "reset_display": _format_reset(summary.get("weeklyResetTime")),
            }
        )
    if bool(summary.get("hourlyWindowPresent")):
        remaining = _safe_float(summary.get("hourlyPercentage"), 0)
        windows.append(
            {
                "label": "5h",
                "used_percent": max(0, 100 - remaining),
                "remaining_percent": max(0, remaining),
                "resets_at": summary.get("hourlyResetTime"),
                "reset_display": _format_reset(summary.get("hourlyResetTime")),
            }
        )
    return _codex_from_windows(windows, "Codex Accounts Manager") if windows else None


def _codex_from_rate_limits(rate_limits: dict[str, Any], source: str, timestamp: str = "") -> dict[str, Any]:
    windows = []
    primary = rate_limits.get("primary")
    secondary = rate_limits.get("secondary")
    if isinstance(primary, dict):
        windows.append(_window_from_rate_limit("7d", primary))
    if isinstance(secondary, dict):
        windows.append(_window_from_rate_limit("5h", secondary))
    result = _codex_from_windows(windows, source)
    result["plan_type"] = rate_limits.get("plan_type") or ""
    result["timestamp"] = timestamp
    if not any(item["label"] == "5h" for item in windows):
        result["five_hour_present"] = False
    return result


def _codex_from_windows(windows: list[dict[str, Any]], source: str) -> dict[str, Any]:
    if not windows:
        return {
            "available": False,
            "summary": "Usage: no Codex limit windows found.",
            "windows": [],
            "source": source,
        }
    parts = []
    for window in windows:
        reset = f", resets {window['reset_display']}" if window.get("reset_display") else ""
        parts.append(f"{window['label']} {window['remaining_percent']:.0f}% remaining{reset}")
    if not any(item["label"] == "5h" for item in windows):
        parts.append("5h window not reported")
    return {
        "available": True,
        "summary": "Usage: " + "; ".join(parts) + ".",
        "windows": windows,
        "source": source,
    }


def _window_from_rate_limit(default_label: str, window: dict[str, Any]) -> dict[str, Any]:
    minutes = _safe_int(window.get("window_minutes"), 0)
    used = _safe_float(window.get("used_percent"), 0)
    reset = window.get("resets_at")
    return {
        "label": _window_label(minutes, default_label),
        "used_percent": used,
        "remaining_percent": max(0, 100 - used),
        "window_minutes": minutes,
        "resets_at": reset,
        "reset_display": _format_reset(reset),
    }


def _window_from_raw(default_label: str, window: dict[str, Any]) -> dict[str, Any]:
    seconds = _safe_int(window.get("limit_window_seconds"), 0)
    minutes = int(seconds / 60) if seconds else 0
    used = _safe_float(window.get("used_percent"), 0)
    reset = window.get("reset_at")
    return {
        "label": _window_label(minutes, default_label),
        "used_percent": used,
        "remaining_percent": max(0, 100 - used),
        "window_minutes": minutes,
        "resets_at": reset,
        "reset_display": _format_reset(reset),
    }


def _window_label(minutes: int, fallback: str) -> str:
    if minutes == 300:
        return "5h"
    if minutes == 10080:
        return "7d"
    if minutes == 1440:
        return "1d"
    if minutes > 0 and minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes > 0 and minutes % 60 == 0:
        return f"{minutes // 60}h"
    return fallback


def _format_reset(value: Any) -> str:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone().strftime("%b %d %I:%M %p")


def _count_gemini_requests_today(storage: Storage) -> int:
    today = datetime.now().astimezone().date()
    rows = storage.fetch_all(
        """
        SELECT ts, message, metadata_json
          FROM activity
         WHERE source = 'AI Brain'
         ORDER BY id DESC
         LIMIT 2000
        """
    )
    total = 0
    for row in rows:
        try:
            ts = datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00")).astimezone()
        except (TypeError, ValueError):
            continue
        if ts.date() != today:
            continue
        metadata = loads(row.get("metadata_json"), {})
        if not isinstance(metadata, dict) or metadata.get("provider") != "gemini":
            continue
        message = str(row.get("message") or "")
        error = str(metadata.get("error") or "").casefold()
        if message == "AI provider used":
            total += 1
        elif message == "AI provider failed" and "not found" not in error and "disabled" not in error:
            total += 1
    return total


def _gemini_auth_type() -> str:
    settings_path = Path.home() / ".gemini" / "settings.json"
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    auth = payload.get("security", {}).get("auth", {}) if isinstance(payload, dict) else {}
    return str(auth.get("selectedType") or "")


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
