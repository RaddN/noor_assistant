from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from standalone_assistant.core.process_utils import hidden_subprocess_kwargs
from standalone_assistant.core.storage import Storage, loads


@dataclass
class CommandResult:
    ok: bool
    stdout: str
    stderr: str
    returncode: int | None

    @property
    def combined_output(self) -> str:
        parts = []
        if self.stdout:
            parts.append(self.stdout.strip())
        if self.stderr:
            parts.append(self.stderr.strip())
        return "\n\n".join(parts).strip()


class ToolRegistry:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def list_tools(self) -> list[dict[str, Any]]:
        rows = self.storage.fetch_all("SELECT * FROM tools ORDER BY name")
        for row in rows:
            row["capabilities"] = loads(row.pop("capabilities_json"), [])
            row["permissions"] = loads(row.pop("permissions_json"), [])
            row["sensitive_paths"] = loads(row.pop("sensitive_paths_json"), [])
            row["open_command"] = loads(row.pop("open_command_json"), None)
            row["test_command"] = loads(row.pop("test_command_json"), None)
            row["safe_commands"] = loads(row.pop("safe_commands_json"), [])
        return rows

    def get_tool(self, tool_id: str) -> dict[str, Any] | None:
        return next((tool for tool in self.list_tools() if tool["id"] == tool_id), None)

    def test_tool(self, tool_id: str) -> CommandResult:
        tool = self.get_tool(tool_id)
        if not tool:
            return CommandResult(False, "", f"Unknown tool: {tool_id}", None)

        if tool.get("kind") == "google-workspace-oauth":
            return self._test_google_workspace(tool)

        path = Path(tool["path"])
        if not path.exists():
            self.storage.update_tool_status(tool_id, "missing", last_error=f"Missing path: {path}")
            return CommandResult(False, "", f"Missing path: {path}", None)

        command = tool.get("test_command")
        if not command:
            self.storage.update_tool_status(tool_id, "available", last_error=None)
            return CommandResult(True, f"Path exists: {path}", "", 0)

        result = self._run_command(command)
        self.storage.update_tool_status(
            tool_id,
            "connected" if result.ok else "error",
            last_error=None if result.ok else result.combined_output[:1200],
            mark_run=True,
        )
        self.storage.log(
            "info" if result.ok else "warning",
            tool["name"],
            f"Test connection: {command.get('label', 'command')}",
            {"returncode": result.returncode},
        )
        return result

    def open_tool(self, tool_id: str) -> CommandResult:
        tool = self.get_tool(tool_id)
        if not tool:
            return CommandResult(False, "", f"Unknown tool: {tool_id}", None)
        command = tool.get("open_command")
        if not command:
            return CommandResult(False, "", "No open command is configured for this tool.", None)
        cwd = command.get("cwd") or tool["path"]
        args = command.get("args") or []
        try:
            subprocess.Popen(args, cwd=cwd, **hidden_subprocess_kwargs())
            self.storage.update_tool_status(tool_id, "launched", current_run=command.get("label"), mark_run=True)
            self.storage.log("info", tool["name"], f"Launched: {command.get('label', args[0] if args else 'tool')}")
            return CommandResult(True, f"Launched {tool['name']}", "", 0)
        except Exception as exc:
            message = str(exc)
            self.storage.update_tool_status(tool_id, "error", last_error=message)
            return CommandResult(False, "", message, None)

    def run_safe_command(self, tool_id: str, index: int = 0) -> CommandResult:
        tool = self.get_tool(tool_id)
        if not tool:
            return CommandResult(False, "", f"Unknown tool: {tool_id}", None)
        commands = tool.get("safe_commands") or []
        if index < 0 or index >= len(commands):
            return CommandResult(False, "", "No safe command is configured at that index.", None)
        result = self._run_command(commands[index])
        self.storage.update_tool_status(
            tool_id,
            "connected" if result.ok else "error",
            last_error=None if result.ok else result.combined_output[:1200],
            mark_run=True,
        )
        return result

    def _run_command(self, command: dict[str, Any]) -> CommandResult:
        args = command.get("args") or []
        cwd = command.get("cwd")
        timeout = int(command.get("timeout_seconds") or 30)
        if not args:
            return CommandResult(False, "", "Command has no args.", None)
        try:
            completed = subprocess.run(
                args,
                cwd=cwd,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
                **hidden_subprocess_kwargs(),
            )
            return CommandResult(
                completed.returncode == 0,
                completed.stdout or "",
                completed.stderr or "",
                completed.returncode,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            return CommandResult(False, stdout, stderr + f"\nTimed out after {timeout} seconds.", None)
        except Exception as exc:
            return CommandResult(False, "", str(exc), None)

    def _test_google_workspace(self, tool: dict[str, Any]) -> CommandResult:
        root = Path(tool["path"])
        checks = {
            "tool_path": root.exists(),
            "credentials_json": (root / "credentials.json").exists(),
            "oauth_token": (root / ".secrets" / "token.json").exists(),
        }
        ok = all(checks.values())
        message = json_like(checks)
        self.storage.update_tool_status(
            tool["id"],
            "connected" if ok else "setup-needed",
            last_error=None if ok else message,
            mark_run=True,
        )
        self.storage.log("info" if ok else "warning", tool["name"], "Checked Google Workspace OAuth file presence.", checks)
        return CommandResult(ok, message, "", 0 if ok else 1)


def json_like(value: dict[str, Any]) -> str:
    lines = []
    for key, item in value.items():
        lines.append(f"{key}: {'yes' if item else 'no'}")
    return "\n".join(lines)
