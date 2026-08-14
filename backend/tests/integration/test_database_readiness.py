import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
def test_ready_reports_healthy_database(client: TestClient) -> None:
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
