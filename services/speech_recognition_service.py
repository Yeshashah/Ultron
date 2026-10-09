"""Microphone capture and speech-to-text using the SpeechRecognition package.

The microphone is only opened while ``listen_once`` runs (i.e. after the user asks Ultron to listen).
Captured audio is sent to Google's free Web Speech API for transcription and is never stored.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

logger = logging.getLogger("ultron.stt")


class SpeechError(Exception):
    """Base class for speech recognition problems; messages are user-facing."""


class MicrophoneUnavailableError(SpeechError):
    pass


class ListenTimeoutError(SpeechError):
    pass


class NotUnderstoodError(SpeechError):
    pass


class RecognitionServiceError(SpeechError):
    pass


class AlreadyListeningError(SpeechError):
    pass


def _import_sr() -> Any:
    try:
        import speech_recognition as sr
    except ImportError as exc:
        raise MicrophoneUnavailableError(
            "Speech recognition isn't installed. Run: pip install -r requirements.txt"
        ) from exc
    return sr


class SpeechRecognitionService:
    def __init__(
        self,
        language: str = "en-US",
        listen_timeout: float = 6.0,
        phrase_time_limit: float = 15.0,
        recognizer_factory: Callable[[], Any] | None = None,
        microphone_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.language = language
        self.listen_timeout = listen_timeout
        self.phrase_time_limit = phrase_time_limit
        self._recognizer_factory = recognizer_factory
        self._microphone_factory = microphone_factory
        self._lock = threading.Lock()

    @property
    def is_listening(self) -> bool:
        return self._lock.locked()

    def availability_problem(self) -> str | None:
        """Return a user-facing explanation if voice input can't work, else None."""
        if self._microphone_factory is not None:
            return None
        try:
            sr = _import_sr()
        except SpeechError as exc:
            return str(exc)
        try:
            import pyaudio  # noqa: F401
        except ImportError:
            return "Voice input needs PyAudio. Run: pip install pyaudio (typed input still works)."
        try:
            names = sr.Microphone.list_microphone_names()
        except Exception as exc:  # PyAudio raises OSError/IOError variants
            logger.warning("Could not list microphones: %s", exc)
            return "I couldn't access the audio system to find a microphone."
        if not names:
            return "No microphone was found. Connect one and check Windows privacy settings."
        return None

    def _make_recognizer(self, sr: Any) -> Any:
        if self._recognizer_factory:
            return self._recognizer_factory()
        recognizer = sr.Recognizer()
        recognizer.dynamic_energy_threshold = True
        recognizer.pause_threshold = 0.8
        recognizer.operation_timeout = 15
        return recognizer

    def _make_microphone(self, sr: Any) -> Any:
        if self._microphone_factory:
            return self._microphone_factory()
        try:
            return sr.Microphone()
        except (AttributeError, OSError) as exc:
            raise MicrophoneUnavailableError(
                "I couldn't open the microphone. Make sure PyAudio is installed and a microphone is connected."
            ) from exc

    def listen_once(self, stop_event: threading.Event | None = None) -> str:
        """Record one phrase and return its transcription.

        Raises a SpeechError subclass with a user-friendly message on failure.
        Returns an empty string if ``stop_event`` was set while listening.
        """
        if not self._lock.acquire(blocking=False):
            raise AlreadyListeningError("I'm already listening.")
        try:
            sr = _import_sr()
            recognizer = self._make_recognizer(sr)
            microphone = self._make_microphone(sr)
            try:
                with microphone as source:
                    recognizer.adjust_for_ambient_noise(source, duration=0.4)
                    if stop_event is not None and stop_event.is_set():
                        return ""
                    audio = recognizer.listen(
                        source, timeout=self.listen_timeout, phrase_time_limit=self.phrase_time_limit
                    )
            except sr.WaitTimeoutError as exc:
                raise ListenTimeoutError("I didn't hear anything. Try again when you're ready.") from exc
            except (OSError, AttributeError) as exc:
                logger.warning("Microphone error: %s", exc)
                raise MicrophoneUnavailableError(
                    "The microphone isn't available. Check that it's connected and that microphone access "
                    "is allowed in Windows privacy settings."
                ) from exc

            if stop_event is not None and stop_event.is_set():
                return ""
            try:
                text = recognizer.recognize_google(audio, language=self.language)
            except sr.UnknownValueError as exc:
                raise NotUnderstoodError("Sorry, I couldn't understand that. Please try again.") from exc
            except sr.RequestError as exc:
                logger.warning("Speech service error: %s", exc)
                raise RecognitionServiceError(
                    "The speech recognition service is unreachable. Check your internet connection or type instead."
                ) from exc
            text = (text or "").strip() if isinstance(text, str) else ""
            if not text:
                raise NotUnderstoodError("I didn't catch any words. Please try again.")
            return text
        finally:
            self._lock.release()
