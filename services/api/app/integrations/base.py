"""Shared HTTP plumbing for public reference APIs.

All four adapters are read-only lookups of public drug *reference* data. None
of them can return a person's prescription, and nothing they return is used
to build a schedule.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass

import httpx

from ..config import settings


class ReferenceUnavailable(Exception):
    """The source is down, slow, or returned something unusable."""


@dataclass
class ReferenceLink:
    source: str
    title: str
    url: str
    summary: str = ""
    version: str = ""


_client: httpx.Client | None = None


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            timeout=settings.reference_timeout_s,
            headers={"User-Agent": "CareLens-prototype/0.1 (synthetic-data research demo)"},
            follow_redirects=True,
        )
    return _client


def set_client(replacement: httpx.Client | None) -> None:
    """Swap the HTTP client (tests inject a mock transport here)."""
    global _client
    _client = replacement


def get_json(url: str, params: dict | None = None) -> dict:
    try:
        response = client().get(url, params=params)
    except httpx.HTTPError as error:
        raise ReferenceUnavailable(type(error).__name__) from error
    if response.status_code == 404:
        return {}
    if response.status_code >= 400:
        raise ReferenceUnavailable(f"HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as error:
        raise ReferenceUnavailable("invalid JSON") from error


_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
ALLOWED_LINK_HOSTS = (
    "medlineplus.gov",
    "dailymed.nlm.nih.gov",
    "nlm.nih.gov",
    "fda.gov",
    "accessdata.fda.gov",
)


def clean_text(raw: str, limit: int = 480) -> str:
    """Reduce retrieved content to plain display text.

    Retrieved text is untrusted. It is shown or read aloud as a quotation from
    the source and is never interpreted as an instruction by any component.
    """
    text = _SPACE.sub(" ", html.unescape(_TAGS.sub(" ", raw or ""))).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("? "))
    return cut[: end + 1] if end > 120 else cut.rstrip() + "…"


def safe_url(url: str) -> bool:
    """Only link to the official hosts we asked; guards against odd redirects."""
    match = re.match(r"^https://([^/]+)/", url or "")
    if not match:
        return False
    host = match.group(1).lower()
    return any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_LINK_HOSTS)
