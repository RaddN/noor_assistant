from __future__ import annotations

import webbrowser
from dataclasses import dataclass
from typing import Any

from standalone_assistant.core.storage import Storage


FIND_HUB_URL = "https://www.google.com/android/find/"


@dataclass
class FindPhoneResult:
    ok: bool
    message: str
    url: str = FIND_HUB_URL
    error: str = ""


class FindPhoneService:
    """Open Google's Find Hub for the owner's phone.

    Google does not provide a supported local API for directly ringing a phone
    from this desktop app, so this intentionally opens the verified Find Hub page.
    """

    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def settings(self) -> dict[str, Any]:
        defaults = {
            "enabled": True,
            "url": FIND_HUB_URL,
            "mode": "play_sound_only",
        }
        configured = self.storage.get_setting("find_phone", {})
        if isinstance(configured, dict):
            defaults.update({key: configured[key] for key in defaults if key in configured})
        escalation = self.storage.get_setting("escalation", {})
        if isinstance(escalation, dict) and "find_hub_enabled" in escalation:
            defaults["enabled"] = bool(escalation.get("find_hub_enabled"))
        return defaults

    def open_find_hub(self) -> FindPhoneResult:
        settings = self.settings()
        if not bool(settings.get("enabled", True)):
            return FindPhoneResult(False, "Find My Phone is disabled in Settings.")
        url = str(settings.get("url") or FIND_HUB_URL)
        try:
            opened = webbrowser.open(url)
        except Exception as exc:
            return FindPhoneResult(False, "Could not open Google Find Hub.", url=url, error=str(exc))
        if not opened:
            return FindPhoneResult(False, "Windows did not accept the Find Hub browser launch.", url=url)
        self.storage.log("warning", "Find My Phone", "Opened Google Find Hub.", {"mode": settings.get("mode", "play_sound_only")})
        return FindPhoneResult(True, "Google Find Hub is open. Select Raihan Hossain's phone and use Play sound.", url=url)
