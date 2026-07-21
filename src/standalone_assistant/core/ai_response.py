from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

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
    needs_human: bool = False
    handoff_reason: str = ""


class AIResponseService:
    """Shared low-cost response pipeline for Noor chat and WhatsApp."""

    def __init__(self, storage: Storage, workspace: Path, progress_callback: Callable[[str, str, str], None] | None = None) -> None:
        self.storage = storage
        self.workspace = workspace
        self.progress_callback = progress_callback

    def settings(self) -> dict[str, Any]:
        defaults = {
            "enabled": True,
            "research_enabled": True,
            "gemini_enabled": True,
            "codex_enabled": True,
            "cache_hours": 24,
            "max_research_pages": 3,
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

    def answer_with_provider(self, provider: str, user_message: str, *, channel: str = "assistant", context: str = "") -> AIResponse:
        provider = provider.strip().casefold() or "auto"
        message = user_message.strip()
        if provider in {"auto", "brain", "fallback"}:
            return self.answer(message, channel=channel)
        settings = self.settings()
        if not bool(settings.get("enabled", True)):
            return AIResponse(False, source=provider, error="AI response pipeline is disabled.")
        if provider == "research":
            research = self._research(message, settings)
            if not research.ok:
                return AIResponse(False, source=provider, error=research.error or "Research failed.")
            return AIResponse(True, self._format_research_answer(research, channel), f"research:{research.confidence}")
        if provider == "gemini":
            if not bool(settings.get("gemini_enabled", True)):
                self._progress("Gemini", "Gemini fallback is disabled in Settings.", "error")
                return AIResponse(False, source=provider, error="Gemini fallback is disabled in Settings.")
            gemini = self._gemini(message, channel, context, settings)
            if gemini.ok:
                return gemini
            return self._fallback_after_provider_failure("gemini", gemini.error, message, channel, context, settings)
        if provider == "codex":
            if not bool(settings.get("codex_enabled", True)):
                self._progress("Codex", "Codex fallback is disabled in Settings.", "error")
                return AIResponse(False, source=provider, error="Codex fallback is disabled in Settings.")
            codex = self._codex(message, channel, context, settings)
            if codex.ok:
                return codex
            return self._fallback_after_provider_failure("codex", codex.error, message, channel, context, settings)
        return AIResponse(False, source=provider, error=f"Unknown AI provider: {provider}")

    def answer_whatsapp_unmatched(self, user_message: str, *, chat_label: str = "", event_type: str = "message") -> AIResponse:
        """Answer an unmatched WhatsApp message using Gemini first, then Codex."""
        if event_type.strip().casefold() != "message":
            reason = "Gemini/Codex fallback is only used for WhatsApp messages, not calls."
            return AIResponse(False, source="ai", error=reason, needs_human=True, handoff_reason=reason)
        message = user_message.strip()
        if not message:
            return AIResponse(False, source="ai", error="Empty WhatsApp message.", needs_human=True, handoff_reason="Empty WhatsApp message.")
        settings = self.settings()
        if not bool(settings.get("enabled", True)):
            return AIResponse(False, source="ai", error="AI response pipeline is disabled.", needs_human=True, handoff_reason="AI response pipeline is disabled.")

        prompt = self._whatsapp_unmatched_prompt(message, chat_label, event_type)
        gemini_error = ""
        if bool(settings.get("gemini_enabled", True)):
            gemini = self._gemini(prompt, "whatsapp", "", settings)
            if gemini.ok and gemini.text:
                decision = self._classify_whatsapp_ai_decision(gemini.text)
                if decision == "human":
                    reason = self._handoff_reason(gemini.text) or "Gemini says Raihan or a manager must reply."
                    return AIResponse(False, source="gemini", error=reason, needs_human=True, handoff_reason=reason)
                if decision != "codex":
                    return AIResponse(True, gemini.text, "gemini")
                gemini_error = self._handoff_reason(gemini.text) or "Gemini deferred this WhatsApp message to Codex."
            else:
                gemini_error = gemini.error or "Gemini did not return a usable answer."
        else:
            gemini_error = "Gemini fallback is disabled in Settings."

        if bool(settings.get("codex_enabled", True)):
            context = f"Gemini status: {gemini_error}" if gemini_error else ""
            codex = self._codex(prompt, "whatsapp", context, settings)
            if codex.ok and codex.text:
                if self._classify_whatsapp_ai_decision(codex.text) == "human":
                    reason = self._handoff_reason(codex.text) or "Codex says Raihan or a manager must reply."
                    return AIResponse(False, source="codex", error=reason, needs_human=True, handoff_reason=reason)
                return AIResponse(True, codex.text, "codex")
            reason = codex.error or "Codex did not return a usable answer."
            return AIResponse(False, source="codex", error=reason, needs_human=True, handoff_reason=reason)

        reason = gemini_error or "No AI provider is enabled."
        return AIResponse(False, source="gemini", error=reason, needs_human=True, handoff_reason=reason)

    def _fallback_after_provider_failure(
        self,
        provider: str,
        error: str,
        message: str,
        channel: str,
        context: str,
        settings: dict[str, Any],
    ) -> AIResponse:
        if provider != "codex" and bool(settings.get("codex_enabled", True)):
            self._progress("Codex", f"{provider.title()} failed; trying Codex fallback...", "progress")
            codex = self._codex(message, channel, context, settings)
            if codex.ok:
                return AIResponse(True, codex.text, f"{provider}-failed:{codex.source}")
        if bool(settings.get("research_enabled", True)) and self._looks_answerable(message):
            research = self._research(message, settings)
            if research.ok:
                self._progress("AI Fallback", "Answered with research after AI provider fallback.", "success")
                return AIResponse(True, self._format_research_answer(research, channel), f"{provider}-failed:research:{research.confidence}")
        return AIResponse(False, source=provider, error=error or f"{provider.title()} did not return a usable answer.")

    def _research(self, message: str, settings: dict[str, Any]) -> ResearchResult:
        if not bool(settings.get("research_enabled", True)):
            return ResearchResult(False, "Research disabled", "", [], "Research is disabled.")
        return answer_question(message, max_pages=int(settings.get("max_research_pages", 3)))

    def _gemini(self, message: str, channel: str, context: str, settings: dict[str, Any]) -> AIResponse:
        provider = "gemini"
        self._progress("Gemini", "Gemini is preparing an answer...", "progress")
        result = GeminiCli(self.storage.get_setting("gemini_cli", {}), self.workspace).answer(message, channel=channel, context=context)
        if result.ok:
            self._log_provider(provider, "used")
            self._progress("Gemini", "Gemini reply is ready.", "success")
            return AIResponse(True, self._clean_ai_text(result.text, channel), provider)
        self._log_provider(provider, "failed", result.error)
        self._progress("Gemini", f"Gemini failed: {result.error[:260]}", "error")
        return AIResponse(False, source=provider, error=result.error)

    def _codex(self, message: str, channel: str, context: str, settings: dict[str, Any]) -> AIResponse:
        provider = "codex"
        self._progress("Codex", "Codex is preparing an answer...", "progress")
        result = CodexCli(self.storage.get_setting("codex_ai", {}), self.workspace).answer(message, channel=channel, context=context)
        if result.ok:
            self._log_provider(provider, "used")
            self._progress("Codex", "Codex reply is ready.", "success")
            return AIResponse(True, self._clean_ai_text(result.text, channel), provider)
        self._log_provider(provider, "failed", result.error)
        self._progress("Codex", f"Codex failed: {result.error[:260]}", "error")
        return AIResponse(False, source=provider, error=result.error)

    def _progress(self, title: str, message: str, state: str = "progress") -> None:
        if self.progress_callback:
            self.progress_callback(title, message, state)

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
    def _whatsapp_unmatched_prompt(message: str, chat_label: str, event_type: str) -> str:
        sender = chat_label.strip() or "the direct WhatsApp contact"
        return (
            "You are Noor, Raihan Hossain's WhatsApp assistant. A direct WhatsApp message did not match any local rule. "
            "If you can answer safely with general knowledge or the message itself, send one concise WhatsApp reply. "
            "If it needs Raihan, a manager, a personal decision, private company context, or live state you cannot verify, reply only with: "
            "REQUIRES_RAIHAN_REPLY: <short reason>. "
            "If Codex is the better fallback because the message is about code, local projects, debugging, or this assistant implementation, reply only with: "
            "USE_CODEX: <short reason>. "
            f"Event type: {event_type or 'message'}. Sender: {sender}. Message: {message}"
        )

    @staticmethod
    def _classify_whatsapp_ai_decision(text: str) -> str:
        lowered = text.casefold()
        human_patterns = [
            r"\brequires?[_\s-]*(raihan|manager|owner|human|manual)[_\s-]*(reply|response|answer)?\b",
            r"\brequire(?:s|d)?\s+(?:a\s+)?(?:reply|response|answer)\s+from\s+(?:me|raihan|manager|owner|human)\b",
            r"\bneed(?:s|ed)?\s+(?:a\s+)?(?:reply|response|answer)\s+from\s+(?:me|raihan|manager|owner|human)\b",
            r"\b(?:ask|contact|check with|wait for)\s+(?:me|raihan|manager|owner|human)\b",
            r"\b(?:human|manual)\s+(?:reply|response|answer|approval|decision)\b",
            r"\b(?:cannot|can't|unable to)\s+(?:answer|reply|respond)\b.*\b(?:raihan|manager|owner|human|private|context)\b",
            r"\bnot enough\s+(?:context|information)\b.*\b(?:raihan|manager|owner|human|reply|response)\b",
        ]
        if any(re.search(pattern, lowered) for pattern in human_patterns):
            return "human"
        codex_patterns = [
            r"\buse[_\s-]*codex\b",
            r"\bask\s+codex\b",
            r"\bcodex\s+(?:can|could|should|would)\s+(?:answer|handle|reply|respond|know)\b",
            r"\bcodex\s+(?:has|have)\s+(?:the\s+)?answer\b",
            r"\bcodex\s+is\s+(?:better|able)\b",
            r"\bbetter\s+(?:for|answered by)\s+codex\b",
        ]
        if any(re.search(pattern, lowered) for pattern in codex_patterns):
            return "codex"
        return "answer"

    @staticmethod
    def _handoff_reason(text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text).strip()
        cleaned = re.sub(r"^(REQUIRES_RAIHAN_REPLY|USE_CODEX)\s*:\s*", "", cleaned, flags=re.IGNORECASE)
        return cleaned[:240].strip()

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
