import http.client
import json
import re
import threading

import pytest

from core.assistant import Assistant
from services.ai_service import AIService
from services.application_service import ApplicationService
from services.web_service import WebService
from web.server import create_server


class SilentTTS:
    muted = False
    is_speaking = False

    def speak(self, text, max_seconds=300):
        return False

    def stop(self):
        pass

    def availability_problem(self):
        return None


@pytest.fixture
def server(settings):
    assistant = Assistant(
        settings,
        ai=AIService(settings),
        tts=SilentTTS(),
        web=WebService(opener=lambda url: True),
        apps=ApplicationService(platform="win32", launcher=lambda spec: None, exists=lambda spec: True),
    )
    srv = create_server(assistant, port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.httpd.shutdown()
    srv.close()
    assistant.shutdown()


def request(srv, method, path, body=None, token=True, host=None, content_type="application/json"):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    headers = {"Host": host or f"127.0.0.1:{srv.port}"}
    if token:
        headers["X-Ultron-Token"] = srv.token if token is True else token
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        headers["Content-Type"] = content_type
    conn.request(method, path, body=data, headers=headers)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    return response, raw


def test_index_served_with_token_and_security_headers(server):
    response, raw = request(server, "GET", "/", token=False)
    assert response.status == 200
    html = raw.decode()
    assert re.search(r'name="ultron-token" content="([^"]+)"', html).group(1) == server.token
    assert "default-src 'self'" in response.getheader("Content-Security-Policy")
    assert response.getheader("X-Frame-Options") == "DENY"
    for asset in ("/app.js", "/styles.css"):
        assert request(server, "GET", asset, token=False)[0].status == 200


def test_static_paths_are_whitelisted(server):
    for path in ("/../config.py", "/web/server.py", "/.env", "/static/app.js"):
        assert request(server, "GET", path, token=False)[0].status == 404


def test_api_requires_token(server):
    assert request(server, "POST", "/api/message", {"text": "hi"}, token=False)[0].status == 403
    assert request(server, "POST", "/api/message", {"text": "hi"}, token="wrong")[0].status == 403
    assert request(server, "GET", "/api/status", token=False)[0].status == 403


def test_foreign_host_rejected(server):
    response, _ = request(server, "GET", "/", host="evil.example:80")
    assert response.status == 403
    assert request(server, "GET", "/", host=f"localhost:{server.port}")[0].status == 200


def test_message_roundtrip_local_command(server):
    response, raw = request(server, "POST", "/api/message", {"text": "who are you"})
    data = json.loads(raw)
    assert response.status == 200
    assert "Ultron" in data["text"] and data["source"] == "command" and not data["is_error"]


def test_message_without_key_reports_ai_setup(server):
    data = json.loads(request(server, "POST", "/api/message", {"text": "explain gravity"})[1])
    assert data["is_error"] and "OPENAI_API_KEY" in data["text"]


def test_speech_is_prepared_for_reading(server):
    data = json.loads(request(server, "POST", "/api/message", {"text": "help"})[1])
    assert "•" in data["text"] and "full list is on screen" in data["speech"]


def test_bad_requests(server):
    assert request(server, "POST", "/api/message", {"text": "  "})[0].status == 400
    assert request(server, "POST", "/api/message", b"{not json")[0].status == 400
    assert request(server, "POST", "/api/message", b"x" * 20_000)[0].status == 413
    assert request(server, "POST", "/api/message", b"text=hi", content_type="text/plain")[0].status == 415
    assert request(server, "POST", "/api/nope", {})[0].status == 404


def test_status_never_exposes_api_key(server):
    server.assistant.settings.openai_api_key = "sk-secret-value-123"
    response, raw = request(server, "GET", "/api/status")
    assert response.status == 200 and b"sk-secret" not in raw
    assert json.loads(raw)["ai_configured"] is True


def test_settings_update_applies_without_saving(server, monkeypatch):
    saved = []
    monkeypatch.setattr("web.server.save_env_values", lambda values: saved.append(values))
    data = json.loads(request(server, "POST", "/api/settings",
                              {"ai_provider": "openai", "ai_model": "my-model", "api_key": "sk-new-key-12345"})[1])
    assert data["ai_model"] == "my-model" and data["ai_configured"] and data["saved"] is False
    assert server.assistant.ai.settings.openai_api_key == "sk-new-key-12345"
    assert saved == []
    assert request(server, "POST", "/api/settings", {"ai_provider": "bogus"})[0].status == 400


def test_clear_endpoint(server):
    request(server, "POST", "/api/message", {"text": "hello"})
    assert len(server.assistant.conversation) == 2
    assert request(server, "POST", "/api/clear", {})[0].status == 200
    assert len(server.assistant.conversation) == 0
