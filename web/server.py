"""Local web interface for Ultron, served at http://127.0.0.1:<port>.

The browser handles the microphone (Web Speech API) and spoken replies (speechSynthesis); this server
runs commands, notes, and AI requests. It only listens on the loopback interface, rejects unexpected
Host headers (DNS rebinding), and requires a per-session token on every API call, so other websites
open in your browser cannot drive it.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from config import APP_VERSION, SUPPORTED_AI_PROVIDERS, save_env_values
from core.assistant import MAX_INPUT_CHARS, Assistant
from core.command_handler import EXIT_ACTION
from core.logging_utils import register_secret
from services.text_to_speech_service import prepare_for_speech

logger = logging.getLogger("ultron.web")

STATIC_DIR = Path(__file__).with_name("static")
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}
MAX_BODY_BYTES = 16_000
TOKEN_PLACEHOLDER = "__ULTRON_TOKEN__"
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
)


class UltronWebServer:
    def __init__(self, assistant: Assistant, host: str = "127.0.0.1", port: int = 8000) -> None:
        self.assistant = assistant
        self.token = secrets.token_urlsafe(24)
        self._lock = threading.Lock()
        self.httpd = ThreadingHTTPServer((host, port), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.ultron = self  # type: ignore[attr-defined]
        self.host = host
        self.port = self.httpd.server_address[1]

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def allowed_hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def serve_forever(self) -> None:
        self.httpd.serve_forever(poll_interval=0.25)

    def shutdown(self) -> None:
        threading.Thread(target=self.httpd.shutdown, daemon=True).start()

    def close(self) -> None:
        self.httpd.server_close()

    # -- API operations --------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        s = self.assistant.settings
        return {
            "version": APP_VERSION,
            "ai_provider": s.ai_provider,
            "ai_model": s.openai_model,
            "ai_configured": s.ai_configured,
            "ai_problem": self.assistant.ai.configuration_problem(),
            "voice": {
                "rate": s.tts_rate,
                "volume": s.tts_volume,
                "voice": s.tts_voice,
                "language": s.speech_language,
                "continuous": s.enable_continuous_mode,
                "muted": s.start_muted,
            },
        }

    def message(self, text: str) -> dict[str, Any]:
        with self._lock:  # command state (e.g. pending confirmations) is per-assistant
            reply = self.assistant.respond(text)
        if reply.action == EXIT_ACTION:
            threading.Timer(1.0, self.shutdown).start()
        return {
            "text": reply.text,
            "speech": prepare_for_speech(reply.speech) if reply.speech else "",
            "action": reply.action,
            "is_error": reply.is_error,
            "source": reply.source,
        }

    def clear(self) -> None:
        with self._lock:
            self.assistant.clear_conversation()

    def update_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        current = self.assistant.settings
        provider = str(data.get("ai_provider", current.ai_provider)).lower()
        if provider not in SUPPORTED_AI_PROVIDERS:
            raise ValueError("Unknown AI provider.")
        model = str(data.get("ai_model") or current.openai_model).strip()[:100]
        new_key = str(data.get("api_key") or "").strip()
        if len(new_key) > 300 or any(c.isspace() for c in new_key):
            raise ValueError("That API key doesn't look valid.")
        updated = dataclasses.replace(
            current, ai_provider=provider, openai_model=model, openai_api_key=new_key or current.openai_api_key
        )
        if new_key:
            register_secret(new_key)
        with self._lock:
            self.assistant.apply_settings(updated)
        saved = False
        if data.get("save"):
            values = {"AI_PROVIDER": provider, "OPENAI_MODEL": model}
            if new_key:
                values["OPENAI_API_KEY"] = new_key
            save_env_values(values)
            saved = True
        result = self.status()
        result["saved"] = saved
        return result


class _Handler(BaseHTTPRequestHandler):
    server_version = "Ultron"
    sys_version = ""

    @property
    def ultron(self) -> UltronWebServer:
        return self.server.ultron  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - signature from base class
        logger.debug("web %s", format % args)

    # -- helpers -------------------------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _host_ok(self) -> bool:
        return (self.headers.get("Host") or "").lower() in self.ultron.allowed_hosts

    def _token_ok(self) -> bool:
        supplied = self.headers.get("X-Ultron-Token") or ""
        return secrets.compare_digest(supplied, self.ultron.token)

    def _read_json(self) -> dict[str, Any] | None:
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Expected JSON.")
            return None
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request too large.")
            return None
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "Invalid JSON.")
            return None
        if not isinstance(data, dict):
            self._error(HTTPStatus.BAD_REQUEST, "Invalid JSON.")
            return None
        return data

    # -- routes ----------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - name required by BaseHTTPRequestHandler
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "Forbidden host.")
        path = self.path.split("?", 1)[0]
        if path in STATIC_FILES:
            filename, content_type = STATIC_FILES[path]
            body = (STATIC_DIR / filename).read_bytes()
            extra = {}
            if filename == "index.html":
                body = body.replace(TOKEN_PLACEHOLDER.encode(), self.ultron.token.encode())
                extra["Content-Security-Policy"] = CSP
            return self._send(HTTPStatus.OK, body, content_type, extra)
        if path == "/api/status":
            if not self._token_ok():
                return self._error(HTTPStatus.FORBIDDEN, "Missing or invalid token.")
            return self._json(HTTPStatus.OK, self.ultron.status())
        self._error(HTTPStatus.NOT_FOUND, "Not found.")

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "Forbidden host.")
        if not self._token_ok():
            return self._error(HTTPStatus.FORBIDDEN, "Missing or invalid token.")
        path = self.path.split("?", 1)[0]
        if path not in {"/api/message", "/api/clear", "/api/settings"}:
            return self._error(HTTPStatus.NOT_FOUND, "Not found.")
        data = self._read_json()
        if data is None:
            return
        try:
            if path == "/api/message":
                text = data.get("text")
                if not isinstance(text, str) or not text.strip():
                    return self._error(HTTPStatus.BAD_REQUEST, "Message text is required.")
                return self._json(HTTPStatus.OK, self.ultron.message(text.strip()[:MAX_INPUT_CHARS]))
            if path == "/api/clear":
                self.ultron.clear()
                return self._json(HTTPStatus.OK, {"ok": True})
            return self._json(HTTPStatus.OK, self.ultron.update_settings(data))
        except ValueError as exc:
            return self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except OSError:
            logger.exception("Web request failed")
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "Couldn't save settings to the .env file.")
        except Exception:
            logger.exception("Web request failed")
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "Something went wrong inside Ultron.")


def create_server(assistant: Assistant, port: int = 8000, attempts: int = 10) -> UltronWebServer:
    """Bind to ``port`` on 127.0.0.1, trying the next few ports if it's busy (port 0 = any free port)."""
    last_error: OSError | None = None
    for offset in range(attempts if port else 1):
        try:
            return UltronWebServer(assistant, port=port + offset if port else 0)
        except OSError as exc:
            last_error = exc
    raise OSError(f"No free port found starting at {port}: {last_error}")
