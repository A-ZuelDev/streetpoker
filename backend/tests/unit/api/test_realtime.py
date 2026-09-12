import asyncio
import base64
import threading
from typing import cast

import pytest
from fastapi import WebSocket
from pydantic import ValidationError

from streetpoker.api.guest_identity import derive_guest_id
from streetpoker.api.realtime import (
    ConnectionFailure,
    ConnectionRegistry,
    RealtimeRoomCoordinator,
    SafeError,
    safe_error,
)
from streetpoker.api.schemas.realtime import (
    CallCommand,
    CloseRoomCommand,
    CommandAckMessage,
    ConnectionErrorMessage,
    ConnectRequest,
    RequestSeatCommand,
    UpdateSettingsCommand,
    client_command_adapter,
    room_view_dto,
)
from streetpoker.application import (
    GuestId,
    InvalidGuestIdError,
    InvalidRoomCodeError,
    InvalidRoomNameError,
    Pbkdf2PasswordHasher,
    RoomId,
    RoomService,
    RoomSettings,
    StaleHandVersionError,
)
from streetpoker.application.rooms import _PasswordRecord
from streetpoker.domain import ActionKind, PlayerId, SeededRandomSource


def token(fill: int) -> str:
    return base64.urlsafe_b64encode(bytes([fill]) * 32).rstrip(b"=").decode("ascii")


class FixedCodeSource:
    def next_code(self) -> str:
        return "ABCDEFGH"


class SequenceCodeSource:
    def __init__(self, *codes: str) -> None:
        self._codes = iter(codes)

    def next_code(self) -> str:
        return next(self._codes)


class ThreadRecordingHasher:
    def __init__(self) -> None:
        self._delegate = Pbkdf2PasswordHasher(iterations=1)
        self.hash_threads: list[int] = []
        self.verify_threads: list[int] = []
        self.hash_started = threading.Event()
        self.hash_release = threading.Event()
        self.hash_release.set()
        self.verify_started = threading.Event()
        self.verify_release = threading.Event()
        self.verify_release.set()

    def hash_password(self, password: str) -> _PasswordRecord:
        self.hash_threads.append(threading.get_ident())
        self.hash_started.set()
        self.hash_release.wait()
        return self._delegate.hash_password(password)

    def verify_password(self, password: str, record: _PasswordRecord) -> bool:
        self.verify_threads.append(threading.get_ident())
        self.verify_started.set()
        self.verify_release.wait()
        return self._delegate.verify_password(password, record)


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


def service_with_hasher(
    hasher: ThreadRecordingHasher,
    *,
    codes: tuple[str, ...] = ("ABCDEFGH",),
) -> RoomService:
    room_ids = iter(f"thread-room-{index}" for index in range(1, len(codes) + 1))
    player_ids = iter(f"thread-player-{index}" for index in range(1, 20))
    return RoomService(
        code_source=SequenceCodeSource(*codes),
        room_id_factory=lambda: RoomId(next(room_ids)),
        player_id_factory=lambda: PlayerId(next(player_ids)),
        password_hasher=hasher,
        random_source_factory=lambda: SeededRandomSource(47),
    )


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


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            InvalidRoomCodeError("private path C:\\secret"),
            SafeError("invalid_room_code", "The room code is invalid."),
        ),
        (
            InvalidRoomNameError("private path C:\\secret"),
            SafeError("invalid_room_name", "The room name is invalid."),
        ),
        (
            InvalidGuestIdError("private path C:\\secret"),
            SafeError("invalid_guest_id", "The guest identifier is invalid."),
        ),
    ],
)
def test_client_validation_errors_have_fixed_safe_translations(
    error: Exception,
    expected: SafeError,
) -> None:
    translated = safe_error(error)

    assert translated == expected
    assert translated.code != "internal_error"
    assert "private" not in translated.message
    assert "secret" not in translated.message


