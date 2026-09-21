import base64
import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from streetpoker.api.app import create_app
from streetpoker.api.guest_identity import derive_guest_id
from streetpoker.api.request_body_limit import ROOM_CREATION_MAX_BODY_BYTES
from streetpoker.application import (
    Pbkdf2PasswordHasher,
    RoomId,
    RoomNotFoundError,
    RoomService,
)
from streetpoker.domain import PlayerId


def token(fill: int) -> str:
    return base64.urlsafe_b64encode(bytes([fill]) * 32).rstrip(b"=").decode("ascii")


class FixedCodeSource:
    def __init__(self, code: str = "ABCDEFGH") -> None:
        self.code = code

    def next_code(self) -> str:
        return self.code


class SequenceCodeSource:
    def __init__(self, *codes: str) -> None:
        self._codes = iter(codes)

    def next_code(self) -> str:
        return next(self._codes)


def built_service(*, codes: tuple[str, ...] = ("ABCDEFGH",)) -> RoomService:
    room_ids = iter(f"private-room-{index}" for index in range(1, 100))
    player_ids = iter(f"private-player-{index}" for index in range(1, 100))
    code_source = FixedCodeSource(codes[0]) if len(codes) == 1 else SequenceCodeSource(*codes)
    return RoomService(
        code_source=code_source,
        room_id_factory=lambda: RoomId(next(room_ids)),
        player_id_factory=lambda: PlayerId(next(player_ids)),
        password_hasher=Pbkdf2PasswordHasher(iterations=1),
    )


@contextmanager
def app_client(service: RoomService) -> Iterator[TestClient]:
    with TestClient(create_app(room_service=service), raise_server_exceptions=False) as client:
        yield client


def create_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "guest_token": token(11),
        "nickname": "Mara",
        "settings": {"room_name": "Friday Night"},
    }
    payload.update(changes)
    return payload


def test_create_room_returns_only_code_and_applies_application_defaults() -> None:
    service = built_service()

    with app_client(service) as client:
        response = client.post("/rooms", json=create_payload())

    assert response.status_code == 201
    assert response.json() == {"room_code": "ABCDEFGH"}
    assert response.headers["cache-control"] == "no-store"
    snapshot = service.get_room_snapshot_by_code("ABCDEFGH")
    assert snapshot.settings.room_name == "Friday Night"
    assert snapshot.settings.small_blind == 50
    assert snapshot.settings.big_blind == 100
    assert snapshot.settings.default_starting_stack == 10_000
    assert snapshot.settings.seating_approval_required
    assert not snapshot.settings.stand_up_enabled
    assert snapshot.settings.stand_up_penalty_per_recipient_chips == 100
    assert snapshot.settings.max_seats == 6


def test_create_room_applies_explicit_settings_and_optional_password() -> None:
    service = built_service()
    password = "correct horse battery staple"
    payload = create_payload(
        settings={
            "room_name": "High Stakes",
            "small_blind": 100,
            "big_blind": 200,
            "default_starting_stack": 25_000,
            "seating_approval_required": False,
            "stand_up_enabled": True,
            "stand_up_penalty_per_recipient_chips": 750,
        },
        password=password,
    )

    with app_client(service) as client:
        response = client.post("/rooms", json=payload)

    assert response.status_code == 201
    assert response.json() == {"room_code": "ABCDEFGH"}
    assert password not in response.text
    snapshot = service.get_room_snapshot_by_code("ABCDEFGH")
    assert snapshot.settings.room_name == "High Stakes"
    assert snapshot.settings.small_blind == 100
    assert snapshot.settings.big_blind == 200
    assert snapshot.settings.default_starting_stack == 25_000
    assert not snapshot.settings.seating_approval_required
    assert snapshot.settings.stand_up_enabled
    assert snapshot.settings.stand_up_penalty_per_recipient_chips == 750
    assert snapshot.settings.password_protected


def test_creation_response_never_exposes_credentials_or_internal_identifiers() -> None:
    service = built_service()
    guest_token = token(17)
    password = "transport-secret-password"

    with app_client(service) as client:
        response = client.post(
            "/rooms",
            json=create_payload(guest_token=guest_token, password=password),
        )

    serialized = response.text
    assert response.status_code == 201
    assert guest_token not in serialized
    assert password not in serialized
    assert derive_guest_id(guest_token).value not in serialized
    for forbidden in ("room_id", "player_id", "digest", "salt", "password"):
        assert forbidden not in serialized.lower()


