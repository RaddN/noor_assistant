from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from standalone_assistant.core.process_utils import hidden_subprocess_kwargs


@dataclass
class CodexResult:
    ok: bool
    text: str = ""
    error: str = ""


def command_for_cli(executable: str, args: list[str]) -> list[str]:
    lowered = executable.lower()
    if lowered.endswith((".cmd", ".bat")):
        return ["cmd", "/d", "/s", "/c", executable, *args]
    if lowered.endswith(".ps1"):
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", executable, *args]
    return [executable, *args]


class CodexCli:
    """Low-budget non-interactive Codex fallback for short answers."""

    def __init__(self, settings: dict[str, Any], workspace: Path) -> None:
        self.settings = settings
        self.workspace = workspace

    def detect(self) -> str | None:
        executable = shutil.which("codex")
        if executable:
            return executable
        try:
            result = subprocess.run(
                ["where.exe", "codex"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                **hidden_subprocess_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        candidates = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        preferred = [value for value in candidates if value.lower().endswith((".cmd", ".exe", ".ps1"))]
        return (preferred or candidates or [None])[0]

    def answer(self, user_message: str, *, channel: str, context: str = "") -> CodexResult:
        if not bool(self.settings.get("enabled", True)):
            return CodexResult(False, error="Codex fallback is disabled in Settings.")
        executable = self.detect()
        if not executable:
            return CodexResult(False, error="Codex CLI was not found on PATH.")

        model = str(self.settings.get("model") or "gpt-5-mini").strip()
        effort = str(self.settings.get("reasoning_effort") or "low").strip()
        timeout = max(20, min(int(self.settings.get("timeout_seconds", 60)), 180))
        max_chars = max(500, min(int(self.settings.get("max_context_characters", 2200)), 6000))
        message = user_message.strip()[:max_chars]
        research_context = context.strip()[:max_chars]
        channel_rule = (
            "Return a concise WhatsApp-safe reply under 500 characters."
            if channel == "whatsapp"
            else "Return a concise direct answer. Use bullets only when they improve clarity."
        )
        prompt = (
            "You are Noor, Raihan Hossain's assistant fallback brain. Do not edit files, run commands, "
            "or claim that you completed external actions. Answer only from the user message and "
            "the supplied research context. If the context is weak, say what is uncertain.\n\n"
            f"{channel_rule}\n\n"
            f"User message:\n{message}\n\n"
            f"Research context, if any:\n{research_context or 'None'}\n"
        )

        output_path = Path(tempfile.gettempdir()) / f"noor-codex-answer-{uuid.uuid4().hex}.txt"
        args = [
            "exec",
            "-m",
            model,
            "-c",
            f'model_reasoning_effort="{effort}"',
            "-C",
            str(self.workspace),
            "--sandbox",
            "read-only",
            "--ask-for-approval",
            "never",
            "--ephemeral",
            "--output-last-message",
            str(output_path),
            "-",
        ]
        try:
            result = subprocess.run(
                command_for_cli(executable, args),
                input=prompt,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                **hidden_subprocess_kwargs(),
            )
        except subprocess.TimeoutExpired:
            return CodexResult(False, error="Codex CLI timed out while preparing an answer.")
        except OSError as exc:
            return CodexResult(False, error=f"Could not run Codex CLI: {exc}")
        try:
            text = output_path.read_text(encoding="utf-8").strip() if output_path.exists() else ""
        except OSError:
            text = ""
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        combined = "\n".join(value for value in (result.stdout, result.stderr) if value).strip()
        if result.returncode != 0:
            return CodexResult(False, error=combined[:500] or "Codex CLI exited with an error.")
        if not text:
            return CodexResult(False, error=combined[:500] or "Codex CLI did not return a usable answer.")
        return CodexResult(True, text=text)
