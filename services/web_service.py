"""Opening approved websites, validated URLs, and Google searches in the default browser."""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import urllib.error
import urllib.request
import webbrowser
from typing import Callable
from urllib.parse import quote_plus, urlencode, urlparse

logger = logging.getLogger("ultron.web")

KNOWN_SITES: dict[str, str] = {
    "google": "https://www.google.com",
    "youtube": "https://www.youtube.com",
    "github": "https://github.com",
    "gmail": "https://mail.google.com",
}

SITE_ALIASES: dict[str, str] = {
    "you tube": "youtube",
    "git hub": "github",
    "google mail": "gmail",
    "g mail": "gmail",
}

_HOST_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$", re.IGNORECASE)
_TLD = re.compile(r"^[a-z]{2,24}$", re.IGNORECASE)
MAX_URL_LENGTH = 2048


class WebServiceError(Exception):
    pass


def _valid_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    labels = host.split(".")
    return len(labels) >= 2 and all(_HOST_LABEL.match(label) for label in labels) and bool(_TLD.match(labels[-1]))


def normalize_url(text: str) -> str | None:
    """Return a safe http(s) URL for ``text``, or None if it is not a valid web address."""
    if not text:
        return None
    candidate = text.strip().strip("<>\"'").rstrip(".,!?;")
    candidate = re.sub(r"\s+dot\s+", ".", candidate, flags=re.IGNORECASE)
    if not candidate or len(candidate) > MAX_URL_LENGTH or re.search(r"\s", candidate):
        return None
    if "://" not in candidate:
        candidate = "https://" + candidate
    try:
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").lower()
        _ = parsed.port  # raises ValueError for invalid ports
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    if "@" in parsed.netloc or not host or not _valid_host(host):
        return None
    return candidate


def resolve_site(name: str) -> str | None:
    key = " ".join(name.lower().split())
    key = SITE_ALIASES.get(key, key)
    return KNOWN_SITES.get(key)


class WebService:
    def __init__(
        self,
        search_provider: str = "none",
        opener: Callable[[str], bool] | None = None,
        timeout: float = 8.0,
    ) -> None:
        self.search_provider = search_provider
        self._open = opener or webbrowser.open
        self.timeout = timeout

    def open_url(self, url: str) -> bool:
        safe = normalize_url(url)
        if not safe:
            raise WebServiceError("That doesn't look like a valid web address.")
        logger.info("Opening URL host=%s", urlparse(safe).hostname)
        try:
            opened = bool(self._open(safe))
        except Exception as exc:  # webbrowser raises varied errors per platform
            logger.error("Browser open failed: %s", exc)
            raise WebServiceError("I couldn't open your web browser.") from exc
        if not opened:
            raise WebServiceError("I couldn't find a web browser to open.")
        return True

    def google_search_url(self, query: str) -> str:
        return "https://www.google.com/search?q=" + quote_plus(query.strip()[:500])

    def search_google(self, query: str) -> bool:
        if not query.strip():
            raise WebServiceError("What would you like me to search for?")
        return self.open_url(self.google_search_url(query))

    @property
    def lookup_available(self) -> bool:
        return self.search_provider == "duckduckgo"

    def lookup(self, query: str) -> str | None:
        """Fetch a short factual answer from the configured search provider."""
        if not self.lookup_available:
            raise WebServiceError("Web search is not configured. Set WEB_SEARCH_PROVIDER=duckduckgo in .env.")
        params = urlencode({"q": query[:300], "format": "json", "no_html": 1, "skip_disambig": 1})
        request = urllib.request.Request(
            f"https://api.duckduckgo.com/?{params}", headers={"User-Agent": "Ultron-Assistant/1.0"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read(1_000_000).decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            logger.warning("Web lookup failed: %s", exc)
            raise WebServiceError("The web search service didn't respond. Check your internet connection.") from exc
        for key in ("Answer", "AbstractText", "Definition"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:1000]
        return None
