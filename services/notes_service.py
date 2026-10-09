"""Local note storage in a JSON file inside the configured data directory."""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("ultron.notes")

MAX_NOTE_LENGTH = 2000


class NotesError(Exception):
    """Raised when notes cannot be read or written."""


@dataclass(frozen=True)
class Note:
    id: int
    text: str
    created_at: str  # ISO 8601, local time

    @property
    def created_display(self) -> str:
        try:
            return datetime.fromisoformat(self.created_at).strftime("%d %b %Y, %I:%M %p")
        except ValueError:
            return self.created_at


def _clean(text: str) -> str:
    text = "".join(ch for ch in text if ch.isprintable() or ch in "\n\t")
    return text.strip()[:MAX_NOTE_LENGTH]


class NotesService:
    def __init__(self, notes_file: Path) -> None:
        self.notes_file = Path(notes_file)
        self._lock = threading.Lock()

    def _load(self) -> list[Note]:
        if not self.notes_file.exists():
            return []
        try:
            raw = json.loads(self.notes_file.read_text(encoding="utf-8"))
            return [Note(int(n["id"]), str(n["text"]), str(n["created_at"])) for n in raw.get("notes", [])]
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            logger.error("Could not read notes file: %s", exc)
            raise NotesError("I couldn't read your notes file. It may be damaged.") from exc

    def _save(self, notes: list[Note]) -> None:
        try:
            self.notes_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.notes_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"notes": [asdict(n) for n in notes]}, indent=2), encoding="utf-8")
            os.replace(tmp, self.notes_file)
        except OSError as exc:
            logger.error("Could not save notes: %s", exc)
            raise NotesError("I couldn't save your notes. Check that the data folder is writable.") from exc

    def add(self, text: str) -> Note:
        text = _clean(text)
        if not text:
            raise NotesError("The note is empty, so I didn't save it.")
        with self._lock:
            notes = self._load()
            next_id = max((n.id for n in notes), default=0) + 1
            note = Note(next_id, text, datetime.now().isoformat(timespec="seconds"))
            notes.append(note)
            self._save(notes)
        logger.info("Saved note #%d (%d chars)", note.id, len(text))
        return note

    def list(self) -> list[Note]:
        with self._lock:
            return self._load()

    def get(self, position: int) -> Note | None:
        """Return the note at a 1-based position in the list (as shown to the user)."""
        notes = self.list()
        if 1 <= position <= len(notes):
            return notes[position - 1]
        return None

    def latest(self) -> Note | None:
        notes = self.list()
        return notes[-1] if notes else None

    def delete(self, note_id: int) -> bool:
        with self._lock:
            notes = self._load()
            kept = [n for n in notes if n.id != note_id]
            if len(kept) == len(notes):
                return False
            self._save(kept)
        logger.info("Deleted note #%d", note_id)
        return True
