import asyncio
import base64
from typing import cast

import pytest
from fastapi import WebSocket
from pydantic import ValidationError

from streetpoker.api.realtime import (
    ConnectionRegistry,
    RealtimeRoomCoordinator,
    SafeError,
    derive_guest_id,
    safe_error,
)
from streetpoker.api.schemas.realtime import (
    CallCommand,
    CloseRoomCommand,
    CommandAckMessage,
    ConnectionErrorMessage,
    RequestSeatCommand,
    UpdateSettingsCommand,
    client_command_adapter,
    room_view_dto,
)
from streetpoker.application import (
    GuestId,
    Pbkdf2PasswordHasher,
    RoomId,
    RoomService,
    RoomSettings,
    StaleHandVersionError,
)
from streetpoker.domain import ActionKind, PlayerId, SeededRandomSource


def token(fill: int) -> str:
    return base64.urlsafe_b64encode(bytes([fill]) * 32).rstrip(b"=").decode("ascii")


class FixedCodeSource:
    def next_code(self) -> str:
        return "ABCDEFGH"


def built_service(*, approval: bool = False) -> tuple[RoomService, RoomId, dict[str, GuestId]]:
    next_player = 0

    def player_id() -> PlayerId:
        nonlocal next_player
        next_player += 1
        return PlayerId(f"private-player-{next_player}")

    guests = {
        "host": derive_guest_id(token(1)),
        "alice": derive_guest_id(token(2)),
        "spectator": derive_guest_id(token(3)),
    }
    service = RoomService(
        code_source=FixedCodeSource(),
        room_id_factory=lambda: RoomId("room-one"),
        player_id_factory=player_id,
        password_hasher=Pbkdf2PasswordHasher(iterations=1),
        random_source_factory=lambda: SeededRandomSource(47),
    )
    created = service.create_room(
        actor=guests["host"],
        nickname="Host",
        settings=RoomSettings(
            room_name="Realtime",
            default_starting_stack=1_000,
            seating_approval_required=approval,
        ),
    )
    return service, created.room_id, guests


class RecordingWebSocket:
    def __init__(self, *, fail_send: bool = False) -> None:
        self.fail_send = fail_send
        self.sent: list[object] = []
        self.closed: list[tuple[int, str]] = []

    async def send_json(self, data: object) -> None:
        if self.fail_send:
            raise OSError("network failed")
        self.sent.append(data)

    async def close(self, *, code: int = 1000, reason: str = "") -> None:
        self.closed.append((code, reason))


