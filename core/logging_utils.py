"""Local logging with secret redaction."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(api[_-]?key|authorization|bearer)(\s*[:=]\s*|\s+)([^\s,;]+)"),
]

REDACTED = "[REDACTED]"


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, REDACTED)
    text = _SECRET_PATTERNS[0].sub(REDACTED, text)
    text = _SECRET_PATTERNS[1].sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    return text


class RedactingFilter(logging.Filter):
    """Removes API keys and similar secrets from every log record."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self.secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        record.msg = redact(message, self.secrets)
        record.args = ()
        if record.exc_info:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info), self.secrets)
            record.exc_info = None
        return True


def register_secret(secret: str) -> None:
    """Redact an additional secret (e.g. an API key entered at runtime) from all Ultron log output."""
    if not secret:
        return
    for handler in logging.getLogger("ultron").handlers:
        for log_filter in handler.filters:
            if isinstance(log_filter, RedactingFilter) and secret not in log_filter.secrets:
                log_filter.secrets.append(secret)


def setup_logging(log_dir: Path, level: str = "INFO", secrets: Iterable[str] = ()) -> logging.Logger:
    logger = logging.getLogger("ultron")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    redactor = RedactingFilter(secrets)
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    handlers: list[logging.Handler] = [console]
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(log_dir / "ultron.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        )
    except OSError:
        pass
    for handler in handlers:
        handler.addFilter(redactor)
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    # Third-party HTTP libraries can log request details; keep them quiet.
    for noisy in ("openai", "httpx", "httpx2", "httpcore", "httpcore2", "comtypes"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return logger
