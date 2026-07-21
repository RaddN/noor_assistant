from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from standalone_assistant.core.paths import APP_SETTINGS, DB_PATH, PROJECTS_CONFIG, TOOLS_CONFIG, ensure_runtime_dirs


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True)


def loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


class Storage:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        ensure_runtime_dirs()
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tools (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    capabilities_json TEXT NOT NULL DEFAULT '[]',
                    permissions_json TEXT NOT NULL DEFAULT '[]',
                    sensitive_paths_json TEXT NOT NULL DEFAULT '[]',
                    open_command_json TEXT,
                    test_command_json TEXT,
                    safe_commands_json TEXT NOT NULL DEFAULT '[]',
                    connection_status TEXT NOT NULL DEFAULT 'unknown',
                    last_run TEXT,
                    current_run TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL UNIQUE,
                    agents_path TEXT,
                    notes TEXT,
                    last_git_status TEXT,
                    last_activity TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    priority TEXT NOT NULL DEFAULT 'Normal',
                    due_at TEXT,
                    status TEXT NOT NULL DEFAULT 'Open',
                    project_id INTEGER,
                    contact TEXT,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    pattern TEXT NOT NULL,
                    action TEXT NOT NULL,
                    require_approval INTEGER NOT NULL DEFAULT 1,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS knowledge (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'General',
                    body TEXT NOT NULL,
                    trusted INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    level TEXT NOT NULL,
                    source TEXT NOT NULL,
                    message TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS codex_sessions (
                    id TEXT PRIMARY KEY,
                    project_id INTEGER,
                    prompt TEXT NOT NULL,
                    allow_edits INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    transcript_path TEXT,
                    last_error TEXT
                );

                CREATE TABLE IF NOT EXISTS escalations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    source TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    policy_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

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
                );

                CREATE TABLE IF NOT EXISTS whatsapp_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_name TEXT NOT NULL,
                    message_hash TEXT NOT NULL UNIQUE,
                    preview TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS whatsapp_auto_replies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_name TEXT NOT NULL,
                    message_hash TEXT NOT NULL UNIQUE,
                    reply_hash TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ai_response_cache (
                    prompt_hash TEXT PRIMARY KEY,
                    channel TEXT NOT NULL,
                    response TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
        self.seed_settings()
        self.seed_tools()
        self.seed_projects()

    def seed_settings(self) -> None:
        if not APP_SETTINGS.exists():
            return
        settings = loads(APP_SETTINGS.read_text(encoding="utf-8"), {})
        now = utc_now()
        with self.connect() as conn:
            for key, value in settings.items():
                exists = conn.execute("SELECT 1 FROM settings WHERE key = ?", (key,)).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO settings (key, value_json, updated_at) VALUES (?, ?, ?)",
                        (key, dumps(value), now),
                    )

    def seed_tools(self) -> None:
        if not TOOLS_CONFIG.exists():
            return
        tools = loads(TOOLS_CONFIG.read_text(encoding="utf-8"), [])
        now = utc_now()
        with self.connect() as conn:
            for tool in tools:
                existing = conn.execute("SELECT id FROM tools WHERE id = ?", (tool["id"],)).fetchone()
                payload = (
                    tool["name"],
                    tool["path"],
                    tool.get("kind", "external-tool"),
                    dumps(tool.get("capabilities", [])),
                    dumps(tool.get("permissions", [])),
                    dumps(tool.get("sensitive_paths", [])),
                    dumps(tool.get("open_command")),
                    dumps(tool.get("test_command")),
                    dumps(tool.get("safe_commands", [])),
                    now,
                    tool["id"],
                )
                if existing:
                    conn.execute(
                        """
                        UPDATE tools
                           SET name = ?, path = ?, kind = ?, capabilities_json = ?,
                               permissions_json = ?, sensitive_paths_json = ?,
                               open_command_json = ?, test_command_json = ?,
                               safe_commands_json = ?, updated_at = ?
                         WHERE id = ?
                        """,
                        payload,
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO tools (
                            name, path, kind, capabilities_json, permissions_json,
                            sensitive_paths_json, open_command_json, test_command_json,
                            safe_commands_json, created_at, updated_at, id
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        payload[:-1] + (now, tool["id"]),
                    )

    def seed_projects(self) -> None:
        if not PROJECTS_CONFIG.exists():
            return
        projects = loads(PROJECTS_CONFIG.read_text(encoding="utf-8"), [])
        now = utc_now()
        with self.connect() as conn:
            for project in projects:
                path = project.get("path")
                name = project.get("name")
                if not path or not name:
                    continue
                exists = conn.execute("SELECT id FROM projects WHERE path = ?", (path,)).fetchone()
                status = "available" if Path(path).exists() else "missing"
                if exists:
                    conn.execute(
                        """
                        UPDATE projects
                           SET name = ?, agents_path = ?, notes = ?,
                               last_git_status = COALESCE(last_git_status, ?),
                               updated_at = ?
                         WHERE path = ?
                        """,
                        (name, project.get("agents_path") or None, project.get("notes"), status, now, path),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO projects (name, path, agents_path, notes, last_git_status, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (name, path, project.get("agents_path") or None, project.get("notes"), status, now, now),
                    )

    def fetch_all(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def fetch_one(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(query, params).fetchone()
            return dict(row) if row else None

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        with self.connect() as conn:
            conn.execute(query, params)

    def log(self, level: str, source: str, message: str, metadata: dict[str, Any] | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO activity (ts, level, source, message, metadata_json) VALUES (?, ?, ?, ?, ?)",
                (utc_now(), level, source, message, dumps(metadata or {})),
            )

    def update_tool_status(
        self,
        tool_id: str,
        status: str,
        *,
        last_error: str | None = None,
        current_run: str | None = None,
        mark_run: bool = False,
    ) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE tools
                   SET connection_status = ?,
                       last_error = ?,
                       current_run = ?,
                       last_run = CASE WHEN ? THEN ? ELSE last_run END,
                       updated_at = ?
                 WHERE id = ?
                """,
                (status, last_error, current_run, 1 if mark_run else 0, now, now, tool_id),
            )

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.fetch_one("SELECT value_json FROM settings WHERE key = ?", (key,))
        return loads(row["value_json"], default) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
                """,
                (key, dumps(value), utc_now()),
            )
