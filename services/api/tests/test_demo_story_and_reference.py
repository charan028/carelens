"""The blueprint's demo story end to end, and the public reference adapters
(exercised against recorded response shapes; no network in tests)."""
import httpx
import pytest
from sqlalchemy import select

from app import clock
from app.config import settings
from app.integrations import base, dailymed, medlineplus, openfda, rxnorm
from app.models import Bill, BillStatus, DoseEvent, DrugContentCache, NotificationOutbox
from app.services.medication import education

from .conftest import DANIEL, PRIYA, ROSA, advance, auth, say


# --- demo story (blueprint section 2) ------------------------------------------


def test_demo_story_steps_one_to_six(client, db):
    advance(db, 10)  # 08:05; the 08:00 reminder has gone out
    notes = client.get("/v1/notifications", headers=auth(ROSA)).json()["notifications"]
    assert [n["kind"] for n in notes] == ["dose_reminder"]

    # 1-2. "What should I do this morning?" -> one verified medicine and the bill.
    briefing = say(client, "What should I do this morning?")
    assert briefing["intent"] == "today_briefing"
    assert "lisinopril 10 mg" in briefing["text"]
    assert '"Take 1 tablet by mouth once daily in the morning."' in briefing["text"]  # verbatim record
    assert "Lakeshore Electric bill of $84.20 is due Saturday, November 28" in briefing["text"]
    assert "metformin" not in briefing["text"]  # evening medicine is not a morning task
    context = {"focus_dose_event_id": briefing["focus"]["dose_event_id"]}

    # 3. "What is this medicine used for?" -> identity, general-info notice, source.
    info = say(client, "What is this medicine used for?", context)
    assert info["intent"] == "medication_education"
    assert "general information, not personal medical advice" in info["text"]
    source = info["sources"][0]
    assert source["source"] == "MedlinePlus" and source["url"].startswith("https://medlineplus.gov/")
    assert source["title"] and source["fetched_at"]
    assert source["origin"] == "bundled" and "saved reference card" in info["text"]  # honest offline label

    # 4. "I took it." -> a self-report, not verified ingestion.
    took = say(client, "I took it.", context)
    assert took["changed"] is True and "your own report" in took["text"]
    dose = db.get(DoseEvent, context["focus_dose_event_id"])
    assert dose.status == "self_reported_taken" and dose.origin == "self_report"

    # 5. "Remind me before my electricity bill is due." -> a reminder, not a payment.
    remind = say(client, "Remind me before my electricity bill is due.")
    assert "create_bill_reminder" in remind["tool_calls"] and "cannot pay" in remind["text"]
    assert "Friday, November 27 at 9:00 AM" in remind["text"]
    assert db.scalar(select(Bill)).status == BillStatus.UPCOMING

    # 6. Caregiver sees the status only because access was granted.
    daniel = client.get("/v1/caregiver/summary", headers=auth(DANIEL)).json()["seniors"][0]
    shown = next(m for m in daniel["medications"] if m["dose_event_id"] == dose.id)
    assert shown["status_label"] == "Self-reported taken"
    assert client.get("/v1/caregiver/summary", headers=auth(PRIYA)).json()["seniors"] == []


def test_missed_confirmation_raises_a_consented_alert(client, db):
    advance(db, 45)
    daniel = client.get("/v1/caregiver/summary", headers=auth(DANIEL)).json()["seniors"][0]
    assert len(daniel["alerts"]) == 1 and "has not confirmed" in daniel["alerts"][0]["title"]
    assert client.get("/v1/notifications", headers=auth(PRIYA)).json()["notifications"] == []


def test_two_medicines_due_together_asks_which_one(client, db):
    from app.models import DoseSchedule

    evening = db.scalar(select(DoseSchedule).where(DoseSchedule.prescription_id == "rx_d1002"))
    evening.local_time = "08:00"
    db.query(DoseEvent).filter(DoseEvent.schedule_id == evening.id).delete()
    db.commit()
    advance(db, 10)
    reply = say(client, "I took it")
    assert reply["options"] == ["lisinopril", "metformin"] and reply["changed"] is False
    one = say(client, "I took the metformin")
    assert "metformin 500 mg" in one["text"] and "lisinopril" not in one["text"]


def test_nothing_is_recorded_when_no_medicine_is_waiting(client, db):
    advance(db, 10)
    say(client, "I took it")
    again = say(client, "I took it", {})
    assert again["changed"] is False and "haven't recorded anything" in again["text"]


def test_unknown_medicine_name_is_not_guessed(client):
    reply = say(client, "what is zorblaxin used for")
    assert reply["text"].startswith("I cannot confirm which medicine this is")
    assert reply["sources"] == []


def test_repeat_and_help(client):
    first = say(client, "what bills do I have")
    again = say(client, "say that again", {"last_reply": first["text"]})
    assert again["text"] == first["text"]
    assert "remind you before a bill is due" in say(client, "what can you do")["text"]


def test_overlong_or_empty_input_is_rejected(client):
    assert client.post("/v1/assistant/message", json={"text": "x" * 401}, headers=auth(ROSA)).status_code == 422
    assert client.post("/v1/assistant/message", json={"text": ""}, headers=auth(ROSA)).status_code == 422


# --- reference adapters ------------------------------------------------------------

