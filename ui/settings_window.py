"""Settings panel for AI and voice preferences."""

from __future__ import annotations

import dataclasses
from typing import Callable

import customtkinter as ctk

from config import SUPPORTED_AI_PROVIDERS, Settings
from ui.components import ACCENT, ACCENT_HOVER, MUTED_BUTTON, MUTED_BUTTON_HOVER, SUBTLE_TEXT

DEFAULT_VOICE_LABEL = "System default"


class SettingsWindow(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        settings: Settings,
        voices: list[dict[str, str]] | None,
        on_apply: Callable[[Settings, bool, bool], None],
    ) -> None:
        """``on_apply(new_settings, save_to_env, api_key_changed)`` is called when the user applies changes."""
        super().__init__(master)
        self.title("Ultron Settings")
        self.geometry("520x640")
        self.minsize(460, 560)
        self.settings = settings
        self.on_apply = on_apply
        self._voice_ids: dict[str, str] = {}
        self.transient(master)
        self.after(100, self.lift)

        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=(16, 8))
        body.grid_columnconfigure(1, weight=1)
        row = 0

        def section(title: str) -> None:
            nonlocal row
            ctk.CTkLabel(body, text=title, font=ctk.CTkFont(size=17, weight="bold"), text_color=ACCENT).grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(12, 6)
            )
            row += 1

        def field(label: str, widget) -> None:
            nonlocal row
            ctk.CTkLabel(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=6)
            widget.grid(row=row, column=1, sticky="ew", pady=6)
            row += 1

        section("AI")
        self.provider = ctk.CTkOptionMenu(body, values=list(SUPPORTED_AI_PROVIDERS))
        self.provider.set(settings.ai_provider)
        field("Provider", self.provider)

        self.model = ctk.CTkEntry(body)
        self.model.insert(0, settings.openai_model)
        field("Model", self.model)

        key_hint = "Key is set — leave blank to keep it" if settings.openai_api_key else "Not set — paste your API key"
        self.api_key = ctk.CTkEntry(body, show="•", placeholder_text=key_hint)
        field("API key", self.api_key)

        self.base_url = ctk.CTkEntry(body, placeholder_text="Optional, for OpenAI-compatible services")
        if settings.openai_base_url:
            self.base_url.insert(0, settings.openai_base_url)
        field("Base URL", self.base_url)

        section("Voice")
        self.rate_label = ctk.CTkLabel(body, text="")
        self.rate = ctk.CTkSlider(body, from_=80, to=300, number_of_steps=44, command=self._update_labels)
        self.rate.set(settings.tts_rate)
        field("Speech rate", self.rate)
        self.rate_label.grid(row=row, column=1, sticky="e")
        row += 1

        self.volume_label = ctk.CTkLabel(body, text="")
        self.volume = ctk.CTkSlider(body, from_=0, to=1, number_of_steps=20, command=self._update_labels)
        self.volume.set(settings.tts_volume)
        field("Volume", self.volume)
        self.volume_label.grid(row=row, column=1, sticky="e")
        row += 1

        self.voice = ctk.CTkOptionMenu(body, values=[DEFAULT_VOICE_LABEL])
        self.voice.set(DEFAULT_VOICE_LABEL)
        field("Voice", self.voice)
        self.set_voices(voices)

        self.language = ctk.CTkEntry(body, placeholder_text="e.g. en, en-GB, hi")
        self.language.insert(0, settings.voice_language)
        field("Recognition language", self.language)

        self.continuous = ctk.CTkSwitch(body, text="Keep listening after each reply")
        if settings.enable_continuous_mode:
            self.continuous.select()
        field("Continuous mode", self.continuous)

        ctk.CTkLabel(
            body,
            text="Privacy: the microphone is only used while Ultron shows 'Listening'. Recorded speech is sent "
            "to Google's speech service for transcription and is never saved.",
            wraplength=420,
            justify="left",
            text_color=SUBTLE_TEXT,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(16, 0))
        row += 1

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=16, pady=(0, 16))
        ctk.CTkButton(buttons, text="Cancel", fg_color=MUTED_BUTTON, hover_color=MUTED_BUTTON_HOVER,
                      text_color=("#0f172a", "#e2e8f0"), width=90, command=self.destroy).pack(side="right")
        ctk.CTkButton(buttons, text="Save to .env", fg_color=ACCENT, hover_color=ACCENT_HOVER, width=120,
                      command=lambda: self._apply(save=True)).pack(side="right", padx=8)
        ctk.CTkButton(buttons, text="Apply", fg_color=ACCENT, hover_color=ACCENT_HOVER, width=90,
                      command=lambda: self._apply(save=False)).pack(side="right")
        self._update_labels()

    def _update_labels(self, _value=None) -> None:
        self.rate_label.configure(text=f"{int(self.rate.get())} words/min")
        self.volume_label.configure(text=f"{int(self.volume.get() * 100)}%")

    def set_voices(self, voices: list[dict[str, str]] | None) -> None:
        if voices is None:
            self.voice.configure(values=["Loading voices…"])
            self.voice.set("Loading voices…")
            return
        self._voice_ids = {v["name"]: v["id"] for v in voices}
        names = [DEFAULT_VOICE_LABEL] + list(self._voice_ids)
        self.voice.configure(values=names)
        current = self.settings.tts_voice
        selected = next(
            (n for n, i in self._voice_ids.items() if current and (current == i or current.lower() in n.lower())),
            DEFAULT_VOICE_LABEL,
        )
        self.voice.set(selected)

    def _apply(self, save: bool) -> None:
        voice_name = self.voice.get()
        voice = self._voice_ids.get(voice_name, "") if voice_name != DEFAULT_VOICE_LABEL else ""
        if voice_name not in self._voice_ids and voice_name != DEFAULT_VOICE_LABEL:
            voice = self.settings.tts_voice  # voices still loading: keep the current choice
        new_key = self.api_key.get().strip()
        updated = dataclasses.replace(
            self.settings,
            ai_provider=self.provider.get(),
            openai_model=self.model.get().strip() or self.settings.openai_model,
            openai_api_key=new_key or self.settings.openai_api_key,
            openai_base_url=self.base_url.get().strip(),
            tts_rate=int(self.rate.get()),
            tts_volume=round(float(self.volume.get()), 2),
            tts_voice=voice,
            voice_language=self.language.get().strip() or "en",
            enable_continuous_mode=bool(self.continuous.get()),
        )
        self.on_apply(updated, save, bool(new_key))
        self.destroy()
