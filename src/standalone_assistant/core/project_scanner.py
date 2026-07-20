from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from standalone_assistant.core.process_utils import hidden_subprocess_kwargs


def run_text(args: list[str], cwd: str | Path, timeout: int = 20) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            **hidden_subprocess_kwargs(),
        )
        output = ((completed.stdout or "") + (completed.stderr or "")).strip()
        return completed.returncode, output
    except Exception as exc:
        return -1, str(exc)


def git_status(path: str | Path) -> dict[str, Any]:
    root = Path(path)
    if not root.exists():
        return {"ok": False, "status": "missing", "output": f"Missing path: {root}"}
    code, output = run_text(["git", "status", "--short"], root)
    if code != 0:
        return {"ok": False, "status": "not-git", "output": output or "Not a git repository"}
    return {"ok": True, "status": "dirty" if output else "clean", "output": output}


def recent_git_activity(path: str | Path, limit: int = 5) -> str:
    code, output = run_text(["git", "log", f"-{limit}", "--date=short", "--pretty=format:%h %ad %s"], path)
    return output if code == 0 else ""


def find_agents(path: str | Path) -> list[str]:
    root = Path(path)
    candidates: list[Path] = []
    if root.exists():
        candidates.extend(root.glob("AGENTS.md"))
        candidates.extend(root.glob("**/AGENTS.md"))
    unique = []
    seen = set()
    for candidate in candidates:
        resolved = str(candidate.resolve())
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique[:20]


def codex_status() -> dict[str, Any]:
    executable = shutil.which("codex")
    if not executable:
        return {"available": False, "path": None, "version": None}
    path = Path(executable)
    ps1_path = path.with_suffix(".ps1")
    launch_path = str(ps1_path) if ps1_path.exists() else executable
    if launch_path.lower().endswith(".ps1"):
        code, output = run_text(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", launch_path, "--version"],
            os.getcwd(),
        )
    else:
        code, output = run_text([launch_path, "--version"], os.getcwd())
    return {"available": code == 0, "path": launch_path, "version": output}


def preflight_project(path: str | Path, agents_path: str | None = None) -> dict[str, Any]:
    root = Path(path)
    agents = find_agents(root)
    selected_agents = Path(agents_path) if agents_path else None
    selected_agents_ok = bool(selected_agents and selected_agents.exists())
    status = git_status(root)
    activity = recent_git_activity(root) if status["ok"] else ""
    codex = codex_status()
    return {
        "path": str(root),
        "path_exists": root.exists(),
        "agents": agents,
        "selected_agents": str(selected_agents) if selected_agents else None,
        "selected_agents_ok": selected_agents_ok,
        "git": status,
        "recent_activity": activity,
        "codex": codex,
        "warnings": build_warnings(root, status, selected_agents, codex),
    }


def build_warnings(root: Path, status: dict[str, Any], selected_agents: Path | None, codex: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if not root.exists():
        warnings.append("Working directory does not exist.")
    if not status["ok"]:
        warnings.append("Git status could not be verified.")
    elif status["status"] == "dirty":
        warnings.append("There are uncommitted changes. Review them before allowing edits.")
    if selected_agents and not selected_agents.exists():
        warnings.append("Selected AGENTS.md does not exist.")
    if not codex["available"]:
        warnings.append("Codex CLI was not found on PATH.")
    return warnings


def build_codex_prompt(user_prompt: str, agents_path: str | None, allow_edits: bool) -> str:
    mode = "File changes are allowed for this task." if allow_edits else "Analysis only. Do not edit files."
    agent_line = f"Project instructions file selected by the assistant: {agents_path}" if agents_path else "Use project instructions discovered in the working directory."
    return (
        f"{mode}\n"
        f"{agent_line}\n"
        "Before acting, verify the working directory, git status, and applicable instructions. "
        "Do not run destructive git commands or claim checks passed unless they actually ran.\n\n"
        f"User task:\n{user_prompt.strip()}\n"
    )