def test_http_creation_hands_existing_host_to_authoritative_websocket() -> None:
    service = built_service()
    guest_token = token(23)

    with app_client(service) as client:
        created = client.post("/rooms", json=create_payload(guest_token=guest_token))
        room_code = created.json()["room_code"]
        with client.websocket_connect(f"/ws/rooms/{room_code}") as socket:
            socket.send_json({"type": "connect", "guest_token": guest_token})
            connected = socket.receive_json()
            state = socket.receive_json()

    guest_id = derive_guest_id(guest_token).value
    assert connected == {
        "type": "connected",
        "guest_id": guest_id,
        "room_code": "ABCDEFGH",
    }
    assert state["type"] == "state"
    members = state["snapshot"]["room"]["members"]
    assert members == [
        {
            "guest_id": guest_id,
            "nickname": "Mara",
            "status": "in_room",
            "is_host": True,
            "stack": None,
        }
    ]
    assert len(service.get_room_snapshot_by_code("ABCDEFGH").members) == 1


def test_malformed_guest_token_is_a_sanitized_client_error() -> None:
    service = built_service()
    malformed = "not-a-private-token"

    with app_client(service) as client:
        response = client.post("/rooms", json=create_payload(guest_token=malformed))

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "invalid_guest_token",
            "message": "The guest token is invalid.",
        }
    }
    assert malformed not in response.text
    with pytest.raises(RoomNotFoundError):
        service.get_room_snapshot_by_code("ABCDEFGH")


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {
            "content": '{"guest_token":"private-token","password":"private-password"',
            "headers": {"Content-Type": "application/json"},
        },
        {"json": {"guest_token": token(1), "password": "private-password"}},
        {"json": create_payload(extra_secret="private-password")},
        {"json": create_payload(nickname=12345)},
    ],
    ids=["malformed-json", "missing-fields", "extra-field", "wrong-type"],
)
def test_request_validation_is_sanitized(request_kwargs: dict[str, object]) -> None:
    with app_client(built_service()) as client:
        response = client.post("/rooms", **request_kwargs)  # type: ignore[arg-type]

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "validation_error",
            "message": "The request payload is invalid.",
        }
    }
    assert "private-token" not in response.text
    assert "private-password" not in response.text


def test_request_validation_is_sanitized_when_application_is_mounted() -> None:
    outer = FastAPI()
    outer.mount("/api", create_app(room_service=built_service()))

    with TestClient(outer) as client:
        response = client.post(
            "/api/rooms",
            json={
                "guest_token": "private-token",
                "password": "private-password",
            },
        )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "validation_error",
            "message": "The request payload is invalid.",
        }
    }
    assert "private-token" not in response.text
    assert "private-password" not in response.text


