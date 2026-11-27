"""Hosted mode: an access code, and a private copy of the demo per tester."""
from datetime import datetime, timedelta, timezone

import pytest

from app import clock, config, main, sandbox
from app.config import settings

CODE = "open-sesame-2026"


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setattr(settings, "sandboxes", True)
    monkeypatch.setattr(settings, "access_code", CODE)
    monkeypatch.setattr(settings, "secret_key", "a-long-private-test-secret-value")
    from app.api.v1 import shared

    shared._attempts.clear()
    sandbox.init()
    yield
    sandbox.reset_for_tests()
    clock.on_change(None)


def enter(client, code: str = CODE):
    return client.post("/v1/sandbox", json={"access_code": code})


def headers(client, sandbox_token: str, user_id: str | None = None) -> dict:
    result = {"X-Sandbox": sandbox_token}
    if user_id:
        login = client.post("/v1/auth/demo-login", json={"user_id": user_id}, headers=result)
        assert login.status_code == 200, login.text
        result["Authorization"] = f"Bearer {login.json()['token']}"
    return result


def new_tester(client, user_id: str = "usr_rosa") -> dict:
    return headers(client, enter(client).json()["sandbox_token"], user_id)


def first_dose(client, tester: dict) -> dict:
    return client.get("/v1/me/today", headers=tester).json()["medications"][0]


# --- the gate -----------------------------------------------------------------


def test_local_mode_has_no_gate(client):
    assert client.get("/v1/auth/config").json() == {"sandboxes": False, "access_code_required": False}
    assert client.post("/v1/sandbox", json={"access_code": CODE}).status_code == 404


def test_nothing_is_reachable_without_a_sandbox(client, hosted):
    assert client.get("/v1/auth/config").json()["access_code_required"] is True
    for method, path in [
        ("get", "/v1/auth/demo-users"),
        ("post", "/v1/auth/demo-login"),
        ("get", "/v1/me/today"),
        ("get", "/v1/caregiver/summary"),
        ("post", "/v1/dev/reset"),
        ("post", "/v1/dev/clock"),
    ]:
        response = getattr(client, method)(path, **({"json": {"user_id": "usr_rosa"}} if method == "post" else {}))
        assert response.status_code == 401, path
        assert response.headers["X-CareLens-Sandbox"] == "required"


def test_wrong_access_code_is_refused(client, hosted):
    for guess in ("", "open-sesame", CODE.upper(), CODE + "x"):
        assert enter(client, guess).status_code == 403
    assert sandbox.count() == 0


def test_access_code_guessing_is_throttled(client, hosted):
    statuses = [enter(client, "nope").status_code for _ in range(12)]
    assert statuses[:8] == [403] * 8
    assert set(statuses[8:]) == {429}
    assert enter(client).status_code == 429  # even the right code waits


def test_right_code_gives_a_seeded_private_copy(client, hosted):
    response = enter(client)
    assert response.status_code == 201
    tester = headers(client, response.json()["sandbox_token"], "usr_rosa")
    today = client.get("/v1/me/today", headers=tester).json()
    assert [m["name"] for m in today["medications"]] == ["lisinopril", "metformin"]
    assert today["synthetic_notice"]


def test_forged_or_unknown_sandbox_tokens_are_rejected(client, hosted):
    real = enter(client).json()["sandbox_token"]
    sandbox_id, signature = real.split(".")
    for forged in (f"{sandbox_id}.{'0' * 40}", f"{'a' * 12}.{signature}", "local", "", "../../etc.x"):
        assert client.get("/v1/auth/demo-users", headers={"X-Sandbox": forged}).status_code == 401
    sandbox.drop(sandbox_id)  # correctly signed, but the sandbox is gone
    assert client.get("/v1/auth/demo-users", headers={"X-Sandbox": real}).status_code == 401


# --- isolation ------------------------------------------------------------------


def test_one_testers_actions_never_touch_anothers(client, hosted):
    ana, ben = new_tester(client), new_tester(client)
    dose = first_dose(client, ana)
    done = client.post(
        f"/v1/dose-events/{dose['dose_event_id']}/self-report", json={"status": "taken"}, headers=ana
    )
    assert done.json()["status"] == "self_reported_taken"
    assert first_dose(client, ana)["status"] == "self_reported_taken"
    assert first_dose(client, ben)["status"] in ("scheduled", "reminded", "overdue", "unknown")

    # Ana's dose id means nothing in Ben's copy.
    cross = client.post(
        f"/v1/dose-events/{dose['dose_event_id']}/self-report", json={"status": "taken"}, headers=ben
    )
    assert cross.status_code == 404


