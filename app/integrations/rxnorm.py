"""NLM RxNorm / RxNav: normalize a typed or spoken medicine name to an RxCUI.

Docs: https://lhncbc.nlm.nih.gov/RxNav/APIs/
The RxNav drug-interaction API was retired on January 2, 2024 and is not used.
"""
from __future__ import annotations

from dataclasses import dataclass

from .base import ReferenceUnavailable, get_json

BASE = "https://rxnav.nlm.nih.gov/REST"
SOURCE = "RxNorm"


@dataclass
class Candidate:
    rxcui: str
    name: str
    score: float
    source: str = SOURCE


def properties(rxcui: str) -> dict | None:
    data = get_json(f"{BASE}/rxcui/{rxcui}/properties.json")
    return data.get("properties") or None


def approximate(term: str, max_entries: int = 6) -> list[Candidate]:
    """Best-effort matches for a name, best first, de-duplicated by RxCUI."""
    data = get_json(
        f"{BASE}/approximateTerm.json", {"term": term, "maxEntries": max_entries, "option": 1}
    )
    raw = (data.get("approximateGroup") or {}).get("candidate") or []
    seen: dict[str, Candidate] = {}
    for item in raw:
        rxcui = str(item.get("rxcui") or "")
        if not rxcui or rxcui in seen:
            continue
        try:
            score = float(item.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        name = item.get("name") or ""
        if not name:
            try:
                name = (properties(rxcui) or {}).get("name", "")
            except ReferenceUnavailable:
                name = ""
        if name:
            seen[rxcui] = Candidate(rxcui=rxcui, name=name, score=score)
    return sorted(seen.values(), key=lambda c: -c.score)
