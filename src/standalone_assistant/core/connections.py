from __future__ import annotations

from pathlib import Path
from typing import Any

from standalone_assistant.core.connectors import ToolRegistry
from standalone_assistant.core.google_productivity import GoogleProductivityService
from standalone_assistant.core.gemini_cli import GeminiCli
from standalone_assistant.core.project_scanner import codex_status, git_status
from standalone_assistant.core.speech import SpeechService
from standalone_assistant.core.storage import Storage
from standalone_assistant.core.teams_alerts import TeamsAlertService
from standalone_assistant.core.usage_limits import usage_snapshot
from standalone_assistant.core.whatsapp_web import WhatsAppWebService


def connection_snapshot(storage: Storage) -> dict[str, Any]:
    registry = ToolRegistry(storage)
    tools = registry.list_tools()
    projects = storage.fetch_all("SELECT id, name, path FROM projects ORDER BY name")
    codex = codex_status()
    speech = SpeechService(storage)
    voice_settings = speech.current_settings()
    productivity = GoogleProductivityService(storage).status()
    whatsapp = WhatsAppWebService(storage).status()
    gemini_settings = storage.get_setting("gemini_cli", {})
    gemini_path = GeminiCli(gemini_settings, Path.cwd()).detect()
    usage = usage_snapshot(storage)
    codex["usage"] = usage["codex"]
    teams = TeamsAlertService(storage).status()

    content_root = Path("E:/ESEO/content-review-manager")
    google_credentials = content_root / "credentials.json"
    google_token = content_root / ".secrets" / "token.json"

    project_states = []
    for project in projects:
        status = git_status(project["path"])
        project_states.append(
            {
                "name": project["name"],
                "path": project["path"],
                "exists": Path(project["path"]).exists(),
                "git": status["status"],
            }
        )

    return {
        "google": {
            "name": "Google Workspace",
            "connected": google_credentials.exists() and google_token.exists(),
            "productivity_connected": productivity["authorization_connected"],
            "credentials_present": google_credentials.exists(),
            "token_present": google_token.exists(),
            "productivity_token_present": productivity["token_present"],
            "productivity_authorized": productivity["authorization_connected"],
            "productivity_error": productivity.get("error", ""),
            "via": "Content Review Manager OAuth",
        },
        "tools": {
            "connected": sum(1 for tool in tools if Path(tool["path"]).exists()),
            "total": len(tools),
            "items": [
                {
                    "name": tool["name"],
                    "exists": Path(tool["path"]).exists(),
                    "status": tool["connection_status"],
                }
                for tool in tools
            ],
        },
        "projects": {
            "connected": sum(1 for project in project_states if project["exists"]),
            "total": len(project_states),
            "items": project_states,
        },
        "codex": codex,
        "voice": {
            "connected": bool(voice_settings.get("enabled", True)),
            "voices": None,
            "selected": voice_settings.get("edge_voice") or voice_settings.get("voice_name", ""),
            "provider": voice_settings.get("tts_provider", "edge"),
        },
        "whatsapp": {
            "connected": whatsapp.ok,
            "state": (whatsapp.data or {}).get("connection_state", "offline"),
            "message": whatsapp.message,
        },
        "gemini": {
            "available": bool(gemini_path),
            "enabled": bool(gemini_settings.get("enabled", False)),
            "path": gemini_path or "",
            "usage": usage["gemini"],
        },
        "teams": {
            "enabled": teams["enabled"],
            "configured": teams["configured"],
            "mode": teams["mode"],
            "message": teams["message"],
            "reply_detection_enabled": teams.get("reply_detection_enabled", False),
            "reply_detection_configured": teams.get("reply_detection_configured", False),
            "active_urgency": teams.get("active_urgency"),
        },
    }
