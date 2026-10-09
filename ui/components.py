"""Reusable CustomTkinter widgets for Ultron's interface."""

from __future__ import annotations

from datetime import datetime

import customtkinter as ctk

# (light mode, dark mode) colour pairs
BG = ("#eef2f9", "#0b1220")
PANEL = ("#ffffff", "#111a2e")
PANEL_BORDER = ("#d5deee", "#1e2a44")
ACCENT = ("#2563eb", "#3b82f6")
ACCENT_HOVER = ("#1d4ed8", "#2563eb")
MUTED_BUTTON = ("#cbd5e1", "#1f2a40")
MUTED_BUTTON_HOVER = ("#b6c2d4", "#2a3854")
TEXT = ("#0f172a", "#e2e8f0")
SUBTLE_TEXT = ("#64748b", "#8a9bb8")

STATE_COLORS = {
    "Ready": ("#16a34a", "#22c55e"),
    "Listening": ("#dc2626", "#ef4444"),
    "Processing": ("#d97706", "#f59e0b"),
    "Speaking": ("#2563eb", "#60a5fa"),
    "Error": ("#dc2626", "#f87171"),
}
LISTENING_BUTTON = ("#dc2626", "#dc2626")
LISTENING_BUTTON_HOVER = ("#b91c1c", "#b91c1c")


def pick(color: tuple[str, str]) -> str:
    """Resolve a (light, dark) pair for plain Tk options that don't accept tuples."""
    return color[1] if ctk.get_appearance_mode() == "Dark" else color[0]


class StatusIndicator(ctk.CTkFrame):
    """Coloured dot plus status text, e.g. '● Listening — speak now'."""

    def __init__(self, master, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.dot = ctk.CTkLabel(self, text="●", font=ctk.CTkFont(size=18), text_color=STATE_COLORS["Ready"])
        self.dot.pack(side="left", padx=(0, 6))
        self.label = ctk.CTkLabel(self, text="Ready", font=ctk.CTkFont(size=14, weight="bold"), text_color=TEXT)
        self.label.pack(side="left")
        self.detail = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=13), text_color=SUBTLE_TEXT)
        self.detail.pack(side="left", padx=(8, 0))

    def set(self, state: str, detail: str = "") -> None:
        self.dot.configure(text_color=STATE_COLORS.get(state, STATE_COLORS["Ready"]))
        self.label.configure(text=state)
        self.detail.configure(text=f"— {detail}" if detail else "")


class ChatPanel(ctk.CTkTextbox):
    """Read-only conversation log with distinct styling for each speaker."""

    ROLE_LABELS = {"user": "You", "assistant": "Ultron", "system": "Ultron", "error": "Ultron"}

    def __init__(self, master, **kwargs) -> None:
        super().__init__(
            master,
            wrap="word",
            fg_color=PANEL,
            border_color=PANEL_BORDER,
            border_width=1,
            corner_radius=12,
            text_color=TEXT,
            font=ctk.CTkFont(size=15),
            **kwargs,
        )
        self.configure(state="disabled")
        self.apply_tag_colors()

    def apply_tag_colors(self) -> None:
        self.tag_config("user_header", foreground=pick(("#1d4ed8", "#93c5fd")), spacing1=10)
        self.tag_config("assistant_header", foreground=pick(ACCENT), spacing1=10)
        self.tag_config("system_header", foreground=pick(SUBTLE_TEXT), spacing1=10)
        self.tag_config("error_header", foreground=pick(("#dc2626", "#f87171")), spacing1=10)
        self.tag_config("user", foreground=pick(TEXT), lmargin1=12, lmargin2=12, spacing3=4)
        self.tag_config("assistant", foreground=pick(TEXT), lmargin1=12, lmargin2=12, spacing3=4)
        self.tag_config("system", foreground=pick(SUBTLE_TEXT), lmargin1=12, lmargin2=12, spacing3=4)
        self.tag_config("error", foreground=pick(("#b91c1c", "#fca5a5")), lmargin1=12, lmargin2=12, spacing3=4)

    def add_message(self, role: str, text: str) -> None:
        role = role if role in self.ROLE_LABELS else "system"
        stamp = datetime.now().strftime("%H:%M")
        self.configure(state="normal")
        self.insert("end", f"{self.ROLE_LABELS[role]}  ·  {stamp}\n", f"{role}_header")
        self.insert("end", text.strip() + "\n", role)
        self.configure(state="disabled")
        self.see("end")

    def clear(self) -> None:
        self.configure(state="normal")
        self.delete("1.0", "end")
        self.configure(state="disabled")


class MicButton(ctk.CTkButton):
    """Start/Stop Listening toggle whose colour reflects microphone activity."""

    def __init__(self, master, **kwargs) -> None:
        super().__init__(
            master,
            text="●  Start Listening",
            width=190,
            height=46,
            corner_radius=23,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            **kwargs,
        )
        self.listening = False

    def set_listening(self, listening: bool) -> None:
        self.listening = listening
        if listening:
            self.configure(text="■  Stop Listening", fg_color=LISTENING_BUTTON, hover_color=LISTENING_BUTTON_HOVER)
        else:
            self.configure(text="●  Start Listening", fg_color=ACCENT, hover_color=ACCENT_HOVER)
