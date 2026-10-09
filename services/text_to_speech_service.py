"""Interruptible text-to-speech built on pyttsx3 (SAPI5 voices on Windows)."""

from __future__ import annotations

import importlib.util
import json
import logging
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("ultron.tts")

WORKER_SCRIPT = Path(__file__).with_name("tts_worker.py")
MAX_SPOKEN_CHARS = 1500


class TTSError(Exception):
    pass


def prepare_for_speech(text: str) -> str:
    """Make on-screen text suitable for reading aloud (no code blocks or markdown symbols)."""
    text = re.sub(r"```.*?(```|$)", " I've shown the code on screen. ", text, flags=re.DOTALL)
    text = re.sub(r"https?://\S+", "the link shown on screen", text)
    text = re.sub(r"[`*_#>|]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_SPOKEN_CHARS:
        cut = text[:MAX_SPOKEN_CHARS]
        text = cut[: cut.rfind(". ") + 1 or len(cut)] + " The rest is on screen."
    return text


def _popen(args: list[str]) -> subprocess.Popen:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.Popen(
        args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=flags,
    )


class TextToSpeechService:
    def __init__(
        self,
        rate: int = 175,
        volume: float = 0.9,
        voice: str = "",
        muted: bool = False,
        process_factory: Callable[[list[str]], Any] | None = None,
    ) -> None:
        self.rate = rate
        self.volume = volume
        self.voice = voice
        self._muted = muted
        self._popen = process_factory or _popen
        self._speak_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._current_stop: threading.Event | None = None

    @property
    def muted(self) -> bool:
        return self._muted

    @muted.setter
    def muted(self, value: bool) -> None:
        self._muted = bool(value)
        if self._muted:
            self.stop()

    @property
    def is_speaking(self) -> bool:
        return self._speak_lock.locked()

    def availability_problem(self) -> str | None:
        if importlib.util.find_spec("pyttsx3") is None:
            return "Spoken replies need pyttsx3. Run: pip install -r requirements.txt"
        return None

    def _command(self) -> list[str]:
        return [sys.executable, str(WORKER_SCRIPT)]

    def speak(self, text: str, max_seconds: float = 300.0) -> bool:
        """Speak ``text`` and block until done. Returns False if muted, empty, or interrupted."""
        spoken = prepare_for_speech(text)
        if self._muted or not spoken:
            return False
        stop = threading.Event()
        with self._speak_lock:
            with self._state_lock:
                self._current_stop = stop
            try:
                return self._run_worker(spoken, stop, max_seconds)
            finally:
                with self._state_lock:
                    self._current_stop = None

    def _run_worker(self, text: str, stop: threading.Event, max_seconds: float) -> bool:
        request = {"mode": "speak", "text": text, "rate": self.rate, "volume": self.volume, "voice": self.voice}
        try:
            proc = self._popen(self._command())
            proc.stdin.write(json.dumps(request).encode("utf-8"))
            proc.stdin.close()
        except OSError as exc:
            logger.error("Could not start speech engine: %s", exc)
            raise TTSError("I couldn't start the speech engine.") from exc

        deadline = time.monotonic() + max_seconds
        while proc.poll() is None:
            if stop.is_set() or self._muted or time.monotonic() > deadline:
                self._terminate(proc)
                return False
            time.sleep(0.05)

        if proc.returncode != 0:
            detail = proc.stderr.read().decode("utf-8", "replace")[-500:] if proc.stderr else ""
            logger.error("Speech engine exited with code %s: %s", proc.returncode, detail.strip())
            raise TTSError("The speech engine failed. Check your audio output device and voice settings.")
        return not stop.is_set()

    @staticmethod
    def _terminate(proc: Any) -> None:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def stop(self) -> None:
        with self._state_lock:
            if self._current_stop is not None:
                self._current_stop.set()

    def list_voices(self, timeout: float = 20.0) -> list[dict[str, str]]:
        """Return installed voices as ``[{"id": ..., "name": ...}]`` (empty list on failure)."""
        proc = None
        try:
            proc = self._popen(self._command())
            out, err = proc.communicate(json.dumps({"mode": "voices"}).encode("utf-8"), timeout=timeout)
            if proc.returncode == 0:
                voices = json.loads(out.decode("utf-8", "replace") or "[]")
                return [v for v in voices if isinstance(v, dict) and "id" in v and "name" in v]
            logger.warning("Voice listing failed: %s", err.decode("utf-8", "replace")[-300:])
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            logger.warning("Voice listing failed: %s", exc)
            if proc is not None:
                self._terminate(proc)
        return []