MEDLINE = {
    "feed": {
        "updated": {"_value": "2026-11-23T17:21:11Z"},
        "entry": [
            {
                "title": {"_value": "Lisinopril"},
                "link": [{"href": "https://medlineplus.gov/druginfo/meds/a692051.html?utm_source=mplusconnect"}],
                "summary": {"_value": "<p>Lisinopril is used alone or with other medications.</p><script>x</script>"},
            }
        ],
    }
}
DAILYMED = {
    "data": [
        {
            "spl_version": 15,
            "published_date": "Nov 16, 2026",
            "title": "LISINOPRIL TABLET [EXAMPLE LABELER]",
            "setid": "03a497fe-fb09-4b2c-8ee0-2019600192b8",
        }
    ]
}
OPENFDA = {"results": [{"set_id": "abc", "effective_time": "20260101", "openfda": {"brand_name": ["Zestril"]}}]}
APPROX = {
    "approximateGroup": {
        "candidate": [
            {"rxcui": "314076", "score": "9.1", "name": "lisinopril 10 MG Oral Tablet"},
            {"rxcui": "314077", "score": "9.0", "name": "lisinopril 20 MG Oral Tablet"},
            {"rxcui": "314076", "score": "9.1", "name": "lisinopril 10 MG Oral Tablet"},
        ]
    }
}


def mock_sources(routes: dict, calls: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request.url.host)
        for fragment, payload in routes.items():
            if fragment in str(request.url):
                if isinstance(payload, int):
                    return httpx.Response(payload)
                if isinstance(payload, Exception):
                    raise payload
                return httpx.Response(200, json=payload)
        return httpx.Response(404)

    base.set_client(httpx.Client(transport=httpx.MockTransport(handler)))
    settings.live_reference_apis = True


def test_adapters_parse_live_response_shapes():
    mock_sources({"connect.medlineplus": MEDLINE, "dailymed": DAILYMED, "api.fda.gov": OPENFDA, "approximateTerm": APPROX})
    link = medlineplus.education_link("314076")
    assert link.title == "Lisinopril" and "<" not in link.summary and "script" not in link.summary.lower().replace("x", "")
    label = dailymed.label_reference("314076", "lisinopril")
    assert "03a497fe" in label.url and "published Nov 16, 2026" in label.version
    assert openfda.label_reference("314076", "lisinopril").title == "FDA label record: Zestril"
    candidates = rxnorm.approximate("lisinopril")
    assert [c.rxcui for c in candidates] == ["314076", "314077"]  # de-duplicated, best first


def test_name_with_several_strengths_needs_disambiguation(db):
    mock_sources({"approximateTerm": APPROX})
    result = education.search_drugs(db, "lisinopril")
    assert result["origin"] == "RxNorm"
    assert result["needs_disambiguation"] is True and result["match"] is None


def test_search_falls_back_to_local_catalog_when_rxnorm_is_down(db):
    mock_sources({"approximateTerm": 503})
    result = education.search_drugs(db, "lisinoprl")  # typo
    assert result["origin"] == "local catalog"
    assert result["match"]["rxcui"] == "314076"
    assert education.search_drugs(db, "zorblaxin")["not_found"] is True


def test_live_content_is_cached_with_source_and_freshness(db):
    calls: list = []
    mock_sources({"connect.medlineplus": MEDLINE, "dailymed": DAILYMED, "api.fda.gov": OPENFDA}, calls)
    first = education.get_education(db, "314076")
    by_source = {s["source"]: s for s in first["sources"]}
    assert set(by_source) == {"MedlinePlus", "DailyMed", "openFDA"}
    for source in by_source.values():
        assert source["origin"] == "live" and source["stale"] is False
        assert source["fetched_at"] and source["expires_at"] > source["fetched_at"]
        assert base.safe_url(source["url"])
    assert first["drug"]["identity_source"].startswith("RxNorm SCD 314076")
    made = len(calls)
    education.get_education(db, "314076")
    assert len(calls) == made  # served from cache; no second round of requests


def test_outage_serves_stale_cache_labelled_stale(db):
    from datetime import timedelta

    mock_sources({"connect.medlineplus": MEDLINE, "dailymed": DAILYMED, "api.fda.gov": OPENFDA})
    education.get_education(db, "314076")
    clock.advance(timedelta(hours=settings.reference_cache_hours + 1))
    mock_sources({"connect.medlineplus": httpx.ConnectTimeout("slow"), "dailymed": 500, "api.fda.gov": 500})
    stale = education.get_education(db, "314076")
    assert all(s["stale"] and s["origin"] == "live" for s in stale["sources"])


def test_outage_with_no_cache_serves_bundled_card_labelled_bundled(db):
    mock_sources({"connect.medlineplus": 503, "dailymed": 503, "api.fda.gov": 503})
    info = education.get_education(db, "861007")
    assert [s["origin"] for s in info["sources"]] == ["bundled", "bundled"]
    assert info["notice"].startswith("This is general information")


def test_link_to_unexpected_host_is_rejected():
    poisoned = {"feed": {"entry": [{"title": {"_value": "Lisinopril"}, "link": [{"href": "https://medlineplus.gov.evil.example/x"}], "summary": {"_value": ""}}]}}
    mock_sources({"connect.medlineplus": poisoned})
    with pytest.raises(base.ReferenceUnavailable):
        medlineplus.education_link("314076")
    assert not base.safe_url("http://medlineplus.gov/x")  # https only
    assert not base.safe_url("javascript:alert(1)")


def test_education_endpoint_validates_rxcui_and_404s_unknown(client):
    assert client.get("/v1/drugs/abc/education", headers=auth(ROSA)).status_code == 422
    assert client.get("/v1/drugs/999999/education", headers=auth(ROSA)).status_code == 404
    ok = client.get("/v1/drugs/314076/education", headers=auth(ROSA)).json()
    assert ok["rxcui"] == "314076" and ok["sources"]


def test_reference_cards_cover_every_seeded_prescription(db):
    carded = {c.rxcui for c in db.scalars(select(DrugContentCache).where(DrugContentCache.source == "MedlinePlus"))}
    assert {"314076", "861007", "617310"} <= carded
