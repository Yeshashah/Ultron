"""Bounded, thread-safe conversation history."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Message:
    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)


class Conversation:
    def __init__(self, max_messages: int = 20) -> None:
        self._messages: deque[Message] = deque(maxlen=max(2, max_messages))
        self._lock = threading.Lock()

    def add(self, role: str, content: str) -> None:
        if role not in {"user", "assistant"} or not content:
            return
        with self._lock:
            self._messages.append(Message(role, content))

    def messages(self) -> list[Message]:
        with self._lock:
            return list(self._messages)

    def as_chat_messages(self) -> list[dict[str, str]]:
        """History in the ``[{"role": ..., "content": ...}]`` format used by chat APIs."""
        return [{"role": m.role, "content": m.content} for m in self.messages()]

    def clear(self) -> None:
        with self._lock:
            self._messages.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._messages)
