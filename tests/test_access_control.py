"""Acceptance: an unauthorized caregiver cannot see or modify another user's
records; sharing is scoped, revocable and audited."""
from sqlalchemy import select

from app.auth import issue_token
from app.models import AuditEvent, Bill, DoseEvent, NotificationOutbox

from .conftest import DANIEL, PRIYA, ROSA, SAM, advance, auth

SENIOR_ONLY_GETS = ["/v1/me/today", "/v1/medications", "/v1/bills", "/v1/consents", "/v1/audit"]


def test_requests_without_a_valid_token_are_rejected(client):
    for path in SENIOR_ONLY_GETS + ["/v1/caregiver/summary", "/v1/drugs/search?q=lisinopril"]:
        assert client.get(path).status_code == 401, path
    forged = {"Authorization": f"Bearer {ROSA}.{'0' * 40}"}
    assert client.get("/v1/me/today", headers=forged).status_code == 401
    swapped = {"Authorization": "Bearer " + issue_token(DANIEL).replace(DANIEL, ROSA)}
    assert client.get("/v1/me/today", headers=swapped).status_code == 401


def test_caregivers_and_verifiers_cannot_use_senior_endpoints(client, db):
    dose_id = db.scalar(select(DoseEvent.id))
    bill_id = db.scalar(select(Bill.id))
    for who in (DANIEL, PRIYA, SAM):
        for path in SENIOR_ONLY_GETS:
            assert client.get(path, headers=auth(who)).status_code == 403, (who, path)
        assert client.post(
            f"/v1/dose-events/{dose_id}/self-report", json={"status": "taken"}, headers=auth(who)
        ).status_code == 403
        assert client.post(
            f"/v1/bills/{bill_id}/status", json={"status": "self_reported_paid"}, headers=auth(who)
        ).status_code == 403
        assert client.post(
            "/v1/assistant/message", json={"text": "what should I do today"}, headers=auth(who)
        ).status_code == 403
        assert client.post(
            "/v1/consents",
            json={"action": "grant", "caregiver_id": who, "scopes": ["medications", "bills"]},
            headers=auth(who),
        ).status_code == 403


def test_caregiver_without_a_grant_sees_nothing(client):
    summary = client.get("/v1/caregiver/summary", headers=auth(PRIYA)).json()
    assert summary["seniors"] == []
    assert "Rosa" not in str(summary)


def test_granted_caregiver_sees_only_granted_categories(client):
    view = client.get("/v1/caregiver/summary", headers=auth(DANIEL)).json()["seniors"][0]
    assert sorted(view["scopes"]) == ["appointments", "medications"]
    assert "medications" in view and "appointments" in view
    assert "bills" not in view  # Rosa did not share bills
    assert "Lakeshore" not in str(view)


def test_caregiver_view_separates_self_report_from_verification(client, db):
    advance(db, 10)
    dose_id = client.get("/v1/me/today", headers=auth(ROSA)).json()["medications"][0]["dose_event_id"]
    client.post(f"/v1/dose-events/{dose_id}/self-report", json={"status": "taken"}, headers=auth(ROSA))
    view = client.get("/v1/caregiver/summary", headers=auth(DANIEL)).json()["seniors"][0]
    dose = next(m for m in view["medications"] if m["dose_event_id"] == dose_id)
    assert dose["status"] == "self_reported_taken"
    assert dose["self_reported"] is True and dose["independently_verified"] is False


def test_revoking_access_takes_effect_immediately(client):
    assert client.get("/v1/caregiver/summary", headers=auth(DANIEL)).json()["seniors"]
    client.post("/v1/consents", json={"action": "revoke", "caregiver_id": DANIEL}, headers=auth(ROSA))
    assert client.get("/v1/caregiver/summary", headers=auth(DANIEL)).json()["seniors"] == []


