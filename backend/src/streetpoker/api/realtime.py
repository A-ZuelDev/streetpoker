"""Process-local coordination for viewer-safe realtime room transport."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from contextlib import suppress
from dataclasses import dataclass
from typing import Final

from fastapi import WebSocket

from streetpoker.api.guest_identity import derive_guest_id
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
    PauseGameCommand,
    RaiseToCommand,
    RejectSeatCommand,
    RequestSeatCommand,
    ResumeGameCommand,
    StandCommand,
    StartHandCommand,
    StateMessage,
    UpdateSettingsCommand,
    room_view_dto,
)
from streetpoker.application import (
    ActionDeadlineExpiredError,
    ActiveHandMutationError,
    CannotKickHostError,
    CannotStartHandError,
    DuplicateMembershipError,
    DuplicateNicknameError,
    DuplicateSeatRequestError,
    GamePausedError,
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
    RoomMemberStatus,
    RoomNotFoundError,
    RoomSeatAlreadyRequestedError,
    RoomSeatOccupiedError,
    RoomService,
    RoomSettings,
    RoomSnapshot,
    RoomStatus,
    SeatRequestNotFoundError,
    StaleHandVersionError,
    TurnDeadline,
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
SUPERSEDED_CLOSE_REASON: Final = "session ended"
DISCONNECT_GRACE_MS: Final = 60_000


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
    suppress_grace: bool = False


@dataclass(slots=True)
class _ScheduledTurn:
    deadline: TurnDeadline
    task: asyncio.Task[None]


@dataclass(frozen=True, slots=True)
class GraceDeadline:
    room_id: RoomId
    guest_id: GuestId
    revision: int
    monotonic_ms: int


@dataclass(slots=True)
class _ScheduledGrace:
    deadline: GraceDeadline
    task: asyncio.Task[None] | None
    expired: bool = False


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
    """Own one authoritative socket per room guest and bounded outbound writers."""

    def __init__(self) -> None:
        self._by_room: dict[RoomId, dict[GuestId, SocketSession]] = {}
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
        room = self._by_room.setdefault(room_id, {})
        previous = room.get(guest_id)
        session.writer = asyncio.create_task(self._write(session))
        room[guest_id] = session
        self._by_socket[websocket] = session
        if previous is not None:
            self._remove(previous)
            self._schedule(self._abort(previous, POLICY_CLOSE, SUPERSEDED_CLOSE_REASON))
        return session

    def is_registered(self, session: SocketSession) -> bool:
        return (
            session.registered
            and self._by_socket.get(session.websocket) is session
            and self._by_room.get(session.room_id, {}).get(session.guest_id) is session
        )

    def sessions_by_guest(self, room_id: RoomId) -> dict[GuestId, tuple[SocketSession, ...]]:
        return {
            guest_id: (session,) for guest_id, session in self._by_room.get(room_id, {}).items()
        }

    def all_sessions(self, room_id: RoomId) -> tuple[SocketSession, ...]:
        return tuple(self._by_room.get(room_id, {}).values())

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
        session = self._by_room.get(room_id, {}).get(guest_id)
        if session is not None:
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
                with suppress(asyncio.CancelledError):
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
        if self._by_socket.get(session.websocket) is session:
            self._by_socket.pop(session.websocket)
        room = self._by_room.get(session.room_id)
        if room is None:
            return
        if room.get(session.guest_id) is session:
            room.pop(session.guest_id)
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
    GamePausedError: SafeError("game_paused", "The game is paused by the room host."),
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
        self._room_creation_lock = asyncio.Lock()
        self._room_locks: dict[RoomId, asyncio.Lock] = {}
        self._turn_tasks: dict[RoomId, _ScheduledTurn] = {}
        self._presence_sessions: dict[tuple[RoomId, GuestId], SocketSession] = {}
        self._graces: dict[tuple[RoomId, GuestId], _ScheduledGrace] = {}
        self._grace_tasks: set[asyncio.Task[None]] = set()
        self._next_grace_revision = 0
        self._shutting_down = False

    async def create_room(
        self,
        *,
        actor: GuestId,
        nickname: str,
        settings: RoomSettings,
        password: str | None = None,
    ) -> RoomSnapshot:
        """Create one room without blocking the event loop on password hashing."""
        async with self._room_creation_lock:
            if password is None:
                return self._room_service.create_room(
                    actor=actor,
                    nickname=nickname,
                    settings=settings,
                )
            return await _run_serialized_in_thread(
                self._room_service.create_room,
                actor=actor,
                nickname=nickname,
                settings=settings,
                password=password,
            )

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
                if self._apply_due_turn_locked(room_id):
                    self._broadcast_current_locked(room_id)
                if self._resolve_due_graces_locked(room_id):
                    self._broadcast_current_locked(room_id)
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
                self._presence_sessions[(room_id, guest_id)] = session
                self._discard_grace_locked((room_id, guest_id))
                self.registry.enqueue(
                    session,
                    ConnectedMessage(guest_id=guest_id.value, room_code=current.room_code),
                )
                if joined:
                    self._enqueue_states(room_id, states)
                else:
                    self.registry.enqueue(session, initial_state)
                self._sync_turn_task_locked(room_id)
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
            if self._resolve_due_graces_locked(session.room_id):
                self._broadcast_current_locked(session.room_id)
            if isinstance(command, PauseGameCommand) and self._apply_due_turn_locked(
                session.room_id
            ):
                self._broadcast_current_locked(session.room_id)
            admitted_at_monotonic_ms: int | None = None
            if isinstance(
                command,
                (FoldCommand, CheckCommand, CallCommand, BetToCommand, RaiseToCommand),
            ):
                admitted_at_monotonic_ms = self._room_service.clock.now_monotonic_ms()
                deadline = self._room_service.current_turn_deadline(session.room_id)
                if (
                    deadline is not None
                    and admitted_at_monotonic_ms >= deadline.monotonic_ms
                    and self._apply_due_turn_locked(session.room_id)
                ):
                    self._reject_late_action_locked(session, command.command_id)
                    return
            try:
                await self._dispatch(session, command, admitted_at_monotonic_ms)
            except ActionDeadlineExpiredError:
                self._apply_due_turn_locked(session.room_id)
                self._reject_late_action_locked(session, command.command_id)
                return
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

            self._settle_expired_graces_locked(session.room_id)
            self._sync_turn_task_locked(session.room_id)
            if isinstance(command, ApproveSeatCommand):
                target = GuestId(command.target_guest_id)
                if (session.room_id, target) not in self._presence_sessions:
                    self._start_grace_locked(session.room_id, target)
            elif isinstance(command, LeaveCommand):
                self._retire_presence_locked(session.room_id, session.guest_id)
            elif isinstance(command, KickCommand):
                self._retire_presence_locked(session.room_id, GuestId(command.target_guest_id))
            elif isinstance(command, CloseRoomCommand):
                self._retire_room_presence_locked(session.room_id)
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
        async with self._lock_for(session.room_id):
            key = (session.room_id, session.guest_id)
            if self._presence_sessions.get(key) is session:
                self._presence_sessions.pop(key)
                if not session.suppress_grace and not self._shutting_down:
                    snapshot = self._room_service.get_room_snapshot(session.room_id)
                    if snapshot.status is not RoomStatus.CLOSED and any(
                        member.guest_id == session.guest_id
                        and member.status is RoomMemberStatus.SEATED
                        for member in snapshot.members
                    ):
                        self._start_grace_locked(session.room_id, session.guest_id)
        await self.registry.disconnect(session)

    async def shutdown(self) -> None:
        self._shutting_down = True
        tasks = tuple(item.task for item in self._turn_tasks.values())
        self._turn_tasks.clear()
        grace_tasks = tuple(self._grace_tasks)
        self._grace_tasks.clear()
        self._graces.clear()
        self._presence_sessions.clear()
        tasks += grace_tasks
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.registry.shutdown()

    def _apply_due_turn_locked(self, room_id: RoomId) -> bool:
        changed = False
        while (deadline := self._room_service.current_turn_deadline(room_id)) is not None:
            if (
                self._room_service.clock.now_monotonic_ms() < deadline.monotonic_ms
                or not self._room_service.expire_turn(deadline)
            ):
                break
            changed = True
        if changed:
            self._sync_turn_task_locked(room_id)
            self._settle_expired_graces_locked(room_id)
        return changed

    def _reject_late_action_locked(self, session: SocketSession, command_id: str) -> None:
        self.registry.enqueue(
            session,
            CommandErrorMessage(
                command_id=command_id,
                code="stale_game_state",
                message="The command used an outdated hand state.",
            ),
        )
        self._broadcast_current_locked(session.room_id)

    def _broadcast_current_locked(self, room_id: RoomId) -> None:
        try:
            self._enqueue_states(room_id, self._viewer_states(room_id))
        except Exception:
            logger.error("Realtime state projection failed after a timeout")
            self.registry.detach_room(room_id, code=1011, reason="state synchronization failed")

    def _sync_turn_task_locked(self, room_id: RoomId) -> None:
        deadline = self._room_service.current_turn_deadline(room_id)
        current = self._turn_tasks.get(room_id)
        if current is not None and current.deadline == deadline:
            return
        if current is not None:
            self._turn_tasks.pop(room_id)
            if current.task is not asyncio.current_task():
                current.task.cancel()
        if deadline is not None and not self._shutting_down:
            task = asyncio.create_task(self._run_turn_timer(deadline))
            self._turn_tasks[room_id] = _ScheduledTurn(deadline, task)

    async def _run_turn_timer(self, deadline: TurnDeadline) -> None:
        try:
            while True:
                remaining = deadline.monotonic_ms - self._room_service.clock.now_monotonic_ms()
                if remaining <= 0:
                    await self.expire_turn(deadline)
                    return
                await asyncio.sleep(remaining / 1_000)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.error("Realtime action timeout failed")
        finally:
            current = self._turn_tasks.get(deadline.room_id)
            if current is not None and current.task is asyncio.current_task():
                self._turn_tasks.pop(deadline.room_id)

    async def expire_turn(self, deadline: TurnDeadline) -> bool:
        """Validate a scheduled callback against current application state under the room lock."""
        async with self._lock_for(deadline.room_id):
            if self._shutting_down or not self._room_service.expire_turn(deadline):
                return False
            self._apply_due_turn_locked(deadline.room_id)
            self._settle_expired_graces_locked(deadline.room_id)
            self._sync_turn_task_locked(deadline.room_id)
            self._broadcast_current_locked(deadline.room_id)
            return True

    def _start_grace_locked(self, room_id: RoomId, guest_id: GuestId) -> None:
        if self._shutting_down:
            return
        key = (room_id, guest_id)
        self._discard_grace_locked(key)
        self._next_grace_revision += 1
        deadline = GraceDeadline(
            room_id,
            guest_id,
            self._next_grace_revision,
            self._room_service.clock.now_monotonic_ms() + DISCONNECT_GRACE_MS,
        )
        task = asyncio.create_task(self._run_grace_timer(deadline))
        self._grace_tasks.add(task)
        task.add_done_callback(self._grace_tasks.discard)
        self._graces[key] = _ScheduledGrace(deadline, task)

    def _discard_grace_locked(self, key: tuple[RoomId, GuestId]) -> None:
        current = self._graces.pop(key, None)
        if (
            current is not None
            and current.task is not None
            and current.task is not asyncio.current_task()
        ):
            current.task.cancel()

    def _retire_presence_locked(self, room_id: RoomId, guest_id: GuestId) -> None:
        key = (room_id, guest_id)
        session = self._presence_sessions.pop(key, None)
        if session is not None:
            session.suppress_grace = True
        self._discard_grace_locked(key)

    def _retire_room_presence_locked(self, room_id: RoomId) -> None:
        for key in tuple(self._presence_sessions):
            if key[0] == room_id:
                self._retire_presence_locked(*key)
        for key in tuple(self._graces):
            if key[0] == room_id:
                self._discard_grace_locked(key)

    def _resolve_due_graces_locked(self, room_id: RoomId) -> bool:
        changed = False
        for current in tuple(self._graces.values()):
            if current.deadline.room_id == room_id:
                changed |= self._expire_grace_locked(current.deadline)
        return changed

    def _expire_grace_locked(self, deadline: GraceDeadline) -> bool:
        key = (deadline.room_id, deadline.guest_id)
        current = self._graces.get(key)
        if (
            current is None
            or current.deadline != deadline
            or current.expired
            or key in self._presence_sessions
            or self._shutting_down
            or self._room_service.clock.now_monotonic_ms() < deadline.monotonic_ms
        ):
            return False
        snapshot = self._room_service.get_room_snapshot(deadline.room_id)
        if snapshot.status is RoomStatus.HAND_IN_PROGRESS:
            current.expired = True
            if current.task is not None and current.task is not asyncio.current_task():
                current.task.cancel()
            current.task = None
            return False
        if snapshot.status is RoomStatus.CLOSED or not any(
            member.guest_id == deadline.guest_id and member.status is RoomMemberStatus.SEATED
            for member in snapshot.members
        ):
            self._discard_grace_locked(key)
            return False
        self._room_service.stand_up(room_id=deadline.room_id, actor=deadline.guest_id)
        self._discard_grace_locked(key)
        return True

    def _settle_expired_graces_locked(self, room_id: RoomId) -> bool:
        if self._room_service.get_room_snapshot(room_id).status is not RoomStatus.OPEN:
            return False
        changed = False
        for current in tuple(self._graces.values()):
            if current.deadline.room_id == room_id:
                key = (room_id, current.deadline.guest_id)
                if key not in self._presence_sessions:
                    if current.expired:
                        changed |= self._expire_deferred_grace_locked(current.deadline)
                    else:
                        changed |= self._expire_grace_locked(current.deadline)
        return changed

    def _expire_deferred_grace_locked(self, deadline: GraceDeadline) -> bool:
        key = (deadline.room_id, deadline.guest_id)
        current = self._graces.get(key)
        if current is None or current.deadline != deadline or key in self._presence_sessions:
            return False
        snapshot = self._room_service.get_room_snapshot(deadline.room_id)
        if snapshot.status is not RoomStatus.OPEN or not any(
            member.guest_id == deadline.guest_id and member.status is RoomMemberStatus.SEATED
            for member in snapshot.members
        ):
            self._discard_grace_locked(key)
            return False
        self._room_service.stand_up(room_id=deadline.room_id, actor=deadline.guest_id)
        self._discard_grace_locked(key)
        return True

    async def _run_grace_timer(self, deadline: GraceDeadline) -> None:
        try:
            while True:
                remaining = deadline.monotonic_ms - self._room_service.clock.now_monotonic_ms()
                if remaining <= 0:
                    await self.expire_grace(deadline)
                    return
                await asyncio.sleep(remaining / 1_000)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.error("Realtime disconnect grace failed")
        finally:
            current = self._graces.get((deadline.room_id, deadline.guest_id))
            if current is not None and current.task is asyncio.current_task():
                current.task = None

    async def expire_grace(self, deadline: GraceDeadline) -> bool:
        """Resolve only the current, due grace under the room's serialization lock."""
        async with self._lock_for(deadline.room_id):
            changed = self._expire_grace_locked(deadline)
            if changed:
                self._broadcast_current_locked(deadline.room_id)
            return changed

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

    async def _dispatch(
        self,
        session: SocketSession,
        command: ClientCommand,
        admitted_at_monotonic_ms: int | None = None,
    ) -> None:
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
                admitted_at_monotonic_ms=admitted_at_monotonic_ms,
            )
        elif isinstance(command, CheckCommand):
            self._room_service.check(
                room_id=session.room_id,
                actor=session.guest_id,
                hand_number=command.hand_number,
                expected_action_sequence=command.expected_action_sequence,
                admitted_at_monotonic_ms=admitted_at_monotonic_ms,
            )
        elif isinstance(command, CallCommand):
            self._room_service.call(
                room_id=session.room_id,
                actor=session.guest_id,
                hand_number=command.hand_number,
                expected_action_sequence=command.expected_action_sequence,
                admitted_at_monotonic_ms=admitted_at_monotonic_ms,
            )
        elif isinstance(command, BetToCommand):
            self._room_service.bet_to(
                room_id=session.room_id,
                actor=session.guest_id,
                hand_number=command.hand_number,
                expected_action_sequence=command.expected_action_sequence,
                total=command.total,
                admitted_at_monotonic_ms=admitted_at_monotonic_ms,
            )
        elif isinstance(command, RaiseToCommand):
            self._room_service.raise_to(
                room_id=session.room_id,
                actor=session.guest_id,
                hand_number=command.hand_number,
                expected_action_sequence=command.expected_action_sequence,
                total=command.total,
                admitted_at_monotonic_ms=admitted_at_monotonic_ms,
            )
        elif isinstance(command, PauseGameCommand):
            self._room_service.pause_game(room_id=session.room_id, actor=session.guest_id)
        elif isinstance(command, ResumeGameCommand):
            self._room_service.resume_game(room_id=session.room_id, actor=session.guest_id)
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
        stand_up_enabled = command.stand_up_enabled
        stand_up_penalty = command.stand_up_penalty_per_recipient_chips
        room_name_value: str | SettingNotProvided
        seating_approval_value: bool | SettingNotProvided
        small_blind_value: int | SettingNotProvided
        big_blind_value: int | SettingNotProvided
        starting_stack_value: int | SettingNotProvided
        action_time_value: int | SettingNotProvided
        timebank_total_value: int | SettingNotProvided
        timebank_refill_amount_value: int | SettingNotProvided
        timebank_refill_hands_value: int | SettingNotProvided
        stand_up_enabled_value: bool | SettingNotProvided
        stand_up_penalty_value: int | SettingNotProvided
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
        action_time = command.action_time_ms
        timebank_total = command.timebank_total_ms
        timebank_refill_amount = command.timebank_refill_amount_ms
        timebank_refill_hands = command.timebank_refill_every_hands
        if "action_time_ms" in supplied:
            assert action_time is not None
            action_time_value = action_time
        else:
            action_time_value = SETTING_NOT_PROVIDED
        if "timebank_total_ms" in supplied:
            assert timebank_total is not None
            timebank_total_value = timebank_total
        else:
            timebank_total_value = SETTING_NOT_PROVIDED
        if "timebank_refill_amount_ms" in supplied:
            assert timebank_refill_amount is not None
            timebank_refill_amount_value = timebank_refill_amount
        else:
            timebank_refill_amount_value = SETTING_NOT_PROVIDED
        if "timebank_refill_every_hands" in supplied:
            assert timebank_refill_hands is not None
            timebank_refill_hands_value = timebank_refill_hands
        else:
            timebank_refill_hands_value = SETTING_NOT_PROVIDED
        if "stand_up_enabled" in supplied:
            assert stand_up_enabled is not None
            stand_up_enabled_value = stand_up_enabled
        else:
            stand_up_enabled_value = SETTING_NOT_PROVIDED
        if "stand_up_penalty_per_recipient_chips" in supplied:
            assert stand_up_penalty is not None
            stand_up_penalty_value = stand_up_penalty
        else:
            stand_up_penalty_value = SETTING_NOT_PROVIDED
        return RoomSettingsUpdate(
            room_name=room_name_value,
            small_blind=small_blind_value,
            big_blind=big_blind_value,
            default_starting_stack=starting_stack_value,
            action_time_ms=action_time_value,
            timebank_total_ms=timebank_total_value,
            timebank_refill_amount_ms=timebank_refill_amount_value,
            timebank_refill_every_hands=timebank_refill_hands_value,
            seating_approval_required=seating_approval_value,
            stand_up_enabled=stand_up_enabled_value,
            stand_up_penalty_per_recipient_chips=stand_up_penalty_value,
            password=command.password if "password" in supplied else SETTING_NOT_PROVIDED,
        )
