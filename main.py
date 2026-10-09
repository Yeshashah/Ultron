"""Ultron v1.0 — personal AI voice assistant.

Usage:
    python main.py            Launch the desktop app
    python main.py --web      Launch the browser interface at http://127.0.0.1:8000
    python main.py --check    Check dependencies and configuration, then exit
    python main.py --cli      Typed chat in the terminal (no GUI)
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys

MIN_PYTHON = (3, 10)

if sys.version_info < MIN_PYTHON:
    sys.exit(f"Ultron needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer (3.12 recommended). "
             f"You are running {sys.version.split()[0]}.")

from config import APP_VERSION, DEFAULT_ENV_FILE, Settings, load_settings  # noqa: E402
from core.logging_utils import setup_logging  # noqa: E402

logger = logging.getLogger("ultron")

DEPENDENCIES = [
    ("customtkinter", "Desktop interface", True),
    ("dotenv", "Reading the .env file (python-dotenv)", True),
    ("speech_recognition", "Voice input (SpeechRecognition)", False),
    ("pyaudio", "Microphone access (PyAudio)", False),
    ("pyttsx3", "Spoken replies", False),
    ("openai", "AI answers", False),
]


def run_checks(settings: Settings) -> int:
    """Print a dependency/configuration report. Returns 1 if a required piece is missing."""
    failures = 0

    def report(ok: bool, label: str, detail: str = "") -> None:
        print(f"  [{'OK' if ok else '!!'}] {label}{' — ' + detail if detail else ''}")

    print(f"Ultron v{APP_VERSION} environment check")
    print(f"  Python {sys.version.split()[0]} on {sys.platform}")
    if sys.platform != "win32":
        print("  Note: Ultron targets Windows 10/11; app launching uses macOS equivalents here.")
    try:
        import tkinter

        report(True, "tkinter", f"Tk {tkinter.TkVersion}")
    except ImportError:
        report(False, "tkinter", "missing — reinstall Python with the 'tcl/tk' option enabled")
        failures += 1
    for module, purpose, required in DEPENDENCIES:
        try:
            importlib.import_module(module)
            report(True, module, purpose)
        except Exception as exc:
            report(False, module, f"{purpose} unavailable ({type(exc).__name__}). Run: pip install -r requirements.txt")
            failures += int(required)

    report(DEFAULT_ENV_FILE.exists(), ".env file", "found" if DEFAULT_ENV_FILE.exists() else
           "not found — copy .env.example to .env (defaults are used meanwhile)")
    if settings.ai_provider == "none":
        report(True, "AI", "disabled (AI_PROVIDER=none); local commands only")
    else:
        report(bool(settings.openai_api_key), "AI credentials",
               f"key set, model {settings.openai_model}" if settings.openai_api_key
               else "OPENAI_API_KEY is empty; AI answers disabled, local commands still work")

    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        probe = settings.data_dir / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        report(True, "Data folder", str(settings.data_dir))
    except OSError as exc:
        report(False, "Data folder", f"{settings.data_dir} is not writable ({exc})")
        failures += 1

    from services.speech_recognition_service import SpeechRecognitionService

    mic_problem = SpeechRecognitionService(settings.speech_language).availability_problem()
    report(mic_problem is None, "Microphone", mic_problem or "available")
    print("Result:", "ready" if failures == 0 else f"{failures} required item(s) missing")
    return 1 if failures else 0


def run_cli(assistant) -> int:
    from core.assistant import AssistantEvent

    def show(event: AssistantEvent) -> None:
        if event.kind == "message" and event.data.get("role") != "user":
            print(f"Ultron: {event.data.get('text', '')}")

    assistant.on_event = show
    print("Ultron text mode. Type 'exit' to quit.")
    try:
        while True:
            try:
                text = input("You: ")
            except EOFError:
                break
            if assistant.process_text(text) == "exit":
                break
    except KeyboardInterrupt:
        print()
    finally:
        assistant.shutdown()
    return 0


def run_gui(assistant, settings: Settings) -> int:
    try:
        import customtkinter as ctk

        from ui.main_window import MainWindow
    except ImportError as exc:
        print(f"The desktop interface can't start: {exc}.\n"
              "Run 'pip install -r requirements.txt', or use 'python main.py --cli' for text mode.")
        return 1
    ctk.set_appearance_mode(settings.appearance_mode)
    ctk.set_default_color_theme("blue")
    try:
        app = MainWindow(assistant)
    except Exception as exc:  # e.g. no display available
        logger.exception("Could not open the main window")
        print(f"Couldn't open the Ultron window: {exc}")
        assistant.shutdown()
        return 1
    try:
        app.mainloop()
    except KeyboardInterrupt:
        app.on_close()
    finally:
        assistant.shutdown()
    return 0


def run_web(assistant, port: int, open_browser: bool) -> int:
    from web.server import create_server

    try:
        server = create_server(assistant, port)
    except OSError as exc:
        print(f"Couldn't start the web server: {exc}")
        assistant.shutdown()
        return 1
    print(f"\n  Ultron is running at:  {server.url}\n", flush=True)
    print("  Open that link in Chrome or Edge (needed for voice input). Press Ctrl+C here to stop.\n", flush=True)
    logger.info("Web interface listening on %s", server.url)
    if open_browser:
        import webbrowser

        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Ultron…")
    finally:
        server.close()
        assistant.shutdown()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ultron personal AI voice assistant")
    parser.add_argument("--check", action="store_true", help="check dependencies and configuration, then exit")
    parser.add_argument("--cli", action="store_true", help="typed chat in the terminal instead of the GUI")
    parser.add_argument("--web", action="store_true", help="run the browser interface at http://127.0.0.1:<port>")
    parser.add_argument("--port", type=int, default=None, help="port for --web (default: WEB_PORT or 8000)")
    parser.add_argument("--no-browser", action="store_true", help="with --web, don't open the browser automatically")
    parser.add_argument("--mute", action="store_true", help="start with spoken replies muted")
    args = parser.parse_args(argv)

    settings = load_settings()
    if args.mute:
        settings.start_muted = True
    setup_logging(settings.log_dir, settings.log_level, settings.secrets())
    if args.check:
        return run_checks(settings)

    logger.info("Starting Ultron v%s (python %s, %s)", APP_VERSION, sys.version.split()[0], sys.platform)
    from core.assistant import Assistant

    assistant = Assistant(settings)
    if args.web:
        code = run_web(assistant, args.port or settings.web_port, not args.no_browser)
    elif args.cli:
        code = run_cli(assistant)
    else:
        code = run_gui(assistant, settings)
    logger.info("Ultron stopped")
    return code


if __name__ == "__main__":
    sys.exit(main())
