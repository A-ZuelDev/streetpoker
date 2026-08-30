"""Process-local coordination for viewer-safe realtime room transport."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
from collections.abc import Callable, Coroutine
from contextlib import suppress
from dataclasses import dataclass
from typing import Final

from fastapi import WebSocket

from streetpoker.api.schemas.realtime import (
    ApproveSeatCommand,
    BetToCommand,
    CallCommand,
    CheckCommand,
    ClientCommand,
    CloseRoomCommand,
    CommandAckMessage,
    CommandErrorMessage,
    ConnectedMessage,
    ConnectRequest,
    FoldCommand,
    KickCommand,
    LeaveCommand,
    OutboundMessage,
    RaiseToCommand,
    RejectSeatCommand,
    RequestSeatCommand,
    StandCommand,
    StartHandCommand,
    StateMessage,
    UpdateSettingsCommand,
    room_view_dto,
)
from streetpoker.application import (
    ActiveHandMutationError,
    CannotKickHostError,
    CannotStartHandError,
    DuplicateMembershipError,
    DuplicateNicknameError,
    DuplicateSeatRequestError,
    GuestId,
    HandAlreadyActiveError,
    HostCannotLeaveRoomError,
    InsufficientEligiblePlayersError,
    InvalidGuestIdError,
    InvalidNicknameError,
    InvalidRoomCodeError,
    InvalidRoomNameError,
    InvalidRoomPasswordError,
    InvalidRoomSeatError,
    InvalidRoomSettingsError,
    MemberAlreadySeatedError,
    MemberNotFoundError,
    MemberNotSeatedError,
    NoActiveHandError,
    NotCurrentActorError,
    NotHandParticipantError,
    NotRoomHostError,
    NotRoomMemberError,
    RoomApplicationError,
    RoomClosedError,
    RoomId,
    RoomNotFoundError,
    RoomSeatAlreadyRequestedError,
    RoomSeatOccupiedError,
    RoomService,
    RoomStatus,
    SeatRequestNotFoundError,
    StaleHandVersionError,
    WrongRoomPasswordError,
)
from streetpoker.application.rooms import (
    SETTING_NOT_PROVIDED,
    RoomSettingsUpdate,
    SettingNotProvided,
)
from streetpoker.domain import (
    BettingActionError,
    IllegalBetError,
    IllegalCallError,
    IllegalCheckError,
    IllegalRaiseError,
    InvalidWagerAmountError,
    PokerDomainError,
    RaiseNotReopenedError,
    WagerBelowMinimumError,
    WagerExceedsStackError,
)

logger = logging.getLogger(__name__)

OUTBOUND_QUEUE_CAPACITY: Final = 32
NORMAL_CLOSE: Final = 1000
POLICY_CLOSE: Final = 1008
TRY_AGAIN_CLOSE: Final = 1013


@dataclass(frozen=True, slots=True)
class SafeError:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ConnectionFailure(Exception):
    error: SafeError
    close_code: int = POLICY_CLOSE


@dataclass(frozen=True, slots=True)
class _CloseSocket:
    code: int
    reason: str


_OutboundItem = OutboundMessage | _CloseSocket


@dataclass(eq=False, slots=True)
class SocketSession:
    websocket: WebSocket
    room_id: RoomId
    guest_id: GuestId
    outbound: asyncio.Queue[_OutboundItem]
    writer: asyncio.Task[None] | None = None
    registered: bool = True


def derive_guest_id(guest_token: str) -> GuestId:
    """Derive a public authorization identity from a private 256-bit bearer token."""
    padding = "=" * (-len(guest_token) % 4)
    try:
        raw = base64.urlsafe_b64decode(guest_token + padding)
    except ValueError as error:
        raise ValueError("Guest token is not valid base64url.") from error
    canonical = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    if len(raw) != 32 or canonical != guest_token:
        raise ValueError("Guest token must canonically encode 32 bytes.")
    return GuestId(f"guest_{hashlib.sha256(raw).hexdigest()}")


async def _run_serialized_in_thread[**P, T](
    operation: Callable[P, T],
    *args: P.args,
    **kwargs: P.kwargs,
) -> T:
    """Keep an enclosing room lock effective until a worker transaction finishes."""
    worker = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        raise


class ConnectionRegistry:
    """Own socket bindings and ordered, bounded outbound writers only."""

    def __init__(self) -> None:
        self._by_room: dict[RoomId, dict[GuestId, set[SocketSession]]] = {}
        self._by_socket: dict[WebSocket, SocketSession] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()

    def register(self, websocket: WebSocket, room_id: RoomId, guest_id: GuestId) -> SocketSession:
        if websocket in self._by_socket:
            raise RuntimeError("A WebSocket cannot be registered twice.")
        session = SocketSession(
            websocket=websocket,
            room_id=room_id,
            guest_id=guest_id,
            outbound=asyncio.Queue(maxsize=OUTBOUND_QUEUE_CAPACITY),
        )
        self._by_room.setdefault(room_id, {}).setdefault(guest_id, set()).add(session)
        self._by_socket[websocket] = session
        session.writer = asyncio.create_task(self._write(session))
        return session

    def is_registered(self, session: SocketSession) -> bool:
        return session.registered and self._by_socket.get(session.websocket) is session

    def sessions_by_guest(self, room_id: RoomId) -> dict[GuestId, tuple[SocketSession, ...]]:
        return {
            guest_id: tuple(sessions)
            for guest_id, sessions in self._by_room.get(room_id, {}).items()
        }

    def all_sessions(self, room_id: RoomId) -> tuple[SocketSession, ...]:
        return tuple(
            session for sessions in self._by_room.get(room_id, {}).values() for session in sessions
        )

    def enqueue(self, session: SocketSession, message: OutboundMessage) -> bool:
        if not self.is_registered(session):
            return False
        try:
            session.outbound.put_nowait(message)
        except asyncio.QueueFull:
            self._remove(session)
            self._schedule(self._abort(session, TRY_AGAIN_CLOSE, "connection too slow"))
            return False
        return True

    def detach_guest(self, room_id: RoomId, guest_id: GuestId, *, code: int, reason: str) -> None:
        sessions = tuple(self._by_room.get(room_id, {}).get(guest_id, ()))
        for session in sessions:
            self._detach_and_queue_close(session, code=code, reason=reason)

    def detach_session(self, session: SocketSession, *, code: int, reason: str) -> None:
        self._detach_and_queue_close(session, code=code, reason=reason)

    def detach_room(self, room_id: RoomId, *, code: int, reason: str) -> None:
        for session in self.all_sessions(room_id):
            self._detach_and_queue_close(session, code=code, reason=reason)

    async def disconnect(self, session: SocketSession) -> None:
        was_registered = self.is_registered(session)
        self._remove(session)
        writer = session.writer
        if writer is not None and writer is not asyncio.current_task() and not writer.done():
            if was_registered:
                writer.cancel()
                with suppress(asyncio.CancelledError):
                    await writer
            else:
                await writer

    async def shutdown(self) -> None:
        sessions = tuple(self._by_socket.values())
        for session in sessions:
            self._remove(session)
        await asyncio.gather(
            *(self._abort(session, NORMAL_CLOSE, "server shutdown") for session in sessions),
            return_exceptions=True,
        )
        if self._background_tasks:
            await asyncio.gather(*tuple(self._background_tasks), return_exceptions=True)

    def _detach_and_queue_close(self, session: SocketSession, *, code: int, reason: str) -> None:
        self._remove(session)
        try:
            session.outbound.put_nowait(_CloseSocket(code=code, reason=reason))
        except asyncio.QueueFull:
            self._schedule(self._abort(session, code, reason))

    def _schedule(self, operation: Coroutine[object, object, None]) -> None:
        task = asyncio.create_task(operation)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _remove(self, session: SocketSession) -> None:
        if not session.registered:
            return
        session.registered = False
        self._by_socket.pop(session.websocket, None)
        room = self._by_room.get(session.room_id)
        if room is None:
            return
        sessions = room.get(session.guest_id)
        if sessions is not None:
            sessions.discard(session)
            if not sessions:
                room.pop(session.guest_id, None)
        if not room:
            self._by_room.pop(session.room_id, None)

    async def _write(self, session: SocketSession) -> None:
        try:
            while True:
                item = await session.outbound.get()
                if isinstance(item, _CloseSocket):
                    await session.websocket.close(code=item.code, reason=item.reason)
                    return
                await session.websocket.send_json(item.model_dump(mode="json"))
        except Exception:
            with suppress(OSError, RuntimeError):
                await session.websocket.close(code=1011, reason="connection send failed")
        finally:
            self._remove(session)

    async def _abort(self, session: SocketSession, code: int, reason: str) -> None:
        writer = session.writer
        if writer is not None and writer is not asyncio.current_task() and not writer.done():
            writer.cancel()
            with suppress(asyncio.CancelledError):
                await writer
        with suppress(OSError, RuntimeError):
            await session.websocket.close(code=code, reason=reason)


_SAFE_APPLICATION_ERRORS: dict[type[BaseException], SafeError] = {
    ActiveHandMutationError: SafeError(
        "active_hand_mutation", "That change is unavailable during a hand."
    ),
    CannotKickHostError: SafeError("cannot_kick_host", "The room host cannot be kicked."),
    CannotStartHandError: SafeError("cannot_start_hand", "The room could not start a hand."),
    DuplicateMembershipError: SafeError(
        "already_room_member", "The guest is already a room member."
    ),
    DuplicateNicknameError: SafeError("duplicate_nickname", "That nickname is already in use."),
    DuplicateSeatRequestError: SafeError(
        "duplicate_seat_request", "A seat request is already pending."
    ),
    HandAlreadyActiveError: SafeError("hand_already_active", "A hand is already active."),
    HostCannotLeaveRoomError: SafeError("host_cannot_leave", "The host must close the room."),
    InsufficientEligiblePlayersError: SafeError(
        "insufficient_players", "At least two eligible players are required."
    ),
    InvalidGuestIdError: SafeError("invalid_guest_id", "The guest identifier is invalid."),
    InvalidNicknameError: SafeError("invalid_nickname", "The nickname is invalid."),
    InvalidRoomCodeError: SafeError("invalid_room_code", "The room code is invalid."),
    InvalidRoomNameError: SafeError("invalid_room_name", "The room name is invalid."),
    InvalidRoomPasswordError: SafeError("invalid_room_password", "The room password is invalid."),
    InvalidRoomSeatError: SafeError("invalid_seat", "The requested seat is invalid."),
    InvalidRoomSettingsError: SafeError("invalid_settings", "The room settings are invalid."),
    MemberAlreadySeatedError: SafeError("already_seated", "The member is already seated."),
    MemberNotFoundError: SafeError("member_not_found", "The target member was not found."),
    MemberNotSeatedError: SafeError("not_seated", "The member is not seated."),
    NoActiveHandError: SafeError("no_active_hand", "The room has no active hand."),
    NotCurrentActorError: SafeError("not_current_actor", "It is not this guest's turn."),
    NotHandParticipantError: SafeError("not_hand_participant", "The guest is not in this hand."),
    NotRoomHostError: SafeError("not_room_host", "Only the room host may do that."),
    NotRoomMemberError: SafeError("not_room_member", "The guest is not a room member."),
    RoomClosedError: SafeError("room_closed", "The room is closed."),
    RoomNotFoundError: SafeError("room_not_found", "The room was not found."),
    RoomSeatAlreadyRequestedError: SafeError(
        "seat_already_requested", "That seat already has a request."
    ),
    RoomSeatOccupiedError: SafeError("seat_occupied", "That seat is occupied."),
    SeatRequestNotFoundError: SafeError(
        "seat_request_not_found", "The seat request was not found."
    ),
    StaleHandVersionError: SafeError(
        "stale_game_state", "The command used an outdated hand state."
    ),
    WrongRoomPasswordError: SafeError("wrong_room_password", "The room credentials were rejected."),
}

_SAFE_DOMAIN_ERRORS: dict[type[BaseException], SafeError] = {
    IllegalCheckError: SafeError("illegal_check", "Checking is not legal now."),
    IllegalCallError: SafeError("illegal_call", "Calling is not legal now."),
    IllegalBetError: SafeError("illegal_bet", "Betting is not legal now."),
    IllegalRaiseError: SafeError("illegal_raise", "Raising is not legal now."),
    RaiseNotReopenedError: SafeError("raise_not_reopened", "Raising has not been reopened."),
    InvalidWagerAmountError: SafeError("invalid_wager", "The wager amount is invalid."),
    WagerBelowMinimumError: SafeError(
        "wager_below_minimum", "The wager is below the required minimum."
    ),
    WagerExceedsStackError: SafeError(
        "wager_exceeds_stack", "The wager exceeds the available stack."
    ),
}


def safe_error(error: BaseException) -> SafeError:
    """Translate expected failures without serializing exception text or private attributes."""
    for error_type, translated in _SAFE_APPLICATION_ERRORS.items():
        if isinstance(error, error_type):
            return translated
    for error_type, translated in _SAFE_DOMAIN_ERRORS.items():
        if isinstance(error, error_type):
            return translated
    if isinstance(error, BettingActionError):
        return SafeError("illegal_action", "That betting action is not legal now.")
    return SafeError("internal_error", "The server could not complete the command.")


class RealtimeRoomCoordinator:
    """Serialize room service calls and prepare per-viewer outbound state."""

    def __init__(
        self,
        room_service: RoomService,
        *,
        registry: ConnectionRegistry | None = None,
    ) -> None:
        self._room_service = room_service
        self.registry = ConnectionRegistry() if registry is None else registry
        self._room_locks: dict[RoomId, asyncio.Lock] = {}

    async def bind(
        self,
        websocket: WebSocket,
        room_code: str,
        request: ConnectRequest,
    ) -> SocketSession:
        try:
            guest_id = derive_guest_id(request.guest_token)
        except ValueError as error:
            raise ConnectionFailure(
                SafeError("invalid_guest_token", "The guest token is invalid.")
            ) from error
        try:
            resolved = self._room_service.get_room_snapshot_by_code(room_code)
        except RoomApplicationError as error:
            raise ConnectionFailure(safe_error(error)) from error
        room_id = resolved.room_id
        async with self._lock_for(room_id):
            try:
                current = self._room_service.get_room_snapshot_by_code(room_code)
                if current.room_id != room_id:
                    raise RuntimeError("Room code resolution changed unexpectedly.")
                if current.status is RoomStatus.CLOSED:
                    raise RoomClosedError("The room is closed.")
                joined = False
                try:
                    initial_view = self._room_service.get_room_view(
                        room_id=room_id,
                        viewer=guest_id,
                    )
                except NotRoomMemberError:
                    if request.nickname is None:
                        raise ConnectionFailure(
                            SafeError(
                                "membership_required",
                                "A nickname is required to join this room.",
                            )
                        ) from None
                    await _run_serialized_in_thread(
                        self._room_service.join_room,
                        room_code=room_code,
                        actor=guest_id,
                        nickname=request.nickname,
                        password=request.password,
                    )
                    initial_view = self._room_service.get_room_view(
                        room_id=room_id,
                        viewer=guest_id,
                    )
                    joined = True
                initial_state = StateMessage(snapshot=room_view_dto(initial_view))
                states = self._viewer_states(room_id) if joined else {}
                if joined:
                    states[guest_id] = initial_state
                session = self.registry.register(websocket, room_id, guest_id)
                self.registry.enqueue(
                    session,
                    ConnectedMessage(guest_id=guest_id.value, room_code=current.room_code),
                )
                if joined:
                    self._enqueue_states(room_id, states)
                else:
                    self.registry.enqueue(session, initial_state)
                return session
            except ConnectionFailure:
                raise
            except RoomApplicationError as error:
                raise ConnectionFailure(safe_error(error)) from error
            except Exception as error:
                logger.error(
                    "Unexpected realtime connection failure", extra={"room_id": room_id.value}
                )
                raise ConnectionFailure(
                    SafeError("internal_error", "The server could not establish the connection."),
                    close_code=1011,
                ) from error

    async def handle_command(self, session: SocketSession, command: ClientCommand) -> None:
        async with self._lock_for(session.room_id):
            if not self.registry.is_registered(session):
                return
            try:
                await self._dispatch(session, command)
            except (RoomApplicationError, PokerDomainError) as error:
                translated = safe_error(error)
                if translated.code == "internal_error":
                    logger.error(
                        "Internal realtime command failure",
                        extra={
                            "room_id": session.room_id.value,
                            "command_type": command.type,
                            "command_id": command.command_id,
                        },
                    )
                self.registry.enqueue(
                    session,
                    CommandErrorMessage(
                        command_id=command.command_id,
                        code=translated.code,
                        message=translated.message,
                    ),
                )
                if isinstance(error, StaleHandVersionError):
                    self._enqueue_current_state(session)
                return
            except Exception:
                logger.error(
                    "Unexpected realtime command failure",
                    extra={
                        "room_id": session.room_id.value,
                        "command_type": command.type,
                        "command_id": command.command_id,
                    },
                )
                self.registry.enqueue(
                    session,
                    CommandErrorMessage(
                        command_id=command.command_id,
                        code="internal_error",
                        message="The server could not complete the command.",
                    ),
                )
                return

            self.registry.enqueue(session, CommandAckMessage(command_id=command.command_id))
            try:
                states = self._viewer_states(session.room_id)
            except Exception:
                logger.error(
                    "Realtime state projection failed after a committed command",
                    extra={
                        "room_id": session.room_id.value,
                        "command_type": command.type,
                        "command_id": command.command_id,
                    },
                )
                self.registry.detach_room(
                    session.room_id,
                    code=1011,
                    reason="state synchronization failed",
                )
                return
            self._enqueue_states(session.room_id, states)

            if isinstance(command, LeaveCommand):
                self.registry.detach_guest(
                    session.room_id,
                    session.guest_id,
                    code=NORMAL_CLOSE,
                    reason="left room",
                )
            elif isinstance(command, KickCommand):
                self.registry.detach_guest(
                    session.room_id,
                    GuestId(command.target_guest_id),
                    code=POLICY_CLOSE,
                    reason="removed from room",
                )
            elif isinstance(command, CloseRoomCommand):
                self.registry.detach_room(
                    session.room_id,
                    code=NORMAL_CLOSE,
                    reason="room closed",
                )

    def enqueue_command_error(
        self,
        session: SocketSession,
        *,
        command_id: str | None,
        code: str,
        message: str,
    ) -> None:
        self.registry.enqueue(
            session,
            CommandErrorMessage(command_id=command_id, code=code, message=message),
        )

    async def disconnect(self, session: SocketSession) -> None:
        await self.registry.disconnect(session)

    async def shutdown(self) -> None:
        await self.registry.shutdown()

    def _lock_for(self, room_id: RoomId) -> asyncio.Lock:
        lock = self._room_locks.get(room_id)
        if lock is None:
            lock = asyncio.Lock()
            self._room_locks[room_id] = lock
        return lock

    def _viewer_states(self, room_id: RoomId) -> dict[GuestId, StateMessage]:
        current_members = {
            member.guest_id for member in self._room_service.get_room_snapshot(room_id).members
        }
        return {
            guest_id: StateMessage(
                snapshot=room_view_dto(
                    self._room_service.get_room_view(room_id=room_id, viewer=guest_id)
                )
            )
            for guest_id in self.registry.sessions_by_guest(room_id)
            if guest_id in current_members
        }

    def _enqueue_states(self, room_id: RoomId, states: dict[GuestId, StateMessage]) -> None:
        sessions = self.registry.sessions_by_guest(room_id)
        for guest_id, message in states.items():
            for session in sessions.get(guest_id, ()):
                self.registry.enqueue(session, message)

    def _enqueue_current_state(self, session: SocketSession) -> None:
        try:
            view = self._room_service.get_room_view(
                room_id=session.room_id,
                viewer=session.guest_id,
            )
        except RoomApplicationError:
            return
        self.registry.enqueue(session, StateMessage(snapshot=room_view_dto(view)))

    async def _dispatch(self, session: SocketSession, command: ClientCommand) -> None:
        if isinstance(command, StartHandCommand):
            self._room_service.start_hand(
                room_id=session.room_id,
                actor=session.guest_id,
                expected_hand_number=command.hand_number,
            )
        elif isinstance(command, FoldCommand):
            self._room_service.fold(
                room_id=session.room_id,
                actor=session.guest_id,
                hand_number=command.hand_number,
                expected_action_sequence=command.expected_action_sequence,
            )
        elif isinstance(command, CheckCommand):
            self._room_service.check(
                room_id=session.room_id,
                actor=session.guest_id,
                hand_number=command.hand_number,
                expected_action_sequence=command.expected_action_sequence,
            )
        elif isinstance(command, CallCommand):
            self._room_service.call(
                room_id=session.room_id,
                actor=session.guest_id,
                hand_number=command.hand_number,
                expected_action_sequence=command.expected_action_sequence,
            )
        elif isinstance(command, BetToCommand):
            self._room_service.bet_to(
                room_id=session.room_id,
                actor=session.guest_id,
                hand_number=command.hand_number,
                expected_action_sequence=command.expected_action_sequence,
                total=command.total,
            )
        elif isinstance(command, RaiseToCommand):
            self._room_service.raise_to(
                room_id=session.room_id,
                actor=session.guest_id,
                hand_number=command.hand_number,
                expected_action_sequence=command.expected_action_sequence,
                total=command.total,
            )
        elif isinstance(command, RequestSeatCommand):
            self._room_service.request_seat(
                room_id=session.room_id,
                actor=session.guest_id,
                seat_index=command.seat_index,
            )
        elif isinstance(command, ApproveSeatCommand):
            self._room_service.approve_seat_request(
                room_id=session.room_id,
                actor=session.guest_id,
                target=GuestId(command.target_guest_id),
            )
        elif isinstance(command, RejectSeatCommand):
            self._room_service.reject_seat_request(
                room_id=session.room_id,
                actor=session.guest_id,
                target=GuestId(command.target_guest_id),
            )
        elif isinstance(command, StandCommand):
            self._room_service.stand_up(room_id=session.room_id, actor=session.guest_id)
        elif isinstance(command, LeaveCommand):
            self._room_service.leave_room(room_id=session.room_id, actor=session.guest_id)
        elif isinstance(command, KickCommand):
            self._room_service.kick_member(
                room_id=session.room_id,
                actor=session.guest_id,
                target=GuestId(command.target_guest_id),
            )
        elif isinstance(command, UpdateSettingsCommand):
            update = self._settings_update(command)
            if "password" in command.model_fields_set:
                await _run_serialized_in_thread(
                    self._room_service.update_room_settings,
                    room_id=session.room_id,
                    actor=session.guest_id,
                    update=update,
                )
            else:
                self._room_service.update_room_settings(
                    room_id=session.room_id,
                    actor=session.guest_id,
                    update=update,
                )
        elif isinstance(command, CloseRoomCommand):
            self._room_service.close_room(room_id=session.room_id, actor=session.guest_id)

    @staticmethod
    def _settings_update(command: UpdateSettingsCommand) -> RoomSettingsUpdate:
        supplied = command.model_fields_set
        room_name = command.room_name
        seating_approval = command.seating_approval_required
        room_name_value: str | SettingNotProvided
        seating_approval_value: bool | SettingNotProvided
        small_blind_value: int | SettingNotProvided
        big_blind_value: int | SettingNotProvided
        starting_stack_value: int | SettingNotProvided
        if "room_name" in supplied:
            assert room_name is not None
            room_name_value = room_name
        else:
            room_name_value = SETTING_NOT_PROVIDED
        if "seating_approval_required" in supplied:
            assert seating_approval is not None
            seating_approval_value = seating_approval
        else:
            seating_approval_value = SETTING_NOT_PROVIDED
        small_blind = command.small_blind
        big_blind = command.big_blind
        starting_stack = command.default_starting_stack
        if "small_blind" in supplied:
            assert small_blind is not None
            small_blind_value = small_blind
        else:
            small_blind_value = SETTING_NOT_PROVIDED
        if "big_blind" in supplied:
            assert big_blind is not None
            big_blind_value = big_blind
        else:
            big_blind_value = SETTING_NOT_PROVIDED
        if "default_starting_stack" in supplied:
            assert starting_stack is not None
            starting_stack_value = starting_stack
        else:
            starting_stack_value = SETTING_NOT_PROVIDED
        return RoomSettingsUpdate(
            room_name=room_name_value,
            small_blind=small_blind_value,
            big_blind=big_blind_value,
            default_starting_stack=starting_stack_value,
            seating_approval_required=seating_approval_value,
            password=command.password if "password" in supplied else SETTING_NOT_PROVIDED,
        )
