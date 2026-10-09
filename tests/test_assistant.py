import dataclasses
import logging
import threading
from types import SimpleNamespace

import pytest

from config import load_settings, parse_custom_apps, save_env_values
from core.assistant import READY, Assistant
from core.conversation import Conversation
from core.logging_utils import REDACTED, RedactingFilter, redact
from services.ai_service import SYSTEM_PROMPT, AIService, AIServiceError
from services.application_service import ApplicationService
from services.speech_recognition_service import (
    AlreadyListeningError,
    ListenTimeoutError,
    MicrophoneUnavailableError,
    NotUnderstoodError,
    RecognitionServiceError,
    SpeechRecognitionService,
)
from services.text_to_speech_service import TextToSpeechService, prepare_for_speech
from services.web_service import WebService

# ---- fakes -------------------------------------------------------------------------


class FakeCompletions:
    def __init__(self, reply="Paris is the capital of France.", error=None):
        self.reply, self.error, self.calls = reply, error, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))])


def fake_client(completions):
    return lambda settings: SimpleNamespace(chat=SimpleNamespace(completions=completions))


def named_error(name, message="error", status=None):
    exc = type(name, (Exception,), {})(message)
    exc.status_code = status
    return exc


class FakeTTS:
    def __init__(self):
        self.spoken, self.muted, self.stops, self.is_speaking = [], False, 0, False

    def speak(self, text, max_seconds=300):
        if not self.muted:
            self.spoken.append(text)
        return not self.muted

    def stop(self):
        self.stops += 1

    def availability_problem(self):
        return None

    def list_voices(self):
        return []


class FakeSTT:
    """Plays back a script of transcriptions or exceptions instead of using a microphone."""

    def __init__(self, script=(), problem=None, gate=None):
        self.script, self.problem, self.gate = list(script), problem, gate
        self.language = "en-US"
        self.listen_timeout = self.phrase_time_limit = 1

    def availability_problem(self):
        return self.problem

    def listen_once(self, stop_event=None):
        if self.gate:
            self.gate.wait(2)
        item = self.script.pop(0) if self.script else ListenTimeoutError("I didn't hear anything.")
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def events():
    return []


def make_assistant(settings, events, ai_completions=None, stt=None, opened=None):
    tts = FakeTTS()
    ai = AIService(settings, client_factory=fake_client(ai_completions or FakeCompletions()))
    assistant = Assistant(
        settings,
        ai=ai,
        stt=stt or FakeSTT(),
        tts=tts,
        web=WebService(opener=(opened.append if opened is not None else lambda url: True)),
        apps=ApplicationService(platform="win32", launcher=lambda spec: None, exists=lambda spec: True),
        on_event=events.append,
    )
    return assistant, tts


def messages(events, role=None):
    return [e.data for e in events if e.kind == "message" and (role is None or e.data["role"] == role)]


# ---- routing & AI --------------------------------------------------------------------


def test_local_commands_work_without_api_key(settings, events):
    assistant, tts = make_assistant(settings, events)
    reply = assistant.respond("what time is it")
    assert reply.source == "command" and reply.text.startswith("It's")


def test_missing_credentials_gives_helpful_error(settings, events):
    completions = FakeCompletions()
    assistant, _ = make_assistant(settings, events, completions)
    reply = assistant.respond("explain photosynthesis")
    assert reply.is_error and "OPENAI_API_KEY" in reply.text
    assert completions.calls == []  # no request attempted without a key


def test_ai_disabled_provider(settings, events):
    assistant, _ = make_assistant(dataclasses.replace(settings, ai_provider="none", openai_api_key="sk-x"), events)
    assert "AI_PROVIDER=none" in assistant.respond("hello there, explain gravity").text