def test_password_join_succeeds_and_wrong_password_is_safe_off_event_loop_thread() -> None:
    async def scenario() -> None:
        hasher = ThreadRecordingHasher()
        service = service_with_hasher(hasher)
        host_token = token(21)
        alice_token = token(22)
        host = derive_guest_id(host_token)
        created = service.create_room(
            actor=host,
            nickname="Host",
            settings=RoomSettings(room_name="Threaded Join"),
            password="correct",
        )
        hasher.verify_threads.clear()
        coordinator = RealtimeRoomCoordinator(service)
        event_loop_thread = threading.get_ident()

        with pytest.raises(ConnectionFailure) as caught:
            await coordinator.bind(
                cast(WebSocket, RecordingWebSocket()),
                created.room_code,
                ConnectRequest(
                    type="connect",
                    guest_token=alice_token,
                    nickname="Alice",
                    password="wrong",
                ),
            )
        assert caught.value.error == SafeError(
            "wrong_room_password", "The room credentials were rejected."
        )
        assert hasher.verify_threads[-1] != event_loop_thread

        session = await coordinator.bind(
            cast(WebSocket, RecordingWebSocket()),
            created.room_code,
            ConnectRequest(
                type="connect",
                guest_token=alice_token,
                nickname="Alice",
                password="correct",
            ),
        )

        assert hasher.verify_threads[-1] != event_loop_thread
        assert derive_guest_id(alice_token) in {
            member.guest_id for member in service.get_room_snapshot(created.room_id).members
        }
        await coordinator.disconnect(session)

    asyncio.run(scenario())


def test_threaded_password_join_keeps_same_room_serialized() -> None:
    async def scenario() -> None:
        hasher = ThreadRecordingHasher()
        service = service_with_hasher(hasher)
        host_token = token(23)
        alice_token = token(24)
        host = derive_guest_id(host_token)
        created = service.create_room(
            actor=host,
            nickname="Host",
            settings=RoomSettings(room_name="Same Room", seating_approval_required=False),
            password="correct",
        )
        coordinator = RealtimeRoomCoordinator(service)
        host_session = await coordinator.bind(
            cast(WebSocket, RecordingWebSocket()),
            created.room_code,
            ConnectRequest(type="connect", guest_token=host_token),
        )
        hasher.verify_started.clear()
        hasher.verify_release.clear()
        join_task = asyncio.create_task(
            coordinator.bind(
                cast(WebSocket, RecordingWebSocket()),
                created.room_code,
                ConnectRequest(
                    type="connect",
                    guest_token=alice_token,
                    nickname="Alice",
                    password="correct",
                ),
            )
        )
        try:
            assert await asyncio.to_thread(hasher.verify_started.wait, 1.0)
            command_task = asyncio.create_task(
                coordinator.handle_command(
                    host_session,
                    RequestSeatCommand(
                        type="request_seat",
                        command_id="same-room",
                        seat_index=1,
                    ),
                )
            )
            await asyncio.sleep(0)
            assert not command_task.done()
        finally:
            hasher.verify_release.set()

        alice_session = await join_task
        await command_task
        assert service.get_room_snapshot(created.room_id).seats[1].guest_id == host
        await coordinator.disconnect(alice_session)
        await coordinator.disconnect(host_session)

    asyncio.run(scenario())


def test_other_room_progresses_while_password_join_runs_in_worker() -> None:
    async def scenario() -> None:
        hasher = ThreadRecordingHasher()
        service = service_with_hasher(hasher, codes=("ABCDEFGH", "BCDEFGHJ"))
        host_token = token(25)
        alice_token = token(26)
        host = derive_guest_id(host_token)
        protected = service.create_room(
            actor=host,
            nickname="Host One",
            settings=RoomSettings(room_name="Protected"),
            password="correct",
        )
        other = service.create_room(
            actor=host,
            nickname="Host Two",
            settings=RoomSettings(room_name="Other", seating_approval_required=False),
        )
        coordinator = RealtimeRoomCoordinator(service)
        other_session = await coordinator.bind(
            cast(WebSocket, RecordingWebSocket()),
            other.room_code,
            ConnectRequest(type="connect", guest_token=host_token),
        )
        hasher.verify_started.clear()
        hasher.verify_release.clear()
        join_task = asyncio.create_task(
            coordinator.bind(
                cast(WebSocket, RecordingWebSocket()),
                protected.room_code,
                ConnectRequest(
                    type="connect",
                    guest_token=alice_token,
                    nickname="Alice",
                    password="correct",
                ),
            )
        )
        try:
            assert await asyncio.to_thread(hasher.verify_started.wait, 1.0)
            await asyncio.wait_for(
                coordinator.handle_command(
                    other_session,
                    RequestSeatCommand(
                        type="request_seat",
                        command_id="other-room",
                        seat_index=4,
                    ),
                ),
                timeout=1.0,
            )
            assert service.get_room_snapshot(other.room_id).seats[4].guest_id == host
        finally:
            hasher.verify_release.set()

        alice_session = await join_task
        await coordinator.disconnect(alice_session)
        await coordinator.disconnect(other_session)

    asyncio.run(scenario())


