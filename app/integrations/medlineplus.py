"""MedlinePlus Connect: patient-education link for a confirmed RxCUI.

Docs: https://medlineplus.gov/medlineplus-connect/web-service/
MedlinePlus asks callers to stay under 100 requests per minute per IP; the
24-hour cache in `services/medication/education.py` keeps us far below that.
Linked pages can carry third-party copyright, so we store a short summary and
the link, not the page.
"""
from __future__ import annotations

from .base import ReferenceLink, ReferenceUnavailable, clean_text, get_json, safe_url

URL = "https://connect.medlineplus.gov/service"
RXNORM_CODE_SYSTEM = "2.16.840.1.113883.6.88"
SOURCE = "MedlinePlus"


def education_link(rxcui: str, name: str = "") -> ReferenceLink:
    data = get_json(
        URL,
        {
            "mainSearchCriteria.v.cs": RXNORM_CODE_SYSTEM,
            "mainSearchCriteria.v.c": rxcui,
            "knowledgeResponseType": "application/json",
        },
    )
    entries = (data.get("feed") or {}).get("entry") or []
    for entry in entries:
        links = entry.get("link") or []
        url = links[0].get("href", "") if links else ""
        title = (entry.get("title") or {}).get("_value", "")
        if title and safe_url(url):
            return ReferenceLink(
                source=SOURCE,
                title=title,
                url=url,
                summary=clean_text((entry.get("summary") or {}).get("_value", "")),
                version=((data.get("feed") or {}).get("updated") or {}).get("_value", ""),
            )
    raise ReferenceUnavailable("no MedlinePlus page for this RxCUI")
