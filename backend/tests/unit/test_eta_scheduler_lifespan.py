from fastapi.testclient import TestClient

from app.main import app


def test_api_lifespan_serves_health_without_scheduler() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_main_does_not_start_scheduler() -> None:
    from pathlib import Path

    text = Path(__file__).resolve().parents[2] / "app" / "main.py"
    source = text.read_text()
    assert "eta_scheduler_service" not in source
    assert "scheduler_runtime" not in source
