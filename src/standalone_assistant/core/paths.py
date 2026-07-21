from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
ASSETS_DIR = PROJECT_ROOT / "assets"
ICON_DIR = ASSETS_DIR / "icons"
SESSION_DIR = DATA_DIR / "codex-sessions"
WHATSAPP_PROFILE_DIR = DATA_DIR / "whatsapp-web-profile"
WHATSAPP_WEBJS_AUTH_DIR = DATA_DIR / "whatsapp-webjs-auth"
WHATSAPP_SELECTORS = CONFIG_DIR / "whatsapp_web_selectors.json"
WHATSAPP_REPLY_RULES = CONFIG_DIR / "whatsapp_reply_rules.json"
WHATSAPP_BRIDGE_DIR = DATA_DIR / "whatsapp-web-bridge"
WHATSAPP_BRIDGE_STATUS = WHATSAPP_BRIDGE_DIR / "status.json"
WHATSAPP_BRIDGE_STARTING = WHATSAPP_BRIDGE_DIR / "starting.json"
WHATSAPP_BRIDGE_REQUEST = WHATSAPP_BRIDGE_DIR / "request.json"
WHATSAPP_BRIDGE_RESPONSE = WHATSAPP_BRIDGE_DIR / "response.json"
WHATSAPP_INCOMING_DIR = WHATSAPP_BRIDGE_DIR / "incoming"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
EMPLOYEE_REPORTS_CONFIG = CONFIG_DIR / "employee_reports.json"
REPORTS_DIR = DATA_DIR / "reports"
GOOGLE_PRODUCTIVITY_TOKEN = DATA_DIR / "google_productivity_token.json"
TOOLS_CONFIG = CONFIG_DIR / "tools.json"
PROJECTS_CONFIG = CONFIG_DIR / "projects.json"
APP_SETTINGS = CONFIG_DIR / "app_settings.json"
DB_PATH = DATA_DIR / "assistant.sqlite"
APP_LOCK = DATA_DIR / "noor.lock"


def ensure_runtime_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    WHATSAPP_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    WHATSAPP_WEBJS_AUTH_DIR.mkdir(parents=True, exist_ok=True)
    WHATSAPP_BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    WHATSAPP_INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
