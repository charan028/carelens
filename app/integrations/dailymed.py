"""DailyMed: structured product label (SPL) reference for an RxCUI.

Docs: https://dailymed.nlm.nih.gov/dailymed/app-support-web-services.cfm
We keep the label's set id and published date so the reader can see exactly
which label was referenced. A label is manufacturer text about a product; it
is never treated as a person's prescription.
"""
from __future__ import annotations

from .base import ReferenceLink, ReferenceUnavailable, get_json

BASE = "https://dailymed.nlm.nih.gov/dailymed/services/v2"
SOURCE = "DailyMed"


def label_reference(rxcui: str, name: str = "") -> ReferenceLink:
    data = get_json(f"{BASE}/spls.json", {"rxcui": rxcui, "pagesize": 1})
    labels = data.get("data") or []
    if not labels:
        raise ReferenceUnavailable("no DailyMed label for this RxCUI")
    label = labels[0]
    setid = label.get("setid", "")
    if not setid:
        raise ReferenceUnavailable("label without set id")
    return ReferenceLink(
        source=SOURCE,
        title=(label.get("title") or f"{name} label").strip(),
        url=f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={setid}",
        summary="Full FDA-submitted product label, including warnings. "
        "Written for health professionals.",
        version=f"setid {setid}, published {label.get('published_date', 'date not given')}",
    )
