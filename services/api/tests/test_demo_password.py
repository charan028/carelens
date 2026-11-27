"""The optional shared password in front of a hosted demo."""
import base64

from app.config import settings


def _basic(password: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"anyone:{password}".encode()).decode()}


def test_password_gate_when_set(client):
    settings.demo_password = "tulip-river-42"
    try:
        assert client.get("/v1/auth/demo-users").status_code == 401
        assert client.get("/v1/auth/demo-users", headers=_basic("wrong")).status_code == 401
        assert client.get("/v1/auth/demo-users", headers=_basic("tulip-river-42")).status_code == 200
        assert client.get("/health").status_code == 200  # the host's health check stays open
    finally:
        settings.demo_password = ""


def test_no_password_means_open(client):
    assert settings.demo_password == ""
    assert client.get("/v1/auth/demo-users").status_code == 200
