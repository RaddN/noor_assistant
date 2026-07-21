from __future__ import annotations

import json
import subprocess
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from standalone_assistant.core.paths import PROJECT_ROOT, SCRIPTS_DIR
from standalone_assistant.core.process_utils import hidden_subprocess_kwargs
from standalone_assistant.core.storage import Storage


FIND_HUB_URL = "https://www.google.com/android/find/"
FIND_PHONE_SCRIPT = SCRIPTS_DIR / "find_phone_play_sound.ps1"
CHROME_CANDIDATES = (
    Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
    Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
)


@dataclass
class FindPhoneResult:
    ok: bool
    message: str
    url: str = FIND_HUB_URL
    error: str = ""


class FindPhoneService:
    """Ring the owner's phone through Google's signed-in Find Hub page."""

    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def settings(self) -> dict[str, Any]:
        defaults = {
            "enabled": True,
            "url": FIND_HUB_URL,
            "mode": "auto_play_sound",
            "target_device": "Symphony innova30",
            "automation_timeout_seconds": 45,
            "post_open_delay_seconds": 3,
            "post_click_wait_seconds": 4,
        }
        configured = self.storage.get_setting("find_phone", {})
        if isinstance(configured, dict):
            defaults.update({key: configured[key] for key in defaults if key in configured})
        if str(defaults.get("mode") or "").strip().casefold() == "play_sound_only":
            defaults["mode"] = "auto_play_sound"
        escalation = self.storage.get_setting("escalation", {})
        if isinstance(escalation, dict) and "find_hub_enabled" in escalation:
            defaults["enabled"] = bool(escalation.get("find_hub_enabled"))
        return defaults

    def open_find_hub(self) -> FindPhoneResult:
        settings = self.settings()
        if not bool(settings.get("enabled", True)):
            return FindPhoneResult(False, "Find My Phone is disabled in Settings.")
        url = str(settings.get("url") or FIND_HUB_URL)
        opened, error = self.open_browser_window(url)
        if error:
            return FindPhoneResult(False, "Could not open Google Find Hub.", url=url, error=error)
        if not opened:
            return FindPhoneResult(False, "Windows did not accept the Find Hub browser launch.", url=url)
        time.sleep(max(1, int(settings.get("post_open_delay_seconds") or 3)))
        if str(settings.get("mode") or "auto_play_sound").strip().casefold() in {"open_only", "manual"}:
            self.storage.log("warning", "Find My Phone", "Opened Google Find Hub.", {"mode": settings.get("mode", "open_only")})
            return FindPhoneResult(True, "Google Find Hub is open. Select Raihan Hossain's phone and use Play sound.", url=url)
        return self.play_sound(settings, url)

    def open_browser_window(self, url: str) -> tuple[bool, str]:
        for chrome_path in CHROME_CANDIDATES:
            if not chrome_path.exists():
                continue
            try:
                subprocess.Popen([str(chrome_path), "--new-window", url], cwd=PROJECT_ROOT)
                return True, ""
            except OSError as exc:
                return False, str(exc)
        try:
            return bool(webbrowser.open(url)), ""
        except Exception as exc:
            return False, str(exc)

    def play_sound(self, settings: dict[str, Any], url: str) -> FindPhoneResult:
        if not FIND_PHONE_SCRIPT.exists():
            return FindPhoneResult(False, "Find My Phone automation helper is missing.", url=url, error=str(FIND_PHONE_SCRIPT))
        timeout = max(10, int(settings.get("automation_timeout_seconds") or 45))
        post_click_wait = max(1, int(settings.get("post_click_wait_seconds") or 4))
        device = str(settings.get("target_device") or "").strip()
        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(FIND_PHONE_SCRIPT),
            "-TimeoutSeconds",
            str(timeout),
            "-PostClickWaitSeconds",
            str(post_click_wait),
        ]
        if device:
            command.extend(["-DeviceName", device])
        try:
            completed = subprocess.run(
                command,
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=timeout + post_click_wait + 10,
                **hidden_subprocess_kwargs(),
            )
        except subprocess.TimeoutExpired:
            self.storage.log("error", "Find My Phone", "Find Hub Play sound automation timed out.", {"device": device})
            return FindPhoneResult(False, "Find Hub opened, but Play sound automation timed out.", url=url, error="timeout")
        except OSError as exc:
            self.storage.log("error", "Find My Phone", "Could not run Find Hub Play sound automation.", {"error": str(exc)})
            return FindPhoneResult(False, "Could not run Find Hub Play sound automation.", url=url, error=str(exc))

        payload = self.parse_helper_output(completed.stdout)
        if completed.returncode != 0 or not payload.get("ok"):
            error = str(payload.get("error") or completed.stderr or completed.stdout).strip()
            message = str(payload.get("message") or "Find Hub opened, but Play sound could not be triggered.")
            self.storage.log("error", "Find My Phone", message, {"device": device, "error": error[:300]})
            return FindPhoneResult(False, message, url=url, error=error[:300])

        actual_device = str(payload.get("device") or device or "selected phone").strip()
        status = str(payload.get("status") or "").strip()
        message = f"Play sound triggered for {actual_device}."
        if status:
            message = f"{message} Find Hub status: {status}."
        self.storage.log("warning", "Find My Phone", "Triggered Play sound in Google Find Hub.", {"device": actual_device, "status": status})
        return FindPhoneResult(True, message, url=url)

    @staticmethod
    def parse_helper_output(output: str) -> dict[str, Any]:
        for line in reversed((output or "").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        return {}