def test_guest_identity_is_stable_but_does_not_expose_bearer_token() -> None:
    guest_token = token(7)

    first = derive_guest_id(guest_token)
    second = derive_guest_id(guest_token)

    assert first == second
    assert first.value.startswith("guest_")
    assert guest_token not in first.value
    with pytest.raises(ValueError):
        derive_guest_id(guest_token + "=")


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "start_hand", "command_id": "1", "hand_number": 1},
        {
            "type": "fold",
            "command_id": "2",
            "hand_number": 1,
            "expected_action_sequence": 0,
        },
        {
            "type": "check",
            "command_id": "3",
            "hand_number": 1,
            "expected_action_sequence": 0,
        },
        {
            "type": "call",
            "command_id": "4",
            "hand_number": 1,
            "expected_action_sequence": 0,
        },
        {
            "type": "bet_to",
            "command_id": "5",
            "hand_number": 1,
            "expected_action_sequence": 0,
            "total": 100,
        },
        {
            "type": "raise_to",
            "command_id": "6",
            "hand_number": 1,
            "expected_action_sequence": 0,
            "total": 200,
        },
        {"type": "request_seat", "command_id": "7", "seat_index": 5},
        {"type": "approve_seat", "command_id": "8", "target_guest_id": "guest_target"},
        {"type": "reject_seat", "command_id": "9", "target_guest_id": "guest_target"},
        {"type": "stand", "command_id": "10"},
        {"type": "leave", "command_id": "11"},
        {"type": "kick", "command_id": "12", "target_guest_id": "guest_target"},
        {"type": "update_settings", "command_id": "13", "password": None},
        {"type": "close_room", "command_id": "14"},
    ],
)
def test_client_command_union_accepts_only_current_phase_commands(
    payload: dict[str, object],
) -> None:
    assert client_command_adapter.validate_python(payload).command_id == payload["command_id"]


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "future_command", "command_id": "1"},
        {"type": "stand", "command_id": "1", "player_id": "private"},
        {"type": "request_seat", "command_id": "1", "seat_index": True},
        {"type": "update_settings", "command_id": "1"},
        {"type": "update_settings", "command_id": "1", "room_name": None},
    ],
)
def test_client_command_union_rejects_unknown_extra_or_ambiguous_input(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        client_command_adapter.validate_python(payload)


def test_snapshot_mapper_serializes_only_viewer_authorized_cards_and_public_ids() -> None:
    service, room_id, guests = built_service()
    room_code = service.get_room_snapshot(room_id).room_code
    service.join_room(room_code=room_code, actor=guests["alice"], nickname="Alice")
    service.join_room(room_code=room_code, actor=guests["spectator"], nickname="Spectator")
    service.request_seat(room_id=room_id, actor=guests["host"], seat_index=0)
    service.request_seat(room_id=room_id, actor=guests["alice"], seat_index=2)
    service.start_hand(room_id=room_id, actor=guests["host"], expected_hand_number=1)

    host = room_view_dto(service.get_room_view(room_id=room_id, viewer=guests["host"]))
    alice = room_view_dto(service.get_room_view(room_id=room_id, viewer=guests["alice"]))
    spectator = room_view_dto(service.get_room_view(room_id=room_id, viewer=guests["spectator"]))

    assert host.active_hand is not None
    assert alice.active_hand is not None
    assert spectator.active_hand is not None
    assert {player.guest_id for player in host.active_hand.players if player.hole_cards} == {
        guests["host"].value
    }
    assert {player.guest_id for player in alice.active_hand.players if player.hole_cards} == {
        guests["alice"].value
    }
    assert all(player.hole_cards is None for player in spectator.active_hand.players)
    encoded = host.model_dump_json()
    assert "private-player" not in encoded
    assert '"player_id"' not in encoded
    assert '"deck"' not in encoded
    assert '"burn"' not in encoded


def test_expected_errors_have_stable_sanitized_messages() -> None:
    translated = safe_error(StaleHandVersionError("private-player and path C:\\secret"))

    assert translated == SafeError(
        code="stale_game_state",
        message="The command used an outdated hand state.",
    )
    assert "private" not in translated.message
    assert "secret" not in translated.message


def test_registry_drains_ordered_messages_before_explicit_close() -> None:
    async def scenario() -> None:
        registry = ConnectionRegistry()
        fake = RecordingWebSocket()
        session = registry.register(
            cast(WebSocket, fake),
            RoomId("room"),
            GuestId("guest"),
        )
        registry.enqueue(session, CommandAckMessage(command_id="command"))
        registry.enqueue(
            session,
            ConnectionErrorMessage(code="ending", message="Session ending."),
        )
        registry.detach_session(session, code=1000, reason="done")

        await registry.disconnect(session)

        assert [cast(dict[str, object], item)["type"] for item in fake.sent] == [
            "command_ack",
            "connection_error",
        ]
        assert fake.closed == [(1000, "done")]

    asyncio.run(scenario())


def test_send_failure_does_not_rollback_committed_room_command() -> None:
    async def scenario() -> None:
        service, room_id, guests = built_service()
        registry = ConnectionRegistry()
        coordinator = RealtimeRoomCoordinator(service, registry=registry)
        fake = RecordingWebSocket(fail_send=True)
        session = registry.register(cast(WebSocket, fake), room_id, guests["host"])

        await coordinator.handle_command(
            session,
            RequestSeatCommand(type="request_seat", command_id="seat", seat_index=1),
        )
        await asyncio.sleep(0)

        assert service.get_room_snapshot(room_id).seats[1].guest_id == guests["host"]
        await coordinator.disconnect(session)

    asyncio.run(scenario())


def test_unexpected_command_error_is_sanitized_in_response_and_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        service, room_id, guests = built_service()
        registry = ConnectionRegistry()
        coordinator = RealtimeRoomCoordinator(service, registry=registry)
        fake = RecordingWebSocket()
        session = registry.register(cast(WebSocket, fake), room_id, guests["host"])

        def explode(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("private-player password C:\\secret")

        monkeypatch.setattr(RoomService, "request_seat", explode)
        await coordinator.handle_command(
            session,
            RequestSeatCommand(type="request_seat", command_id="explode", seat_index=1),
        )
        await asyncio.sleep(0)

        outbound = cast(dict[str, object], fake.sent[0])
        assert outbound["code"] == "internal_error"
        assert "private-player" not in str(outbound)
        assert "password" not in str(outbound)
        await coordinator.disconnect(session)

    asyncio.run(scenario())
    assert "private-player" not in caplog.text
    assert "password" not in caplog.text


def test_same_room_stale_actions_are_serialized_and_different_rooms_have_distinct_locks() -> None:
    async def scenario() -> None:
        service, room_id, guests = built_service()
        room_code = service.get_room_snapshot(room_id).room_code
        service.join_room(room_code=room_code, actor=guests["alice"], nickname="Alice")
        service.request_seat(room_id=room_id, actor=guests["host"], seat_index=0)
        service.request_seat(room_id=room_id, actor=guests["alice"], seat_index=2)
        started = service.start_hand(
            room_id=room_id,
            actor=guests["host"],
            expected_hand_number=1,
        )
        hand = started.active_hand
        assert hand is not None and ActionKind.CALL in hand.legal_actions.kinds
        registry = ConnectionRegistry()
        coordinator = RealtimeRoomCoordinator(service, registry=registry)
        fake = RecordingWebSocket()
        session = registry.register(cast(WebSocket, fake), room_id, hand.current_actor)
        command = CallCommand(
            type="call",
            command_id="same",
            hand_number=hand.hand_number,
            expected_action_sequence=hand.action_sequence,
        )

        await asyncio.gather(
            coordinator.handle_command(session, command),
            coordinator.handle_command(session, command),
        )
        await asyncio.sleep(0)

        current = service.get_room_view(room_id=room_id, viewer=hand.current_actor).active_hand
        assert current is not None and current.action_sequence == hand.action_sequence + 1
        message_types = [cast(dict[str, object], item)["type"] for item in fake.sent]
        assert "command_ack" in message_types
        assert "command_error" in message_types
        assert coordinator._lock_for(room_id) is coordinator._lock_for(room_id)
        assert coordinator._lock_for(room_id) is not coordinator._lock_for(RoomId("other"))
        await coordinator.disconnect(session)

    asyncio.run(scenario())


def test_settings_mapping_preserves_omission_and_explicit_password_clear() -> None:
    command = UpdateSettingsCommand(
        type="update_settings",
        command_id="settings",
        room_name="New Name",
        password=None,
    )

    update = RealtimeRoomCoordinator._settings_update(command)

    assert update.room_name == "New Name"
    assert update.password is None


def test_close_command_model_has_no_room_or_actor_identity_fields() -> None:
    command = CloseRoomCommand(type="close_room", command_id="close")

    assert command.model_dump() == {"command_id": "close", "type": "close_room"}