def test_restarting_the_day_only_wipes_your_own_copy(client, hosted):
    ana, ben = new_tester(client), new_tester(client)
    client.post("/v1/bills", json={"payee_label": "Ben's water (synthetic)", "due_date": "2026-12-20"}, headers=ben)
    assert client.post("/v1/dev/reset", json={"set_local_time": "07:55"}, headers={"X-Sandbox": ana["X-Sandbox"]}).status_code == 200
    labels = [b["payee_label"] for b in client.get("/v1/bills", headers=ben).json()["bills"]]
    assert "Ben's water (synthetic)" in labels
    assert len(client.get("/v1/bills", headers=ana).json()["bills"]) == 1


def test_each_tester_has_their_own_clock(client, hosted):
    ana, ben = new_tester(client), new_tester(client)
    client.post("/v1/dev/reset", json={"set_local_time": "07:55"}, headers={"X-Sandbox": ana["X-Sandbox"]})
    client.post("/v1/dev/reset", json={"set_local_time": "07:55"}, headers={"X-Sandbox": ben["X-Sandbox"]})
    client.post("/v1/dev/clock", json={"advance_minutes": 10}, headers={"X-Sandbox": ana["X-Sandbox"]})

    ana_today = client.get("/v1/me/today", headers=ana).json()
    ben_today = client.get("/v1/me/today", headers=ben).json()
    assert ana_today["local_now"] == "8:05 AM" and ben_today["local_now"] == "7:55 AM"
    assert ana_today["medications"][0]["status"] == "reminded"
    assert ben_today["medications"][0]["status"] == "scheduled"
    assert len(client.get("/v1/notifications", headers=ana).json()["notifications"]) == 1
    assert client.get("/v1/notifications", headers=ben).json()["notifications"] == []


def test_sign_in_from_one_sandbox_is_useless_in_another(client, hosted):
    ana, ben = new_tester(client), new_tester(client)
    mixed = {"X-Sandbox": ben["X-Sandbox"], "Authorization": ana["Authorization"]}
    assert client.get("/v1/me/today", headers=mixed).status_code == 401
    assert client.get("/v1/me/today", headers=ana).status_code == 200


def test_consent_rules_still_hold_inside_a_sandbox(client, hosted):
    token = enter(client).json()["sandbox_token"]
    daniel, priya = headers(client, token, "usr_daniel"), headers(client, token, "usr_priya")
    assert len(client.get("/v1/caregiver/summary", headers=daniel).json()["seniors"]) == 1
    assert client.get("/v1/caregiver/summary", headers=priya).json()["seniors"] == []
    assert client.get("/v1/me/today", headers=daniel).status_code == 403


# --- engine, limits, cleanup ----------------------------------------------------


def test_engine_runs_every_sandbox_on_its_own_clock(client, hosted):
    ana, ben = new_tester(client), new_tester(client)
    for tester in (ana, ben):
        client.post("/v1/dev/reset", json={"set_local_time": "07:55"}, headers={"X-Sandbox": tester["X-Sandbox"]})
    ana_id = sandbox.verify_token(ana["X-Sandbox"])
    with clock.scope(ana_id):
        clock.advance(timedelta(minutes=10))
    main.run_engine_once()  # what the background loop does
    assert len(client.get("/v1/notifications", headers=ana).json()["notifications"]) == 1
    assert client.get("/v1/notifications", headers=ben).json()["notifications"] == []
    assert clock.sandbox() == clock.LOCAL  # the loop leaves no sandbox entered


def test_sandbox_count_is_capped(client, hosted, monkeypatch):
    monkeypatch.setattr(settings, "sandbox_max", 2)
    assert [enter(client).status_code for _ in range(3)] == [201, 201, 503]


def test_idle_sandboxes_are_removed_and_their_tokens_stop_working(client, hosted):
    old, fresh = new_tester(client), new_tester(client)
    old_id = sandbox.verify_token(old["X-Sandbox"])
    sandbox._last_seen[old_id] = datetime.now(timezone.utc) - timedelta(days=settings.sandbox_idle_days + 1)
    assert sandbox.purge_expired() == 1
    assert client.get("/v1/me/today", headers=old).status_code == 401
    assert client.get("/v1/me/today", headers=fresh).status_code == 200
    assert sandbox.count() == 1