def test_oversized_room_request_is_rejected_without_reflecting_secrets() -> None:
    guest_token = token(41)
    password = "obvious-private-password"
    body = json.dumps(
        {
            **create_payload(guest_token=guest_token, password=password),
            "padding": "x" * ROOM_CREATION_MAX_BODY_BYTES,
        }
    )

    with app_client(built_service()) as client:
        response = client.post(
            "/rooms",
            content=body,
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json() == {
        "error": {
            "code": "request_too_large",
            "message": "The request payload is too large.",
        }
    }
    assert response.headers["cache-control"] == "no-store"
    assert guest_token not in response.text
    assert password not in response.text
    assert "padding" not in response.text


def test_oversized_room_request_is_rejected_when_application_is_mounted() -> None:
    outer = FastAPI()
    outer.mount("/api", create_app(room_service=built_service()))

    with TestClient(outer) as client:
        response = client.post(
            "/api/rooms",
            content=b"x" * (ROOM_CREATION_MAX_BODY_BYTES + 1),
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


@pytest.mark.parametrize(
    "headers",
    [
        [(b"content-type", b"application/json")],
        [(b"content-type", b"application/json"), (b"content-length", b"1")],
    ],
    ids=["no-content-length", "dishonest-content-length"],
)
def test_streamed_room_request_cannot_bypass_cumulative_limit(
    headers: list[tuple[bytes, bytes]],
) -> None:
    app = create_app(room_service=built_service())
    chunks = [b"x" * 4096, b"y" * 4096, b"private-secret"]

    status_code, response_headers, response_body = _asgi_post(
        app,
        path="/rooms",
        headers=headers,
        chunks=chunks,
    )

    assert status_code == 413
    assert json.loads(response_body) == {
        "error": {
            "code": "request_too_large",
            "message": "The request payload is too large.",
        }
    }
    assert response_headers[b"cache-control"] == b"no-store"
    assert b"private-secret" not in response_body


def test_streamed_valid_room_request_reaches_fastapi_unchanged() -> None:
    body = json.dumps(create_payload()).encode("utf-8")
    midpoint = len(body) // 2

    status_code, _, response_body = _asgi_post(
        create_app(room_service=built_service()),
        path="/rooms",
        headers=[(b"content-type", b"application/json")],
        chunks=[body[:midpoint], body[midpoint:]],
    )

    assert status_code == 201
    assert json.loads(response_body) == {"room_code": "ABCDEFGH"}


def test_room_body_limit_does_not_change_unrelated_health_endpoint() -> None:
    with app_client(built_service()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    ("changes", "expected_code"),
    [
        ({"nickname": "   "}, "invalid_nickname"),
        ({"settings": {"room_name": "   "}}, "invalid_room_name"),
        (
            {
                "settings": {
                    "room_name": "Invalid blinds",
                    "small_blind": 100,
                    "big_blind": 50,
                }
            },
            "invalid_settings",
        ),
        (
            {
                "settings": {
                    "room_name": "Unsafe Stand-Up",
                    "stand_up_enabled": True,
                    "stand_up_penalty_per_recipient_chips": 1_801_439_850_948_199,
                }
            },
            "validation_error",
        ),
        (
            {
                "settings": {
                    "room_name": "Invalid Stand-Up",
                    "stand_up_enabled": True,
                    "stand_up_penalty_per_recipient_chips": 0,
                }
            },
            "validation_error",
        ),
        ({"password": ""}, "invalid_room_password"),
    ],
)
def test_application_validation_errors_are_stable_and_atomic(
    changes: dict[str, object],
    expected_code: str,
) -> None:
    service = built_service()

    with app_client(service) as client:
        invalid = client.post("/rooms", json=create_payload(**changes))
        valid = client.post("/rooms", json=create_payload())

    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == expected_code
    assert invalid.headers["cache-control"] == "no-store"
    assert valid.status_code == 201
    assert len(service.get_room_snapshot_by_code("ABCDEFGH").members) == 1


def test_exhausted_room_code_generation_is_sanitized() -> None:
    service = built_service()

    with app_client(service) as client:
        assert client.post("/rooms", json=create_payload()).status_code == 201
        response = client.post(
            "/rooms",
            json=create_payload(guest_token=token(12), nickname="Second"),
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "room_creation_unavailable",
            "message": "The room could not be created right now.",
        }
    }
    assert "32" not in response.text


def test_unexpected_creation_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = built_service()
    secret = "private-exception-detail"

    def fail(_service: RoomService, **_kwargs: object) -> None:
        raise RuntimeError(secret)

    monkeypatch.setattr(RoomService, "create_room", fail)
    with app_client(service) as client:
        response = client.post("/rooms", json=create_payload(password="private-password"))

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "The server could not create the room.",
        }
    }
    assert secret not in response.text
    assert "private-password" not in response.text
    assert secret not in caplog.text


def test_allowed_origin_preflight_allows_post_and_disallowed_origin_is_not_granted() -> None:
    headers = {
        "Origin": "http://127.0.0.1:5173",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    }

    with app_client(built_service()) as client:
        allowed = client.options("/rooms", headers=headers)
        disallowed = client.options(
            "/rooms",
            headers={**headers, "Origin": "https://attacker.example"},
        )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    assert "POST" in allowed.headers["access-control-allow-methods"]
    assert "access-control-allow-origin" not in disallowed.headers


def test_repeated_successful_posts_create_distinct_rooms() -> None:
    service = built_service(codes=("ABCDEFGH", "BCDEFGHJ"))

    with app_client(service) as client:
        first = client.post("/rooms", json=create_payload())
        second = client.post(
            "/rooms",
            json=create_payload(guest_token=token(31), nickname="Other"),
        )

    assert first.status_code == second.status_code == 201
    assert first.json() == {"room_code": "ABCDEFGH"}
    assert second.json() == {"room_code": "BCDEFGHJ"}


def test_no_http_join_or_gameplay_routes_are_added() -> None:
    with app_client(built_service()) as client:
        assert client.post("/rooms/ABCDEFGH/join", json={}).status_code == 404
        assert client.post("/rooms/ABCDEFGH/actions", json={}).status_code == 404


def _asgi_post(
    app: FastAPI,
    *,
    path: str,
    headers: list[tuple[bytes, bytes]],
    chunks: list[bytes],
) -> tuple[int, dict[bytes, bytes], bytes]:
    import asyncio

    async def request() -> tuple[int, dict[bytes, bytes], bytes]:
        pending = iter(enumerate(chunks))
        sent: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            index, chunk = next(pending)
            return {
                "type": "http.request",
                "body": chunk,
                "more_body": index < len(chunks) - 1,
            }

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
        await app(scope, receive, send)
        response_start = next(
            message for message in sent if message["type"] == "http.response.start"
        )
        response_body = b"".join(
            message.get("body", b"") for message in sent if message["type"] == "http.response.body"
        )
        return (
            response_start["status"],
            dict(response_start["headers"]),
            response_body,
        )

    return asyncio.run(request())
