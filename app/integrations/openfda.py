"""openFDA: advisory metadata only (label record and recall reports).

Docs: https://open.fda.gov/apis/drug/
openFDA is not a medical decision engine. A missing recall record does not
mean a product is safe, and nothing here is used to clear a drug or a
combination of drugs.
"""
from __future__ import annotations

from .base import ReferenceLink, ReferenceUnavailable, get_json

LABEL_URL = "https://api.fda.gov/drug/label.json"
ENFORCEMENT_URL = "https://api.fda.gov/drug/enforcement.json"
SOURCE = "openFDA"


def label_reference(rxcui: str, name: str = "") -> ReferenceLink:
    data = get_json(LABEL_URL, {"search": f'openfda.rxcui:"{rxcui}"', "limit": 1})
    results = data.get("results") or []
    if not results:
        raise ReferenceUnavailable("no openFDA label for this RxCUI")
    record = results[0]
    setid = record.get("set_id", "")
    brand = ((record.get("openfda") or {}).get("brand_name") or [name])[0]
    return ReferenceLink(
        source=SOURCE,
        title=f"FDA label record: {brand}",
        url=f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={setid}"
        if setid
        else "https://open.fda.gov/apis/drug/label/",
        summary="FDA-submitted labeling record. Advisory reference only.",
        version=f"effective {record.get('effective_time', 'date not given')}",
    )


def recent_recalls(generic_name: str, limit: int = 3) -> list[dict]:
    """Recall reports mentioning a generic name. Advisory; may be out of date."""
    data = get_json(
        ENFORCEMENT_URL,
        {
            "search": f'openfda.generic_name:"{generic_name}"',
            "sort": "report_date:desc",
            "limit": limit,
        },
    )
    return [
        {
            "recall_number": item.get("recall_number", ""),
            "status": item.get("status", ""),
            "classification": item.get("classification", ""),
            "report_date": item.get("report_date", ""),
            "reason": (item.get("reason_for_recall") or "")[:240],
        }
        for item in data.get("results") or []
    ]