def test_full_demo_makes_room_by_dropping_idle_sandboxes(client, hosted, monkeypatch):
    monkeypatch.setattr(settings, "sandbox_max", 1)
    old_id = sandbox.verify_token(enter(client).json()["sandbox_token"])
    sandbox._last_seen[old_id] = datetime.now(timezone.utc) - timedelta(days=30)
    assert enter(client).status_code == 201
    assert sandbox.count() == 1 and old_id not in sandbox._last_seen


def test_clock_and_registry_survive_a_restart(client, hosted):
    ana = new_tester(client)
    ana_id = sandbox.verify_token(ana["X-Sandbox"])
    with clock.scope(ana_id):
        clock.advance(timedelta(hours=3))
        before = clock.now()
    # "Restart": forget everything held in memory, then start up again.
    clock.forget(ana_id)
    sandbox._last_seen.clear()
    sandbox.init()
    assert ana_id in sandbox.active_ids()
    with clock.scope(ana_id):
        assert abs(clock.now() - before) < timedelta(seconds=5)


# --- configuration guards -------------------------------------------------------


def test_hosted_mode_refuses_weak_configuration(monkeypatch):
    monkeypatch.setattr(settings, "sandboxes", True)
    monkeypatch.setattr(settings, "secret_key", config.DEMO_SECRET)
    monkeypatch.setattr(settings, "access_code", "")
    assert len(settings.hosted_problems()) == 2
    monkeypatch.setattr(settings, "secret_key", "a-long-private-test-secret-value")
    monkeypatch.setattr(settings, "access_code", "123")
    assert len(settings.hosted_problems()) == 1
    monkeypatch.setattr(settings, "access_code", CODE)
    assert settings.hosted_problems() == []


def test_app_will_not_start_hosted_with_the_demo_secret(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(settings, "sandboxes", True)
    monkeypatch.setattr(settings, "access_code", CODE)
    with pytest.raises(RuntimeError, match="Refusing to start"):
        with TestClient(main.app):
            pass


def test_host_style_postgres_urls_use_the_psycopg_driver(monkeypatch):
    for given in ("postgres://u:p@host/db", "postgresql://u:p@host/db"):
        monkeypatch.setenv("CARELENS_DATABASE_URL", given)
        assert config._database_url() == "postgresql+psycopg://u:p@host/db"
    monkeypatch.setenv("CARELENS_DATABASE_URL", "sqlite:///x.db")
    assert config._database_url() == "sqlite:///x.db"


def test_responses_carry_security_headers(client):
    response = client.get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "noindex" in response.headers["X-Robots-Tag"]
    api = client.get("/v1/auth/config")
    assert api.headers["Cache-Control"] == "no-store"


def test_startup_report_never_shows_the_password(monkeypatch):
    from app import db as database

    secret = "S3cretPassw0rd"
    monkeypatch.setattr(
        settings, "database_url",
        f"postgresql+psycopg://postgres.abcdefgh:{secret}@aws-0-us-east-1.pooler.supabase.com:5432/postgres",
    )
    info = database.describe_database()
    assert secret not in str(info)
    assert info["location"] == "aws-0-us-east-1.pooler.supabase.com:5432/postgres"
    assert info["user"] == "postgres.abcdefgh" and "***" in info["url"]
    assert info["hints"] == ["Supabase shared pooler, session mode."]


def test_startup_report_flags_common_supabase_mistakes(monkeypatch):
    from app import db as database

    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://postgres:pw@db.abcdefgh.supabase.co:5432/postgres")
    assert "IPv6-only" in database.describe_database()["hints"][0]
    monkeypatch.setattr(
        settings, "database_url",
        "postgresql+psycopg://postgres.abcdefgh:[YOUR-PASSWORD]@aws-0-us-east-1.pooler.supabase.com:6543/postgres",
    )
    hints = database.describe_database()["hints"]
    assert hints[0] == "Supabase shared pooler, transaction mode."
    assert any("placeholder" in hint for hint in hints)
    monkeypatch.setattr(settings, "database_url", "sqlite:////tmp/x.db")
    assert database.describe_database()["kind"] == "SQLite file"
