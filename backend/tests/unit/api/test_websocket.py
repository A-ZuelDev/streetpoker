import base64
import json
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from streetpoker.api.app import create_app
from streetpoker.api.guest_identity import derive_guest_id
from streetpoker.api.routes import realtime as realtime_route
from streetpoker.api.schemas.realtime import room_view_dto
from streetpoker.application import (
    GuestId,
    Pbkdf2PasswordHasher,
    RoomId,
    RoomService,
    RoomSettings,
)
from streetpoker.domain import PlayerId, SeededRandomSource


def token(fill: int) -> str:
    return base64.urlsafe_b64encode(bytes([fill]) * 32).rstrip(b"=").decode("ascii")


class FixedCodeSource:
    def next_code(self) -> str:
        return "ABCDEFGH"


def built_service(
    *,
    approval: bool = False,
    password: str | None = None,
    join_alice: bool = False,
    seat_players: bool = False,
) -> tuple[RoomService, RoomId, dict[str, str], dict[str, GuestId]]:
    next_player = 0

    def player_id() -> PlayerId:
        nonlocal next_player
        next_player += 1
        return PlayerId(f"transport-private-player-{next_player}")

    tokens = {"host": token(11), "alice": token(12), "spectator": token(13)}
    guests = {name: derive_guest_id(value) for name, value in tokens.items()}
    service = RoomService(
        code_source=FixedCodeSource(),
        room_id_factory=lambda: RoomId("realtime-room"),
        player_id_factory=player_id,
        password_hasher=Pbkdf2PasswordHasher(iterations=1),
        random_source_factory=lambda: SeededRandomSource(91),
    )
    created = service.create_room(
        actor=guests["host"],
        nickname="Host",
        settings=RoomSettings(
            room_name="WebSocket",
            default_starting_stack=1_000,
            seating_approval_required=approval,
        ),
        password=password,
    )
    if join_alice:
        service.join_room(
            room_code=created.room_code,
            actor=guests["alice"],
            nickname="Alice",
            password=password,
        )
    if seat_players:
        service.request_seat(room_id=created.room_id, actor=guests["host"], seat_index=0)
        service.request_seat(room_id=created.room_id, actor=guests["alice"], seat_index=2)
    return service, created.room_id, tokens, guests


@contextmanager
def app_client(service: RoomService) -> Iterator[TestClient]:
    with TestClient(create_app(room_service=service)) as client:
        yield client


