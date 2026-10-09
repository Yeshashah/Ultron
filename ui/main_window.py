"""Ultron's main desktop window."""

from __future__ import annotations

import logging
import queue
import threading

import customtkinter as ctk

from config import APP_VERSION, Settings, save_env_values
from core.assistant import ERROR, LISTENING, PROCESSING, READY, Assistant, AssistantEvent
from core.logging_utils import register_secret
from ui.components import (
    ACCENT,
    ACCENT_HOVER,
    BG,
    MUTED_BUTTON,
    MUTED_BUTTON_HOVER,
    SUBTLE_TEXT,
    TEXT,
    ChatPanel,
    MicButton,
    StatusIndicator,
)
from ui.settings_window import SettingsWindow

logger = logging.getLogger("ultron.ui")

POLL_MS = 50
WELCOME = (
    "Hello, I'm Ultron, your personal AI voice assistant. Type a message below or press Start Listening "
    "and speak. Say or type 'help' to see what I can do."
)


class MainWindow(ctk.CTk):
    def __init__(self, assistant: Assistant) -> None:
        super().__init__(fg_color=BG)
        self.assistant = assistant
        self.settings: Settings = assistant.settings
        self._events: queue.Queue[AssistantEvent] = queue.Queue()
        self.assistant.on_event = self._events.put
        self._closing = False
        self._voices: list[dict[str, str]] | None = None
        self._settings_window: SettingsWindow | None = None

        self.title("Ultron — Personal AI Voice Assistant")
        self.geometry("900x720")
        self.minsize(560, 480)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build()
        self._bind_keys()

        self.chat.add_message("assistant", WELCOME)
        threading.Thread(target=self._run_startup_checks, name="ultron-startup", daemon=True).start()
        self.after(POLL_MS, self._poll_events)
        self.after(200, self.entry.focus_set)

    # -- layout -------------------------------------------------------------------

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 0))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="ULTRON", font=ctk.CTkFont(size=38, weight="bold"), text_color=ACCENT).grid(
            row=0, column=0
        )
        ctk.CTkLabel(
            header, text=f"Personal AI Voice Assistant  ·  v{APP_VERSION}", font=ctk.CTkFont(size=13),
            text_color=SUBTLE_TEXT,
        ).grid(row=1, column=0)
        ctk.CTkButton(
            header, text="⚙ Settings", width=100, fg_color=MUTED_BUTTON, hover_color=MUTED_BUTTON_HOVER,
            text_color=TEXT, command=self.open_settings,
        ).grid(row=0, column=0, sticky="e")

        self.status = StatusIndicator(self)
        self.status.grid(row=1, column=0, pady=(8, 8))

        self.chat = ChatPanel(self)
        self.chat.grid(row=2, column=0, sticky="nsew", padx=24)

        input_row = ctk.CTkFrame(self, fg_color="transparent")
        input_row.grid(row=3, column=0, sticky="ew", padx=24, pady=(12, 6))
        input_row.grid_columnconfigure(0, weight=1)
        self.entry = ctk.CTkEntry(
            input_row, height=42, corner_radius=21, font=ctk.CTkFont(size=15),
            placeholder_text="Type a message, e.g. 'what time is it' or 'open YouTube'…",
        )
        self.entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        ctk.CTkButton(
            input_row, text="Send", width=96, height=42, corner_radius=21, font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self.send_text,
        ).grid(row=0, column=1)

        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.grid(row=4, column=0, pady=(6, 18))
        self.mic_button = MicButton(controls, command=self.toggle_listening)
        self.mic_button.grid(row=0, column=0, padx=8)

        secondary = {"fg_color": MUTED_BUTTON, "hover_color": MUTED_BUTTON_HOVER, "text_color": TEXT, "height": 38}
        self.mute_button = ctk.CTkButton(controls, width=110, command=self.toggle_mute, **secondary)
        self.mute_button.grid(row=0, column=1, padx=6)
        ctk.CTkButton(controls, text="Stop Speaking", width=120, command=self.assistant.stop_speaking,
                      **secondary).grid(row=0, column=2, padx=6)
        ctk.CTkButton(controls, text="Clear Conversation", width=150, command=self.clear_conversation,
                      **secondary).grid(row=0, column=3, padx=6)
        self.continuous_switch = ctk.CTkSwitch(controls, text="Continuous", command=self._toggle_continuous)
        if self.assistant.continuous:
            self.continuous_switch.select()
        self.continuous_switch.grid(row=0, column=4, padx=(10, 0))
        self._refresh_mute_button()

    def _bind_keys(self) -> None:
        self.entry.bind("<Return>", lambda _e: self.send_text())
        self.bind("<Escape>", lambda _e: self.assistant.stop_speaking())

    # -- actions ------------------------------------------------------------------------

    def send_text(self) -> None:
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        if self.assistant.submit_text(text) is None:
            self.status.set(ERROR, "Ultron is shutting down")

    def toggle_listening(self) -> None:
        if self.mic_button.listening:
            self.assistant.stop_listening()
        elif self.assistant.start_listening(continuous=bool(self.continuous_switch.get())):
            self.mic_button.set_listening(True)
            self.status.set(LISTENING, "Opening microphone…")

    def _toggle_continuous(self) -> None:
        self.assistant.continuous = bool(self.continuous_switch.get())

    def toggle_mute(self) -> None:
        self.assistant.set_muted(not self.assistant.muted)
        self._refresh_mute_button()

    def _refresh_mute_button(self) -> None:
        self.mute_button.configure(text="Unmute" if self.assistant.muted else "Mute")

    def clear_conversation(self) -> None:
        self.assistant.clear_conversation()
        self.chat.clear()
        self.chat.add_message("system", "Conversation cleared.")

    # -- settings ----------------------------------------------------------------------

    def open_settings(self) -> None:
        if self._settings_window is not None and self._settings_window.winfo_exists():
            self._settings_window.focus()
            return
        self._settings_window = SettingsWindow(self, self.settings, self._voices, self._apply_settings)
        if self._voices is None:
            threading.Thread(target=self._load_voices, name="ultron-voices", daemon=True).start()

    def _load_voices(self) -> None:
        voices = self.assistant.tts.list_voices()
        self._events.put(AssistantEvent("voices", {"voices": voices}))

    def _apply_settings(self, settings: Settings, save: bool, key_changed: bool) -> None:
        if key_changed:
            register_secret(settings.openai_api_key)
        self.settings = settings
        self.assistant.apply_settings(settings)
        if bool(self.continuous_switch.get()) != settings.enable_continuous_mode:
            self.continuous_switch.toggle()
        message = "Settings applied for this session."
        if save:
            values = {
                "AI_PROVIDER": settings.ai_provider,
                "OPENAI_MODEL": settings.openai_model,
                "OPENAI_BASE_URL": settings.openai_base_url,
                "TTS_RATE": str(settings.tts_rate),
                "TTS_VOLUME": str(settings.tts_volume),
                "TTS_VOICE": settings.tts_voice,
                "VOICE_LANGUAGE": settings.voice_language,
                "ENABLE_CONTINUOUS_MODE": str(settings.enable_continuous_mode).lower(),
            }
            if key_changed:
                values["OPENAI_API_KEY"] = settings.openai_api_key
            try:
                save_env_values(values)
                message = "Settings saved to .env."
            except OSError as exc:
                logger.error("Could not save .env: %s", exc)
                message = "Settings applied, but I couldn't write the .env file."
        self.chat.add_message("system", message)
        problem = self.assistant.ai.configuration_problem()
        if problem:
            self.chat.add_message("system", problem)

    # -- background events -----------------------------------------------------------

    def _run_startup_checks(self) -> None:
        for notice in self.assistant.startup_notices():
            self._events.put(AssistantEvent("message", {"role": "system", "text": notice}))

    def _poll_events(self) -> None:
        if self._closing:
            return
        try:
            while True:
                self._handle_event(self._events.get_nowait())
        except queue.Empty:
            pass
        except Exception:
            logger.exception("UI event handling failed")
        self.after(POLL_MS, self._poll_events)

    def _handle_event(self, event: AssistantEvent) -> None:
        data = event.data
        if event.kind == "status":
            state = data.get("state", READY)
            if state == READY and self.mic_button.listening:
                state, detail = LISTENING, "Listening…"
            else:
                detail = data.get("detail", "")
            self.status.set(state, detail)
        elif event.kind == "message":
            self.chat.add_message(data.get("role", "system"), data.get("text", ""))
        elif event.kind == "listening":
            self.mic_button.set_listening(bool(data.get("active")))
        elif event.kind == "clear":
            self.chat.clear()
        elif event.kind == "voices":
            self._voices = data.get("voices") or []
            if self._settings_window is not None and self._settings_window.winfo_exists():
                self._settings_window.set_voices(self._voices)
        elif event.kind == "exit":
            self.after(200, self.on_close)

    def on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.status.set(PROCESSING, "Shutting down…")
        self.update_idletasks()
        try:
            self.assistant.shutdown(timeout=1.0)
        finally:
            self.destroy()
