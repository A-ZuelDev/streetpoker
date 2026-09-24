"""Application service and in-memory repository for private rooms."""

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import replace
from threading import RLock
from typing import Protocol
from uuid import uuid4

from streetpoker.application.clock import Clock, SystemClock
from streetpoker.application.errors import (
    ActionDeadlineExpiredError,
    CannotStartHandError,
    DuplicateRoomCodeError,
    DuplicateRoomIdError,
    GamePausedError,
    GameplaySettlementError,
    HandAlreadyActiveError,
    InsufficientEligiblePlayersError,
    InvalidRoomPasswordError,
    InvalidRoomStateError,
    NoActiveHandError,
    NotCurrentActorError,
    NotHandParticipantError,
    PlayerIdGenerationError,
    RoomCreationCollisionError,
    RoomNotFoundError,
    StaleHandVersionError,
    WrongRoomPasswordError,
)
from streetpoker.application.gameplay import (
    RoomViewSnapshot,
    TurnDeadline,
    _ActiveHand,
    _HandIdentity,
    completed_hand_record,
    project_current_room_snapshot,
    project_room_view,
)
from streetpoker.application.rooms import (
    ROOM_CODE_ALPHABET,
    ROOM_CODE_LENGTH,
    SETTING_NOT_PROVIDED,
    GuestId,
    RoomId,
    RoomSettings,
    RoomSettingsUpdate,
    RoomSnapshot,
    RoomStatus,
    SettingNotProvided,
    StackAdjustmentType,
    _PasswordRecord,
    _Room,
    normalize_room_code,
)
from streetpoker.domain import (
    ActionKind,
    ChipStack,
    HandSettlementResult,
    HoldemHand,
    HoldemHandSnapshot,
    InvalidHandInitializationError,
    ParticipationStatus,
    PlayerId,
    RandomSource,
    SeatIndex,
    SecureRandomSource,
    StandUpCancelReason,
    StandUpHandOutcome,
    StandUpParticipant,
    StandUpResolution,
    StandUpRound,
    settle_holdem_hand,
)

PASSWORD_HASH_ITERATIONS = 600_000
PASSWORD_SALT_BYTES = 16
MAX_PASSWORD_CHARACTERS = 128
MAX_PASSWORD_BYTES = 256
MAX_GENERATION_ATTEMPTS = 32


class PasswordHasher(Protocol):
    """Minimal injectable password hashing and verification boundary."""

    def hash_password(self, password: str) -> _PasswordRecord:
        """Validate and irreversibly hash a room password."""
        ...

    def verify_password(self, password: str, record: _PasswordRecord) -> bool:
        """Return whether a candidate password matches an existing record."""
        ...


def _validated_password_bytes(password: str) -> bytes:
    if not isinstance(password, str):
        raise InvalidRoomPasswordError("A room password must be a string.")
    if not password:
        raise InvalidRoomPasswordError("A room password cannot be empty.")
    if len(password) > MAX_PASSWORD_CHARACTERS:
        raise InvalidRoomPasswordError(
            f"A room password cannot exceed {MAX_PASSWORD_CHARACTERS} characters."
        )
    try:
        encoded = password.encode("utf-8")
    except UnicodeEncodeError:
        raise InvalidRoomPasswordError(
            "A room password must contain valid UTF-8 characters."
        ) from None
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise InvalidRoomPasswordError(
            f"A room password cannot exceed {MAX_PASSWORD_BYTES} UTF-8 bytes."
        )
    return encoded


class Pbkdf2PasswordHasher:
    """Standard-library PBKDF2-HMAC-SHA256 room-password implementation."""

    __slots__ = ("_iterations",)

    def __init__(self, *, iterations: int = PASSWORD_HASH_ITERATIONS) -> None:
        if not isinstance(iterations, int) or isinstance(iterations, bool) or iterations <= 0:
            raise ValueError("PBKDF2 iterations must be a positive integer.")
        self._iterations = iterations

    def hash_password(self, password: str) -> _PasswordRecord:
        encoded = _validated_password_bytes(password)
        salt = secrets.token_bytes(PASSWORD_SALT_BYTES)
        digest = hashlib.pbkdf2_hmac("sha256", encoded, salt, self._iterations)
        return _PasswordRecord(salt=salt, digest=digest, iterations=self._iterations)

    def verify_password(self, password: str, record: _PasswordRecord) -> bool:
        encoded = _validated_password_bytes(password)
        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            encoded,
            record.salt,
            record.iterations,
        )
        return hmac.compare_digest(candidate, record.digest)


class RoomCodeSource(Protocol):
    """Injectable source for canonical candidate room codes."""

    def next_code(self) -> str:
        """Return the next candidate room code."""
        ...


class SecureRoomCodeSource:
    """Production room-code source backed by operating-system entropy."""

    def next_code(self) -> str:
        return "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(ROOM_CODE_LENGTH))


