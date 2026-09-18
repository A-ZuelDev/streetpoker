import asyncio
import base64
import threading
from dataclasses import replace
from typing import cast

import pytest
from fastapi import WebSocket
from pydantic import ValidationError

from streetpoker.api.guest_identity import derive_guest_id
from streetpoker.api.realtime import (
    DISCONNECT_GRACE_MS,
    ConnectionFailure,
    ConnectionRegistry,
    RealtimeRoomCoordinator,
    SafeError,
    SocketSession,
    safe_error,
)
from streetpoker.api.schemas.realtime import (
    CallCommand,
    CloseRoomCommand,
    CommandAckMessage,
    ConnectionErrorMessage,
    ConnectRequest,
    RequestSeatCommand,
    StartHandCommand,
    UpdateSettingsCommand,
    client_command_adapter,
    room_view_dto,
)
from streetpoker.application import (
    ActionDeadlineExpiredError,
    Clock,
    GuestId,
    InvalidGuestIdError,
    InvalidRoomCodeError,
    InvalidRoomNameError,
    Pbkdf2PasswordHasher,
    RoomId,
    RoomService,
    RoomSettings,
    StaleHandVersionError,
    TurnDeadline,
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


class FakeClock:
    def __init__(self) -> None:
        self.monotonic_ms = 1_000
        self.unix_ms = 1_800_000_000_000
        self.advance_after_read = False

    def now_monotonic_ms(self) -> int:
        value = self.monotonic_ms
        if self.advance_after_read:
            self.monotonic_ms += 1
        return value

    def now_unix_ms(self) -> int:
        return self.unix_ms

    def advance(self, milliseconds: int) -> None:
        self.monotonic_ms += milliseconds
        self.unix_ms += milliseconds


def built_service(
    *, approval: bool = False, clock: Clock | None = None
) -> tuple[RoomService, RoomId, dict[str, GuestId]]:
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
        clock=clock,
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


def test_replaced_host_cannot_issue_room_commands_or_remove_new_binding() -> None:
    async def scenario() -> None:
        service, room_id, guests = built_service()
        room_code = service.get_room_snapshot(room_id).room_code
        service.join_room(room_code=room_code, actor=guests["alice"], nickname="Alice")
        service.request_seat(room_id=room_id, actor=guests["host"], seat_index=0)
        service.request_seat(room_id=room_id, actor=guests["alice"], seat_index=2)
        coordinator = RealtimeRoomCoordinator(service)
        old_socket = RecordingWebSocket()
        old = await coordinator.bind(
            cast(WebSocket, old_socket),
            room_code,
            ConnectRequest(type="connect", guest_token=token(1)),
        )
        before = service.get_room_view(room_id=room_id, viewer=guests["host"])
        new_socket = RecordingWebSocket()
        current = await coordinator.bind(
            cast(WebSocket, new_socket),
            room_code,
            ConnectRequest(type="connect", guest_token=token(1)),
        )
        assert service.get_room_view(room_id=room_id, viewer=guests["host"]) == before
        assert not coordinator.registry.is_registered(old)
        assert coordinator.registry.is_registered(current)

        stale_commands = [
            {"type": "start_hand", "command_id": "stale-start", "hand_number": 1},
            {"type": "update_settings", "command_id": "stale-settings", "room_name": "Changed"},
            {
                "type": "kick",
                "command_id": "stale-kick",
                "target_guest_id": guests["alice"].value,
            },
            {"type": "close_room", "command_id": "stale-close"},
        ]
        for payload in stale_commands:
            await coordinator.handle_command(old, client_command_adapter.validate_python(payload))
            assert service.get_room_view(room_id=room_id, viewer=guests["host"]) == before
        await coordinator.disconnect(old)
        assert coordinator.registry.is_registered(current)
        assert coordinator.registry.sessions_by_guest(room_id)[guests["host"]] == (current,)
        await asyncio.sleep(0)
        assert old_socket.closed == [(1008, "session ended")]
        assert all("stale" not in str(message) for message in old_socket.sent)

        await coordinator.handle_command(
            current,
            client_command_adapter.validate_python(
                {"type": "start_hand", "command_id": "current-start", "hand_number": 1}
            ),
        )
        assert service.get_room_view(room_id=room_id, viewer=guests["host"]).active_hand is not None
        await asyncio.sleep(0)
        assert any(
            cast(dict[str, object], message).get("command_id") == "current-start"
            for message in new_socket.sent
        )
        await coordinator.disconnect(current)

    asyncio.run(scenario())


def test_replaced_actor_cannot_act_but_new_binding_can() -> None:
    async def scenario() -> None:
        service, room_id, guests = built_service()
        room_code = service.get_room_snapshot(room_id).room_code
        service.join_room(room_code=room_code, actor=guests["alice"], nickname="Alice")
        service.request_seat(room_id=room_id, actor=guests["host"], seat_index=0)
        service.request_seat(room_id=room_id, actor=guests["alice"], seat_index=2)
        started = service.start_hand(room_id=room_id, actor=guests["host"], expected_hand_number=1)
        hand = started.active_hand
        assert hand is not None and ActionKind.CALL in hand.legal_actions.kinds
        actor = hand.current_actor
        actor_token = token(1) if actor == guests["host"] else token(2)
        coordinator = RealtimeRoomCoordinator(service)
        old = await coordinator.bind(
            cast(WebSocket, RecordingWebSocket()),
            room_code,
            ConnectRequest(type="connect", guest_token=actor_token),
        )
        before = service.get_room_view(room_id=room_id, viewer=actor)
        current = await coordinator.bind(
            cast(WebSocket, RecordingWebSocket()),
            room_code,
            ConnectRequest(type="connect", guest_token=actor_token),
        )
        command = CallCommand(
            type="call",
            command_id="call",
            hand_number=hand.hand_number,
            expected_action_sequence=hand.action_sequence,
        )
        await coordinator.handle_command(old, command)
        assert service.get_room_view(room_id=room_id, viewer=actor) == before
        await coordinator.disconnect(old)
        assert coordinator.registry.is_registered(current)
        await coordinator.handle_command(current, command)
        after = service.get_room_view(room_id=room_id, viewer=actor)
        assert after.active_hand is not None
        assert after.active_hand.action_sequence == hand.action_sequence + 1
        await coordinator.disconnect(current)

    asyncio.run(scenario())


def test_replaced_guest_cannot_leave_but_current_guest_can() -> None:
    async def scenario() -> None:
        service, room_id, guests = built_service()
        room_code = service.get_room_snapshot(room_id).room_code
        service.join_room(room_code=room_code, actor=guests["alice"], nickname="Alice")
        coordinator = RealtimeRoomCoordinator(service)
        old = await coordinator.bind(
            cast(WebSocket, RecordingWebSocket()),
            room_code,
            ConnectRequest(type="connect", guest_token=token(2)),
        )
        current = await coordinator.bind(
            cast(WebSocket, RecordingWebSocket()),
            room_code,
            ConnectRequest(type="connect", guest_token=token(2)),
        )
        before = service.get_room_snapshot(room_id)
        leave = client_command_adapter.validate_python({"type": "leave", "command_id": "leave"})
        await coordinator.handle_command(old, leave)
        assert service.get_room_snapshot(room_id) == before
        assert coordinator.registry.is_registered(current)
        await coordinator.handle_command(current, leave)
        assert guests["alice"] not in {
            member.guest_id for member in service.get_room_snapshot(room_id).members
        }
        assert not coordinator.registry.is_registered(current)
        await coordinator.disconnect(old)
        await coordinator.disconnect(current)

    asyncio.run(scenario())


def test_near_simultaneous_binds_install_last_successful_session() -> None:
    async def scenario() -> None:
        service, room_id, guests = built_service()
        room_code = service.get_room_snapshot(room_id).room_code
        coordinator = RealtimeRoomCoordinator(service)
        before = service.get_room_view(room_id=room_id, viewer=guests["host"])
        async with coordinator._lock_for(room_id):
            first_task = asyncio.create_task(
                coordinator.bind(
                    cast(WebSocket, RecordingWebSocket()),
                    room_code,
                    ConnectRequest(type="connect", guest_token=token(1)),
                )
            )
            await asyncio.sleep(0)
            second_task = asyncio.create_task(
                coordinator.bind(
                    cast(WebSocket, RecordingWebSocket()),
                    room_code,
                    ConnectRequest(type="connect", guest_token=token(1)),
                )
            )
            await asyncio.sleep(0)
            assert not first_task.done() and not second_task.done()
        first, second = await asyncio.gather(first_task, second_task)
        assert not coordinator.registry.is_registered(first)
        assert coordinator.registry.is_registered(second)
        assert coordinator.registry.sessions_by_guest(room_id)[guests["host"]] == (second,)
        assert service.get_room_view(room_id=room_id, viewer=guests["host"]) == before
        await coordinator.disconnect(first)
        assert coordinator.registry.is_registered(second)
        await coordinator.handle_command(
            second,
            RequestSeatCommand(type="request_seat", command_id="seat", seat_index=1),
        )
        assert service.get_room_snapshot(room_id).seats[1].guest_id == guests["host"]
        await coordinator.disconnect(second)

    asyncio.run(scenario())


def test_queued_old_command_loses_when_replacement_acquires_room_lock_first() -> None:
    async def scenario() -> None:
        service, room_id, guests = built_service()
        room_code = service.get_room_snapshot(room_id).room_code
        coordinator = RealtimeRoomCoordinator(service)
        old = await coordinator.bind(
            cast(WebSocket, RecordingWebSocket()),
            room_code,
            ConnectRequest(type="connect", guest_token=token(1)),
        )
        async with coordinator._lock_for(room_id):
            bind_task = asyncio.create_task(
                coordinator.bind(
                    cast(WebSocket, RecordingWebSocket()),
                    room_code,
                    ConnectRequest(type="connect", guest_token=token(1)),
                )
            )
            await asyncio.sleep(0)
            stale_command_task = asyncio.create_task(
                coordinator.handle_command(
                    old,
                    RequestSeatCommand(type="request_seat", command_id="stale", seat_index=1),
                )
            )
            await asyncio.sleep(0)
            assert not bind_task.done() and not stale_command_task.done()
        current = await bind_task
        await stale_command_task
        assert coordinator.registry.is_registered(current)
        assert not coordinator.registry.is_registered(old)
        assert service.get_room_snapshot(room_id).seats[1].guest_id is None
        await coordinator.disconnect(old)
        assert coordinator.registry.is_registered(current)
        await coordinator.handle_command(
            current,
            RequestSeatCommand(type="request_seat", command_id="current", seat_index=1),
        )
        assert service.get_room_snapshot(room_id).seats[1].guest_id == guests["host"]
        await coordinator.disconnect(current)

    asyncio.run(scenario())


def started_timed_service() -> tuple[RoomService, RoomId, dict[str, GuestId], FakeClock]:
    clock = FakeClock()
    service, room_id, guests = built_service(clock=clock)
    room_code = service.get_room_snapshot(room_id).room_code
    service.join_room(room_code=room_code, actor=guests["alice"], nickname="Alice")
    service.request_seat(room_id=room_id, actor=guests["host"], seat_index=0)
    service.request_seat(room_id=room_id, actor=guests["alice"], seat_index=2)
    started = service.start_hand(room_id=room_id, actor=guests["host"], expected_hand_number=1)
    assert started.active_hand is not None
    return service, room_id, guests, clock


def current_deadline(service: RoomService, room_id: RoomId) -> TurnDeadline:
    deadline = service.current_turn_deadline(room_id)
    assert deadline is not None
    return deadline


async def bind_existing_guest(
    coordinator: RealtimeRoomCoordinator, service: RoomService, room_id: RoomId, fill: int
) -> tuple[SocketSession, RecordingWebSocket]:
    socket = RecordingWebSocket()
    session = await coordinator.bind(
        cast(WebSocket, socket),
        service.get_room_snapshot(room_id).room_code,
        ConnectRequest(type="connect", guest_token=token(fill)),
    )
    await asyncio.sleep(0)
    return session, socket


def test_timebank_starts_full_and_is_preserved_on_early_actions() -> None:
    service, room_id, guests, clock = started_timed_service()
    first = current_deadline(service, room_id)
    initial = service.get_room_view(room_id=room_id, viewer=guests["host"])
    assert initial.active_hand is not None
    assert initial.active_hand.current_actor_timebank_ms == 60_000
    assert not initial.active_hand.current_actor_using_timebank

    clock.advance(29_999)
    service.call(
        room_id=room_id,
        actor=first.actor,
        hand_number=first.hand_number,
        expected_action_sequence=first.action_sequence,
    )
    second = current_deadline(service, room_id)
    assert second.actor != first.actor
    second_view = service.get_room_view(room_id=room_id, viewer=guests["alice"])
    assert second_view.active_hand is not None
    assert second_view.active_hand.current_actor_timebank_ms == 60_000
    service.check(
        room_id=room_id,
        actor=second.actor,
        hand_number=second.hand_number,
        expected_action_sequence=second.action_sequence,
    )
    flop = current_deadline(service, room_id)
    assert flop.actor == second.actor
    service.check(
        room_id=room_id,
        actor=flop.actor,
        hand_number=flop.hand_number,
        expected_action_sequence=flop.action_sequence,
    )
    later = current_deadline(service, room_id)
    assert later.actor == first.actor
    later_view = service.get_room_view(room_id=room_id, viewer=first.actor)
    assert later_view.active_hand is not None
    assert later_view.active_hand.current_actor_timebank_ms == 60_000


def test_base_expiry_consumes_timebank_once_and_stale_callbacks_cannot_extend() -> None:
    service, room_id, guests, clock = started_timed_service()
    base = current_deadline(service, room_id)
    clock.advance(30_000)
    assert service.expire_turn(base) is True
    extended = current_deadline(service, room_id)
    assert extended.actor == base.actor
    assert extended.hand_number == base.hand_number
    assert extended.action_sequence == base.action_sequence + 1
    assert extended.revision == base.revision + 1
    assert extended.monotonic_ms == base.monotonic_ms + 60_000
    assert extended.unix_ms == base.unix_ms + 60_000
    view = service.get_room_view(room_id=room_id, viewer=guests["host"])
    assert view.active_hand is not None
    assert view.active_hand.current_actor_timebank_ms == 0
    assert view.active_hand.current_actor_using_timebank
    assert view.active_hand.action_deadline_unix_ms == extended.unix_ms
    assert view.active_hand.action_sequence == 1
    assert service.expire_turn(base) is False
    assert service.expire_turn(replace(extended, revision=base.revision)) is False
    assert service.current_turn_deadline(room_id) == extended


def test_timebank_action_uses_fresh_cas_and_consumption_survives_later_turns() -> None:
    service, room_id, guests, clock = started_timed_service()
    base = current_deadline(service, room_id)
    clock.advance(30_000)
    with pytest.raises(ActionDeadlineExpiredError):
        service.call(
            room_id=room_id,
            actor=base.actor,
            hand_number=base.hand_number,
            expected_action_sequence=base.action_sequence,
        )
    assert service.expire_turn(base) is True
    extended = current_deadline(service, room_id)
    with pytest.raises(StaleHandVersionError):
        service.call(
            room_id=room_id,
            actor=base.actor,
            hand_number=base.hand_number,
            expected_action_sequence=base.action_sequence,
        )
    clock.advance(59_999)
    service.call(
        room_id=room_id,
        actor=extended.actor,
        hand_number=extended.hand_number,
        expected_action_sequence=extended.action_sequence,
    )
    assert service.expire_turn(extended) is False
    second = current_deadline(service, room_id)
    service.check(
        room_id=room_id,
        actor=second.actor,
        hand_number=second.hand_number,
        expected_action_sequence=second.action_sequence,
    )
    flop = current_deadline(service, room_id)
    service.check(
        room_id=room_id,
        actor=flop.actor,
        hand_number=flop.hand_number,
        expected_action_sequence=flop.action_sequence,
    )
    later = current_deadline(service, room_id)
    assert later.actor == base.actor
    view = service.get_room_view(room_id=room_id, viewer=guests["host"])
    assert view.active_hand is not None
    assert view.active_hand.current_actor_timebank_ms == 0
    assert not view.active_hand.current_actor_using_timebank
    clock.advance(30_000)
    assert service.expire_turn(later) is True
    assert current_deadline(service, room_id).action_sequence == later.action_sequence + 1


def test_extended_deadline_equality_times_out_and_next_hand_refills_bank() -> None:
    service, room_id, guests, clock = started_timed_service()
    base = current_deadline(service, room_id)
    clock.advance(30_000)
    assert service.expire_turn(base) is True
    extended = current_deadline(service, room_id)
    clock.advance(60_000)
    with pytest.raises(ActionDeadlineExpiredError):
        service.call(
            room_id=room_id,
            actor=extended.actor,
            hand_number=extended.hand_number,
            expected_action_sequence=extended.action_sequence,
        )
    assert service.expire_turn(extended) is True
    assert service.expire_turn(extended) is False
    settled = service.get_room_view(room_id=room_id, viewer=guests["host"])
    assert settled.active_hand is None
    assert settled.last_hand is not None
    assert settled.last_hand.final_action_sequence == 2
    assert service.current_turn_deadline(room_id) is None
    next_hand = service.start_hand(room_id=room_id, actor=guests["host"], expected_hand_number=2)
    assert next_hand.active_hand is not None
    assert next_hand.active_hand.current_actor_timebank_ms == 60_000
    assert not next_hand.active_hand.current_actor_using_timebank


def test_fake_clock_creates_and_replaces_turn_deadlines_without_real_sleep() -> None:
    service, room_id, guests, clock = started_timed_service()
    first = current_deadline(service, room_id)
    view = service.get_room_view(room_id=room_id, viewer=guests["host"])
    assert view.active_hand is not None
    assert first.monotonic_ms == 31_000
    assert first.unix_ms == 1_800_000_030_000
    assert first.revision == 1
    assert view.active_hand.action_deadline_unix_ms == first.unix_ms
    assert view.active_hand.current_actor == first.actor

    clock.advance(29_999)
    acted = service.call(
        room_id=room_id,
        actor=first.actor,
        hand_number=first.hand_number,
        expected_action_sequence=first.action_sequence,
    )
    second = current_deadline(service, room_id)
    assert acted.active_hand is not None
    assert second.action_sequence == 1
    assert second.revision == 2
    assert second.actor != first.actor
    assert second.monotonic_ms == 60_999
    assert second.unix_ms == 1_800_000_059_999

    clock.advance(1)
    assert service.expire_turn(first) is False
    assert service.current_turn_deadline(room_id) == second
    clock.unix_ms += 100_000
    assert service.expire_turn(second) is False


def test_equal_deadline_rejects_player_action_and_timeout_checks_to_next_street() -> None:
    service, room_id, guests, clock = started_timed_service()
    first = current_deadline(service, room_id)
    service.call(
        room_id=room_id,
        actor=first.actor,
        hand_number=first.hand_number,
        expected_action_sequence=first.action_sequence,
    )
    second = current_deadline(service, room_id)
    before = service.get_room_view(room_id=room_id, viewer=guests["host"])
    assert before.active_hand is not None
    assert ActionKind.CHECK in before.active_hand.legal_actions.kinds
    clock.advance(30_000)
    with pytest.raises(ActionDeadlineExpiredError):
        service.check(
            room_id=room_id,
            actor=second.actor,
            hand_number=second.hand_number,
            expected_action_sequence=second.action_sequence,
        )
    assert service.get_room_view(room_id=room_id, viewer=guests["host"]) == before
    assert service.expire_turn(second) is True
    extended = current_deadline(service, room_id)
    assert extended.action_sequence == 2
    assert extended.revision == 3
    clock.advance(60_000)
    assert service.expire_turn(extended) is True
    after = service.get_room_view(room_id=room_id, viewer=guests["host"])
    assert after.active_hand is not None
    assert after.active_hand.action_sequence == 3
    assert after.active_hand.phase.value == "flop"
    assert current_deadline(service, room_id).revision == 4
    assert service.expire_turn(second) is False


def test_timeout_folds_when_check_illegal_and_settles_once() -> None:
    service, room_id, guests, clock = started_timed_service()
    first = current_deadline(service, room_id)
    before = service.get_room_view(room_id=room_id, viewer=guests["host"])
    assert before.active_hand is not None
    assert ActionKind.CHECK not in before.active_hand.legal_actions.kinds
    clock.advance(30_001)
    with pytest.raises(ActionDeadlineExpiredError):
        service.call(
            room_id=room_id,
            actor=first.actor,
            hand_number=first.hand_number,
            expected_action_sequence=first.action_sequence,
        )
    assert service.expire_turn(first) is True
    extended = current_deadline(service, room_id)
    assert service.expire_turn(first) is False
    clock.advance(59_999)
    assert service.expire_turn(extended) is True
    after = service.get_room_view(room_id=room_id, viewer=guests["host"])
    assert after.active_hand is None
    assert after.last_hand is not None
    assert after.last_hand.final_action_sequence == 2
    assert after.last_hand.source.value == "complete_by_fold"
    assert service.current_turn_deadline(room_id) is None
    assert service.expire_turn(first) is False
    assert sum(seat.stack or 0 for seat in after.room.seats) == 2_000


def test_old_hand_sequence_actor_and_revision_callbacks_are_ignored() -> None:
    service, room_id, guests, clock = started_timed_service()
    first = current_deadline(service, room_id)
    clock.advance(30_000)
    assert service.expire_turn(replace(first, hand_number=2)) is False
    assert service.expire_turn(replace(first, action_sequence=1)) is False
    other = guests["alice"] if first.actor == guests["host"] else guests["host"]
    assert service.expire_turn(replace(first, actor=other)) is False
    assert service.expire_turn(replace(first, revision=first.revision + 1)) is False
    assert service.expire_turn(replace(first, monotonic_ms=first.monotonic_ms + 1)) is False
    assert service.current_turn_deadline(room_id) == first
    assert service.expire_turn(first) is True
    assert service.expire_turn(first) is False
    clock.advance(60_000)
    assert service.expire_turn(current_deadline(service, room_id)) is True
    service.start_hand(room_id=room_id, actor=guests["host"], expected_hand_number=2)
    second_hand = current_deadline(service, room_id)
    assert second_hand.hand_number == 2
    assert service.expire_turn(first) is False
    assert service.current_turn_deadline(room_id) == second_hand


def test_disconnect_reconnect_and_replacement_preserve_deadline_then_sync_timeout_state() -> None:
    async def scenario() -> None:
        service, room_id, guests, clock = started_timed_service()
        room_code = service.get_room_snapshot(room_id).room_code
        deadline = current_deadline(service, room_id)
        actor_token = token(1) if deadline.actor == guests["host"] else token(2)
        coordinator = RealtimeRoomCoordinator(service)
        first = await coordinator.bind(
            cast(WebSocket, RecordingWebSocket()),
            room_code,
            ConnectRequest(type="connect", guest_token=actor_token),
        )
        scheduled = coordinator._turn_tasks[room_id]
        await coordinator.disconnect(first)
        assert service.current_turn_deadline(room_id) == deadline
        assert coordinator._turn_tasks[room_id] is scheduled

        second_socket = RecordingWebSocket()
        second = await coordinator.bind(
            cast(WebSocket, second_socket),
            room_code,
            ConnectRequest(type="connect", guest_token=actor_token),
        )
        replacement_socket = RecordingWebSocket()
        replacement = await coordinator.bind(
            cast(WebSocket, replacement_socket),
            room_code,
            ConnectRequest(type="connect", guest_token=actor_token),
        )
        await coordinator.disconnect(second)
        assert service.current_turn_deadline(room_id) == deadline
        assert coordinator._turn_tasks[room_id] is scheduled
        assert coordinator.registry.is_registered(replacement)
        await asyncio.sleep(0)
        initial = cast(dict[str, object], replacement_socket.sent[1])
        snapshot = cast(dict[str, object], initial["snapshot"])
        active = cast(dict[str, object], snapshot["active_hand"])
        assert active["action_deadline_unix_ms"] == deadline.unix_ms

        clock.advance(30_000)
        assert await coordinator.expire_turn(deadline) is True
        extended = current_deadline(service, room_id)
        assert extended.action_sequence == 1
        assert room_id in coordinator._turn_tasks
        clock.advance(60_000)
        assert await coordinator.expire_turn(extended) is True
        assert room_id not in coordinator._turn_tasks
        await coordinator.disconnect(replacement)
        reconnected_socket = RecordingWebSocket()
        reconnected = await coordinator.bind(
            cast(WebSocket, reconnected_socket),
            room_code,
            ConnectRequest(type="connect", guest_token=actor_token),
        )
        await asyncio.sleep(0)
        latest = cast(dict[str, object], reconnected_socket.sent[1])
        latest_snapshot = cast(dict[str, object], latest["snapshot"])
        assert latest_snapshot["active_hand"] is None
        assert latest_snapshot["last_hand"] is not None
        await coordinator.disconnect(reconnected)
        await coordinator.shutdown()

    asyncio.run(scenario())


def test_reconnect_and_replacement_during_timebank_keep_state_and_private_views() -> None:
    async def scenario() -> None:
        service, room_id, guests, clock = started_timed_service()
        room_code = service.get_room_snapshot(room_id).room_code
        base = current_deadline(service, room_id)
        actor_token = token(1) if base.actor == guests["host"] else token(2)
        other_token = token(2) if base.actor == guests["host"] else token(1)
        coordinator = RealtimeRoomCoordinator(service)
        actor_socket = RecordingWebSocket()
        other_socket = RecordingWebSocket()
        actor = await coordinator.bind(
            cast(WebSocket, actor_socket),
            room_code,
            ConnectRequest(type="connect", guest_token=actor_token),
        )
        other = await coordinator.bind(
            cast(WebSocket, other_socket),
            room_code,
            ConnectRequest(type="connect", guest_token=other_token),
        )
        old_task = coordinator._turn_tasks[room_id].task
        clock.advance(30_000)
        assert await coordinator.expire_turn(base) is True
        extended = current_deadline(service, room_id)
        extended_task = coordinator._turn_tasks[room_id].task
        await asyncio.sleep(0)
        assert old_task.done()
        assert extended_task is not old_task
        assert len(coordinator._turn_tasks) == 1
        assert await coordinator.expire_turn(base) is False
        assert coordinator._turn_tasks[room_id].task is extended_task
        for socket, viewer in ((actor_socket, base.actor), (other_socket, other.guest_id)):
            message = cast(dict[str, object], socket.sent[-1])
            snapshot = cast(dict[str, object], message["snapshot"])
            active = cast(dict[str, object], snapshot["active_hand"])
            players = cast(list[dict[str, object]], active["players"])
            assert active["current_actor_timebank_ms"] == 0
            assert active["current_actor_using_timebank"] is True
            assert active["action_deadline_unix_ms"] == extended.unix_ms
            assert {player["guest_id"] for player in players if player["hole_cards"]} == {
                viewer.value
            }
            assert "private-player" not in str(message)
            assert actor_token not in str(message)

        await coordinator.disconnect(actor)
        reconnect_socket = RecordingWebSocket()
        reconnect = await coordinator.bind(
            cast(WebSocket, reconnect_socket),
            room_code,
            ConnectRequest(type="connect", guest_token=actor_token),
        )
        replacement_socket = RecordingWebSocket()
        replacement = await coordinator.bind(
            cast(WebSocket, replacement_socket),
            room_code,
            ConnectRequest(type="connect", guest_token=actor_token),
        )
        await coordinator.disconnect(reconnect)
        assert coordinator.registry.is_registered(replacement)
        assert current_deadline(service, room_id) == extended
        assert coordinator._turn_tasks[room_id].task is extended_task
        await asyncio.sleep(0)
        state = cast(dict[str, object], replacement_socket.sent[1])
        snapshot = cast(dict[str, object], state["snapshot"])
        active = cast(dict[str, object], snapshot["active_hand"])
        assert active["current_actor_timebank_ms"] == 0
        assert active["current_actor_using_timebank"] is True
        assert active["action_deadline_unix_ms"] == extended.unix_ms
        await coordinator.disconnect(replacement)
        await coordinator.disconnect(other)
        await coordinator.shutdown()
        assert coordinator._turn_tasks == {}
        assert extended_task.done()

    asyncio.run(scenario())


def test_reconnect_after_expiry_resolves_timeout_before_fresh_state() -> None:
    async def scenario() -> None:
        service, room_id, guests, clock = started_timed_service()
        deadline = current_deadline(service, room_id)
        clock.advance(90_000)
        actor_token = token(1) if deadline.actor == guests["host"] else token(2)
        coordinator = RealtimeRoomCoordinator(service)
        socket = RecordingWebSocket()
        session = await coordinator.bind(
            cast(WebSocket, socket),
            service.get_room_snapshot(room_id).room_code,
            ConnectRequest(type="connect", guest_token=actor_token),
        )
        await asyncio.sleep(0)
        state = cast(dict[str, object], socket.sent[1])
        snapshot = cast(dict[str, object], state["snapshot"])
        assert snapshot["active_hand"] is None
        assert snapshot["last_hand"] is not None
        assert service.current_turn_deadline(room_id) is None
        assert room_id not in coordinator._turn_tasks
        await coordinator.disconnect(session)
        await coordinator.shutdown()

    asyncio.run(scenario())


def test_expired_client_action_resolves_timeout_before_rejecting_command() -> None:
    async def scenario() -> None:
        service, room_id, guests, clock = started_timed_service()
        room_code = service.get_room_snapshot(room_id).room_code
        deadline = current_deadline(service, room_id)
        actor_token = token(1) if deadline.actor == guests["host"] else token(2)
        coordinator = RealtimeRoomCoordinator(service)
        socket = RecordingWebSocket()
        session = await coordinator.bind(
            cast(WebSocket, socket),
            room_code,
            ConnectRequest(type="connect", guest_token=actor_token),
        )
        await asyncio.sleep(0)
        socket.sent.clear()
        clock.advance(90_000)
        await coordinator.handle_command(
            session,
            CallCommand(
                type="call",
                command_id="late",
                hand_number=deadline.hand_number,
                expected_action_sequence=deadline.action_sequence,
            ),
        )
        await asyncio.sleep(0)
        assert [cast(dict[str, object], item)["type"] for item in socket.sent] == [
            "command_error",
            "state",
        ]
        assert cast(dict[str, object], socket.sent[0])["code"] == "stale_game_state"
        assert service.current_turn_deadline(room_id) is None
        settled = service.get_room_view(room_id=room_id, viewer=deadline.actor)
        assert settled.last_hand is not None
        assert settled.last_hand.final_action_sequence == 2
        await coordinator.disconnect(session)
        await coordinator.shutdown()

    asyncio.run(scenario())


def test_timeout_and_late_action_race_mutates_once() -> None:
    async def scenario() -> None:
        service, room_id, guests, clock = started_timed_service()
        deadline = current_deadline(service, room_id)
        actor_token = token(1) if deadline.actor == guests["host"] else token(2)
        coordinator = RealtimeRoomCoordinator(service)
        socket = RecordingWebSocket()
        session = await coordinator.bind(
            cast(WebSocket, socket),
            service.get_room_snapshot(room_id).room_code,
            ConnectRequest(type="connect", guest_token=actor_token),
        )
        await asyncio.sleep(0)
        socket.sent.clear()
        clock.advance(30_000)
        await asyncio.gather(
            coordinator.expire_turn(deadline),
            coordinator.handle_command(
                session,
                CallCommand(
                    type="call",
                    command_id="racing",
                    hand_number=deadline.hand_number,
                    expected_action_sequence=deadline.action_sequence,
                ),
            ),
        )
        settled = service.get_room_view(room_id=room_id, viewer=deadline.actor)
        assert settled.active_hand is not None
        assert settled.active_hand.action_sequence == 1
        assert settled.active_hand.current_actor_using_timebank
        assert service.expire_turn(deadline) is False
        await asyncio.sleep(0)
        message_types = [cast(dict[str, object], item)["type"] for item in socket.sent]
        assert message_types.count("command_error") == 1
        assert message_types.count("state") >= 1
        assert "command_ack" not in message_types
        clock.advance(60_000)
        assert await coordinator.expire_turn(current_deadline(service, room_id)) is True
        settled = service.get_room_view(room_id=room_id, viewer=deadline.actor)
        assert settled.last_hand is not None
        assert settled.last_hand.final_action_sequence == 2
        await coordinator.disconnect(session)
        await coordinator.shutdown()

    asyncio.run(scenario())


def test_timeout_broadcasts_separate_private_views_and_ignores_stale_callbacks() -> None:
    async def scenario() -> None:
        service, room_id, guests, clock = started_timed_service()
        first = current_deadline(service, room_id)
        service.call(
            room_id=room_id,
            actor=first.actor,
            hand_number=first.hand_number,
            expected_action_sequence=first.action_sequence,
        )
        second = current_deadline(service, room_id)
        room_code = service.get_room_snapshot(room_id).room_code
        coordinator = RealtimeRoomCoordinator(service)
        host_socket = RecordingWebSocket()
        alice_socket = RecordingWebSocket()
        host = await coordinator.bind(
            cast(WebSocket, host_socket),
            room_code,
            ConnectRequest(type="connect", guest_token=token(1)),
        )
        alice = await coordinator.bind(
            cast(WebSocket, alice_socket),
            room_code,
            ConnectRequest(type="connect", guest_token=token(2)),
        )
        await asyncio.sleep(0)
        host_socket.sent.clear()
        alice_socket.sent.clear()
        clock.advance(30_000)
        assert await coordinator.expire_turn(replace(second, revision=99)) is False
        assert host_socket.sent == [] and alice_socket.sent == []
        assert await coordinator.expire_turn(second) is True
        await asyncio.sleep(0)
        assert len(host_socket.sent) == len(alice_socket.sent) == 1
        for socket, viewer in ((host_socket, guests["host"]), (alice_socket, guests["alice"])):
            message = cast(dict[str, object], socket.sent[0])
            assert message["type"] == "state"
            snapshot = cast(dict[str, object], message["snapshot"])
            active = cast(dict[str, object], snapshot["active_hand"])
            players = cast(list[dict[str, object]], active["players"])
            assert {player["guest_id"] for player in players if player["hole_cards"]} == {
                viewer.value
            }
            assert active["current_actor_timebank_ms"] == 0
            assert active["current_actor_using_timebank"] is True
            assert "private-player" not in str(message)
            assert token(1) not in str(message) and token(2) not in str(message)
        await coordinator.disconnect(host)
        await coordinator.disconnect(alice)
        await coordinator.shutdown()

    asyncio.run(scenario())


def test_room_close_after_settlement_leaves_no_timer_and_shutdown_cancels_live_timer() -> None:
    async def scenario() -> None:
        service, room_id, _, clock = started_timed_service()
        room_code = service.get_room_snapshot(room_id).room_code
        coordinator = RealtimeRoomCoordinator(service)
        host = await coordinator.bind(
            cast(WebSocket, RecordingWebSocket()),
            room_code,
            ConnectRequest(type="connect", guest_token=token(1)),
        )
        first = current_deadline(service, room_id)
        assert room_id in coordinator._turn_tasks
        clock.advance(30_000)
        assert await coordinator.expire_turn(first) is True
        clock.advance(60_000)
        assert await coordinator.expire_turn(current_deadline(service, room_id)) is True
        assert room_id not in coordinator._turn_tasks
        await coordinator.handle_command(
            host, CloseRoomCommand(type="close_room", command_id="close")
        )
        assert service.get_room_snapshot(room_id).status.value == "closed"
        assert room_id not in coordinator._turn_tasks
        await coordinator.shutdown()
        assert coordinator._turn_tasks == {}

    asyncio.run(scenario())


def test_scheduler_wakes_and_times_out_disconnected_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        service, room_id, guests = built_service()
        room_code = service.get_room_snapshot(room_id).room_code
        service.join_room(room_code=room_code, actor=guests["alice"], nickname="Alice")
        service.request_seat(room_id=room_id, actor=guests["host"], seat_index=0)
        service.request_seat(room_id=room_id, actor=guests["alice"], seat_index=2)
        coordinator = RealtimeRoomCoordinator(service)
        host = await coordinator.bind(
            cast(WebSocket, RecordingWebSocket()),
            room_code,
            ConnectRequest(type="connect", guest_token=token(1)),
        )
        await coordinator.handle_command(
            host, StartHandCommand(type="start_hand", command_id="start", hand_number=1)
        )
        assert room_id in coordinator._turn_tasks
        await coordinator.disconnect(host)

        async def settled() -> None:
            while service.get_room_view(room_id=room_id, viewer=guests["host"]).active_hand:
                await asyncio.sleep(0.005)

        await asyncio.wait_for(settled(), timeout=1)
        view = service.get_room_view(room_id=room_id, viewer=guests["host"])
        assert view.last_hand is not None
        assert view.last_hand.final_action_sequence == 2
        assert room_id not in coordinator._turn_tasks
        await coordinator.shutdown()

    monkeypatch.setattr("streetpoker.application.room_service.BASE_ACTION_TIME_MS", 10)
    monkeypatch.setattr("streetpoker.application.room_service.PER_HAND_TIMEBANK_MS", 10)
    asyncio.run(scenario())


def test_action_admitted_before_expiry_wins_even_if_clock_crosses_during_dispatch() -> None:
    async def scenario() -> None:
        service, room_id, guests, clock = started_timed_service()
        deadline = current_deadline(service, room_id)
        actor_token = token(1) if deadline.actor == guests["host"] else token(2)
        coordinator = RealtimeRoomCoordinator(service)
        session = await coordinator.bind(
            cast(WebSocket, RecordingWebSocket()),
            service.get_room_snapshot(room_id).room_code,
            ConnectRequest(type="connect", guest_token=actor_token),
        )
        clock.monotonic_ms = deadline.monotonic_ms - 1
        clock.advance_after_read = True
        await coordinator.handle_command(
            session,
            CallCommand(
                type="call",
                command_id="before-expiry",
                hand_number=deadline.hand_number,
                expected_action_sequence=deadline.action_sequence,
            ),
        )
        after = service.get_room_view(room_id=room_id, viewer=deadline.actor)
        assert after.active_hand is not None
        assert after.active_hand.action_sequence == 1
        assert service.expire_turn(deadline) is False
        await coordinator.disconnect(session)
        await coordinator.shutdown()

    asyncio.run(scenario())


def test_timer_replacement_cleanup_settlement_and_shutdown() -> None:
    async def scenario() -> None:
        service, room_id, guests, clock = started_timed_service()
        room_code = service.get_room_snapshot(room_id).room_code
        first = current_deadline(service, room_id)
        actor_token = token(1) if first.actor == guests["host"] else token(2)
        coordinator = RealtimeRoomCoordinator(service)
        actor_session = await coordinator.bind(
            cast(WebSocket, RecordingWebSocket()),
            room_code,
            ConnectRequest(type="connect", guest_token=actor_token),
        )
        old_task = coordinator._turn_tasks[room_id].task
        await coordinator.handle_command(
            actor_session,
            CallCommand(
                type="call",
                command_id="call",
                hand_number=first.hand_number,
                expected_action_sequence=first.action_sequence,
            ),
        )
        second = current_deadline(service, room_id)
        new_task = coordinator._turn_tasks[room_id].task
        assert new_task is not old_task
        await asyncio.sleep(0)
        assert old_task.done()
        assert await coordinator.expire_turn(first) is False
        assert coordinator._turn_tasks[room_id].task is new_task
        clock.advance(30_000)
        assert await coordinator.expire_turn(second) is True
        assert coordinator._turn_tasks[room_id].task is not new_task
        assert await coordinator.expire_turn(second) is False
        assert coordinator._turn_tasks[room_id].deadline.action_sequence == 2
        clock.advance(60_000)
        assert await coordinator.expire_turn(current_deadline(service, room_id)) is True
        assert coordinator._turn_tasks[room_id].deadline.action_sequence == 3
        live_task = coordinator._turn_tasks[room_id].task
        await coordinator.disconnect(actor_session)
        await coordinator.shutdown()
        assert coordinator._turn_tasks == {}
        assert live_task.done()

    asyncio.run(scenario())


def test_open_room_grace_stands_once_and_reconnect_gets_retained_stack() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        service, room_id, guests = built_service(clock=clock)
        room_code = service.get_room_snapshot(room_id).room_code
        service.join_room(room_code=room_code, actor=guests["alice"], nickname="Alice")
        service.join_room(room_code=room_code, actor=guests["spectator"], nickname="Spectator")
        service.request_seat(room_id=room_id, actor=guests["host"], seat_index=0)
        service.request_seat(room_id=room_id, actor=guests["alice"], seat_index=2)
        coordinator = RealtimeRoomCoordinator(service)
        spectator, _ = await bind_existing_guest(coordinator, service, room_id, 3)
        await coordinator.disconnect(spectator)
        assert coordinator._graces == {}

        alice, _ = await bind_existing_guest(coordinator, service, room_id, 2)
        before = service.get_room_snapshot(room_id)
        await coordinator.disconnect(alice)
        key = (room_id, guests["alice"])
        grace = coordinator._graces[key]
        assert grace.deadline.monotonic_ms == clock.monotonic_ms + DISCONNECT_GRACE_MS
        assert grace.task is not None and not grace.task.done()
        assert len(coordinator._graces) == 1
        assert service.get_room_snapshot(room_id) == before
        assert await coordinator.expire_grace(grace.deadline) is False

        clock.advance(DISCONNECT_GRACE_MS - 1)
        reconnected, _ = await bind_existing_guest(coordinator, service, room_id, 2)
        assert key not in coordinator._graces
        assert service.get_room_snapshot(room_id) == before
        clock.advance(1)
        assert await coordinator.expire_grace(grace.deadline) is False

        replacement, _ = await bind_existing_guest(coordinator, service, room_id, 2)
        await coordinator.disconnect(reconnected)
        assert key not in coordinator._graces
        await coordinator.disconnect(replacement)
        current = coordinator._graces[key].deadline
        assert current.revision != grace.deadline.revision
        await coordinator.disconnect(reconnected)
        assert coordinator._graces[key].deadline == current
        clock.advance(DISCONNECT_GRACE_MS)
        assert await coordinator.expire_grace(current) is True
        assert await coordinator.expire_grace(current) is False
        snapshot = service.get_room_snapshot(room_id)
        member = next(member for member in snapshot.members if member.guest_id == guests["alice"])
        assert member.status.value == "in_room"
        assert member.stack == 1_000
        assert all(seat.guest_id != guests["alice"] for seat in snapshot.seats)
        assert snapshot.host_guest_id == guests["host"]
        assert key not in coordinator._graces

        later, later_socket = await bind_existing_guest(coordinator, service, room_id, 2)
        state = cast(dict[str, object], later_socket.sent[1])
        projected = cast(dict[str, object], state["snapshot"])
        room = cast(dict[str, object], projected["room"])
        assert all(
            cast(dict[str, object], seat).get("guest_id") != guests["alice"].value
            for seat in cast(list[object], room["seats"])
        )
        assert "private-player" not in str(state)
        assert token(2) not in str(state)
        await coordinator.disconnect(later)
        await coordinator.shutdown()
        assert coordinator._grace_tasks == set()

    asyncio.run(scenario())


def test_host_grace_stands_seat_but_keeps_host_authority() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        service, room_id, guests = built_service(clock=clock)
        service.request_seat(room_id=room_id, actor=guests["host"], seat_index=0)
        coordinator = RealtimeRoomCoordinator(service)
        host, _ = await bind_existing_guest(coordinator, service, room_id, 1)
        await coordinator.disconnect(host)
        grace = coordinator._graces[(room_id, guests["host"])].deadline
        assert service.get_room_snapshot(room_id).host_guest_id == guests["host"]
        clock.advance(DISCONNECT_GRACE_MS)
        assert await coordinator.expire_grace(grace)
        snapshot = service.get_room_snapshot(room_id)
        assert snapshot.host_guest_id == guests["host"]
        assert next(member for member in snapshot.members if member.is_host).stack == 1_000
        host_again, _ = await bind_existing_guest(coordinator, service, room_id, 1)
        await coordinator.handle_command(
            host_again,
            client_command_adapter.validate_python(
                {"type": "update_settings", "command_id": "host", "room_name": "Still Host"}
            ),
        )
        assert service.get_room_snapshot(room_id).settings.room_name == "Still Host"
        await coordinator.shutdown()

    asyncio.run(scenario())


def test_explicit_leave_kick_and_close_do_not_start_grace() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        service, room_id, guests = built_service(clock=clock)
        room_code = service.get_room_snapshot(room_id).room_code
        service.join_room(room_code=room_code, actor=guests["alice"], nickname="Alice")
        service.request_seat(room_id=room_id, actor=guests["host"], seat_index=0)
        service.request_seat(room_id=room_id, actor=guests["alice"], seat_index=2)
        coordinator = RealtimeRoomCoordinator(service)
        host, _ = await bind_existing_guest(coordinator, service, room_id, 1)
        alice, _ = await bind_existing_guest(coordinator, service, room_id, 2)
        await coordinator.handle_command(
            alice, client_command_adapter.validate_python({"type": "leave", "command_id": "leave"})
        )
        await coordinator.disconnect(alice)
        assert coordinator._graces == {}
        assert guests["alice"] not in {
            member.guest_id for member in service.get_room_snapshot(room_id).members
        }

        service.join_room(room_code=room_code, actor=guests["alice"], nickname="Alice")
        service.request_seat(room_id=room_id, actor=guests["alice"], seat_index=2)
        alice, _ = await bind_existing_guest(coordinator, service, room_id, 2)
        await coordinator.handle_command(
            host,
            client_command_adapter.validate_python(
                {"type": "kick", "command_id": "kick", "target_guest_id": guests["alice"].value}
            ),
        )
        await coordinator.disconnect(alice)
        assert coordinator._graces == {}

        await coordinator.handle_command(
            host, CloseRoomCommand(type="close_room", command_id="close")
        )
        await coordinator.disconnect(host)
        assert coordinator._graces == {}
        assert service.get_room_snapshot(room_id).status.value == "closed"
        await coordinator.shutdown()

    asyncio.run(scenario())


def test_grace_expiry_during_hand_defers_stand_until_timeout_settlement() -> None:
    async def scenario() -> None:
        service, room_id, guests, clock = started_timed_service()
        coordinator = RealtimeRoomCoordinator(service)
        base = current_deadline(service, room_id)
        actor_fill = 1 if base.actor == guests["host"] else 2
        other_fill = 2 if actor_fill == 1 else 1
        actor, _ = await bind_existing_guest(coordinator, service, room_id, actor_fill)
        other, other_socket = await bind_existing_guest(coordinator, service, room_id, other_fill)
        before = service.get_room_view(room_id=room_id, viewer=base.actor)
        await coordinator.disconnect(actor)
        grace = coordinator._graces[(room_id, base.actor)].deadline
        assert service.get_room_view(room_id=room_id, viewer=base.actor) == before
        assert current_deadline(service, room_id) == base

        clock.advance(30_000)
        assert await coordinator.expire_turn(base)
        extended = current_deadline(service, room_id)
        assert extended.actor == base.actor
        assert coordinator._graces[(room_id, base.actor)].deadline == grace
        assert service.get_room_view(
            room_id=room_id, viewer=base.actor
        ).active_hand.current_actor_using_timebank

        clock.advance(30_000)
        before_grace = service.get_room_view(room_id=room_id, viewer=base.actor)
        assert await coordinator.expire_grace(grace) is False
        assert coordinator._graces[(room_id, base.actor)].expired
        assert service.get_room_view(room_id=room_id, viewer=base.actor) == before_grace
        assert current_deadline(service, room_id) == extended
        assert any(seat.guest_id == base.actor for seat in service.get_room_snapshot(room_id).seats)

        clock.advance(30_000)
        assert await coordinator.expire_turn(extended)
        assert await coordinator.expire_grace(grace) is False
        snapshot = service.get_room_snapshot(room_id)
        assert snapshot.status.value == "open"
        assert all(seat.guest_id != base.actor for seat in snapshot.seats)
        actor_member = next(member for member in snapshot.members if member.guest_id == base.actor)
        assert actor_member.status.value == "in_room"
        assert sum(member.stack or 0 for member in snapshot.members) == 2_000
        assert snapshot.host_guest_id == guests["host"]
        assert (room_id, base.actor) not in coordinator._graces
        await asyncio.sleep(0)
        latest = cast(dict[str, object], other_socket.sent[-1])
        assert "private-player" not in str(latest)
        assert token(actor_fill) not in str(latest)
        assert token(other_fill) not in str(latest)
        assert service.get_room_view(room_id=room_id, viewer=guests["host"]).last_hand is not None

        reconnected, socket = await bind_existing_guest(coordinator, service, room_id, actor_fill)
        state = cast(dict[str, object], socket.sent[1])
        room = cast(dict[str, object], cast(dict[str, object], state["snapshot"])["room"])
        assert all(
            cast(dict[str, object], seat)["guest_id"] != base.actor.value
            for seat in cast(list[object], room["seats"])
        )
        await coordinator.disconnect(reconnected)
        await coordinator.disconnect(other)
        await coordinator.shutdown()
        assert coordinator._graces == {}
        assert coordinator._grace_tasks == set()

    asyncio.run(scenario())


def test_reconnect_during_timebank_and_socket_replacement_preserves_hand() -> None:
    async def scenario() -> None:
        service, room_id, guests, clock = started_timed_service()
        coordinator = RealtimeRoomCoordinator(service)
        base = current_deadline(service, room_id)
        actor_fill = 1 if base.actor == guests["host"] else 2
        other_fill = 2 if actor_fill == 1 else 1
        actor, _ = await bind_existing_guest(coordinator, service, room_id, actor_fill)
        other, other_socket = await bind_existing_guest(coordinator, service, room_id, other_fill)
        await coordinator.disconnect(actor)
        first_grace = coordinator._graces[(room_id, base.actor)].deadline
        clock.advance(30_000)
        assert await coordinator.expire_turn(base)
        extended = current_deadline(service, room_id)

        reconnected, actor_socket = await bind_existing_guest(
            coordinator, service, room_id, actor_fill
        )
        assert (room_id, base.actor) not in coordinator._graces
        assert current_deadline(service, room_id) == extended
        state = cast(dict[str, object], actor_socket.sent[1])
        active = cast(dict[str, object], cast(dict[str, object], state["snapshot"])["active_hand"])
        assert active["action_deadline_unix_ms"] == extended.unix_ms
        assert active["current_actor_using_timebank"] is True
        assert active["current_actor_timebank_ms"] == 0
        assert {
            player["guest_id"]
            for player in cast(list[dict[str, object]], active["players"])
            if player["hole_cards"]
        } == {base.actor.value}
        assert token(actor_fill) not in str(state)
        assert "private-player" not in str(state)
        other_state = cast(dict[str, object], other_socket.sent[-1])
        other_active = cast(
            dict[str, object], cast(dict[str, object], other_state["snapshot"])["active_hand"]
        )
        assert {
            player["guest_id"]
            for player in cast(list[dict[str, object]], other_active["players"])
            if player["hole_cards"]
        } == {other.guest_id.value}

        replacement, _ = await bind_existing_guest(coordinator, service, room_id, actor_fill)
        await coordinator.disconnect(reconnected)
        assert (room_id, base.actor) not in coordinator._graces
        assert current_deadline(service, room_id) == extended
        await coordinator.disconnect(replacement)
        second_grace = coordinator._graces[(room_id, base.actor)].deadline
        assert second_grace.revision != first_grace.revision
        clock.advance(30_000)
        assert await coordinator.expire_grace(first_grace) is False
        assert await coordinator.expire_grace(second_grace) is False
        assert not coordinator._graces[(room_id, base.actor)].expired

        resumed, _ = await bind_existing_guest(coordinator, service, room_id, actor_fill)
        assert (room_id, base.actor) not in coordinator._graces
        assert current_deadline(service, room_id) == extended
        clock.advance(30_000)
        assert await coordinator.expire_turn(extended)
        snapshot = service.get_room_snapshot(room_id)
        assert any(seat.guest_id == base.actor for seat in snapshot.seats)
        assert sum(member.stack or 0 for member in snapshot.members) == 2_000
        await coordinator.disconnect(resumed)
        await coordinator.disconnect(other)
        await coordinator.shutdown()

    asyncio.run(scenario())


def test_equal_grace_reconnect_and_callback_serialize_to_one_stand() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        service, room_id, guests = built_service(clock=clock)
        service.request_seat(room_id=room_id, actor=guests["host"], seat_index=0)
        coordinator = RealtimeRoomCoordinator(service)
        host, _ = await bind_existing_guest(coordinator, service, room_id, 1)
        await coordinator.disconnect(host)
        deadline = coordinator._graces[(room_id, guests["host"])].deadline
        clock.advance(DISCONNECT_GRACE_MS)
        expired, (reconnected, socket) = await asyncio.gather(
            coordinator.expire_grace(deadline),
            bind_existing_guest(coordinator, service, room_id, 1),
        )
        assert expired in (True, False)
        assert await coordinator.expire_grace(deadline) is False
        assert coordinator._graces == {}
        snapshot = service.get_room_snapshot(room_id)
        assert snapshot.host_guest_id == guests["host"]
        assert all(seat.guest_id is None for seat in snapshot.seats)
        assert next(member for member in snapshot.members if member.is_host).stack == 1_000
        state = cast(dict[str, object], socket.sent[1])
        room = cast(dict[str, object], cast(dict[str, object], state["snapshot"])["room"])
        assert all(
            cast(dict[str, object], seat)["guest_id"] is None
            for seat in cast(list[object], room["seats"])
        )
        await coordinator.disconnect(reconnected)
        await coordinator.shutdown()

    asyncio.run(scenario())


def test_reconnect_after_active_hand_grace_expiry_cancels_deferred_stand() -> None:
    async def scenario() -> None:
        service, room_id, guests, clock = started_timed_service()
        coordinator = RealtimeRoomCoordinator(service)
        base = current_deadline(service, room_id)
        actor_fill = 1 if base.actor == guests["host"] else 2
        actor, _ = await bind_existing_guest(coordinator, service, room_id, actor_fill)
        await coordinator.disconnect(actor)
        grace = coordinator._graces[(room_id, base.actor)].deadline
        clock.advance(30_000)
        assert await coordinator.expire_turn(base)
        extended = current_deadline(service, room_id)
        clock.advance(30_000)
        assert await coordinator.expire_grace(grace) is False
        assert coordinator._graces[(room_id, base.actor)].expired
        resumed, socket = await bind_existing_guest(coordinator, service, room_id, actor_fill)
        assert (room_id, base.actor) not in coordinator._graces
        assert current_deadline(service, room_id) == extended
        state = cast(dict[str, object], socket.sent[1])
        active = cast(dict[str, object], cast(dict[str, object], state["snapshot"])["active_hand"])
        assert active["current_actor_using_timebank"] is True
        assert active["action_deadline_unix_ms"] == extended.unix_ms
        clock.advance(30_000)
        assert await coordinator.expire_turn(extended)
        assert any(seat.guest_id == base.actor for seat in service.get_room_snapshot(room_id).seats)
        await coordinator.disconnect(resumed)
        await coordinator.shutdown()

    asyncio.run(scenario())


def test_failed_authoritative_writer_still_starts_grace_on_route_cleanup() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        service, room_id, guests = built_service(clock=clock)
        service.request_seat(room_id=room_id, actor=guests["host"], seat_index=0)
        coordinator = RealtimeRoomCoordinator(service)
        socket = RecordingWebSocket(fail_send=True)
        session = await coordinator.bind(
            cast(WebSocket, socket),
            service.get_room_snapshot(room_id).room_code,
            ConnectRequest(type="connect", guest_token=token(1)),
        )
        await asyncio.sleep(0)
        assert not coordinator.registry.is_registered(session)
        await coordinator.disconnect(session)
        assert (room_id, guests["host"]) in coordinator._graces
        await coordinator.shutdown()
        assert coordinator._grace_tasks == set()

    asyncio.run(scenario())


def test_generic_protocol_and_projection_detaches_start_grace() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        service, room_id, guests = built_service(clock=clock)
        service.request_seat(room_id=room_id, actor=guests["host"], seat_index=0)
        coordinator = RealtimeRoomCoordinator(service)
        host, _ = await bind_existing_guest(coordinator, service, room_id, 1)
        coordinator.registry.detach_session(host, code=1009, reason="message_too_large")
        await coordinator.disconnect(host)
        first = coordinator._graces[(room_id, guests["host"])].deadline
        assert service.get_room_snapshot(room_id).seats[0].guest_id == guests["host"]

        reconnected, _ = await bind_existing_guest(coordinator, service, room_id, 1)
        assert (room_id, guests["host"]) not in coordinator._graces
        coordinator.registry.detach_room(room_id, code=1011, reason="state synchronization failed")
        await coordinator.disconnect(reconnected)
        second = coordinator._graces[(room_id, guests["host"])].deadline
        assert second.revision != first.revision
        await coordinator.shutdown()

    asyncio.run(scenario())


def test_host_approval_of_offline_member_starts_seat_grace() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        service, room_id, guests = built_service(approval=True, clock=clock)
        room_code = service.get_room_snapshot(room_id).room_code
        service.join_room(room_code=room_code, actor=guests["alice"], nickname="Alice")
        coordinator = RealtimeRoomCoordinator(service)
        host, _ = await bind_existing_guest(coordinator, service, room_id, 1)
        alice, _ = await bind_existing_guest(coordinator, service, room_id, 2)
        await coordinator.handle_command(
            alice, RequestSeatCommand(type="request_seat", command_id="request", seat_index=2)
        )
        await coordinator.disconnect(alice)
        assert coordinator._graces == {}
        await coordinator.handle_command(
            host,
            client_command_adapter.validate_python(
                {
                    "type": "approve_seat",
                    "command_id": "approve",
                    "target_guest_id": guests["alice"].value,
                }
            ),
        )
        assert service.get_room_snapshot(room_id).seats[2].guest_id == guests["alice"]
        grace = coordinator._graces[(room_id, guests["alice"])].deadline
        clock.advance(DISCONNECT_GRACE_MS)
        assert await coordinator.expire_grace(grace)
        snapshot = service.get_room_snapshot(room_id)
        assert snapshot.seats[2].guest_id is None
        assert (
            next(member for member in snapshot.members if member.guest_id == guests["alice"]).stack
            == 1_000
        )
        alice_again, socket = await bind_existing_guest(coordinator, service, room_id, 2)
        state = cast(dict[str, object], socket.sent[1])
        room = cast(dict[str, object], cast(dict[str, object], state["snapshot"])["room"])
        assert cast(list[dict[str, object]], room["seats"])[2]["guest_id"] is None
        await coordinator.disconnect(alice_again)
        await coordinator.shutdown()

    asyncio.run(scenario())


def test_kick_close_and_shutdown_clear_pending_grace_tasks() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        service, room_id, guests = built_service(clock=clock)
        room_code = service.get_room_snapshot(room_id).room_code
        service.join_room(room_code=room_code, actor=guests["alice"], nickname="Alice")
        service.request_seat(room_id=room_id, actor=guests["host"], seat_index=0)
        service.request_seat(room_id=room_id, actor=guests["alice"], seat_index=2)
        coordinator = RealtimeRoomCoordinator(service)
        host, _ = await bind_existing_guest(coordinator, service, room_id, 1)
        alice, _ = await bind_existing_guest(coordinator, service, room_id, 2)
        await coordinator.disconnect(alice)
        kicked_grace = coordinator._graces[(room_id, guests["alice"])].deadline
        await coordinator.handle_command(
            host,
            client_command_adapter.validate_python(
                {"type": "kick", "command_id": "kick", "target_guest_id": guests["alice"].value}
            ),
        )
        assert coordinator._graces == {}
        clock.advance(DISCONNECT_GRACE_MS)
        assert await coordinator.expire_grace(kicked_grace) is False
        assert guests["alice"] not in {
            member.guest_id for member in service.get_room_snapshot(room_id).members
        }

        service.join_room(room_code=room_code, actor=guests["alice"], nickname="Alice")
        service.request_seat(room_id=room_id, actor=guests["alice"], seat_index=2)
        alice, _ = await bind_existing_guest(coordinator, service, room_id, 2)
        await coordinator.disconnect(alice)
        closed_grace = coordinator._graces[(room_id, guests["alice"])].deadline
        await coordinator.handle_command(
            host, CloseRoomCommand(type="close_room", command_id="close")
        )
        assert coordinator._graces == {}
        assert await coordinator.expire_grace(closed_grace) is False
        await coordinator.disconnect(host)
        await coordinator.shutdown()
        assert coordinator._grace_tasks == set()
        assert coordinator._presence_sessions == {}

    asyncio.run(scenario())


def test_timeout_settlement_before_grace_expiry_stands_only_after_grace() -> None:
    async def scenario() -> None:
        service, room_id, guests, clock = started_timed_service()
        coordinator = RealtimeRoomCoordinator(service)
        base = current_deadline(service, room_id)
        actor_fill = 1 if base.actor == guests["host"] else 2
        actor, _ = await bind_existing_guest(coordinator, service, room_id, actor_fill)
        clock.advance(30_000)
        assert await coordinator.expire_turn(base)
        extended = current_deadline(service, room_id)
        clock.advance(10_000)
        await coordinator.disconnect(actor)
        grace = coordinator._graces[(room_id, base.actor)].deadline
        assert grace.monotonic_ms > extended.monotonic_ms
        clock.advance(50_000)
        assert await coordinator.expire_turn(extended)
        assert service.get_room_snapshot(room_id).status.value == "open"
        assert any(seat.guest_id == base.actor for seat in service.get_room_snapshot(room_id).seats)
        assert (room_id, base.actor) in coordinator._graces
        clock.advance(10_000)
        assert await coordinator.expire_grace(grace)
        assert all(seat.guest_id != base.actor for seat in service.get_room_snapshot(room_id).seats)
        assert (
            sum(member.stack or 0 for member in service.get_room_snapshot(room_id).members) == 2_000
        )
        await coordinator.shutdown()

    asyncio.run(scenario())


def test_settlement_resolves_overdue_grace_even_if_callback_wakes_late() -> None:
    async def scenario() -> None:
        service, room_id, guests, clock = started_timed_service()
        coordinator = RealtimeRoomCoordinator(service)
        base = current_deadline(service, room_id)
        actor_fill = 1 if base.actor == guests["host"] else 2
        actor, _ = await bind_existing_guest(coordinator, service, room_id, actor_fill)
        await coordinator.disconnect(actor)
        grace = coordinator._graces[(room_id, base.actor)].deadline
        clock.advance(30_000)
        assert await coordinator.expire_turn(base)
        extended = current_deadline(service, room_id)
        clock.advance(60_000)
        assert await coordinator.expire_turn(extended)
        assert (room_id, base.actor) not in coordinator._graces
        assert await coordinator.expire_grace(grace) is False
        assert all(seat.guest_id != base.actor for seat in service.get_room_snapshot(room_id).seats)
        await coordinator.shutdown()

    asyncio.run(scenario())


def test_player_action_settlement_applies_deferred_grace_once() -> None:
    async def scenario() -> None:
        service, room_id, guests, clock = started_timed_service()
        coordinator = RealtimeRoomCoordinator(service)
        base = current_deadline(service, room_id)
        actor_fill = 1 if base.actor == guests["host"] else 2
        other_fill = 2 if actor_fill == 1 else 1
        actor, _ = await bind_existing_guest(coordinator, service, room_id, actor_fill)
        other, _ = await bind_existing_guest(coordinator, service, room_id, other_fill)
        await coordinator.disconnect(other)
        grace = coordinator._graces[(room_id, other.guest_id)].deadline
        clock.advance(30_000)
        assert await coordinator.expire_turn(base)
        clock.advance(30_000)
        assert await coordinator.expire_grace(grace) is False
        assert coordinator._graces[(room_id, other.guest_id)].expired
        view = service.get_room_view(room_id=room_id, viewer=base.actor)
        assert view.active_hand is not None
        await coordinator.handle_command(
            actor,
            client_command_adapter.validate_python(
                {
                    "type": "fold",
                    "command_id": "settle",
                    "hand_number": view.active_hand.hand_number,
                    "expected_action_sequence": view.active_hand.action_sequence,
                }
            ),
        )
        snapshot = service.get_room_snapshot(room_id)
        assert snapshot.status.value == "open"
        assert all(seat.guest_id != other.guest_id for seat in snapshot.seats)
        assert any(seat.guest_id == base.actor for seat in snapshot.seats)
        assert (room_id, other.guest_id) not in coordinator._graces
        assert await coordinator.expire_grace(grace) is False
        assert sum(member.stack or 0 for member in snapshot.members) == 2_000
        await coordinator.shutdown()

    asyncio.run(scenario())


def test_shutdown_cancels_and_awaits_pending_grace() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        service, room_id, guests = built_service(clock=clock)
        service.request_seat(room_id=room_id, actor=guests["host"], seat_index=0)
        coordinator = RealtimeRoomCoordinator(service)
        host, _ = await bind_existing_guest(coordinator, service, room_id, 1)
        await coordinator.disconnect(host)
        pending = coordinator._graces[(room_id, guests["host"])].task
        assert pending is not None and not pending.done()
        await coordinator.shutdown()
        assert pending.done()
        assert coordinator._graces == {}
        assert coordinator._grace_tasks == set()
        clock.advance(DISCONNECT_GRACE_MS)
        assert any(
            seat.guest_id == guests["host"] for seat in service.get_room_snapshot(room_id).seats
        )

    asyncio.run(scenario())


def test_grace_scheduler_wakes_and_stands_open_room_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        service, room_id, guests = built_service()
        service.request_seat(room_id=room_id, actor=guests["host"], seat_index=0)
        coordinator = RealtimeRoomCoordinator(service)
        host, _ = await bind_existing_guest(coordinator, service, room_id, 1)
        await coordinator.disconnect(host)

        async def stood() -> None:
            while any(
                seat.guest_id == guests["host"] for seat in service.get_room_snapshot(room_id).seats
            ):
                await asyncio.sleep(0.005)

        await asyncio.wait_for(stood(), timeout=1)
        assert coordinator._graces == {}
        assert service.get_room_snapshot(room_id).host_guest_id == guests["host"]
        await coordinator.shutdown()
        assert coordinator._grace_tasks == set()

    monkeypatch.setattr("streetpoker.api.realtime.DISCONNECT_GRACE_MS", 10)
    asyncio.run(scenario())
