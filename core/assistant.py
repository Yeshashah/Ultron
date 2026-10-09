"""Ultron's brain: routes input through local commands or the AI, then speaks the reply.

All blocking work runs on background threads. The UI receives updates through the ``on_event``
callback, which may be called from any thread (the UI must marshal them onto its own thread).
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from config import Settings
from core.command_handler import (
    CLEAR_ACTION,
    EXIT_ACTION,
    STOP_SPEAKING_ACTION,
    CommandHandler,
)
from core.conversation import Conversation
from services.ai_service import AIService, AIServiceError
from services.application_service import ApplicationService
from services.notes_service import NotesService
from services.speech_recognition_service import (
    ListenTimeoutError,
    NotUnderstoodError,
    SpeechError,
    SpeechRecognitionService,
)
from services.text_to_speech_service import TextToSpeechService, TTSError
from services.web_service import WebService

logger = logging.getLogger("ultron.assistant")

MAX_INPUT_CHARS = 4000

READY = "Ready"
LISTENING = "Listening"
PROCESSING = "Processing"
SPEAKING = "Speaking"
ERROR = "Error"


@dataclass
class AssistantEvent:
    kind: str  # "status", "message", "listening", "clear", "exit"
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Reply:
    text: str
    speech: str
    action: str | None = None
    is_error: bool = False
    source: str = "command"  # "command" or "ai"


class Assistant:
    def __init__(
        self,
        settings: Settings,
        *,
        ai: AIService | None = None,
        stt: SpeechRecognitionService | None = None,
        tts: TextToSpeechService | None = None,
        notes: NotesService | None = None,
        web: WebService | None = None,
        apps: ApplicationService | None = None,
        on_event: Callable[[AssistantEvent], None] | None = None,
    ) -> None:
        self.settings = settings
        self.ai = ai or AIService(settings)
        self.stt = stt or SpeechRecognitionService(
            settings.speech_language, settings.listen_timeout, settings.phrase_time_limit
        )
        self.tts = tts or TextToSpeechService(
            settings.tts_rate, settings.tts_volume, settings.tts_voice, muted=settings.start_muted
        )
        self.notes = notes or NotesService(settings.notes_file)
        self.web = web or WebService(settings.web_search_provider)
        self.apps = apps or ApplicationService(settings.custom_apps)
        self.commands = CommandHandler(self.notes, self.web, self.apps)
        self.conversation = Conversation(settings.max_history_messages)
        self.on_event = on_event or (lambda event: None)

        self.continuous = settings.enable_continuous_mode
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ultron-worker")
        self._listen_thread: threading.Thread | None = None
        self._listen_lock = threading.Lock()
        self._stop_listening = threading.Event()
        self._shutting_down = threading.Event()

    # -- events ------------------------------------------------------------------

    def _emit(self, kind: str, **data: Any) -> None:
        try:
            self.on_event(AssistantEvent(kind, data))
        except Exception:
            logger.exception("Event handler failed for %s", kind)

    def _status(self, state: str, detail: str = "") -> None:
        self._emit("status", state=state, detail=detail)

    # -- properties ----------------------------------------------------------------

    @property
    def is_listening(self) -> bool:
        thread = self._listen_thread
        return bool(thread and thread.is_alive())

    @property
    def muted(self) -> bool:
        return self.tts.muted

    # -- core flow -------------------------------------------------------------------

    def respond(self, text: str) -> Reply:
        """Compute Ultron's reply to ``text`` without speaking it."""
        result = self.commands.handle(text)
        if result.handled:
            reply = Reply(result.text, result.spoken_text, result.action, source="command")
        else:
            try:
                answer = self.ai.ask(text, history=self.conversation.as_chat_messages())
                reply = Reply(answer, answer, source="ai")
            except AIServiceError as exc:
                reply = Reply(str(exc), str(exc), is_error=True, source="ai")
        if reply.action == CLEAR_ACTION:
            self.conversation.clear()
        elif not reply.is_error:
            self.conversation.add("user", text)
            self.conversation.add("assistant", reply.text)
        return reply

    def process_text(self, text: str, source: str = "typed") -> str | None:
        """Full interaction for one input: display, process, respond, speak. Returns the reply action."""
        text = (text or "").strip()[:MAX_INPUT_CHARS]
        if not text or self._shutting_down.is_set():
            return None
        self._emit("message", role="user", text=text, source=source)
        self._status(PROCESSING, "Thinking…")
        reply = self.respond(text)

        if reply.action == CLEAR_ACTION:
            self._emit("clear")
        if reply.text:
            self._emit("message", role="error" if reply.is_error else "assistant", text=reply.text)
        if reply.action == STOP_SPEAKING_ACTION:
            self.tts.stop()

        if reply.speech and not self.tts.muted and not self._shutting_down.is_set():
            self._speak(reply.speech, max_seconds=6 if reply.action == EXIT_ACTION else 300)

        if reply.action == EXIT_ACTION:
            self._emit("exit")
        elif reply.is_error:
            self._status(ERROR, "AI unavailable — local commands still work")
        else:
            self._status(READY)
        return reply.action

    def _speak(self, text: str, max_seconds: float = 300) -> None:
        self._status(SPEAKING, "Speaking… (say or type 'stop' to interrupt)")
        try:
            self.tts.speak(text, max_seconds=max_seconds)
        except TTSError as exc:
            self._emit("message", role="error", text=f"{exc} Replies will still appear on screen.")

    def _safe_process(self, text: str, source: str) -> str | None:
        try:
            return self.process_text(text, source)
        except Exception:
            logger.exception("Unexpected error while processing input")
            self._emit("message", role="error", text="Sorry, something unexpected went wrong. Please try again.")
            self._status(ERROR, "Unexpected error — see logs/ultron.log")
            return None

    def submit_text(self, text: str, source: str = "typed") -> Future | None:
        """Process typed input on the background worker. Interrupts any current speech."""
        if self._shutting_down.is_set() or not (text or "").strip():
            return None
        self.tts.stop()
        try:
            return self._executor.submit(self._safe_process, text, source)
        except RuntimeError:  # executor already shut down
            return None

    # -- listening --------------------------------------------------------------------

    def start_listening(self, continuous: bool | None = None) -> bool:
        """Start listening on a background thread. Returns False if already listening."""
        if self._shutting_down.is_set():
            return False
        with self._listen_lock:
            if self.is_listening:
                return False
            if continuous is not None:
                self.continuous = continuous
            self._stop_listening.clear()
            self._listen_thread = threading.Thread(target=self._listen_loop, name="ultron-listener", daemon=True)
            self._listen_thread.start()
        return True

    def stop_listening(self) -> None:
        if self.is_listening:
            self._stop_listening.set()
            self._status(LISTENING, "Stopping after the current listening window…")

    def _listen_loop(self) -> None:
        self.tts.stop()
        self._emit("listening", active=True, continuous=self.continuous)
        try:
            problem = self.stt.availability_problem()
            if problem:
                self._emit("message", role="error", text=problem)
                self._status(ERROR, "Microphone unavailable — type instead")
                return
            while not self._stop_listening.is_set() and not self._shutting_down.is_set():
                self._status(LISTENING, "Listening… speak now")
                try:
                    heard = self.stt.listen_once(self._stop_listening)
                except ListenTimeoutError as exc:
                    if self.continuous:
                        continue
                    self._emit("message", role="system", text=str(exc))
                    break
                except NotUnderstoodError as exc:
                    self._emit("message", role="system", text=str(exc))
                    if self.continuous:
                        continue
                    break
                except SpeechError as exc:
                    self._emit("message", role="error", text=str(exc))
                    self._status(ERROR, "Voice input problem — you can type instead")
                    return
                if self._stop_listening.is_set() or not heard:
                    break
                try:
                    action = self._executor.submit(self._safe_process, heard, "voice").result()
                except RuntimeError:
                    break
                if action == EXIT_ACTION or not self.continuous:
                    break
        except Exception:
            logger.exception("Listening loop crashed")
            self._emit("message", role="error", text="Voice input stopped because of an unexpected error.")
        finally:
            self._emit("listening", active=False, continuous=self.continuous)
            if not self._shutting_down.is_set() and not self.tts.is_speaking:
                self._status(READY)

    # -- controls -----------------------------------------------------------------------

    def stop_speaking(self) -> None:
        self.tts.stop()

    def set_muted(self, muted: bool) -> None:
        self.tts.muted = muted

    def clear_conversation(self) -> None:
        self.conversation.clear()
        self.commands.cancel_pending()

    def apply_settings(self, settings: Settings) -> None:
        self.settings = settings
        self.ai.update_settings(settings)
        self.tts.rate, self.tts.volume, self.tts.voice = settings.tts_rate, settings.tts_volume, settings.tts_voice
        self.stt.language = settings.speech_language
        self.stt.listen_timeout = settings.listen_timeout
        self.stt.phrase_time_limit = settings.phrase_time_limit
        self.continuous = settings.enable_continuous_mode

    def startup_notices(self) -> list[str]:
        """User-facing warnings about features that won't work in this environment."""
        notices = []
        problem = self.ai.configuration_problem()
        if problem:
            notices.append(problem)
        for check in (self.stt.availability_problem, self.tts.availability_problem):
            try:
                problem = check()
            except Exception as exc:  # never let a diagnostic crash startup
                logger.warning("Startup check failed: %s", exc)
                problem = None
            if problem:
                notices.append(problem)
        return notices

    def shutdown(self, timeout: float = 2.0) -> None:
        if self._shutting_down.is_set():
            return
        logger.info("Shutting down")
        self._shutting_down.set()
        self._stop_listening.set()
        self.tts.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
        thread = self._listen_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout)
