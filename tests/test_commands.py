import random
from datetime import datetime

import pytest

from config import parse_custom_apps
from core.command_handler import (
    EXIT_ACTION,
    FACTS,
    JOKES,
    STOP_SPEAKING_ACTION,
    CommandHandler,
    CommandResult,
    evaluate_expression,
    normalize,
)
from services.application_service import ApplicationService
from services.notes_service import NotesService
from services.web_service import WebService, normalize_url

FIXED_NOW = datetime(2026, 10, 9, 20, 14)


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, item):
        self.calls.append(item)
        return True


@pytest.fixture
def opened_urls():
    return Recorder()


@pytest.fixture
def launched():
    return Recorder()


def make_handler(tmp_path, opened_urls, launched, platform="win32", installed=True, custom_apps=None):
    apps = ApplicationService(custom_apps or {}, platform=platform, launcher=launched, exists=lambda spec: installed)
    return CommandHandler(
        NotesService(tmp_path / "notes.json"),
        WebService(opener=opened_urls),
        apps,
        clock=lambda: FIXED_NOW,
        rng=random.Random(1),
    )


@pytest.fixture
def handler(tmp_path, opened_urls, launched):
    return make_handler(tmp_path, opened_urls, launched)


# ---- parsing --------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Hey Ultron, what time is it?", "what time is it"),
        ("ultron open youtube please", "open youtube"),
        ("  Can you   tell me a joke  ", "tell me a joke"),
        ("hello ultron", "hello"),
        ("Hey Ultron", "Hey"),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


# ---- basic commands -------------------------------------------------------------


@pytest.mark.parametrize("phrase", ["what time is it", "What's the time?", "time", "Ultron, tell me the time"])
def test_time(handler, phrase):
    result = handler.handle(phrase)
    assert result.handled and result.command == "time"
    assert result.text == "It's 8:14 PM."


@pytest.mark.parametrize("phrase", ["what's the date", "what is today's date", "what day is it", "date"])
def test_date(handler, phrase):
    result = handler.handle(phrase)
    assert result.command == "date"
    assert result.text == "Today is Friday, 9 October 2026."


def test_greeting_and_identity_mention_ultron(handler):
    assert "Good evening" in handler.handle("hello").text
    assert "Ultron" in handler.handle("who are you").text
    assert "Ultron" in handler.handle("what is your name?").text


def test_how_are_you_joke_fact_help(handler):
    assert handler.handle("how are you").handled
    assert handler.handle("tell me a joke").text in JOKES
    assert handler.handle("random fact").text.removeprefix("Here's a fact: ") in FACTS
    help_result = handler.handle("help")
    assert "Notes" in help_result.text and help_result.spoken_text != help_result.text


def test_stop_and_exit_actions(handler):
    stop = handler.handle("stop speaking")
    assert stop.action == STOP_SPEAKING_ACTION and stop.spoken_text == ""
    assert handler.handle("exit").action == EXIT_ACTION
    assert handler.handle("goodbye ultron").action == EXIT_ACTION


def test_unknown_input_is_not_handled(handler):
    for phrase in ["explain recursion in python", "what is the capital of France", "summarize this: hello"]:
        assert handler.handle(phrase).handled is False


def test_empty_input(handler):
    result = handler.handle("   ")
    assert result.handled and "didn't catch" in result.text


# ---- calculations --------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase, answer",
    [
        ("calculate 12 times 8", "96"),
        ("what is 2 + 2", "4"),
        ("what's 15 percent of 200", "30"),
        ("square root of 81", "9"),
        ("calculate square root of 81", "9"),
        ("what is the square root of a banana", None),
        ("what is 7 divided by 2", "3.5"),
        ("0.1 + 0.2", "0.3"),
        ("calculate 2 to the power of 10", "1024"),
        ("calculate 1000 * 1000", "1,000,000"),
    ],
)
def test_calculations(handler, phrase, answer):
    result = handler.handle(phrase)
    if answer is None:
        assert result.command != "calculate"
    else:
        assert result.text == f"The answer is {answer}."


def test_division_by_zero(handler):
    assert "division by zero" in handler.handle("calculate 5 / 0").text


@pytest.mark.parametrize("expr", ["__import__('os').system('dir')", "open('x')", "2 ** 999999", "a + 1"])
def test_evaluator_rejects_unsafe_input(expr):
    with pytest.raises((ValueError, SyntaxError)):
        evaluate_expression(expr)


