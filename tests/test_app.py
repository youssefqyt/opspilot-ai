
from fastapi.testclient import TestClient
import httpx

from app import main
from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_diagnose_requires_valid_incident():
    response = client.post(
        "/api/diagnose",
        json={"incident": "ba"},
    )
    assert response.status_code == 422


def test_remote_health_url_is_rejected():
    response = client.post(
        "/api/diagnose",
        json={
            "incident": "My service is not responding",
            "health_url": "http://example.com",
        },
    )
    assert response.status_code == 400


def test_diagnose_uses_agent_and_returns_dashboard_shape(monkeypatch):
    async def fake_run_agent(incident, logs, health_url):
        assert incident == "My service is not responding"
        assert logs == "ERROR connection refused"
        assert health_url == "http://127.0.0.1:4000/api/health"
        return {
            "summary": "Local service check failed.",
            "tool_trace": [
                {
                    "tool": "check_local_service",
                    "result": {"healthy": False},
                }
            ],
        }

    monkeypatch.setattr(main, "run_agent", fake_run_agent)
    response = client.post(
        "/api/diagnose",
        json={
            "incident": "My service is not responding",
            "logs": "ERROR connection refused",
            "health_url": "http://127.0.0.1:4000/api/health",
        },
    )

    assert response.status_code == 200
    assert response.json()["diagnosis"]["summary"] == "Local service check failed."
    assert response.json()["health_check"] == {"healthy": False}


def test_report_endpoint_forwards_report_and_returns_pdf(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, timeout):
            assert timeout == 45.0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json, headers):
            captured.update(url=url, json=json, headers=headers)
            return httpx.Response(
                200,
                headers={"content-type": "application/pdf"},
                content=b"%PDF-1.4 test",
            )

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(main, "REPORT_SERVICE_URL", "http://report-service:3001")
    monkeypatch.setattr(main, "REPORT_API_TOKEN", "test-token")

    response = client.post(
        "/api/reports",
        json={
            "incident": "I" * 2501,
            "summary": "Disk space is nearly exhausted.",
            "severity": "high",
            "confidence": "medium",
            "evidence": ["Disk usage: 99.7%"],
            "recommendations": ["Free disk space."],
            "model": "test-model",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content == b"%PDF-1.4 test"
    assert captured["url"] == "http://report-service:3001/reports"
    assert captured["headers"]["Authorization"] == "Bearer test-token"
    assert len(captured["json"]["incident"]) == 2501
    assert captured["json"]["summary"] == "Disk space is nearly exhausted."