def test_senior_can_change_scopes_and_private_bills_stay_hidden(client, db):
    client.post(
        "/v1/bills",
        json={"payee_label": "Private Matter (synthetic)", "due_date": "2026-11-30", "private": True},
        headers=auth(ROSA),
    )
    client.post(
        "/v1/consents",
        json={"action": "grant", "caregiver_id": DANIEL, "scopes": ["bills"], "alerts_enabled": False},
        headers=auth(ROSA),
    )
    view = client.get("/v1/caregiver/summary", headers=auth(DANIEL)).json()["seniors"][0]
    assert view["scopes"] == ["bills"] and "medications" not in view
    labels = [bill["payee_label"] for bill in view["bills"]]
    assert labels == ["Lakeshore Electric (synthetic)"]


def test_consent_rejects_unknown_scope_and_non_caregiver(client):
    bad_scope = client.post(
        "/v1/consents",
        json={"action": "grant", "caregiver_id": DANIEL, "scopes": ["everything"]},
        headers=auth(ROSA),
    )
    assert bad_scope.status_code == 422
    not_caregiver = client.post(
        "/v1/consents", json={"action": "grant", "caregiver_id": SAM, "scopes": ["bills"]}, headers=auth(ROSA)
    )
    assert not_caregiver.status_code == 404


def test_escalation_alerts_only_consented_caregivers(client, db):
    advance(db, 45)  # 08:40: two reminders unanswered for a full interval
    alerts = db.scalars(select(NotificationOutbox).where(NotificationOutbox.kind == "caregiver_alert")).all()
    assert [a.target_user_id for a in alerts] == [DANIEL]
    assert "not an emergency alert" in alerts[0].body
    assert "does not mean a dose was missed" in alerts[0].body
    assert "lisinopril" not in (alerts[0].title + alerts[0].body).lower()  # no drug name in the alert


def test_no_escalation_when_senior_turns_it_off_or_revokes_alerts(client, db):
    client.post(
        "/v1/me/escalation",
        json={"escalation_enabled": False, "escalation_after_reminders": 2},
        headers=auth(ROSA),
    )
    advance(db, 120)
    assert db.scalars(select(NotificationOutbox).where(NotificationOutbox.kind == "caregiver_alert")).all() == []


def test_alert_scope_is_checked_at_send_time(client, db):
    client.post(
        "/v1/consents",
        json={"action": "grant", "caregiver_id": DANIEL, "scopes": ["appointments"], "alerts_enabled": True},
        headers=auth(ROSA),
    )
    advance(db, 120)
    assert db.scalars(select(NotificationOutbox).where(NotificationOutbox.kind == "caregiver_alert")).all() == []


def test_permission_changes_and_caregiver_views_are_audited(client, db):
    client.get("/v1/caregiver/summary", headers=auth(DANIEL))
    client.post("/v1/consents", json={"action": "revoke", "caregiver_id": DANIEL}, headers=auth(ROSA))
    client.post(
        "/v1/consents",
        json={"action": "grant", "caregiver_id": PRIYA, "scopes": ["medications"], "alerts_enabled": False},
        headers=auth(ROSA),
    )
    actions = [e["action"] for e in client.get("/v1/audit", headers=auth(ROSA)).json()["events"]]
    assert {"caregiver.view", "consent.revoke", "consent.grant"} <= set(actions)


def test_audit_log_holds_no_clinical_or_spoken_content(client, db):
    client.post(
        "/v1/assistant/message",
        json={"text": "what is lisinopril used for, my secret phrase is zebra"},
        headers=auth(ROSA),
    )
    blob = " ".join(f"{e.action} {e.resource_id} {e.detail}" for e in db.scalars(select(AuditEvent)).all()).lower()
    for term in ("zebra", "lisinopril", "metformin", "tablet", "by mouth"):
        assert term not in blob


def test_dev_endpoints_are_hidden_outside_demo(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "env", "production")
    assert client.post("/v1/dev/reset").status_code == 404
    assert client.post("/v1/dev/clock", json={"advance_minutes": 5}).status_code == 404
    assert client.get("/v1/auth/demo-users").status_code == 404
    assert client.post("/v1/auth/demo-login", json={"user_id": ROSA}).status_code == 404


def test_health_has_no_sensitive_payload(client):
    assert client.get("/health").json() == {"status": "ok"}