def test_unknown_input_routes_to_ai_with_history(settings, events):
    completions = FakeCompletions()
    configured = dataclasses.replace(settings, openai_api_key="sk-test-123456789", openai_model="test-model")
    assistant, _ = make_assistant(configured, events, completions)

    first = assistant.respond("What is the capital of France?")
    assert first.source == "ai" and first.text == "Paris is the capital of France."
    call = completions.calls[0]
    assert call["model"] == "test-model"
    assert call["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert "Ultron" in SYSTEM_PROMPT

    assistant.respond("And of Germany?")
    sent = completions.calls[1]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[1]["content"] == "What is the capital of France?"


def test_history_is_limited():
    convo = Conversation(max_messages=4)
    for i in range(10):
        convo.add("user", f"m{i}")
    assert [m["content"] for m in convo.as_chat_messages()] == ["m6", "m7", "m8", "m9"]


@pytest.mark.parametrize(
    "error, expected",
    [
        (named_error("AuthenticationError", status=401), "rejected"),
        (named_error("RateLimitError", "Rate limit reached", 429), "rate limiting"),
        (named_error("RateLimitError", "You exceeded your current quota", 429), "credits or quota"),
        (named_error("APITimeoutError"), "too long"),
        (named_error("APIConnectionError"), "internet connection"),
        (named_error("NotFoundError", status=404), "isn't available"),
        (named_error("InternalServerError", status=503), "having problems"),
        (RuntimeError("something odd"), "Something went wrong"),
    ],
)
def test_ai_failures_are_translated(settings, events, error, expected):
    configured = dataclasses.replace(settings, openai_api_key="sk-test-123456789")
    assistant, _ = make_assistant(configured, events, FakeCompletions(error=error))
    reply = assistant.respond("tell me about black holes")
    assert reply.is_error and expected in reply.text
    assert "sk-test" not in reply.text


def test_real_openai_connection_error_is_translated(settings):
    openai = pytest.importorskip("openai")
    try:
        import httpx2 as httpx  # openai >= 3 uses httpx2
    except ImportError:
        httpx = pytest.importorskip("httpx")
    error = openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1/chat"))
    configured = dataclasses.replace(settings, openai_api_key="sk-test-123456789")
    service = AIService(configured, client_factory=fake_client(FakeCompletions(error=error)))
    with pytest.raises(AIServiceError, match="internet connection"):
        service.ask("hi")


def test_empty_ai_answer(settings):
    configured = dataclasses.replace(settings, openai_api_key="sk-test-123456789")
    service = AIService(configured, client_factory=fake_client(FakeCompletions(reply="  ")))
    with pytest.raises(AIServiceError, match="empty"):
        service.ask("hi")


# ---- full interaction flow ------------------------------------------------------------


def test_process_text_displays_and_speaks(settings, events):
    assistant, tts = make_assistant(settings, events)
    assistant.process_text("who are you")
    assert messages(events, "user")[0]["text"] == "who are you"
    assert "Ultron" in messages(events, "assistant")[0]["text"]
    assert tts.spoken and "Ultron" in tts.spoken[0]
    states = [e.data["state"] for e in events if e.kind == "status"]
    assert states == ["Processing", "Speaking", READY]


def test_muted_assistant_does_not_speak(settings, events):
    assistant, tts = make_assistant(settings, events)
    assistant.set_muted(True)
    assistant.process_text("hello")
    assert tts.spoken == [] and messages(events, "assistant")


def test_exit_and_clear_actions(settings, events):
    assistant, _ = make_assistant(settings, events)
    assistant.respond("hello")
    assert len(assistant.conversation) == 2
    assistant.process_text("clear conversation")
    assert len(assistant.conversation) == 0 and any(e.kind == "clear" for e in events)
    assert assistant.process_text("exit") == "exit"
    assert events[-1].kind == "exit"


def test_submit_text_runs_in_background_and_interrupts_speech(settings, events):
    assistant, tts = make_assistant(settings, events)
    future = assistant.submit_text("what's the date")
    future.result(timeout=5)
    assert tts.stops >= 1
    assert messages(events, "assistant")[0]["text"].startswith("Today is")
    assistant.shutdown()


def test_voice_flow_single_shot(settings, events):
    assistant, tts = make_assistant(settings, events, stt=FakeSTT(["what time is it"]))
    assert assistant.start_listening(continuous=False)
    assistant._listen_thread.join(5)
    user = messages(events, "user")
    assert user[0]["text"] == "what time is it" and user[0]["source"] == "voice"
    assert tts.spoken[0].startswith("It's")
    listening = [e.data["active"] for e in events if e.kind == "listening"]
    assert listening == [True, False]


def test_voice_continuous_mode_keeps_listening(settings, events):
    stt = FakeSTT(["hello", "how are you", MicrophoneUnavailableError("The microphone isn't available.")])
    assistant, tts = make_assistant(settings, events, stt=stt)
    assistant.start_listening(continuous=True)
    assistant._listen_thread.join(5)
    assert [m["text"] for m in messages(events, "user")] == ["hello", "how are you"]
    assert "microphone" in messages(events, "error")[0]["text"].lower()


@pytest.mark.parametrize(
    "error, role",
    [
        (ListenTimeoutError("I didn't hear anything."), "system"),
        (NotUnderstoodError("Sorry, I couldn't understand that."), "system"),
        (RecognitionServiceError("speech service unreachable"), "error"),
    ],
)
def test_voice_errors_are_reported_not_raised(settings, events, error, role):
    assistant, _ = make_assistant(settings, events, stt=FakeSTT([error]))
    assistant.start_listening(continuous=False)
    assistant._listen_thread.join(5)
    assert messages(events, role)[0]["text"] == str(error)
    assert messages(events, "user") == []


def test_microphone_unavailable_at_start(settings, events):
    stt = FakeSTT(problem="Voice input needs PyAudio.")
    assistant, _ = make_assistant(settings, events, stt=stt)
    assistant.start_listening()
    assistant._listen_thread.join(5)
    assert "PyAudio" in messages(events, "error")[0]["text"]


def test_duplicate_listening_prevented(settings, events):
    gate = threading.Event()
    assistant, _ = make_assistant(settings, events, stt=FakeSTT(["hello"], gate=gate))
    assert assistant.start_listening() is True
    assert assistant.start_listening() is False
    gate.set()
    assistant._listen_thread.join(5)
    assert not assistant.is_listening


def test_shutdown_is_graceful(settings, events):
    gate = threading.Event()
    assistant, _ = make_assistant(settings, events, stt=FakeSTT(["hello"], gate=gate))
    assistant.start_listening()
    assistant.shutdown(timeout=0.2)
    gate.set()
    assistant._listen_thread.join(5)
    assert assistant.submit_text("hello") is None
    assert assistant.start_listening() is False
    assistant.shutdown()  # idempotent


def test_startup_notices_report_missing_key(settings, events):
    assistant, _ = make_assistant(settings, events)
    assert any("OPENAI_API_KEY" in n for n in assistant.startup_notices())


# ---- speech services (no hardware) ---------------------------------------------------


class FakeMic:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeRecognizer:
    def __init__(self, listen_error=None, recognize_result="open youtube", recognize_error=None):
        self.listen_error, self.result, self.recognize_error = listen_error, recognize_result, recognize_error

    def adjust_for_ambient_noise(self, source, duration=1):
        pass

    def listen(self, source, timeout=None, phrase_time_limit=None):
        if self.listen_error:
            raise self.listen_error
        return b"audio"

    def recognize_google(self, audio, language="en-US"):
        if self.recognize_error:
            raise self.recognize_error
        return self.result


def stt_with(recognizer):
    return SpeechRecognitionService(recognizer_factory=lambda: recognizer, microphone_factory=FakeMic)


def test_speech_recognition_success_and_errors():
    sr = pytest.importorskip("speech_recognition")
    assert stt_with(FakeRecognizer()).listen_once() == "open youtube"
    with pytest.raises(ListenTimeoutError):
        stt_with(FakeRecognizer(listen_error=sr.WaitTimeoutError("timeout"))).listen_once()
    with pytest.raises(NotUnderstoodError):
        stt_with(FakeRecognizer(recognize_error=sr.UnknownValueError())).listen_once()
    with pytest.raises(NotUnderstoodError):
        stt_with(FakeRecognizer(recognize_result="   ")).listen_once()
    with pytest.raises(RecognitionServiceError):
        stt_with(FakeRecognizer(recognize_error=sr.RequestError("offline"))).listen_once()
    with pytest.raises(MicrophoneUnavailableError):
        stt_with(FakeRecognizer(listen_error=OSError("device unavailable"))).listen_once()


def test_speech_recognition_rejects_concurrent_listen():
    pytest.importorskip("speech_recognition")
    service = stt_with(FakeRecognizer())
    service._lock.acquire()
    try:
        with pytest.raises(AlreadyListeningError):
            service.listen_once()
    finally:
        service._lock.release()


def test_stop_event_discards_audio():
    pytest.importorskip("speech_recognition")
    stop = threading.Event()
    stop.set()
    assert stt_with(FakeRecognizer()).listen_once(stop) == ""


class FakeProcess:
    def __init__(self, finishes=True):
        self.finishes, self.returncode, self.terminated = finishes, None, False
        self.stdin = SimpleNamespace(write=lambda data: None, close=lambda: None)
        self.stderr = None

    def poll(self):
        if self.finishes or self.terminated:
            self.returncode = 0 if self.finishes else -15
        return self.returncode

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return self.returncode


def test_tts_speak_and_interrupt():
    done = FakeProcess(finishes=True)
    assert TextToSpeechService(process_factory=lambda args: done).speak("Hello") is True

    endless = FakeProcess(finishes=False)
    tts = TextToSpeechService(process_factory=lambda args: endless)
    threading.Timer(0.2, tts.stop).start()
    assert tts.speak("A very long answer") is False
    assert endless.terminated


def test_tts_muted_spawns_nothing():
    spawned = []
    tts = TextToSpeechService(muted=True, process_factory=lambda args: spawned.append(args))
    assert tts.speak("hello") is False and spawned == []


def test_prepare_for_speech_skips_code_and_links():
    text = prepare_for_speech("Use this:\n```python\nprint('hi')\n```\nSee https://docs.python.org **now**")
    assert "print" not in text and "https" not in text and "*" not in text
    assert "code on screen" in text


# ---- configuration & logging ----------------------------------------------------------


def test_load_settings_from_environment(tmp_path):
    settings = load_settings(environ={
        "OPENAI_API_KEY": " sk-abc ", "TTS_RATE": "999", "TTS_VOLUME": "loud",
        "ENABLE_CONTINUOUS_MODE": "TRUE", "VOICE_LANGUAGE": "hi", "AI_PROVIDER": "bogus",
        "ULTRON_DATA_DIR": str(tmp_path / "notes"),
    })
    assert settings.openai_api_key == "sk-abc" and settings.ai_configured
    assert settings.tts_rate == 300 and settings.tts_volume == 0.9
    assert settings.enable_continuous_mode is True
    assert settings.speech_language == "hi-IN"
    assert settings.ai_provider == "openai"
    assert settings.notes_file == tmp_path / "notes" / "notes.json"
    assert "sk-abc" not in repr(settings)


def test_defaults_without_env():
    settings = load_settings(environ={})
    assert not settings.ai_configured and settings.enable_continuous_mode is False
    assert settings.tts_rate == 175 and settings.speech_language == "en-US"


def test_parse_custom_apps():
    assert parse_custom_apps('Paint = "C:\\Windows\\mspaint.exe" ; junk ; =x') == {"paint": "C:\\Windows\\mspaint.exe"}


def test_save_env_values_preserves_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nOPENAI_API_KEY=sk-keep\nTTS_RATE=150\n", encoding="utf-8")
    save_env_values({"TTS_RATE": "200", "TTS_VOICE": "Zira"}, env)
    assert env.read_text(encoding="utf-8") == "# comment\nOPENAI_API_KEY=sk-keep\nTTS_RATE=200\nTTS_VOICE=Zira\n"


def test_secret_redaction(caplog):
    assert "sk-proj-abcdefghijkl" not in redact("key is sk-proj-abcdefghijkl")
    assert redact("api_key=hunter2secret") == f"api_key={REDACTED}"
    assert redact("my token mysecretvalue", ["mysecretvalue"]) == f"my token {REDACTED}"

    logger = logging.getLogger("ultron.test")
    record = logger.makeRecord("ultron.test", logging.INFO, __file__, 1, "using %s", ("sk-live-1234567890",), None)
    RedactingFilter(["sk-live-1234567890"]).filter(record)
    assert record.getMessage() == f"using {REDACTED}"
