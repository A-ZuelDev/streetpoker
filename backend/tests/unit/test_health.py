from typing import NoReturn

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session


def test_health_reports_liveness_without_database(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_if_engine_is_requested() -> NoReturn:
        raise AssertionError("The liveness endpoint must not request a database engine.")

    monkeypatch.setattr("streetpoker.db.session.get_engine", fail_if_engine_is_requested)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_hides_database_failures(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_query(*_args: object, **_kwargs: object) -> NoReturn:
        raise SQLAlchemyError("sensitive database detail")

    monkeypatch.setattr(Session, "execute", fail_query)

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}
    assert "sensitive" not in response.text