class RoomRepository(Protocol):
    """Thread-safe primitive storage boundary for process-local room aggregates.

    Callers serialize complete same-room transactions spanning multiple operations.
    """

    def add(self, room: _Room) -> None:
        """Atomically insert a room under both immutable indexes."""
        ...

    def get_by_id(self, room_id: RoomId) -> _Room:
        """Resolve a room by immutable ID."""
        ...

    def get_by_code(self, room_code: str) -> _Room:
        """Resolve a room by normalized, case-insensitive code."""
        ...

    def replace(self, room: _Room) -> None:
        """Atomically replace an existing same-identity room candidate."""
        ...

    def contains_code(self, room_code: str) -> bool:
        """Return whether a normalized code remains reserved."""
        ...


class InMemoryRoomRepository:
    """Thread-safe primitive room storage with two reconciled indexes.

    Callers must still serialize complete same-room service transactions.
    """

    __slots__ = ("_ids_by_code", "_lock", "_rooms_by_id")

    def __init__(self) -> None:
        self._rooms_by_id: dict[RoomId, _Room] = {}
        self._ids_by_code: dict[str, RoomId] = {}
        self._lock = RLock()

    def add(self, room: _Room) -> None:
        room.validate()
        with self._lock:
            if room.room_id in self._rooms_by_id:
                raise DuplicateRoomIdError(f"Room ID {room.room_id.value!r} already exists.")
            if room.room_code in self._ids_by_code:
                raise DuplicateRoomCodeError(f"Room code {room.room_code!r} already exists.")
            self._rooms_by_id[room.room_id] = room
            self._ids_by_code[room.room_code] = room.room_id

    def get_by_id(self, room_id: RoomId) -> _Room:
        with self._lock:
            try:
                room = self._rooms_by_id[room_id]
            except KeyError:
                raise RoomNotFoundError(f"Room ID {room_id.value!r} was not found.") from None
            indexed_id = self._ids_by_code.get(room.room_code)
            if indexed_id != room.room_id:
                raise InvalidRoomStateError("The room repository indexes are inconsistent.")
            return room

    def get_by_code(self, room_code: str) -> _Room:
        normalized = normalize_room_code(room_code)
        with self._lock:
            try:
                room_id = self._ids_by_code[normalized]
            except KeyError:
                raise RoomNotFoundError(f"Room code {normalized!r} was not found.") from None
            try:
                room = self._rooms_by_id[room_id]
            except KeyError:
                raise InvalidRoomStateError(
                    "The room repository indexes are inconsistent."
                ) from None
            if room.room_code != normalized:
                raise InvalidRoomStateError("The room repository indexes are inconsistent.")
            return room

    def replace(self, room: _Room) -> None:
        room.validate()
        with self._lock:
            try:
                existing = self._rooms_by_id[room.room_id]
            except KeyError:
                raise RoomNotFoundError(f"Room ID {room.room_id.value!r} was not found.") from None
            if room.room_code != existing.room_code:
                raise InvalidRoomStateError(
                    "Room codes are immutable and cannot change on replacement."
                )
            indexed_id = self._ids_by_code.get(room.room_code)
            if indexed_id != room.room_id:
                raise InvalidRoomStateError("The room repository indexes are inconsistent.")
            self._rooms_by_id[room.room_id] = room

    def contains_code(self, room_code: str) -> bool:
        normalized = normalize_room_code(room_code)
        with self._lock:
            return normalized in self._ids_by_code


def _new_room_id() -> RoomId:
    return RoomId(str(uuid4()))


def _new_player_id() -> PlayerId:
    return PlayerId(str(uuid4()))