def test_password_settings_hash_runs_off_event_loop_thread() -> None:
    async def scenario() -> None:
        hasher = ThreadRecordingHasher()
        service = service_with_hasher(hasher)
        host_token = token(27)
        host = derive_guest_id(host_token)
        created = service.create_room(
            actor=host,
            nickname="Host",
            settings=RoomSettings(room_name="Settings"),
        )
        coordinator = RealtimeRoomCoordinator(service)
        session = await coordinator.bind(
            cast(WebSocket, RecordingWebSocket()),
            created.room_code,
            ConnectRequest(type="connect", guest_token=host_token),
        )
        hasher.hash_threads.clear()
        event_loop_thread = threading.get_ident()

        await coordinator.handle_command(
            session,
            UpdateSettingsCommand(
                type="update_settings",
                command_id="password",
                password="replacement",
            ),
        )

        assert len(hasher.hash_threads) == 1
        assert hasher.hash_threads[0] != event_loop_thread
        assert service.get_room_snapshot(created.room_id).settings.password_protected
        await coordinator.disconnect(session)

    asyncio.run(scenario())


def test_protected_room_creation_hashes_off_event_loop_without_socket_side_effects() -> None:
    async def scenario() -> None:
        hasher = ThreadRecordingHasher()
        service = service_with_hasher(hasher)
        coordinator = RealtimeRoomCoordinator(service)
        event_loop_thread = threading.get_ident()
        hasher.hash_threads.clear()

        created = await coordinator.create_room(
            actor=derive_guest_id(token(31)),
            nickname="Host",
            settings=RoomSettings(room_name="Created"),
            password="correct",
        )

        assert len(hasher.hash_threads) == 1
        assert hasher.hash_threads[0] != event_loop_thread
        assert created.settings.password_protected
        assert coordinator.registry.all_sessions(created.room_id) == ()

    asyncio.run(scenario())


def test_concurrent_room_creations_are_serialized() -> None:
    async def scenario() -> None:
        hasher = ThreadRecordingHasher()
        service = service_with_hasher(hasher, codes=("ABCDEFGH", "BCDEFGHJ"))
        coordinator = RealtimeRoomCoordinator(service)
        hasher.hash_started.clear()
        hasher.hash_release.clear()
        first_task = asyncio.create_task(
            coordinator.create_room(
                actor=derive_guest_id(token(32)),
                nickname="First",
                settings=RoomSettings(room_name="First"),
                password="correct",
            )
        )
        try:
            assert await asyncio.to_thread(hasher.hash_started.wait, 1.0)
            second_task = asyncio.create_task(
                coordinator.create_room(
                    actor=derive_guest_id(token(33)),
                    nickname="Second",
                    settings=RoomSettings(room_name="Second"),
                )
            )
            await asyncio.sleep(0)
            assert not second_task.done()
        finally:
            hasher.hash_release.set()

        first, second = await asyncio.gather(first_task, second_task)
        assert (first.room_code, second.room_code) == ("ABCDEFGH", "BCDEFGHJ")

    asyncio.run(scenario())


