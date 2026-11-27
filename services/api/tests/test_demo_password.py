"""The optional shared password in front of a hosted demo: a form that sets a
cookie, so the app's background API calls pass too."""
from app.config import settings


def test_password_gate_when_set(client):
    settings.demo_password = "tulip-river-42"
    try:
        assert client.get("/v1/auth/demo-users").status_code == 401  # API: plain 401
        page = client.get("/")
        assert page.status_code == 401 and "Password" in page.text  # people: the form
        assert client.get("/health").status_code == 200  # the host's health check stays open

        wrong = client.post("/demo-password", data={"password": "nope"})
        assert wrong.status_code == 401 and "isn't right" in wrong.text
        assert client.get("/v1/auth/demo-users").status_code == 401

        right = client.post("/demo-password", data={"password": "tulip-river-42"}, follow_redirects=False)
        assert right.status_code == 303 and "carelens_demo" in right.cookies
        assert "tulip-river-42" not in right.cookies["carelens_demo"]
        # The TestClient keeps the cookie, like a browser.
        assert client.get("/v1/auth/demo-users").status_code == 200
        assert client.get("/").status_code == 200
    finally:
        settings.demo_password = ""


def test_no_password_means_open(client):
    assert settings.demo_password == ""
    assert client.get("/v1/auth/demo-users").status_code == 200