class RoomService:
    """Coordinates authorized room commands and exposes immutable snapshots only."""

    __slots__ = (
        "_clock",
        "_code_source",
        "_password_hasher",
        "_player_id_factory",
        "_random_source_factory",
        "_repository",
        "_room_id_factory",
        "_settler",
    )

    def __init__(
        self,
        *,
        repository: RoomRepository | None = None,
        code_source: RoomCodeSource | None = None,
        password_hasher: PasswordHasher | None = None,
        room_id_factory: Callable[[], RoomId] = _new_room_id,
        player_id_factory: Callable[[], PlayerId] = _new_player_id,
        random_source_factory: Callable[[], RandomSource] = SecureRandomSource,
        settler: Callable[[HoldemHandSnapshot], HandSettlementResult] = settle_holdem_hand,
        clock: Clock | None = None,
    ) -> None:
        self._repository = InMemoryRoomRepository() if repository is None else repository
        self._code_source = SecureRoomCodeSource() if code_source is None else code_source
        self._password_hasher = (
            Pbkdf2PasswordHasher() if password_hasher is None else password_hasher
        )
        self._room_id_factory = room_id_factory
        self._player_id_factory = player_id_factory
        self._random_source_factory = random_source_factory
        self._settler = settler
        self._clock = SystemClock() if clock is None else clock

    @property
    def clock(self) -> Clock:
        return self._clock

    def create_room(
        self,
        *,
        actor: GuestId,
        nickname: str,
        settings: RoomSettings,
        password: str | None = None,
    ) -> RoomSnapshot:
        password_record = (
            None if password is None else self._password_hasher.hash_password(password)
        )
        for _ in range(MAX_GENERATION_ATTEMPTS):
            room = _Room(
                room_id=self._room_id_factory(),
                room_code=normalize_room_code(self._code_source.next_code()),
                host_guest_id=actor,
                settings=settings,
                password_record=password_record,
                host_player_id=self._player_id_factory(),
                host_nickname=nickname,
            )
            snapshot = room.snapshot()
            try:
                self._repository.add(room)
            except DuplicateRoomIdError, DuplicateRoomCodeError:
                continue
            return snapshot
        raise RoomCreationCollisionError(
            f"Could not allocate a unique room after {MAX_GENERATION_ATTEMPTS} attempts."
        )

    def join_room(
        self,
        *,
        room_code: str,
        actor: GuestId,
        nickname: str,
        password: str | None = None,
    ) -> RoomSnapshot:
        original = self._repository.get_by_code(room_code)
        original.require_not_closed()
        self._verify_join_password(original, password)
        candidate = original.copy()
        candidate.add_member(
            guest_id=actor,
            player_id=self._unique_player_id(candidate),
            nickname=nickname,
        )
        return self._commit(candidate)

    def leave_room(self, *, room_id: RoomId, actor: GuestId) -> RoomSnapshot:
        original = self._authorized_room(room_id, actor)
        candidate = original.copy()
        candidate.leave(actor=actor)
        return self._commit(candidate)

    def request_seat(
        self,
        *,
        room_id: RoomId,
        actor: GuestId,
        seat_index: int,
    ) -> RoomSnapshot:
        original = self._authorized_room(room_id, actor)
        candidate = original.copy()
        candidate.request_seat(actor=actor, seat_index=seat_index)
        return self._commit(candidate)

    def approve_seat_request(
        self,
        *,
        room_id: RoomId,
        actor: GuestId,
        target: GuestId,
    ) -> RoomSnapshot:
        original = self._host_room(room_id, actor)
        candidate = original.copy()
        candidate.approve_seat_request(target=target)
        return self._commit(candidate)

    def reject_seat_request(
        self,
        *,
        room_id: RoomId,
        actor: GuestId,
        target: GuestId,
    ) -> RoomSnapshot:
        original = self._host_room(room_id, actor)
        candidate = original.copy()
        candidate.reject_seat_request(target=target)
        return self._commit(candidate)

    def stand_up(self, *, room_id: RoomId, actor: GuestId) -> RoomSnapshot:
        original = self._authorized_room(room_id, actor)
        candidate = original.copy()
        candidate.stand_up(actor=actor)
        return self._commit(candidate)

    def kick_member(
        self,
        *,
        room_id: RoomId,
        actor: GuestId,
        target: GuestId,
    ) -> RoomSnapshot:
        original = self._host_room(room_id, actor)
        candidate = original.copy()
        candidate.kick(target=target)
        return self._commit(candidate)

    def update_room_settings(
        self,
        *,
        room_id: RoomId,
        actor: GuestId,
        update: RoomSettingsUpdate,
    ) -> RoomSnapshot:
        original = self._host_room(room_id, actor)
        original.require_not_closed()
        candidate = original.copy()
        settings = self._updated_settings(candidate.settings, update)
        password_record: _PasswordRecord | SettingNotProvided | None = SETTING_NOT_PROVIDED
        if update.password is not SETTING_NOT_PROVIDED:
            password_record = (
                None
                if update.password is None
                else self._password_hasher.hash_password(update.password)
            )
        candidate.update_settings(settings=settings, password_record=password_record)
        return self._commit(candidate)

    def close_room(self, *, room_id: RoomId, actor: GuestId) -> RoomSnapshot:
        original = self._host_room(room_id, actor)
        candidate = original.copy()
        candidate.close()
        return self._commit(candidate)

    def adjust_player_stack(
        self,
        *,
        room_id: RoomId,
        actor: GuestId,
        target: GuestId,
        adjustment_type: StackAdjustmentType,
        amount: int,
        reason: str | None,
        command_id: str,
        expected_next_hand_number: int,
        expected_ledger_sequence: int,
    ) -> RoomViewSnapshot:
        original = self._host_room(room_id, actor)
        candidate = original.copy()
        changed = candidate.adjust_stack(
            actor=actor,
            target=target,
            adjustment_type=adjustment_type,
            amount=amount,
            reason=reason,
            command_id=command_id,
            expected_next_hand_number=expected_next_hand_number,
            expected_ledger_sequence=expected_ledger_sequence,
        )
        if not changed:
            return self._project_view(original, actor)
        return self._commit_view(candidate, actor)

    def start_hand(
        self,
        *,
        room_id: RoomId,
        actor: GuestId,
        expected_hand_number: int,
    ) -> RoomViewSnapshot:
        original = self._host_room(room_id, actor)
        original.require_not_closed()
        candidate = original.copy()
        if candidate.status is RoomStatus.HAND_IN_PROGRESS:
            raise HandAlreadyActiveError("The room already has an active hand.")
        self._require_hand_number(
            supplied=expected_hand_number,
            current=candidate.next_hand_number,
        )
        eligible_count = sum(
            seat.occupant is not None
            and seat.occupant.status is ParticipationStatus.SITTING_IN
            and seat.occupant.stack.chips > 0
            for seat in candidate.table.seats
        )
        if eligible_count < 2:
            raise InsufficientEligiblePlayersError(
                "At least two seated, sitting-in players with chips are required."
            )
        candidate.table.move_button()
        try:
            hand = HoldemHand.start(
                table=candidate.table,
                small_blind=candidate.settings.small_blind,
                big_blind=candidate.settings.big_blind,
                random_source=self._random_source_factory(),
            )
        except InvalidHandInitializationError as error:
            raise CannotStartHandError("The room table could not start a Hold'em hand.") from error
        member_by_player = {member.player_id: member for member in candidate.members.values()}
        identities = tuple(
            _HandIdentity(
                guest_id=member_by_player[participant.player_id].guest_id,
                player_id=participant.player_id,
                nickname=member_by_player[participant.player_id].nickname,
            )
            for participant in hand.snapshot.participants
        )
        timebank_remaining_ms: dict[PlayerId, int] = {}
        timebank_hands_until_refill: dict[PlayerId, int | None] = {}
        for identity in identities:
            balance = candidate.timebank_balances_ms.get(
                identity.player_id,
                candidate.settings.timebank_total_ms,
            )
            completed_hands = candidate.timebank_hands_since_refill.get(
                identity.player_id,
                0,
            )
            if (
                candidate.settings.timebank_refill_amount_ms > 0
                and completed_hands >= candidate.settings.timebank_refill_every_hands
            ):
                balance = min(
                    candidate.settings.timebank_total_ms,
                    balance + candidate.settings.timebank_refill_amount_ms,
                )
                completed_hands = 0
            candidate.timebank_balances_ms[identity.player_id] = balance
            candidate.timebank_hands_since_refill[identity.player_id] = completed_hands
            timebank_remaining_ms[identity.player_id] = balance
            timebank_hands_until_refill[identity.player_id] = (
                None
                if candidate.settings.timebank_refill_amount_ms == 0
                else candidate.settings.timebank_refill_every_hands - completed_hands
            )
        active = _ActiveHand(
            hand_number=expected_hand_number,
            action_sequence=0,
            hand=hand,
            identities=identities,
            timebank_remaining_ms=timebank_remaining_ms,
            timebank_hands_until_refill=timebank_hands_until_refill,
            base_action_time_ms=candidate.settings.action_time_ms,
            timebank_total_ms=candidate.settings.timebank_total_ms,
            timebank_refill_amount_ms=candidate.settings.timebank_refill_amount_ms,
        )
        if candidate.stand_up_round is None and candidate.settings.stand_up_enabled:
            candidate.stand_up_round = StandUpRound.start(
                start_hand_number=expected_hand_number,
                participants=(
                    StandUpParticipant(participant.player_id, participant.seat_index)
                    for participant in hand.snapshot.participants
                ),
                penalty_per_recipient_chips=(
                    candidate.settings.stand_up_penalty_per_recipient_chips
                ),
            )
        candidate.active_hand = active
        candidate.next_hand_number += 1
        candidate.status = RoomStatus.HAND_IN_PROGRESS
        if hand.snapshot.terminal:
            self._complete_hand(candidate)
        else:
            self._set_turn_deadline(candidate.room_id, active)
        return self._commit_view(candidate, actor)

    def current_turn_deadline(self, room_id: RoomId) -> TurnDeadline | None:
        room = self._repository.get_by_id(room_id)
        if room.status is RoomStatus.CLOSED or room.active_hand is None or room.is_paused:
            return None
        return room.active_hand.deadline

    def pause_game(self, *, room_id: RoomId, actor: GuestId) -> RoomViewSnapshot:
        room = self._host_room(room_id, actor)
        if room.active_hand is None:
            raise NoActiveHandError("The room has no active hand.")
        if room.is_paused:
            return self._project_view(room, actor)
        candidate = room.copy()
        active = candidate.active_hand
        assert active is not None and active.deadline is not None
        remaining = max(0, active.deadline.monotonic_ms - self._clock.now_monotonic_ms())
        active.frozen_remaining_ms = remaining
        if active.using_timebank:
            current_player = active.hand.betting_round_snapshot
            assert current_player is not None and current_player.current_player is not None
            active.timebank_remaining_ms[current_player.current_player] = remaining
        candidate.is_paused = True
        return self._commit_view(candidate, actor)

    def resume_game(self, *, room_id: RoomId, actor: GuestId) -> RoomViewSnapshot:
        room = self._host_room(room_id, actor)
        if room.active_hand is None:
            raise NoActiveHandError("The room has no active hand.")
        if not room.is_paused:
            return self._project_view(room, actor)
        candidate = room.copy()
        active = candidate.active_hand
        assert (
            active is not None
            and active.deadline is not None
            and active.frozen_remaining_ms is not None
        )
        remaining = active.frozen_remaining_ms
        active.deadline = replace(
            active.deadline,
            revision=active.deadline.revision + 1,
            monotonic_ms=self._clock.now_monotonic_ms() + remaining,
            unix_ms=self._clock.now_unix_ms() + remaining,
        )
        active.frozen_remaining_ms = None
        candidate.is_paused = False
        return self._commit_view(candidate, actor)

    def expire_turn(self, expected: TurnDeadline) -> bool:
        """Extend or time out one due turn only when its full identity matches."""
        current = self.current_turn_deadline(expected.room_id)
        if current != expected or self._clock.now_monotonic_ms() < expected.monotonic_ms:
            return False
        room = self._repository.get_by_id(expected.room_id)
        active = room.active_hand
        if active is None:
            return False
        legal = active.hand.legal_actions()
        current_player = active.hand.betting_round_snapshot
        if (
            current_player is None
            or current_player.current_player is None
            or active.identity_for_player(current_player.current_player).guest_id != expected.actor
        ):
            return False
        player_id = current_player.current_player
        remaining = active.timebank_remaining_ms[player_id]
        if remaining > 0 and not active.using_timebank:
            candidate = room.copy()
            extending = candidate.active_hand
            assert extending is not None
            extending.using_timebank = True
            # Advance the command CAS version so a command sent for the base
            # deadline cannot be admitted after this authoritative transition.
            extending.action_sequence += 1
            extending.deadline = replace(
                expected,
                action_sequence=extending.action_sequence,
                revision=expected.revision + 1,
                monotonic_ms=expected.monotonic_ms + remaining,
                unix_ms=expected.unix_ms + remaining,
            )
            self._commit_view(candidate, expected.actor)
            return True
        operation = (
            (lambda hand, player_id: hand.check(player_id=player_id))
            if ActionKind.CHECK in legal.kinds
            else (lambda hand, player_id: hand.fold(player_id=player_id))
        )
        self._act(
            room_id=expected.room_id,
            actor=expected.actor,
            hand_number=expected.hand_number,
            expected_action_sequence=expected.action_sequence,
            operation=operation,
            is_timeout=True,
        )
        return True

    def fold(
        self,
        *,
        room_id: RoomId,
        actor: GuestId,
        hand_number: int,
        expected_action_sequence: int,
        admitted_at_monotonic_ms: int | None = None,
    ) -> RoomViewSnapshot:
        return self._act(
            room_id=room_id,
            actor=actor,
            hand_number=hand_number,
            expected_action_sequence=expected_action_sequence,
            admitted_at_monotonic_ms=admitted_at_monotonic_ms,
            operation=lambda hand, player_id: hand.fold(player_id=player_id),
        )

    def check(
        self,
        *,
        room_id: RoomId,
        actor: GuestId,
        hand_number: int,
        expected_action_sequence: int,
        admitted_at_monotonic_ms: int | None = None,
    ) -> RoomViewSnapshot:
        return self._act(
            room_id=room_id,
            actor=actor,
            hand_number=hand_number,
            expected_action_sequence=expected_action_sequence,
            admitted_at_monotonic_ms=admitted_at_monotonic_ms,
            operation=lambda hand, player_id: hand.check(player_id=player_id),
        )

    def call(
        self,
        *,
        room_id: RoomId,
        actor: GuestId,
        hand_number: int,
        expected_action_sequence: int,
        admitted_at_monotonic_ms: int | None = None,
    ) -> RoomViewSnapshot:
        return self._act(
            room_id=room_id,
            actor=actor,
            hand_number=hand_number,
            expected_action_sequence=expected_action_sequence,
            admitted_at_monotonic_ms=admitted_at_monotonic_ms,
            operation=lambda hand, player_id: hand.call(player_id=player_id),
        )

    def bet_to(
        self,
        *,
        room_id: RoomId,
        actor: GuestId,
        hand_number: int,
        expected_action_sequence: int,
        total: int,
        admitted_at_monotonic_ms: int | None = None,
    ) -> RoomViewSnapshot:
        return self._act(
            room_id=room_id,
            actor=actor,
            hand_number=hand_number,
            expected_action_sequence=expected_action_sequence,
            admitted_at_monotonic_ms=admitted_at_monotonic_ms,
            operation=lambda hand, player_id: hand.bet_to(player_id=player_id, total=total),
        )

    def raise_to(
        self,
        *,
        room_id: RoomId,
        actor: GuestId,
        hand_number: int,
        expected_action_sequence: int,
        total: int,
        admitted_at_monotonic_ms: int | None = None,
    ) -> RoomViewSnapshot:
        return self._act(
            room_id=room_id,
            actor=actor,
            hand_number=hand_number,
            expected_action_sequence=expected_action_sequence,
            admitted_at_monotonic_ms=admitted_at_monotonic_ms,
            operation=lambda hand, player_id: hand.raise_to(player_id=player_id, total=total),
        )

    def get_room_view(self, *, room_id: RoomId, viewer: GuestId) -> RoomViewSnapshot:
        room = self._authorized_room(room_id, viewer)
        return self._project_view(room, viewer)

    def get_room_snapshot(self, room_id: RoomId) -> RoomSnapshot:
        room = self._repository.get_by_id(room_id)
        return project_current_room_snapshot(room.snapshot(), room.active_hand)

    def get_room_snapshot_by_code(self, room_code: str) -> RoomSnapshot:
        room = self._repository.get_by_code(room_code)
        return project_current_room_snapshot(room.snapshot(), room.active_hand)

    def _authorized_room(self, room_id: RoomId, actor: GuestId) -> _Room:
        room = self._repository.get_by_id(room_id)
        room.require_actor(actor)
        return room

    def _host_room(self, room_id: RoomId, actor: GuestId) -> _Room:
        room = self._repository.get_by_id(room_id)
        room.require_host(actor)
        return room

    def _commit(self, candidate: _Room) -> RoomSnapshot:
        candidate.validate()
        snapshot = project_current_room_snapshot(candidate.snapshot(), candidate.active_hand)
        self._repository.replace(candidate)
        return snapshot

    def _act(
        self,
        *,
        room_id: RoomId,
        actor: GuestId,
        hand_number: int,
        expected_action_sequence: int,
        operation: Callable[[HoldemHand, PlayerId], object],
        is_timeout: bool = False,
        admitted_at_monotonic_ms: int | None = None,
    ) -> RoomViewSnapshot:
        original = self._authorized_room(room_id, actor)
        candidate = original.copy()
        active = candidate.active_hand
        if active is None:
            raise NoActiveHandError("The room has no active hand.")
        if candidate.is_paused:
            raise GamePausedError("Gameplay is paused by the room host.")
        self._require_hand_number(supplied=hand_number, current=active.hand_number)
        if (
            not isinstance(expected_action_sequence, int)
            or isinstance(expected_action_sequence, bool)
            or expected_action_sequence != active.action_sequence
        ):
            raise StaleHandVersionError(f"Expected action sequence {active.action_sequence}.")
        if (
            not is_timeout
            and active.deadline is not None
            and (
                self._clock.now_monotonic_ms()
                if admitted_at_monotonic_ms is None
                else admitted_at_monotonic_ms
            )
            >= active.deadline.monotonic_ms
        ):
            raise ActionDeadlineExpiredError("The current action deadline has expired.")
        identity = active.identity_for_guest(actor)
        if identity is None:
            raise NotHandParticipantError("The room member is not in the active hand.")
        round_snapshot = active.hand.betting_round_snapshot
        if round_snapshot is None or round_snapshot.current_player != identity.player_id:
            current_actor = (
                None
                if round_snapshot is None or round_snapshot.current_player is None
                else active.identity_for_player(round_snapshot.current_player).guest_id.value
            )
            raise NotCurrentActorError(
                f"The acting guest is not current; current actor is {current_actor!r}."
            )
        if active.using_timebank and is_timeout:
            active.timebank_remaining_ms[identity.player_id] = 0
        if active.using_timebank and not is_timeout and active.deadline is not None:
            admitted = (
                self._clock.now_monotonic_ms()
                if admitted_at_monotonic_ms is None
                else admitted_at_monotonic_ms
            )
            active.timebank_remaining_ms[identity.player_id] = max(
                0,
                active.deadline.monotonic_ms - admitted,
            )
        operation(active.hand, identity.player_id)
        active.action_sequence += 1
        if active.hand.snapshot.terminal:
            self._complete_hand(candidate)
        else:
            self._set_turn_deadline(room_id, active)
        return self._commit_view(candidate, actor)

    def _set_turn_deadline(self, room_id: RoomId, active: _ActiveHand) -> None:
        round_snapshot = active.hand.betting_round_snapshot
        assert round_snapshot is not None and round_snapshot.current_player is not None
        previous = active.deadline
        active.using_timebank = False
        active.frozen_remaining_ms = None
        active.deadline = TurnDeadline(
            room_id=room_id,
            hand_number=active.hand_number,
            action_sequence=active.action_sequence,
            actor=active.identity_for_player(round_snapshot.current_player).guest_id,
            revision=1 if previous is None else previous.revision + 1,
            monotonic_ms=self._clock.now_monotonic_ms() + active.base_action_time_ms,
            unix_ms=self._clock.now_unix_ms() + active.base_action_time_ms,
        )

    def _complete_hand(self, candidate: _Room) -> None:
        active = candidate.active_hand
        if active is None or not active.hand.snapshot.terminal:
            raise GameplaySettlementError("Only a terminal active hand can be completed.")
        snapshot = active.hand.snapshot
        settlement = self._settler(snapshot)
        participant_by_id = {item.player_id: item for item in snapshot.participants}
        settlement_by_id = {item.player_id: item for item in settlement.player_settlements}
        identity_ids = {item.player_id for item in active.identities}
        if (
            set(participant_by_id) != set(settlement_by_id)
            or set(participant_by_id) != identity_ids
        ):
            raise GameplaySettlementError(
                "Settlement participants must exactly match captured hand participants."
            )
        if sum(item.starting_stack.chips for item in snapshot.participants) != sum(
            item.final_stack.chips for item in settlement.player_settlements
        ):
            raise GameplaySettlementError("Terminal settlement must conserve participant chips.")
        for player_id, participant in participant_by_id.items():
            item = settlement_by_id[player_id]
            seat = candidate.table.seat_at(participant.seat_index)
            if (
                item.seat_index != participant.seat_index
                or seat.occupant is None
                or seat.occupant.player_id != player_id
                or seat.occupant.stack != participant.starting_stack
            ):
                raise GameplaySettlementError(
                    "Settlement identity, seat, or starting stack disagrees with the room table."
                )
        completed = completed_hand_record(active, settlement)
        for item in settlement.player_settlements:
            candidate.table.leave_seat(player_id=item.player_id)
            candidate.table.seat_player(
                seat_index=SeatIndex(item.seat_index.value),
                player_id=item.player_id,
                stack=ChipStack(item.final_stack.chips),
                status=(
                    ParticipationStatus.SITTING_IN
                    if item.final_stack.chips > 0
                    else ParticipationStatus.SITTING_OUT
                ),
            )
        self._settle_stand_up(candidate, active, settlement)
        candidate.record_completed_hand(set(participant_by_id))
        for player_id in participant_by_id:
            candidate.timebank_balances_ms[player_id] = active.timebank_remaining_ms[player_id]
            candidate.timebank_hands_since_refill[player_id] = min(
                candidate.settings.timebank_refill_every_hands,
                candidate.timebank_hands_since_refill.get(player_id, 0) + 1,
            )
        candidate.last_hand = completed
        candidate.active_hand = None
        candidate.is_paused = False
        candidate.status = RoomStatus.OPEN

    @staticmethod
    def _stand_up_outcome(
        active: _ActiveHand, settlement: HandSettlementResult
    ) -> StandUpHandOutcome:
        if not settlement.pot_awards or settlement.pot_awards[0].pot_index != 0:
            raise GameplaySettlementError("Stand-Up requires the validated main-pot award.")
        main_pot = settlement.pot_awards[0]
        button = active.hand.snapshot.button_position
        if main_pot.button_position != button:
            raise GameplaySettlementError("Main-pot button disagrees with the completed hand.")
        return StandUpHandOutcome(
            hand_number=active.hand_number,
            participants=tuple(item.player_id for item in settlement.player_settlements),
            main_pot_winners=main_pot.winners,
            button_seat=button,
        )

    def _settle_stand_up(
        self, candidate: _Room, active: _ActiveHand, settlement: HandSettlementResult
    ) -> None:
        round_state = candidate.stand_up_round
        if round_state is None:
            return
        outcome = self._stand_up_outcome(active, settlement)
        settled_stacks: dict[PlayerId, int] = {}
        for participant in round_state.participants:
            seat = candidate.table.seat_at(participant.seat_index)
            if seat.occupant is None or seat.occupant.player_id != participant.player_id:
                raise GameplaySettlementError("Stand-Up cohort seat changed during settlement.")
            settled_stacks[participant.player_id] = seat.occupant.stack.chips
        if any(chips == 0 for chips in settled_stacks.values()):
            candidate.last_stand_up_result = round_state.cancel(
                StandUpCancelReason.PARTICIPANT_BUSTED
            )
            candidate.stand_up_round = None
            return
        transition = round_state.apply_hand(outcome, settled_stacks_by_player=settled_stacks)
        if isinstance(transition, StandUpRound):
            candidate.stand_up_round = transition
            return
        if (
            transition.participants != round_state.participants
            or transition.start_hand_number != round_state.start_hand_number
            or transition.hand_number != active.hand_number
            or transition.penalty_per_recipient_chips != round_state.penalty_per_recipient_chips
            or transition.button_seat != outcome.button_seat
        ):
            raise GameplaySettlementError("Stand-Up resolution disagrees with its active round.")
        self._apply_stand_up_transfers(candidate, transition)
        candidate.stand_up_round = None
        candidate.last_stand_up_result = transition

    @staticmethod
    def _apply_stand_up_transfers(candidate: _Room, resolution: StandUpResolution) -> None:
        seats = {item.player_id: item.seat_index for item in resolution.participants}
        stacks: dict[PlayerId, int] = {}
        for player_id, seat_index in seats.items():
            occupant = candidate.table.seat_at(seat_index).occupant
            if occupant is None or occupant.player_id != player_id:
                raise GameplaySettlementError(
                    "Stand-Up transfer recipient no longer owns the seat."
                )
            stacks[player_id] = occupant.stack.chips
        if stacks[resolution.squid] != resolution.squid_available_stack:
            raise GameplaySettlementError("The squid stack changed before side-game transfer.")
        total_before = sum(
            seat.occupant.stack.chips for seat in candidate.table.seats if seat.occupant is not None
        )
        recipients: set[PlayerId] = set()
        transferred = 0
        for transfer in resolution.transfers:
            if (
                transfer.from_player_id != resolution.squid
                or transfer.to_player_id not in seats
                or transfer.to_player_id == resolution.squid
                or transfer.to_player_id in recipients
                or transfer.chips <= 0
                or transfer.chips > resolution.penalty_per_recipient_chips
            ):
                raise GameplaySettlementError("Stand-Up transfer identities or chips are invalid.")
            recipients.add(transfer.to_player_id)
            transferred += transfer.chips
            stacks[resolution.squid] -= transfer.chips
            stacks[transfer.to_player_id] += transfer.chips
            if stacks[resolution.squid] < 0:
                raise GameplaySettlementError(
                    "Stand-Up transfer exceeds the squid's settled stack."
                )
        if transferred != resolution.actual_total or transferred > resolution.intended_total:
            raise GameplaySettlementError("Stand-Up transfers do not match the resolution total.")
        for player_id, seat_index in seats.items():
            seat = candidate.table.seat_at(seat_index)
            occupant = seat.occupant
            assert occupant is not None
            if occupant.stack.chips == stacks[player_id]:
                continue
            candidate.table.leave_seat(player_id=player_id)
            candidate.table.seat_player(
                seat_index=seat_index,
                player_id=player_id,
                stack=ChipStack(stacks[player_id]),
                status=(
                    ParticipationStatus.SITTING_IN
                    if stacks[player_id] > 0
                    else ParticipationStatus.SITTING_OUT
                ),
            )
        total_after = sum(
            seat.occupant.stack.chips for seat in candidate.table.seats if seat.occupant is not None
        )
        if total_after != total_before:
            raise GameplaySettlementError("Stand-Up transfers must conserve table chips.")

    def _commit_view(self, candidate: _Room, viewer: GuestId) -> RoomViewSnapshot:
        candidate.validate()
        view = self._project_view(candidate, viewer)
        self._repository.replace(candidate)
        return view

    def _project_view(self, room: _Room, viewer: GuestId) -> RoomViewSnapshot:
        return project_room_view(
            room.snapshot(),
            room.next_hand_number,
            room.active_hand,
            room.last_hand,
            viewer,
            paused=room.is_paused,
        )

    @staticmethod
    def _require_hand_number(*, supplied: int, current: int) -> None:
        if not isinstance(supplied, int) or isinstance(supplied, bool) or supplied != current:
            raise StaleHandVersionError(f"Expected hand number {current}.")

    def _verify_join_password(self, room: _Room, password: str | None) -> None:
        record = room.password_record
        if record is None:
            return
        if password is None or not self._password_hasher.verify_password(password, record):
            raise WrongRoomPasswordError("The room password is incorrect.")

    def _unique_player_id(self, room: _Room) -> PlayerId:
        existing = {member.player_id for member in room.members.values()}
        for _ in range(MAX_GENERATION_ATTEMPTS):
            candidate = self._player_id_factory()
            if candidate not in existing:
                return candidate
        raise PlayerIdGenerationError(
            f"Could not allocate a unique player ID after {MAX_GENERATION_ATTEMPTS} attempts."
        )

    @staticmethod
    def _updated_settings(
        current: RoomSettings,
        update: RoomSettingsUpdate,
    ) -> RoomSettings:
        if not isinstance(update, RoomSettingsUpdate):
            raise InvalidRoomStateError("Settings updates require RoomSettingsUpdate values.")
        return RoomSettings(
            room_name=(
                current.room_name if update.room_name is SETTING_NOT_PROVIDED else update.room_name
            ),
            small_blind=(
                current.small_blind
                if update.small_blind is SETTING_NOT_PROVIDED
                else update.small_blind
            ),
            big_blind=(
                current.big_blind if update.big_blind is SETTING_NOT_PROVIDED else update.big_blind
            ),
            default_starting_stack=(
                current.default_starting_stack
                if update.default_starting_stack is SETTING_NOT_PROVIDED
                else update.default_starting_stack
            ),
            action_time_ms=(
                current.action_time_ms
                if update.action_time_ms is SETTING_NOT_PROVIDED
                else update.action_time_ms
            ),
            timebank_total_ms=(
                current.timebank_total_ms
                if update.timebank_total_ms is SETTING_NOT_PROVIDED
                else update.timebank_total_ms
            ),
            timebank_refill_amount_ms=(
                current.timebank_refill_amount_ms
                if update.timebank_refill_amount_ms is SETTING_NOT_PROVIDED
                else update.timebank_refill_amount_ms
            ),
            timebank_refill_every_hands=(
                current.timebank_refill_every_hands
                if update.timebank_refill_every_hands is SETTING_NOT_PROVIDED
                else update.timebank_refill_every_hands
            ),
            seating_approval_required=(
                current.seating_approval_required
                if update.seating_approval_required is SETTING_NOT_PROVIDED
                else update.seating_approval_required
            ),
            stand_up_enabled=(
                current.stand_up_enabled
                if update.stand_up_enabled is SETTING_NOT_PROVIDED
                else update.stand_up_enabled
            ),
            stand_up_penalty_per_recipient_chips=(
                current.stand_up_penalty_per_recipient_chips
                if update.stand_up_penalty_per_recipient_chips is SETTING_NOT_PROVIDED
                else update.stand_up_penalty_per_recipient_chips
            ),
        )