def test_cancelled_creation_holds_lock_until_worker_finishes() -> None:
    async def scenario() -> None:
        hasher = ThreadRecordingHasher()
        service = service_with_hasher(hasher, codes=("ABCDEFGH", "BCDEFGHJ"))
        coordinator = RealtimeRoomCoordinator(service)
        hasher.hash_started.clear()
        hasher.hash_release.clear()
        cancelled = asyncio.create_task(
            coordinator.create_room(
                actor=derive_guest_id(token(34)),
                nickname="Cancelled",
                settings=RoomSettings(room_name="Cancelled"),
                password="correct",
            )
        )
        assert await asyncio.to_thread(hasher.hash_started.wait, 1.0)
        cancelled.cancel()
        waiting = asyncio.create_task(
            coordinator.create_room(
                actor=derive_guest_id(token(35)),
                nickname="Waiting",
                settings=RoomSettings(room_name="Waiting"),
            )
        )
        await asyncio.sleep(0)
        assert not cancelled.done()
        assert not waiting.done()

        hasher.hash_release.set()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        created = await waiting
        assert created.room_code == "BCDEFGHJ"
        assert service.get_room_snapshot_by_code("ABCDEFGH").settings.password_protected

    asyncio.run(scenario())


def test_creation_can_safely_progress_while_other_room_password_join_hashes() -> None:
    async def scenario() -> None:
        hasher = ThreadRecordingHasher()
        service = service_with_hasher(hasher, codes=("ABCDEFGH", "BCDEFGHJ"))
        host_token = token(36)
        joining_token = token(37)
        protected = service.create_room(
            actor=derive_guest_id(host_token),
            nickname="Protected Host",
            settings=RoomSettings(room_name="Protected"),
            password="correct",
        )
        coordinator = RealtimeRoomCoordinator(service)
        host_session = await coordinator.bind(
            cast(WebSocket, RecordingWebSocket()),
            protected.room_code,
            ConnectRequest(type="connect", guest_token=host_token),
        )
        hasher.verify_started.clear()
        hasher.verify_release.clear()
        join_task = asyncio.create_task(
            coordinator.bind(
                cast(WebSocket, RecordingWebSocket()),
                protected.room_code,
                ConnectRequest(
                    type="connect",
                    guest_token=joining_token,
                    nickname="Joining",
                    password="correct",
                ),
            )
        )
        try:
            assert await asyncio.to_thread(hasher.verify_started.wait, 1.0)
            created = await asyncio.wait_for(
                coordinator.create_room(
                    actor=derive_guest_id(token(38)),
                    nickname="Other Host",
                    settings=RoomSettings(room_name="Other"),
                ),
                timeout=1.0,
            )
            assert created.room_code == "BCDEFGHJ"
        finally:
            hasher.verify_release.set()

        joined_session = await join_task
        assert len(service.get_room_snapshot(protected.room_id).members) == 2
        assert len(service.get_room_snapshot(created.room_id).members) == 1
        await coordinator.disconnect(joined_session)
        await coordinator.disconnect(host_session)

    asyncio.run(scenario())


def test_creation_can_safely_progress_while_other_room_password_update_hashes() -> None:
    async def scenario() -> None:
        hasher = ThreadRecordingHasher()
        service = service_with_hasher(hasher, codes=("ABCDEFGH", "BCDEFGHJ"))
        host_token = token(39)
        host = derive_guest_id(host_token)
        original = service.create_room(
            actor=host,
            nickname="Original Host",
            settings=RoomSettings(room_name="Original"),
        )
        coordinator = RealtimeRoomCoordinator(service)
        host_session = await coordinator.bind(
            cast(WebSocket, RecordingWebSocket()),
            original.room_code,
            ConnectRequest(type="connect", guest_token=host_token),
        )
        hasher.hash_started.clear()
        hasher.hash_release.clear()
        update_task = asyncio.create_task(
            coordinator.handle_command(
                host_session,
                UpdateSettingsCommand(
                    type="update_settings",
                    command_id="protect",
                    password="correct",
                ),
            )
        )
        try:
            assert await asyncio.to_thread(hasher.hash_started.wait, 1.0)
            created = await asyncio.wait_for(
                coordinator.create_room(
                    actor=derive_guest_id(token(40)),
                    nickname="Other Host",
                    settings=RoomSettings(room_name="Other"),
                ),
                timeout=1.0,
            )
            assert created.room_code == "BCDEFGHJ"
        finally:
            hasher.hash_release.set()

        await update_task
        assert service.get_room_snapshot(original.room_id).settings.password_protected
        assert len(service.get_room_snapshot(created.room_id).members) == 1
        await coordinator.disconnect(host_session)

    asyncio.run(scenario())


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
