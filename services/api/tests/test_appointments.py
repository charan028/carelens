"""Manual appointment entry (blueprint 3C): the senior adds one, it shows on
today's list and the consented caregiver view, and a reminder goes out."""
from datetime import timedelta

from app import clock

from .conftest import CHICAGO, DANIEL, PRIYA, ROSA, advance, auth


def _local_today():
    return clock.now().astimezone(CHICAGO).date()


def test_senior_adds_an_appointment_and_is_reminded(client, db):
    tomorrow = _local_today() + timedelta(days=1)
    created = client.post(
        "/v1/appointments",
        json={"title": "Eye exam (synthetic)", "starts_on": str(tomorrow), "local_time": "14:00", "location": "Oak Clinic (synthetic)"},
        headers=auth(ROSA),
    )
    assert created.status_code == 201, created.text
    assert created.json()["local_time"] == "2:00 PM"

    titles = [a["title"] for a in client.get("/v1/me/today", headers=auth(ROSA)).json()["appointments"]]
    assert "Eye exam (synthetic)" in titles

    # Daniel has the "appointments" scope; Priya has nothing.
    daniel = client.get("/v1/caregiver/summary", headers=auth(DANIEL)).json()["seniors"][0]
    assert any(a["title"] == "Eye exam (synthetic)" for a in daniel["appointments"])
    assert client.get("/v1/caregiver/summary", headers=auth(PRIYA)).json()["seniors"] == []

    # Reminder 120 minutes before: 12:00 tomorrow. From 07:55 that is 28h05; go to 12:05.
    advance(db, 28 * 60 + 10, step=60)
    notes = client.get("/v1/notifications", headers=auth(ROSA)).json()["notifications"]
    assert any(n["kind"] == "appointment_reminder" and n["title"] == "Eye exam (synthetic)" for n in notes)

    actions = [e["action"] for e in client.get("/v1/audit", headers=auth(ROSA)).json()["events"]]
    assert "appointment.create" in actions


def test_appointment_input_is_validated(client):
    yesterday = _local_today() - timedelta(days=1)
    past = client.post(
        "/v1/appointments",
        json={"title": "Too late (synthetic)", "starts_on": str(yesterday), "local_time": "09:00"},
        headers=auth(ROSA),
    )
    assert past.status_code == 422
    bad = client.post(
        "/v1/appointments",
        json={"title": "", "starts_on": "soon", "local_time": "25:99"},
        headers=auth(ROSA),
    )
    assert bad.status_code == 422
    # Only the older adult adds to their own list.
    forbidden = client.post(
        "/v1/appointments",
        json={"title": "Not yours (synthetic)", "starts_on": str(_local_today() + timedelta(days=1)), "local_time": "09:00"},
        headers=auth(DANIEL),
    )
    assert forbidden.status_code == 403
