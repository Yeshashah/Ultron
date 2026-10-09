from datetime import datetime

import pytest

from core.command_handler import CommandHandler
from services.application_service import ApplicationService
from services.notes_service import MAX_NOTE_LENGTH, NotesError, NotesService
from services.web_service import WebService


@pytest.fixture
def notes(tmp_path):
    return NotesService(tmp_path / "data" / "notes.json")


@pytest.fixture
def handler(notes):
    return CommandHandler(
        notes,
        WebService(opener=lambda url: True),
        ApplicationService(platform="win32", launcher=lambda spec: None, exists=lambda spec: False),
    )


# ---- storage -----------------------------------------------------------------


def test_add_and_retrieve_persists(notes, tmp_path):
    first = notes.add("Buy milk")
    second = notes.add("  Call mom at 6  ")
    assert (first.id, second.id) == (1, 2)
    assert second.text == "Call mom at 6"
    datetime.fromisoformat(first.created_at)  # valid timestamp

    reloaded = NotesService(tmp_path / "data" / "notes.json")
    assert [n.text for n in reloaded.list()] == ["Buy milk", "Call mom at 6"]
    assert reloaded.get(2).text == "Call mom at 6"
    assert reloaded.latest().id == 2
    assert reloaded.get(3) is None and reloaded.get(0) is None


def test_empty_note_rejected(notes):
    with pytest.raises(NotesError):
        notes.add("   \n ")


def test_long_note_truncated_and_control_chars_removed(notes):
    note = notes.add("a\x00b" + "x" * (MAX_NOTE_LENGTH + 100))
    assert note.text.startswith("abx") and len(note.text) == MAX_NOTE_LENGTH


def test_delete(notes):
    a = notes.add("one")
    notes.add("two")
    assert notes.delete(a.id) is True
    assert notes.delete(a.id) is False
    assert [n.text for n in notes.list()] == ["two"]
    assert notes.add("three").id == 3


def test_corrupt_file_reports_error(tmp_path):
    path = tmp_path / "notes.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(NotesError):
        NotesService(path).list()


def test_no_notes_file_means_empty(notes):
    assert notes.list() == [] and notes.latest() is None


# ---- voice/text commands -----------------------------------------------------------


@pytest.mark.parametrize(
    "phrase, saved",
    [
        ("take a note buy groceries", "buy groceries"),
        ("Take a note: Buy groceries.", "Buy groceries"),
        ("make a note that the exam is on Monday", "the exam is on Monday"),
        ("note that the wifi password is on the fridge", "the wifi password is on the fridge"),
        ("remember to water the plants", "to water the plants"),
        ("create a new note saying finish the report", "finish the report"),
    ],
)
def test_create_note_commands(handler, notes, phrase, saved):
    result = handler.handle(phrase)
    assert result.command == "create_note"
    assert notes.latest().text == saved


def test_note_follow_up_prompt(handler, notes):
    assert "What should the note say" in handler.handle("take a note").text
    assert handler.has_pending
    result = handler.handle("Pick up the parcel")
    assert "saved" in result.text.lower()
    assert notes.latest().text == "Pick up the parcel"
    assert not handler.has_pending


def test_note_follow_up_cancel(handler, notes):
    handler.handle("take a note")
    assert "won't save" in handler.handle("cancel").text
    assert notes.list() == []


def test_list_and_read_notes(handler, notes):
    assert "don't have any notes" in handler.handle("list my notes").text
    notes.add("first idea")
    notes.add("second idea")

    listing = handler.handle("show my notes")
    assert "You have 2 notes" in listing.text and "1." in listing.text and "second idea" in listing.text

    assert "second idea" in handler.handle("read note 2").text
    assert "first idea" in handler.handle("read the first note").text
    assert "second idea" in handler.handle("read my last note").spoken_text
    assert "couldn't find note 9" in handler.handle("read note 9").text
    all_notes = handler.handle("read my notes")
    assert "first idea" in all_notes.spoken_text and "second idea" in all_notes.spoken_text


def test_delete_requires_confirmation(handler, notes):
    notes.add("keep me")
    notes.add("delete me")

    prompt = handler.handle("delete note 2")
    assert "Say 'yes' to confirm" in prompt.text
    assert len(notes.list()) == 2  # nothing deleted yet

    assert "cancelled" in handler.handle("no").text.lower()
    assert len(notes.list()) == 2

    handler.handle("delete note 2")
    assert handler.handle("yes").text == "Note deleted."
    assert [n.text for n in notes.list()] == ["keep me"]


def test_unrelated_reply_cancels_confirmation(handler, notes):
    notes.add("important")
    handler.handle("delete note 1")
    result = handler.handle("what time is it")
    assert result.command == "time"
    assert handler.handle("yes").handled is False  # a late "yes" no longer deletes anything
    assert len(notes.list()) == 1
