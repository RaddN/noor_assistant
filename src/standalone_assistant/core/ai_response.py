from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from standalone_assistant.core.codex_cli import CodexCli
from standalone_assistant.core.gemini_cli import GeminiCli
from standalone_assistant.core.storage import Storage, utc_now
from standalone_assistant.core.web_research import ResearchResult, answer_question


@dataclass
class AIResponse:
    ok: bool
    text: str = ""
    source: str = ""
    error: str = ""


class AIResponseService:
    """Shared low-cost response pipeline for Noor chat and WhatsApp."""

    def __init__(self, storage: Storage, workspace: Path) -> None:
        self.storage = storage
        self.workspace = workspace

    def settings(self) -> dict[str, Any]:
        defaults = {
            "enabled": True,
            "research_enabled": True,
            "gemini_enabled": True,
            "codex_enabled": True,
            "cache_hours": 24,
            "max_research_pages": 3,
            "max_gemini_calls_per_hour": 4,
            "max_codex_calls_per_hour": 2,
            "provider_cooldown_minutes": 20,
            "whatsapp_ai_for_questions_only": True,
        }
        configured = self.storage.get_setting("ai_brain", {})
        if isinstance(configured, dict):
            defaults.update({key: configured[key] for key in defaults if key in configured})
        return defaults

    def answer(self, user_message: str, *, channel: str = "assistant") -> AIResponse:
        message = user_message.strip()
        if not message:
            return AIResponse(False, error="Empty message.")
        settings = self.settings()
        if not bool(settings.get("enabled", True)):
            return AIResponse(False, error="AI response pipeline is disabled.")

        if channel == "whatsapp" and bool(settings.get("whatsapp_ai_for_questions_only", True)) and not self._looks_answerable(message):
            return AIResponse(True, "I have received your message. I will prepare the relevant update for Khadija Noor.", "local-ack")

        cached = self._read_cache(message, channel, int(settings.get("cache_hours", 24)))
        if cached:
            return cached

        research = self._research(message, settings)
        context = self._research_context(research)
        if research.ok and research.confidence in {"medium", "high"}:
            text = self._format_research_answer(research, channel)
            response = AIResponse(True, text, f"research:{research.confidence}")
            self._write_cache(message, channel, response)
            return response

        if bool(settings.get("gemini_enabled", True)):
            gemini = self._gemini(message, channel, context, settings)
            if gemini.ok:
                self._write_cache(message, channel, gemini)
                return gemini

        if bool(settings.get("codex_enabled", True)):
            codex = self._codex(message, channel, context, settings)
            if codex.ok:
                self._write_cache(message, channel, codex)
                return codex

        if research.ok:
            text = self._format_research_answer(research, channel)
            response = AIResponse(True, text, f"research:{research.confidence}")
            self._write_cache(message, channel, response)
            return response

        return AIResponse(False, error=research.error or "No reliable answer source was available.")

    def _research(self, message: str, settings: dict[str, Any]) -> ResearchResult:
        if not bool(settings.get("research_enabled", True)):
            return ResearchResult(False, "Research disabled", "", [], "Research is disabled.")
        return answer_question(message, max_pages=int(settings.get("max_research_pages", 3)))

    def _gemini(self, message: str, channel: str, context: str, settings: dict[str, Any]) -> AIResponse:
        provider = "gemini"
        if not self._provider_available(provider, settings):
            return AIResponse(False, source=provider, error="Gemini is cooling down after a recent failure.")
        if not self._within_rate_limit(provider, int(settings.get("max_gemini_calls_per_hour", 4))):
            return AIResponse(False, source=provider, error="Gemini hourly limit reached.")
        result = GeminiCli(self.storage.get_setting("gemini_cli", {}), self.workspace).answer(message, channel=channel, context=context)
        if result.ok:
            self._log_provider(provider, "used")
            return AIResponse(True, self._clean_ai_text(result.text, channel), provider)
        self._log_provider(provider, "failed", result.error)
        return AIResponse(False, source=provider, error=result.error)

    def _codex(self, message: str, channel: str, context: str, settings: dict[str, Any]) -> AIResponse:
        provider = "codex"
        if not self._provider_available(provider, settings):
            return AIResponse(False, source=provider, error="Codex is cooling down after a recent failure.")
        if not self._within_rate_limit(provider, int(settings.get("max_codex_calls_per_hour", 2))):
            return AIResponse(False, source=provider, error="Codex hourly limit reached.")
        result = CodexCli(self.storage.get_setting("codex_ai", {}), self.workspace).answer(message, channel=channel, context=context)
        if result.ok:
            self._log_provider(provider, "used")
            return AIResponse(True, self._clean_ai_text(result.text, channel), provider)
        self._log_provider(provider, "failed", result.error)
        return AIResponse(False, source=provider, error=result.error)

    def _within_rate_limit(self, provider: str, limit: int) -> bool:
        if limit <= 0:
            return False
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        pattern = f'"provider": "{provider}"'
        row = self.storage.fetch_one(
            "SELECT COUNT(*) AS c FROM activity WHERE source = 'AI Brain' AND message = 'AI provider used' AND ts >= ? AND metadata_json LIKE ?",
            (cutoff, f"%{pattern}%"),
        )
        return not row or int(row["c"]) < limit

    def _provider_available(self, provider: str, settings: dict[str, Any]) -> bool:
        cooldown = max(0, min(int(settings.get("provider_cooldown_minutes", 20)), 240))
        if cooldown == 0:
            return True
        row = self.storage.fetch_one(
            "SELECT ts FROM activity WHERE source = 'AI Brain' AND message = 'AI provider failed' AND metadata_json LIKE ? ORDER BY id DESC LIMIT 1",
            (f'%"provider": "{provider}"%',),
        )
        if not row:
            return True
        try:
            prior = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return True
        return datetime.now(timezone.utc) - prior >= timedelta(minutes=cooldown)

    def _log_provider(self, provider: str, outcome: str, error: str = "") -> None:
        self.storage.log(
            "info" if outcome == "used" else "warning",
            "AI Brain",
            "AI provider used" if outcome == "used" else "AI provider failed",
            {"provider": provider, "error": error[:300]},
        )

    def _read_cache(self, message: str, channel: str, hours: int) -> AIResponse | None:
        if hours <= 0:
            return None
        row = self.storage.fetch_one(
            "SELECT response, source, created_at FROM ai_response_cache WHERE prompt_hash = ?",
            (self._cache_key(message, channel),),
        )
        if not row:
            return None
        try:
            created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if datetime.now(timezone.utc) - created > timedelta(hours=hours):
            return None
        return AIResponse(True, row["response"], f"cache:{row['source']}")

    def _write_cache(self, message: str, channel: str, response: AIResponse) -> None:
        self.storage.execute(
            """
            INSERT INTO ai_response_cache (prompt_hash, channel, response, source, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(prompt_hash) DO UPDATE SET
                response = excluded.response,
                source = excluded.source,
                created_at = excluded.created_at
            """,
            (self._cache_key(message, channel), channel[:40], response.text[:4000], response.source[:80], utc_now()),
        )

    @staticmethod
    def _cache_key(message: str, channel: str) -> str:
        normalized = re.sub(r"\s+", " ", message.strip().casefold())
        return hashlib.sha256(f"{channel}\n{normalized}".encode("utf-8")).hexdigest()

    @staticmethod
    def _looks_answerable(message: str) -> bool:
        lowered = message.casefold()
        if "?" in message:
            return True
        return bool(
            re.search(
                r"\b(what|why|how|when|where|which|who|can you|could you|please|explain|tell me|need|help|price|cost|status|update)\b",
                lowered,
            )
        )

    @staticmethod
    def _research_context(result: ResearchResult) -> str:
        if not result.ok:
            return ""
        links = "\n".join(result.links[:3])
        return f"{result.title}\nConfidence: {result.confidence}\n{result.summary}\nSources:\n{links}"

    @staticmethod
    def _format_research_answer(result: ResearchResult, channel: str) -> str:
        if channel == "whatsapp":
            source = result.links[0] if result.links else ""
            suffix = f" Source: {source}" if source else ""
            return (result.summary[:430].rstrip() + suffix)[:500]
        links = "\n".join(f"- {link}" for link in result.links[:3])
        return f"{result.summary}\n\nSources:\n{links}" if links else result.summary

    @staticmethod
    def _clean_ai_text(text: str, channel: str) -> str:
        cleaned = re.sub(r"\s+", " ", text).strip() if channel == "whatsapp" else text.strip()
        if channel == "whatsapp":
            return cleaned[:500].rstrip()
        return cleaned[:3000].rstrip()
