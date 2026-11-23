"""Drug lookup and general education, with source, freshness and fallback.

Order of preference for each source: fresh live cache, new live fetch, stale
live cache (labelled stale), bundled reference card (labelled bundled). The
caller always learns which one it got and when it was retrieved.
"""
from __future__ import annotations

import difflib
import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import clock
from ...config import settings
from ...integrations import dailymed, medlineplus, openfda, rxnorm
from ...integrations.base import ReferenceLink, ReferenceUnavailable
from ...models import DrugContentCache, MedicationCatalog

GENERAL_NOTICE = "This is general information, not personal medical advice."
ASK_PHARMACIST = "Your pharmacist or prescriber can answer questions about your own prescription."

FETCHERS = {
    medlineplus.SOURCE: medlineplus.education_link,
    dailymed.SOURCE: dailymed.label_reference,
    openfda.SOURCE: openfda.label_reference,
}

# After a failed call, leave a source alone for a few minutes so an outage
# does not slow every answer.
_down_until: dict[str, datetime] = {}
DOWN_COOLDOWN = timedelta(minutes=5)
AMBIGUITY_MARGIN = 0.12


def reset_outage_memory() -> None:
    _down_until.clear()


def _source_dict(row: DrugContentCache, now: datetime) -> dict:
    return {
        "source": row.source,
        "title": row.title,
        "url": row.url,
        "summary": row.summary,
        "version": row.version,
        "origin": row.origin,
        "fetched_at": row.fetched_at.isoformat(),
        "expires_at": row.expires_at.isoformat(),
        "stale": row.origin == "live" and row.expires_at <= now,
    }


def _fetch_live(source: str, rxcui: str, name: str, now: datetime) -> ReferenceLink | None:
    if not settings.live_reference_apis:
        return None
    if _down_until.get(source, now) > now:
        return None
    try:
        return FETCHERS[source](rxcui, name)
    except ReferenceUnavailable:
        _down_until[source] = now + DOWN_COOLDOWN
        return None


def get_education(db: Session, rxcui: str) -> dict | None:
    """Sourced general information for a drug in the reference catalog."""
    drug = db.get(MedicationCatalog, rxcui)
    if drug is None:
        return None
    now = clock.now()
    sources: list[dict] = []
    for source in FETCHERS:
        row = db.scalar(
            select(DrugContentCache).where(
                DrugContentCache.rxcui == rxcui, DrugContentCache.source == source
            )
        )
        fresh = row is not None and row.origin == "live" and row.expires_at > now
        if not fresh:
            link = _fetch_live(source, rxcui, drug.generic_name, now)
            if link is not None:
                if row is None:
                    row = DrugContentCache(rxcui=rxcui, source=source, fetched_at=now, expires_at=now)
                    db.add(row)
                row.title, row.url, row.version = link.title[:300], link.url[:500], link.version[:80]
                # Keep a reviewed bundled summary when the live source has none.
                row.summary = link.summary or row.summary or ""
                row.origin = "live"
                row.fetched_at = now
                row.expires_at = now + timedelta(hours=settings.reference_cache_hours)
        if row is not None:
            sources.append(_source_dict(row, now))
    db.commit()
    return {
        "rxcui": drug.rxcui,
        "drug": {
            "name": drug.name,
            "generic_name": drug.generic_name,
            "brand_names": drug.brand_names,
            "strength": drug.strength,
            "dose_form": drug.dose_form,
            "identity_source": drug.provenance,
        },
        "notice": GENERAL_NOTICE,
        "ask": ASK_PHARMACIST,
        "sources": sources,
        "retrieved_at": now.isoformat(),
    }


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).strip()


def _local_candidates(db: Session, query: str) -> list[dict]:
    wanted = _normalize(query)
    results = []
    for drug in db.scalars(select(MedicationCatalog)).all():
        names = [drug.generic_name, *drug.brand_names]
        best = max(
            difflib.SequenceMatcher(None, wanted, _normalize(name)).ratio() for name in names
        )
        if any(_normalize(name) in wanted.split() or _normalize(name) == wanted for name in names):
            best = 1.0
        if best >= 0.72:
            results.append(
                {"rxcui": drug.rxcui, "name": drug.name, "score": round(best, 3), "source": "local catalog"}
            )
    return sorted(results, key=lambda item: -item["score"])


def search_drugs(db: Session, query: str) -> dict:
    """Normalize a name to candidates and say plainly when it is ambiguous.

    The caller must ask the person to choose when `needs_disambiguation` is
    true. A name alone rarely identifies strength and form.
    """
    query = query.strip()
    candidates: list[dict] = []
    origin = "local catalog"
    now = clock.now()
    if settings.live_reference_apis and _down_until.get(rxnorm.SOURCE, now) <= now:
        try:
            live = rxnorm.approximate(query)
            if live:
                top = live[0].score or 1.0
                candidates = [
                    {
                        "rxcui": c.rxcui,
                        "name": c.name,
                        "score": round(c.score / top, 3),
                        "source": rxnorm.SOURCE,
                    }
                    for c in live
                ]
                origin = rxnorm.SOURCE
        except ReferenceUnavailable:
            _down_until[rxnorm.SOURCE] = now + DOWN_COOLDOWN
    if not candidates:
        candidates = _local_candidates(db, query)
    close = [c for c in candidates if candidates[0]["score"] - c["score"] <= AMBIGUITY_MARGIN]
    return {
        "query": query,
        "origin": origin,
        "candidates": candidates,
        "match": candidates[0] if len(close) == 1 else None,
        "needs_disambiguation": len(close) > 1,
        "not_found": not candidates,
        "note": "Reference identity only. This is not a prescription record.",
    }