def handshake(
    socket: WebSocketTestSession,
    guest_token: str,
    *,
    nickname: str | None = None,
    password: str | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    payload: dict[str, object] = {"type": "connect", "guest_token": guest_token}
    if nickname is not None:
        payload["nickname"] = nickname
    if password is not None:
        payload["password"] = password
    socket.send_json(payload)
    connected = socket.receive_json()
    state = socket.receive_json()
    assert connected["type"] == "connected"
    assert state["type"] == "state"
    return connected, state


def receive_ack_and_state(socket: WebSocketTestSession, command_id: str) -> dict[str, object]:
    ack = socket.receive_json()
    state = socket.receive_json()
    assert ack == {"type": "command_ack", "command_id": command_id}
    assert state["type"] == "state"
    return state


def test_valid_member_connects_and_receives_viewer_state_immediately() -> None:
    service, _, tokens, guests = built_service()

    with app_client(service) as client, client.websocket_connect("/ws/rooms/abcdefgh") as socket:
        connected, state = handshake(socket, tokens["host"])

    assert connected["guest_id"] == guests["host"].value
    snapshot = state["snapshot"]
    assert isinstance(snapshot, dict)
    room = snapshot["room"]
    assert isinstance(room, dict)
    assert room["room_code"] == "ABCDEFGH"


def test_missing_room_and_invalid_handshake_are_typed_and_closed() -> None:
    service, _, tokens, _ = built_service()

    with app_client(service) as client:
        with client.websocket_connect("/ws/rooms/ZZZZZZZZ") as missing:
            missing.send_json({"type": "connect", "guest_token": tokens["host"]})
            error = missing.receive_json()
            assert error["type"] == "connection_error"
            assert error["code"] == "room_not_found"
            with pytest.raises(WebSocketDisconnect):
                missing.receive_json()
        with client.websocket_connect("/ws/rooms/ABCDEFGH") as invalid:
            invalid.send_json({"type": "connect", "guest_token": "not-a-token"})
            error = invalid.receive_json()
            assert error["code"] == "invalid_handshake"
            with pytest.raises(WebSocketDisconnect):
                invalid.receive_json()


def test_invalid_room_code_is_a_safe_client_error() -> None:
    service, _, tokens, _ = built_service()

    with app_client(service) as client, client.websocket_connect("/ws/rooms/foo") as socket:
        socket.send_json({"type": "connect", "guest_token": tokens["host"]})
        error = socket.receive_json()
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()

    assert error == {
        "type": "connection_error",
        "code": "invalid_room_code",
        "message": "The room code is invalid.",
    }


def test_initial_handshake_timeout_closes_without_registration_or_membership_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, room_id, _, _ = built_service()
    before = service.get_room_snapshot(room_id)
    app = create_app(room_service=service)
    coordinator = app.state.realtime_coordinator
    monkeypatch.setattr(realtime_route, "HANDSHAKE_TIMEOUT_SECONDS", 0.01)

    with TestClient(app) as client, client.websocket_connect("/ws/rooms/ABCDEFGH") as socket:
        error = socket.receive_json()
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()

        assert coordinator.registry.all_sessions(room_id) == ()

    assert error == {
        "type": "connection_error",
        "code": "handshake_timeout",
        "message": "The connection handshake timed out.",
    }
    assert closed.value.code == 1008
    assert service.get_room_snapshot(room_id) == before


def test_normal_handshake_completes_with_timeout_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, tokens, _ = built_service()
    monkeypatch.setattr(realtime_route, "HANDSHAKE_TIMEOUT_SECONDS", 0.5)

    with app_client(service) as client, client.websocket_connect("/ws/rooms/ABCDEFGH") as socket:
        connected, state = handshake(socket, tokens["host"])

    assert connected["type"] == "connected"
    assert state["type"] == "state"


def test_handshake_joins_new_member_and_never_echoes_password_or_token() -> None:
    service, _, tokens, guests = built_service(password="alpha")

    with app_client(service) as client:
        with client.websocket_connect("/ws/rooms/ABCDEFGH") as wrong:
            wrong.send_json(
                {
                    "type": "connect",
                    "guest_token": tokens["alice"],
                    "nickname": "Alice",
                    "password": "wrong",
                }
            )
            error = wrong.receive_json()
            assert error["code"] == "wrong_room_password"
            assert error["message"] != "wrong"
        with client.websocket_connect("/ws/rooms/ABCDEFGH") as joined:
            connected, state = handshake(
                joined,
                tokens["alice"],
                nickname="Alice",
                password="alpha",
            )

    serialized = f"{connected}{state}"
    assert guests["alice"].value in serialized
    assert tokens["alice"] not in serialized
    assert "alpha" not in serialized


def test_phase_11a_duplicate_tabs_remain_authorized_and_only_issuer_receives_ack() -> None:
    service, _, tokens, _ = built_service()

    with app_client(service) as client, client.websocket_connect("/ws/rooms/ABCDEFGH") as first:
        handshake(first, tokens["host"])
        with client.websocket_connect("/ws/rooms/ABCDEFGH") as second:
            handshake(second, tokens["host"])
            first.send_json({"type": "request_seat", "command_id": "seat", "seat_index": 1})

            first_state = receive_ack_and_state(first, "seat")
            second_state = second.receive_json()

    assert second_state["type"] == "state"
    assert first_state["snapshot"] == second_state["snapshot"]


def test_duplicate_tabs_receive_only_their_guest_projection_during_a_hand() -> None:
    service, room_id, tokens, guests = built_service(join_alice=True, seat_players=True)
    service.start_hand(room_id=room_id, actor=guests["host"], expected_hand_number=1)

    with app_client(service) as client, client.websocket_connect("/ws/rooms/ABCDEFGH") as first:
        first_connected, first_state = handshake(first, tokens["host"])
        with client.websocket_connect("/ws/rooms/ABCDEFGH") as second:
            second_connected, second_state = handshake(second, tokens["host"])
            with client.websocket_connect("/ws/rooms/ABCDEFGH") as alice:
                _, alice_state = handshake(alice, tokens["alice"])

    assert first_connected["guest_id"] == second_connected["guest_id"] == guests["host"].value
    assert first_state == second_state
    for state, viewer in (
        (first_state, guests["host"]),
        (second_state, guests["host"]),
        (alice_state, guests["alice"]),
    ):
        players = state["snapshot"]["active_hand"]["players"]
        assert {player["guest_id"] for player in players if player["hole_cards"] is not None} == {
            viewer.value
        }


def test_protocol_errors_are_typed_sanitized_and_connection_can_continue() -> None:
    service, _, tokens, _ = built_service()

    with app_client(service) as client, client.websocket_connect("/ws/rooms/ABCDEFGH") as socket:
        handshake(socket, tokens["host"])
        socket.send_text("{")
        assert socket.receive_json() == {
            "type": "command_error",
            "command_id": None,
            "code": "malformed_json",
            "message": "The command is not valid JSON.",
        }
        socket.send_json({"type": "future_action", "command_id": "unknown", "player_id": "secret"})
        unknown = socket.receive_json()
        assert unknown["code"] == "unknown_command"
        assert unknown["command_id"] == "unknown"
        assert "secret" not in str(unknown)
        socket.send_json(
            {
                "type": "request_seat",
                "command_id": "invalid",
                "seat_index": True,
                "player_id": "secret",
            }
        )
        invalid = socket.receive_json()
        assert invalid["code"] == "validation_error"
        assert "secret" not in str(invalid)
        socket.send_json({"type": "request_seat", "command_id": "valid", "seat_index": 3})
        receive_ack_and_state(socket, "valid")


def test_application_validation_errors_are_client_errors_not_internal_faults(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, _, tokens, _ = built_service()
    invalid_name = "private-path-" + "x" * 61

    with app_client(service) as client, client.websocket_connect("/ws/rooms/ABCDEFGH") as socket:
        handshake(socket, tokens["host"])
        socket.send_json(
            {
                "type": "update_settings",
                "command_id": "bad-name",
                "room_name": invalid_name,
            }
        )
        name_error = socket.receive_json()
        socket.send_json(
            {
                "type": "kick",
                "command_id": "bad-guest",
                "target_guest_id": "   ",
            }
        )
        guest_error = socket.receive_json()

    assert name_error == {
        "type": "command_error",
        "command_id": "bad-name",
        "code": "invalid_room_name",
        "message": "The room name is invalid.",
    }
    assert guest_error == {
        "type": "command_error",
        "command_id": "bad-guest",
        "code": "invalid_guest_id",
        "message": "The guest identifier is invalid.",
    }
    assert invalid_name not in str(name_error)
    assert "Internal realtime command failure" not in caplog.text


def test_start_and_every_gameplay_action_route_with_authoritative_versions() -> None:
    service, _, tokens, guests = built_service(join_alice=True, seat_players=True)

    with (
        app_client(service) as client,
        client.websocket_connect("/ws/rooms/ABCDEFGH") as host_socket,
    ):
        handshake(host_socket, tokens["host"])
        with client.websocket_connect("/ws/rooms/ABCDEFGH") as alice_socket:
            handshake(alice_socket, tokens["alice"])
            sockets = {
                guests["host"].value: host_socket,
                guests["alice"].value: alice_socket,
            }
            host_socket.send_json({"type": "start_hand", "command_id": "start", "hand_number": 1})
            state = receive_ack_and_state(host_socket, "start")
            alice_socket.receive_json()

            hand = state["snapshot"]["active_hand"]
            actor = hand["current_actor"]
            other = next(guest for guest in sockets if guest != actor)
            sockets[actor].send_json(
                {
                    "type": "check",
                    "command_id": "illegal-check",
                    "hand_number": hand["hand_number"],
                    "expected_action_sequence": hand["action_sequence"],
                }
            )
            illegal = sockets[actor].receive_json()
            assert illegal["type"] == "command_error"
            assert illegal["code"] == "illegal_check"
            sockets[actor].send_json(
                {
                    "type": "call",
                    "command_id": "call",
                    "hand_number": hand["hand_number"],
                    "expected_action_sequence": hand["action_sequence"],
                }
            )
            state = receive_ack_and_state(sockets[actor], "call")
            sockets[other].receive_json()

            hand = state["snapshot"]["active_hand"]
            actor = hand["current_actor"]
            other = next(guest for guest in sockets if guest != actor)
            sockets[actor].send_json(
                {
                    "type": "check",
                    "command_id": "check",
                    "hand_number": hand["hand_number"],
                    "expected_action_sequence": hand["action_sequence"],
                }
            )
            state = receive_ack_and_state(sockets[actor], "check")
            sockets[other].receive_json()

            hand = state["snapshot"]["active_hand"]
            actor = hand["current_actor"]
            other = next(guest for guest in sockets if guest != actor)
            minimum_bet = hand["legal_actions"]["bet_to"]["minimum_full_to"]
            sockets[actor].send_json(
                {
                    "type": "bet_to",
                    "command_id": "bet",
                    "hand_number": hand["hand_number"],
                    "expected_action_sequence": hand["action_sequence"],
                    "total": minimum_bet,
                }
            )
            state = receive_ack_and_state(sockets[actor], "bet")
            sockets[other].receive_json()

            hand = state["snapshot"]["active_hand"]
            actor = hand["current_actor"]
            other = next(guest for guest in sockets if guest != actor)
            minimum_raise = hand["legal_actions"]["raise_to"]["minimum_full_to"]
            sockets[actor].send_json(
                {
                    "type": "raise_to",
                    "command_id": "raise",
                    "hand_number": hand["hand_number"],
                    "expected_action_sequence": hand["action_sequence"],
                    "total": minimum_raise,
                }
            )
            state = receive_ack_and_state(sockets[actor], "raise")
            sockets[other].receive_json()

            hand = state["snapshot"]["active_hand"]
            actor = hand["current_actor"]
            other = next(guest for guest in sockets if guest != actor)
            sockets[actor].send_json(
                {
                    "type": "fold",
                    "command_id": "fold",
                    "hand_number": hand["hand_number"],
                    "expected_action_sequence": hand["action_sequence"],
                }
            )
            final_state = receive_ack_and_state(sockets[actor], "fold")
            sockets[other].receive_json()

    assert final_state["snapshot"]["active_hand"] is None
    assert final_state["snapshot"]["last_hand"] is not None


def test_nonhost_start_is_rejected_without_state_broadcast() -> None:
    service, _, tokens, _ = built_service(join_alice=True, seat_players=True)

    with app_client(service) as client, client.websocket_connect("/ws/rooms/ABCDEFGH") as alice:
        handshake(alice, tokens["alice"])
        alice.send_json({"type": "start_hand", "command_id": "unauthorized", "hand_number": 1})
        error = alice.receive_json()

    assert error["type"] == "command_error"
    assert error["code"] == "not_room_host"


def test_broadcasts_are_viewer_specific_for_players_spectator_and_host() -> None:
    service, _, tokens, guests = built_service(join_alice=True, seat_players=True)

    with app_client(service) as client, client.websocket_connect("/ws/rooms/ABCDEFGH") as host:
        handshake(host, tokens["host"])
        with client.websocket_connect("/ws/rooms/ABCDEFGH") as alice:
            handshake(alice, tokens["alice"])
            with client.websocket_connect("/ws/rooms/ABCDEFGH") as spectator:
                _, spectator_joined = handshake(
                    spectator,
                    tokens["spectator"],
                    nickname="Spectator",
                )
                host.receive_json()
                alice.receive_json()
                host.send_json({"type": "start_hand", "command_id": "start", "hand_number": 1})
                host_state = receive_ack_and_state(host, "start")
                alice_state = alice.receive_json()
                spectator_state = spectator.receive_json()

    assert spectator_joined["type"] == "state"
    host_players = host_state["snapshot"]["active_hand"]["players"]
    alice_players = alice_state["snapshot"]["active_hand"]["players"]
    spectator_players = spectator_state["snapshot"]["active_hand"]["players"]
    assert {item["guest_id"] for item in host_players if item["hole_cards"]} == {
        guests["host"].value
    }
    assert {item["guest_id"] for item in alice_players if item["hole_cards"]} == {
        guests["alice"].value
    }
    assert all(item["hole_cards"] is None for item in spectator_players)
    assert "transport-private-player" not in str(host_state)


def test_seat_request_reject_approve_and_stand_route_through_room_service() -> None:
    service, _, tokens, guests = built_service(approval=True, join_alice=True)

    with app_client(service) as client, client.websocket_connect("/ws/rooms/ABCDEFGH") as host:
        handshake(host, tokens["host"])
        with client.websocket_connect("/ws/rooms/ABCDEFGH") as alice:
            handshake(alice, tokens["alice"])
            alice.send_json({"type": "request_seat", "command_id": "request-1", "seat_index": 2})
            receive_ack_and_state(alice, "request-1")
            host.receive_json()
            host.send_json(
                {
                    "type": "reject_seat",
                    "command_id": "reject",
                    "target_guest_id": guests["alice"].value,
                }
            )
            receive_ack_and_state(host, "reject")
            alice.receive_json()
            alice.send_json({"type": "request_seat", "command_id": "request-2", "seat_index": 2})
            receive_ack_and_state(alice, "request-2")
            host.receive_json()
            host.send_json(
                {
                    "type": "approve_seat",
                    "command_id": "approve",
                    "target_guest_id": guests["alice"].value,
                }
            )
            receive_ack_and_state(host, "approve")
            alice.receive_json()
            alice.send_json({"type": "stand", "command_id": "stand"})
            final_state = receive_ack_and_state(alice, "stand")
            host.receive_json()

    members = final_state["snapshot"]["room"]["members"]
    alice_member = next(item for item in members if item["guest_id"] == guests["alice"].value)
    assert alice_member["status"] == "in_room"


def test_leave_acknowledges_issuer_broadcasts_remaining_state_then_closes_all_guest_tabs() -> None:
    service, _, tokens, guests = built_service(join_alice=True)

    with app_client(service) as client, client.websocket_connect("/ws/rooms/ABCDEFGH") as host:
        handshake(host, tokens["host"])
        with client.websocket_connect("/ws/rooms/ABCDEFGH") as alice_one:
            handshake(alice_one, tokens["alice"])
            with client.websocket_connect("/ws/rooms/ABCDEFGH") as alice_two:
                handshake(alice_two, tokens["alice"])
                alice_one.send_json({"type": "leave", "command_id": "leave"})

                assert alice_one.receive_json() == {
                    "type": "command_ack",
                    "command_id": "leave",
                }
                host_state = host.receive_json()
                with pytest.raises(WebSocketDisconnect):
                    alice_one.receive_json()
                with pytest.raises(WebSocketDisconnect):
                    alice_two.receive_json()

    members = host_state["snapshot"]["room"]["members"]
    assert guests["alice"].value not in {item["guest_id"] for item in members}


def test_kick_acknowledges_host_broadcasts_remaining_state_and_sends_target_no_state() -> None:
    service, _, tokens, guests = built_service(join_alice=True)

    with app_client(service) as client, client.websocket_connect("/ws/rooms/ABCDEFGH") as host:
        handshake(host, tokens["host"])
        with client.websocket_connect("/ws/rooms/ABCDEFGH") as alice:
            handshake(alice, tokens["alice"])
            host.send_json(
                {
                    "type": "kick",
                    "command_id": "kick",
                    "target_guest_id": guests["alice"].value,
                }
            )

            host_state = receive_ack_and_state(host, "kick")
            with pytest.raises(WebSocketDisconnect):
                alice.receive_json()

    members = host_state["snapshot"]["room"]["members"]
    assert guests["alice"].value not in {item["guest_id"] for item in members}


def test_close_room_queues_host_ack_before_final_state_then_closes_every_socket() -> None:
    service, _, tokens, _ = built_service(join_alice=True)

    with app_client(service) as client, client.websocket_connect("/ws/rooms/ABCDEFGH") as host:
        handshake(host, tokens["host"])
        with client.websocket_connect("/ws/rooms/ABCDEFGH") as alice:
            handshake(alice, tokens["alice"])
            host.send_json({"type": "close_room", "command_id": "close"})

            host_ack = host.receive_json()
            host_state = host.receive_json()
            alice_state = alice.receive_json()
            with pytest.raises(WebSocketDisconnect):
                host.receive_json()
            with pytest.raises(WebSocketDisconnect):
                alice.receive_json()

    assert host_ack == {"type": "command_ack", "command_id": "close"}
    assert host_state["type"] == "state"
    assert host_state["snapshot"]["room"]["status"] == "closed"
    assert alice_state["snapshot"]["room"]["status"] == "closed"


@pytest.mark.parametrize("departure", ["leave", "kick"])
def test_explicit_departure_requires_a_new_join_not_same_token_reconnect(departure: str) -> None:
    service, room_id, tokens, guests = built_service(join_alice=True)

    with app_client(service) as client, client.websocket_connect("/ws/rooms/ABCDEFGH") as host:
        handshake(host, tokens["host"])
        with client.websocket_connect("/ws/rooms/ABCDEFGH") as alice:
            handshake(alice, tokens["alice"])
            if departure == "leave":
                alice.send_json({"type": "leave", "command_id": "depart"})
                assert alice.receive_json() == {"type": "command_ack", "command_id": "depart"}
                host_state = host.receive_json()
            else:
                host.send_json(
                    {
                        "type": "kick",
                        "command_id": "depart",
                        "target_guest_id": guests["alice"].value,
                    }
                )
                host_state = receive_ack_and_state(host, "depart")
            with pytest.raises(WebSocketDisconnect):
                alice.receive_json()

        assert guests["alice"].value not in {
            member["guest_id"] for member in host_state["snapshot"]["room"]["members"]
        }
        assert guests["alice"] not in {
            member.guest_id for member in service.get_room_snapshot(room_id).members
        }
        with client.websocket_connect("/ws/rooms/ABCDEFGH") as reconnect:
            reconnect.send_json({"type": "connect", "guest_token": tokens["alice"]})
            rejected = reconnect.receive_json()
            assert rejected["type"] == "connection_error"
            assert rejected["code"] == "membership_required"
            with pytest.raises(WebSocketDisconnect):
                reconnect.receive_json()


def test_closed_room_rejects_same_token_reconnect() -> None:
    service, room_id, tokens, _ = built_service()

    with app_client(service) as client:
        with client.websocket_connect("/ws/rooms/ABCDEFGH") as host:
            handshake(host, tokens["host"])
            host.send_json({"type": "close_room", "command_id": "close"})
            closed = receive_ack_and_state(host, "close")
            assert closed["snapshot"]["room"]["status"] == "closed"
            with pytest.raises(WebSocketDisconnect):
                host.receive_json()

        with client.websocket_connect("/ws/rooms/ABCDEFGH") as reconnect:
            reconnect.send_json({"type": "connect", "guest_token": tokens["host"]})
            rejected = reconnect.receive_json()
            assert rejected["type"] == "connection_error"
            assert rejected["code"] == "room_closed"
            with pytest.raises(WebSocketDisconnect):
                reconnect.receive_json()

    assert service.get_room_snapshot(room_id).status.value == "closed"


def test_host_transport_disconnect_preserves_open_room_and_host_authority() -> None:
    service, room_id, tokens, guests = built_service(join_alice=True, seat_players=True)
    before = service.get_room_view(room_id=room_id, viewer=guests["host"])

    with app_client(service) as client, client.websocket_connect("/ws/rooms/ABCDEFGH") as alice:
        handshake(alice, tokens["alice"])
        with client.websocket_connect("/ws/rooms/ABCDEFGH") as host:
            first_connected, first_state = handshake(host, tokens["host"])

        assert service.get_room_view(room_id=room_id, viewer=guests["host"]) == before
        assert service.get_room_snapshot(room_id).host_guest_id == guests["host"]
        alice.send_json({"type": "start_hand", "command_id": "not-host", "hand_number": 1})
        assert alice.receive_json()["code"] == "not_room_host"

        with client.websocket_connect("/ws/rooms/ABCDEFGH") as reconnected:
            connected, state = handshake(reconnected, tokens["host"])
            assert connected == first_connected
            assert state == first_state
            assert state["snapshot"] == room_view_dto(before).model_dump(mode="json")
            reconnected.send_json(
                {"type": "start_hand", "command_id": "host-start", "hand_number": 1}
            )
            started = receive_ack_and_state(reconnected, "host-start")
            assert started["snapshot"]["active_hand"] is not None


@pytest.mark.parametrize("disconnect_current_actor", [True, False])
def test_active_hand_disconnect_and_same_token_reconnect_preserve_private_hand(
    disconnect_current_actor: bool,
) -> None:
    service, room_id, tokens, guests = built_service(
        password="private-password", join_alice=True, seat_players=True
    )
    service.start_hand(room_id=room_id, actor=guests["host"], expected_hand_number=1)
    initial = service.get_room_view(room_id=room_id, viewer=guests["host"])
    assert initial.active_hand is not None
    actor = initial.active_hand.current_actor
    disconnected_guest = (
        actor
        if disconnect_current_actor
        else next(guest for guest in (guests["host"], guests["alice"]) if guest != actor)
    )
    connected_guest = next(
        guest for guest in (guests["host"], guests["alice"]) if guest != disconnected_guest
    )
    token_by_guest = {guests[name]: tokens[name] for name in ("host", "alice")}
    before = service.get_room_view(room_id=room_id, viewer=disconnected_guest)
    assert before.active_hand is not None

    with app_client(service) as client, client.websocket_connect("/ws/rooms/ABCDEFGH") as remaining:
        _, remaining_state = handshake(remaining, token_by_guest[connected_guest])
        with client.websocket_connect("/ws/rooms/ABCDEFGH") as original:
            first_connected, _ = handshake(original, token_by_guest[disconnected_guest])

        # Transport loss performs no fold, check, stand, leave, or stack mutation.
        assert service.get_room_view(room_id=room_id, viewer=disconnected_guest) == before
        if not disconnect_current_actor:
            remaining.send_json(
                {
                    "type": "call",
                    "command_id": "while-offline",
                    "hand_number": before.active_hand.hand_number,
                    "expected_action_sequence": before.active_hand.action_sequence,
                }
            )
            receive_ack_and_state(remaining, "while-offline")

        with client.websocket_connect("/ws/rooms/ABCDEFGH") as reconnected:
            connected, state = handshake(reconnected, token_by_guest[disconnected_guest])
            current = service.get_room_view(room_id=room_id, viewer=disconnected_guest)
            assert connected == first_connected
            assert state["snapshot"] == room_view_dto(current).model_dump(mode="json")
            assert current.room.host_guest_id == before.room.host_guest_id
            assert [member.guest_id for member in current.room.members] == [
                member.guest_id for member in before.room.members
            ]
            assert [seat.guest_id for seat in current.room.seats] == [
                seat.guest_id for seat in before.room.seats
            ]
            hand = state["snapshot"]["active_hand"]
            assert hand is not None
            if disconnect_current_actor:
                assert hand["current_actor"] == actor.value
                assert hand["action_sequence"] == before.active_hand.action_sequence
                assert current == before
            else:
                assert hand["action_sequence"] == before.active_hand.action_sequence + 1

            players = hand["players"]
            assert {
                player["guest_id"] for player in players if player["hole_cards"] is not None
            } == {disconnected_guest.value}
            opponent_players = remaining_state["snapshot"]["active_hand"]["players"]
            assert {
                player["guest_id"]
                for player in opponent_players
                if player["hole_cards"] is not None
            } == {connected_guest.value}
            encoded = json.dumps(state)
            assert token_by_guest[disconnected_guest] not in encoded
            assert "private-password" not in encoded
            assert "transport-private-player" not in encoded
            assert '"player_id"' not in encoded


def test_stale_command_returns_error_then_authoritative_state_without_mutation() -> None:
    service, room_id, tokens, guests = built_service(join_alice=True, seat_players=True)
    service.start_hand(room_id=room_id, actor=guests["host"], expected_hand_number=1)
    before = service.get_room_view(room_id=room_id, viewer=guests["host"])

    with app_client(service) as client, client.websocket_connect("/ws/rooms/ABCDEFGH") as host:
        handshake(host, tokens["host"])
        host.send_json(
            {
                "type": "fold",
                "command_id": "stale",
                "hand_number": 1,
                "expected_action_sequence": 99,
            }
        )
        error = host.receive_json()
        state = host.receive_json()

    assert error["type"] == "command_error"
    assert error["code"] == "stale_game_state"
    assert state["type"] == "state"
    assert service.get_room_view(room_id=room_id, viewer=guests["host"]) == before


def test_oversized_message_is_rejected_then_socket_closes() -> None:
    service, _, tokens, _ = built_service()

    with app_client(service) as client, client.websocket_connect("/ws/rooms/ABCDEFGH") as socket:
        handshake(socket, tokens["host"])
        socket.send_text("x" * (64 * 1024 + 1))
        error = socket.receive_json()
        assert error["code"] == "message_too_large"
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()

    assert closed.value.code == 1009