# ---- websites --------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase, url",
    [
        ("open youtube", "https://www.youtube.com"),
        ("Open Google", "https://www.google.com"),
        ("launch github", "https://github.com"),
        ("open gmail", "https://mail.google.com"),
        ("open example.com", "https://example.com"),
        ("go to https://docs.python.org/3/", "https://docs.python.org/3/"),
        ("open python dot org", "https://python.org"),
    ],
)
def test_open_websites(handler, opened_urls, phrase, url):
    result = handler.handle(phrase)
    assert result.text.startswith("Opening")
    assert opened_urls.calls == [url]


def test_google_search(handler, opened_urls):
    result = handler.handle("search google for python list comprehension")
    assert "python list comprehension" in result.text
    handler.handle("search for cute cats on google")
    handler.handle("google weather in pune")
    assert opened_urls.calls == [
        "https://www.google.com/search?q=python+list+comprehension",
        "https://www.google.com/search?q=cute+cats",
        "https://www.google.com/search?q=weather+in+pune",
    ]


def test_web_lookup_without_provider_falls_back_to_google(handler, opened_urls):
    result = handler.handle("look up eiffel tower height")
    assert "isn't configured" in result.text
    assert opened_urls.calls[0].startswith("https://www.google.com/search?q=eiffel")


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "file:///C:/Windows/system32", "ftp://example.com", "http://user@evil.com",
     "exa mple.com", "http://", "notaurl", "https://-bad-.com", "https://example.c0m", ""],
)
def test_invalid_urls_rejected(url):
    assert normalize_url(url) is None


@pytest.mark.parametrize(
    "url", ["example.com", "https://github.com/user/repo?tab=1", "http://localhost:8080", "https://127.0.0.1"]
)
def test_valid_urls_accepted(url):
    assert normalize_url(url) is not None


def test_open_unknown_target_opens_nothing(handler, opened_urls, launched):
    result = handler.handle("open the pod bay doors")
    assert "can't open" in result.text
    assert opened_urls.calls == [] and launched.calls == []


# ---- applications -----------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase, exe",
    [
        ("open notepad", "notepad.exe"),
        ("open the calculator app", "calc.exe"),
        ("open file explorer", "explorer.exe"),
        ("open command prompt", "cmd.exe"),
        ("open vs code", "Code.exe"),
    ],
)
def test_open_windows_apps(handler, launched, phrase, exe):
    result = handler.handle(phrase)
    assert result.text.startswith("Opening")
    assert launched.calls[0].args[0].endswith(exe)


def test_app_not_installed(tmp_path, opened_urls, launched):
    handler = make_handler(tmp_path, opened_urls, launched, installed=False)
    result = handler.handle("open vs code")
    assert "doesn't appear to be installed" in result.text
    assert launched.calls == []


def test_unlisted_app_refused(handler, launched):
    assert "can't open" in handler.handle("open regedit").text
    assert launched.calls == []


def test_custom_configured_app(tmp_path, opened_urls, launched):
    apps = parse_custom_apps(r"paint=C:\Windows\System32\mspaint.exe;bad=C:\evil\script.bat;Bad Name!=x.exe")
    assert set(apps) == {"paint", "bad"}
    handler = make_handler(tmp_path, opened_urls, launched, custom_apps=apps)
    handler.apps.custom_apps["paint"] = str(tmp_path / "mspaint.exe")
    assert handler.handle("open paint").text == "Opening Paint."
    assert launched.calls[0].args == (str(tmp_path / "mspaint.exe"),)
    handler.apps.custom_apps["bad"] = str(tmp_path / "script.bat")
    assert "doesn't point to an existing application" in handler.handle("open bad").text
    assert len(launched.calls) == 1


# ---- registry ---------------------------------------------------------------------------


def test_registry_is_extensible(handler):
    handler.registry.add("weather", [r"^weather$"], lambda m, t: CommandResult("Weather isn't supported yet."))
    assert handler.handle("weather").command == "weather"
    assert "weather" in handler.registry.names


def test_command_exception_is_contained(handler):
    def broken(m, t):
        raise RuntimeError("boom")

    handler.registry.add("broken", [r"^crash$"], broken)
    result = handler.handle("crash")
    assert result.handled and "went wrong" in result.text
