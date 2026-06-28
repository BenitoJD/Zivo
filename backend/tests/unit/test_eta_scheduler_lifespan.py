from fastapi.testclient import TestClient

from app.main import app


def test_lifespan_starts_and_stops_scheduler(monkeypatch) -> None:
    events: list[str] = []

    class _RecordingService:
        def start(self) -> None:
            events.append("start")

        def stop(self) -> None:
            events.append("stop")

    monkeypatch.setattr("app.main.eta_scheduler_service", _RecordingService())

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert events == ["start", "stop"]
