"""AI question answering through a configurable provider (OpenAI or OpenAI-compatible APIs)."""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from config import Settings

logger = logging.getLogger("ultron.ai")

SYSTEM_PROMPT = """You are Ultron, a friendly personal AI voice assistant. Always refer to yourself as Ultron.
- Your replies are shown on screen and read aloud, so keep them concise and conversational \
(usually 1-4 sentences) unless the user asks for detail, code, or step-by-step help.
- You help with general knowledge, coding, studying, explanations, and summarizing short texts.
- You cannot control the computer, browse the web, read files, send messages, or make purchases. \
The app itself handles a fixed set of local commands (opening approved apps and websites, notes, time and date). \
Never claim you performed an action, and never ask the app to run commands.
- If a question needs live or private data you don't have, say so honestly."""

MAX_RESPONSE_CHARS = 6000


class AIServiceError(Exception):
    """An AI failure with a message that is safe and helpful to show the user."""


class AIService:
    def __init__(self, settings: Settings, client_factory: Callable[[Settings], Any] | None = None) -> None:
        self.settings = settings
        self._client_factory = client_factory or self._create_openai_client
        self._client: Any = None
        self._lock = threading.Lock()

    @property
    def is_configured(self) -> bool:
        return self.settings.ai_configured

    def configuration_problem(self) -> str | None:
        if self.settings.ai_provider == "none":
            return "AI answers are turned off (AI_PROVIDER=none). Local commands still work."
        if not self.settings.openai_api_key:
            return (
                "AI answers aren't set up yet: add your OPENAI_API_KEY to the .env file and restart Ultron. "
                "Local commands like time, notes, and opening apps still work. Say 'help' to see them."
            )
        return None

    def update_settings(self, settings: Settings) -> None:
        with self._lock:
            self.settings = settings
            self._client = None

    @staticmethod
    def _create_openai_client(settings: Settings) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise AIServiceError("The 'openai' package isn't installed. Run: pip install -r requirements.txt") from exc
        return OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
            timeout=settings.ai_timeout,
            max_retries=1,
        )

    def _get_client(self) -> Any:
        with self._lock:
            if self._client is None:
                self._client = self._client_factory(self.settings)
            return self._client

    def ask(self, prompt: str, history: list[dict[str, str]] | None = None) -> str:
        problem = self.configuration_problem()
        if problem:
            raise AIServiceError(problem)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages += [m for m in (history or []) if m.get("role") in {"user", "assistant"} and m.get("content")]
        messages.append({"role": "user", "content": prompt[:8000]})

        try:
            response = self._complete(messages)
        except AIServiceError:
            raise
        except Exception as exc:
            raise self._translate(exc) from exc

        try:
            content = response.choices[0].message.content or ""
        except (AttributeError, IndexError, TypeError):
            content = ""
        content = content.strip()
        if not content:
            raise AIServiceError("The AI service returned an empty answer. Please try again.")
        return content[:MAX_RESPONSE_CHARS]

    def _complete(self, messages: list[dict[str, str]]) -> Any:
        client = self._get_client()
        params = {
            "model": self.settings.openai_model,
            "messages": messages,
            "temperature": self.settings.ai_temperature,
            "max_completion_tokens": self.settings.ai_max_tokens,
        }
        try:
            return client.chat.completions.create(**params)
        except Exception as exc:
            # Some models/providers reject optional tuning parameters; retry once with the minimum.
            text = str(exc).lower()
            if type(exc).__name__ == "BadRequestError" and ("temperature" in text or "max_completion_tokens" in text):
                logger.info("Retrying AI request without optional parameters")
                return client.chat.completions.create(model=params["model"], messages=messages)
            raise

    def _translate(self, exc: Exception) -> AIServiceError:
        name = type(exc).__name__
        text = str(exc).lower()
        logger.warning("AI request failed: %s", name)
        status = getattr(exc, "status_code", None)
        if name == "AuthenticationError" or status == 401:
            return AIServiceError("The AI API key was rejected. Check OPENAI_API_KEY in your .env file.")
        if name == "RateLimitError" or status == 429:
            if "quota" in text or "billing" in text:
                return AIServiceError("Your AI account has run out of credits or quota. Check your billing settings.")
            return AIServiceError("The AI service is rate limiting requests. Please wait a moment and try again.")
        if name == "APITimeoutError":
            return AIServiceError("The AI service took too long to respond. Please try again.")
        if name == "APIConnectionError":
            return AIServiceError("I couldn't reach the AI service. Check your internet connection.")
        if name == "NotFoundError" or status == 404:
            return AIServiceError(
                f"The AI model '{self.settings.openai_model}' isn't available. Set a different OPENAI_MODEL in .env."
            )
        if name == "PermissionDeniedError" or status == 403:
            return AIServiceError("Your API key doesn't have permission to use this model.")
        if name == "BadRequestError" or status == 400:
            return AIServiceError("The AI service couldn't process that request. Try rephrasing it.")
        if isinstance(status, int) and status >= 500:
            return AIServiceError("The AI service is having problems right now. Please try again later.")
        return AIServiceError("Something went wrong while contacting the AI service.")
