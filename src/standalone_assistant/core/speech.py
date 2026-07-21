from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from standalone_assistant.core.paths import SCRIPTS_DIR
from standalone_assistant.core.process_utils import hidden_subprocess_kwargs
from standalone_assistant.core.storage import Storage


_VOICE_CACHE: list[dict[str, Any]] | None = None


def _pythonw_executable() -> str:
    executable = Path(sys.executable)
    pythonw = executable.with_name("pythonw.exe")
    return str(pythonw if pythonw.exists() else executable)


class SpeechService:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def list_voices(self) -> list[dict[str, Any]]:
        global _VOICE_CACHE
        if _VOICE_CACHE is not None:
            return _VOICE_CACHE
        script = SCRIPTS_DIR / "list_voices.ps1"
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
                **hidden_subprocess_kwargs(),
            )
        except Exception:
            return []
        if completed.returncode != 0 or not completed.stdout.strip():
            return []
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return []
        if isinstance(payload, dict):
            _VOICE_CACHE = [payload]
            return _VOICE_CACHE
        if isinstance(payload, list):
            _VOICE_CACHE = payload
            return _VOICE_CACHE
        return []

    def default_voice_name(self) -> str:
        voices = self.list_voices()
        if not voices:
            return ""
        preferred_tokens = ("zira", "jenny", "aria", "sonia", "emma", "female")
        for token in preferred_tokens:
            for voice in voices:
                name = str(voice.get("name", ""))
                gender = str(voice.get("gender", ""))
                if token in name.lower() or token in gender.lower():
                    return name
        return str(voices[0].get("name", ""))

    def current_settings(self) -> dict[str, Any]:
        settings = self.storage.get_setting("voice", {})
        settings.setdefault("voice_name", "")
        settings.setdefault("tts_provider", "edge")
        settings.setdefault("edge_voice", "en-US-JennyNeural")
        settings.setdefault("rate", 0)
        settings.setdefault("volume", 100)
        settings.setdefault("listen_timeout_seconds", 12)
        settings.setdefault("min_confidence", 0.25)
        settings.setdefault("recognition_mode", "hybrid")
        settings.setdefault("speak_confirmations", True)
        return settings

    def speak(self, text: str, *, voice_name: str | None = None, rate: int | None = None, volume: int | None = None) -> bool:
        text = (text or "").strip()
        if not text:
            return False
        settings = self.current_settings()
        if not settings.get("enabled", True):
            return False
        provider = str(settings.get("tts_provider", "windows")).lower()
        if provider == "edge" and self.speak_edge(text, rate=rate):
            return True
        return self.speak_windows(text, voice_name=voice_name, rate=rate, volume=volume)

    def speak_edge(self, text: str, *, rate: int | None = None) -> bool:
        settings = self.current_settings()
        args = [
            _pythonw_executable(),
            str(SCRIPTS_DIR / "edge_speak.py"),
            "--text",
            text,
            "--voice",
            str(settings.get("edge_voice", "en-US-JennyNeural")),
            "--rate",
            str(rate if rate is not None else int(settings.get("rate", -1))),
        ]
        try:
            subprocess.Popen(args, **hidden_subprocess_kwargs())
            self.storage.log("info", "Voice", "Spoke assistant response with Edge TTS.", {"voice": settings.get("edge_voice", "")})
            return True
        except Exception as exc:
            self.storage.log("warning", "Voice", f"Edge TTS failed: {exc}")
            return False

    def speak_windows(self, text: str, *, voice_name: str | None = None, rate: int | None = None, volume: int | None = None) -> bool:
        settings = self.current_settings()
        script = SCRIPTS_DIR / "speak.ps1"
        args = [
            "powershell",
            "-NoProfile",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Text",
            text,
            "-VoiceName",
            voice_name if voice_name is not None else str(settings.get("voice_name", "")),
            "-Rate",
            str(rate if rate is not None else int(settings.get("rate", 0))),
            "-Volume",
            str(volume if volume is not None else int(settings.get("volume", 100))),
        ]
        try:
            subprocess.Popen(args, **hidden_subprocess_kwargs())
            self.storage.log("info", "Voice", "Spoke assistant response.", {"voice": args[9] if len(args) > 9 else ""})
            return True
        except Exception as exc:
            self.storage.log("warning", "Voice", f"Speech failed: {exc}")
            return False

    def listen_command_args(self) -> list[str]:
        settings = self.current_settings()
        configured_mode = str(settings.get("recognition_mode", "hybrid")).lower()
        if configured_mode == "dictation":
            mode = "Dictation"
        elif configured_mode == "command":
            mode = "Command"
        else:
            mode = "Hybrid"
        return [
            "powershell",
            "-NoProfile",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPTS_DIR / "listen_once.ps1"),
            "-TimeoutSeconds",
            str(int(settings.get("listen_timeout_seconds", 8))),
            "-Mode",
            mode,
        ]
