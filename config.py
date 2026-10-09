"""Configuration for Ultron, loaded from environment variables and an optional .env file."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

APP_NAME = "Ultron"
APP_VERSION = "1.0"
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

SUPPORTED_AI_PROVIDERS = ("openai", "none")
SUPPORTED_SEARCH_PROVIDERS = ("none", "duckduckgo")

_LANGUAGE_DEFAULTS = {
    "en": "en-US",
    "hi": "hi-IN",
    "es": "es-ES",
    "fr": "fr-FR",
    "de": "de-DE",
    "it": "it-IT",
    "pt": "pt-BR",
    "ja": "ja-JP",
}

_APP_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9 _-]{0,40}$")


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_float(value: str | None, default: float, low: float, high: float) -> float:
    try:
        number = float(value) if value not in (None, "") else default
    except ValueError:
        number = default
    return max(low, min(high, number))


def _as_int(value: str | None, default: int, low: int, high: int) -> int:
    return int(_as_float(value, default, low, high))


def parse_custom_apps(raw: str | None) -> dict[str, str]:
    """Parse ``ULTRON_APPS`` (``name=path;name2=path2``) into a name -> path mapping.

    Invalid entries are skipped; they are never executed.
    """
    apps: dict[str, str] = {}
    if not raw:
        return apps
    for entry in raw.split(";"):
        if "=" not in entry:
            continue
        name, path = entry.split("=", 1)
        name = " ".join(name.strip().lower().split())
        path = path.strip().strip('"')
        if path and _APP_NAME_PATTERN.match(name):
            apps[name] = path
    return apps


@dataclass
class Settings:
    ai_provider: str = "openai"
    openai_api_key: str = field(default="", repr=False)
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = ""
    ai_timeout: float = 30.0
    ai_max_tokens: int = 500
    ai_temperature: float = 0.7
    max_history_messages: int = 20

    tts_rate: int = 175
    tts_volume: float = 0.9
    tts_voice: str = ""
    start_muted: bool = False

    voice_language: str = "en"
    enable_continuous_mode: bool = False
    listen_timeout: float = 6.0
    phrase_time_limit: float = 15.0

    web_search_provider: str = "none"
    custom_apps: dict[str, str] = field(default_factory=dict)

    data_dir: Path = PROJECT_ROOT / "data"
    log_dir: Path = PROJECT_ROOT / "logs"
    log_level: str = "INFO"
    appearance_mode: str = "dark"
    web_port: int = 8000

    @property
    def ai_configured(self) -> bool:
        return self.ai_provider == "openai" and bool(self.openai_api_key)

    @property
    def speech_language(self) -> str:
        """Language code for speech recognition, e.g. ``en`` -> ``en-US``."""
        lang = self.voice_language.strip() or "en"
        if "-" in lang:
            return lang
        return _LANGUAGE_DEFAULTS.get(lang.lower(), lang)

    @property
    def notes_file(self) -> Path:
        return self.data_dir / "notes.json"

    def secrets(self) -> list[str]:
        return [s for s in (self.openai_api_key,) if s]


def load_settings(
    env_file: Path | str | None = DEFAULT_ENV_FILE,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Build Settings from ``environ`` (defaults to ``os.environ`` after loading ``env_file``)."""
    if environ is None:
        if env_file is not None and Path(env_file).exists():
            try:
                from dotenv import load_dotenv

                load_dotenv(env_file, override=False)
            except ImportError:
                pass
        environ = os.environ

    def get(key: str, default: str = "") -> str:
        return (environ.get(key) or default).strip()

    provider = get("AI_PROVIDER", "openai").lower()
    if provider not in SUPPORTED_AI_PROVIDERS:
        provider = "openai"
    search = get("WEB_SEARCH_PROVIDER", "none").lower()
    if search not in SUPPORTED_SEARCH_PROVIDERS:
        search = "none"

    data_dir = Path(get("ULTRON_DATA_DIR") or PROJECT_ROOT / "data").expanduser()
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir
    log_dir = Path(get("ULTRON_LOG_DIR") or PROJECT_ROOT / "logs").expanduser()
    if not log_dir.is_absolute():
        log_dir = PROJECT_ROOT / log_dir

    appearance = get("APPEARANCE_MODE", "dark").lower()
    if appearance not in {"dark", "light", "system"}:
        appearance = "dark"

    return Settings(
        ai_provider=provider,
        openai_api_key=get("OPENAI_API_KEY"),
        openai_model=get("OPENAI_MODEL") or "gpt-4o-mini",
        openai_base_url=get("OPENAI_BASE_URL"),
        ai_timeout=_as_float(environ.get("AI_TIMEOUT"), 30.0, 5.0, 120.0),
        ai_max_tokens=_as_int(environ.get("AI_MAX_TOKENS"), 500, 50, 4000),
        ai_temperature=_as_float(environ.get("AI_TEMPERATURE"), 0.7, 0.0, 2.0),
        max_history_messages=_as_int(environ.get("MAX_HISTORY_MESSAGES"), 20, 2, 100),
        tts_rate=_as_int(environ.get("TTS_RATE"), 175, 80, 300),
        tts_volume=_as_float(environ.get("TTS_VOLUME"), 0.9, 0.0, 1.0),
        tts_voice=get("TTS_VOICE"),
        start_muted=_as_bool(environ.get("START_MUTED"), False),
        voice_language=get("VOICE_LANGUAGE", "en"),
        enable_continuous_mode=_as_bool(environ.get("ENABLE_CONTINUOUS_MODE"), False),
        listen_timeout=_as_float(environ.get("LISTEN_TIMEOUT"), 6.0, 1.0, 30.0),
        phrase_time_limit=_as_float(environ.get("PHRASE_TIME_LIMIT"), 15.0, 2.0, 60.0),
        web_search_provider=search,
        custom_apps=parse_custom_apps(environ.get("ULTRON_APPS")),
        data_dir=data_dir,
        log_dir=log_dir,
        log_level=get("LOG_LEVEL", "INFO").upper(),
        appearance_mode=appearance,
        web_port=_as_int(environ.get("WEB_PORT"), 8000, 1024, 65535),
    )


def save_env_values(updates: Mapping[str, str], env_file: Path | str = DEFAULT_ENV_FILE) -> None:
    """Update or append ``KEY=value`` lines in the .env file, preserving everything else."""
    path = Path(env_file)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = {k: str(v).replace("\n", " ").strip() for k, v in updates.items()}
    output: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in remaining:
            output.append(f"{key}={remaining.pop(key)}")
        else:
            output.append(line)
    output.extend(f"{k}={v}" for k, v in remaining.items())
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(output) + "\n", encoding="utf-8")
    os.replace(tmp, path)
