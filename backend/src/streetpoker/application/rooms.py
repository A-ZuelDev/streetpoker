"""Room values, immutable snapshots, and the private room aggregate."""

import unicodedata
from dataclasses import dataclass, replace
from enum import Enum, StrEnum

from streetpoker.application.errors import (
    ActiveHandMutationError,
    CannotKickHostError,
    DuplicateMembershipError,
    DuplicateNicknameError,
    DuplicateSeatRequestError,
    HostCannotLeaveRoomError,
    InvalidGuestIdError,
    InvalidNicknameError,
    InvalidRoomCodeError,
    InvalidRoomIdError,
    InvalidRoomNameError,
    InvalidRoomSeatError,
    InvalidRoomSettingsError,
    InvalidRoomStateError,
    InvalidStackAdjustmentError,
    MemberAlreadySeatedError,
    MemberNotFoundError,
    MemberNotSeatedError,
    NotRoomHostError,
    NotRoomMemberError,
    RoomChipLimitError,
    RoomClosedError,
    RoomSeatAlreadyRequestedError,
    RoomSeatOccupiedError,
    SeatRequestNotFoundError,
    StackAdjustmentCommandConflictError,
    StackAdjustmentTargetError,
    StaleHandVersionError,
)
from streetpoker.application.gameplay import _ActiveHand, _CompletedHandRecord
from streetpoker.domain import (
    SIX_MAX_CAPACITY,
    ChipStack,
    ParticipationStatus,
    PlayerAlreadySeatedError,
    PlayerId,
    PlayerNotSeatedError,
    Seat,
    SeatedPlayer,
    SeatIndex,
    SeatOccupiedError,
    SeatOutOfRangeError,
    StandUpCancellation,
    StandUpCancelReason,
    StandUpParticipant,
    StandUpResolution,
    StandUpRound,
    TableState,
)

ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ROOM_CODE_LENGTH = 8
MAX_NICKNAME_LENGTH = 24
MAX_ROOM_NAME_LENGTH = 60
DEFAULT_SMALL_BLIND = 50
DEFAULT_BIG_BLIND = 100
DEFAULT_STARTING_STACK = 10_000
DEFAULT_ACTION_TIME_MS = 30_000
MIN_ACTION_TIME_MS = 5_000
MAX_ACTION_TIME_MS = 120_000
DEFAULT_TIMEBANK_TOTAL_MS = 60_000
DEFAULT_TIMEBANK_REFILL_AMOUNT_MS = 60_000
DEFAULT_TIMEBANK_REFILL_EVERY_HANDS = 1
MAX_TIMEBANK_MS = 300_000
MAX_TIMEBANK_REFILL_EVERY_HANDS = 100
DEFAULT_STAND_UP_PENALTY_PER_RECIPIENT_CHIPS = 100
MAX_SAFE_INTEGER = 9_007_199_254_740_991
MAX_INITIAL_STACK = MAX_SAFE_INTEGER // SIX_MAX_CAPACITY
MAX_CHIP_STACK = MAX_SAFE_INTEGER
MAX_ADJUSTMENT_REASON_LENGTH = 80
MAX_STAND_UP_PENALTY_PER_RECIPIENT_CHIPS = MAX_SAFE_INTEGER // (SIX_MAX_CAPACITY - 1)


@dataclass(frozen=True, slots=True)
class RoomId:
    """Opaque immutable room identity."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value or self.value.isspace():
            raise InvalidRoomIdError("A room ID must be a non-blank string.")


@dataclass(frozen=True, slots=True)
class GuestId:
    """Opaque immutable guest identity used for room authorization."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value or self.value.isspace():
            raise InvalidGuestIdError("A guest ID must be a non-blank string.")


class RoomStatus(StrEnum):
    """Application lifecycle state for a private room."""

    OPEN = "open"
    HAND_IN_PROGRESS = "hand_in_progress"
    CLOSED = "closed"


class RoomMemberStatus(StrEnum):
    """Current relationship between a member and the room table."""

    IN_ROOM = "in_room"
    SEATED = "seated"


class StackAdjustmentType(StrEnum):
    """Explicit external stack-change semantics for the session ledger."""

    REBUY = "rebuy"
    CASH_OUT = "cash_out"
    CORRECTION = "correction"


class SettingNotProvided(Enum):
    """Typed sentinel distinguishing omission from clearing a password."""

    TOKEN = 0


SETTING_NOT_PROVIDED = SettingNotProvided.TOKEN


def _normalize_display_text(
    value: str,
    *,
    label: str,
    maximum_length: int,
    error_type: type[InvalidNicknameError] | type[InvalidRoomNameError],
) -> str:
    if not isinstance(value, str):
        raise error_type(f"{label} must be a string.")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if not normalized:
        raise error_type(f"{label} cannot be empty.")
    if len(normalized) > maximum_length:
        raise error_type(f"{label} cannot exceed {maximum_length} normalized characters.")
    if any(unicodedata.category(character).startswith("C") for character in normalized):
        raise error_type(f"{label} cannot contain control characters.")
    return normalized


def normalize_nickname(value: str) -> str:
    """Return the canonical display nickname used by every room command."""
    return _normalize_display_text(
        value,
        label="A nickname",
        maximum_length=MAX_NICKNAME_LENGTH,
        error_type=InvalidNicknameError,
    )


def normalize_room_name(value: str) -> str:
    """Return the canonical display room name."""
    return _normalize_display_text(
        value,
        label="A room name",
        maximum_length=MAX_ROOM_NAME_LENGTH,
        error_type=InvalidRoomNameError,
    )


def normalize_room_code(value: str) -> str:
    """Normalize a case-insensitive human-entered room code."""
    if not isinstance(value, str):
        raise InvalidRoomCodeError("A room code must be a string.")
    normalized = value.strip().upper()
    if len(normalized) != ROOM_CODE_LENGTH or any(
        character not in ROOM_CODE_ALPHABET for character in normalized
    ):
        raise InvalidRoomCodeError(
            f"A room code must contain {ROOM_CODE_LENGTH} characters from the room alphabet."
        )
    return normalized


def normalize_adjustment_reason(value: str | None) -> str | None:
    """Normalize one optional public ledger reason without accepting control text."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidStackAdjustmentError("An adjustment reason must be a string.")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if not normalized:
        return None
    if len(normalized) > MAX_ADJUSTMENT_REASON_LENGTH:
        raise InvalidStackAdjustmentError(
            f"An adjustment reason cannot exceed {MAX_ADJUSTMENT_REASON_LENGTH} characters."
        )
    if any(unicodedata.category(character).startswith("C") for character in normalized):
        raise InvalidStackAdjustmentError("An adjustment reason cannot contain control characters.")
    return normalized


def _strict_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


@dataclass(frozen=True, slots=True)
class RoomSettings:
    """Complete validated settings for one six-max room."""

    room_name: str
    small_blind: int = DEFAULT_SMALL_BLIND
    big_blind: int = DEFAULT_BIG_BLIND
    default_starting_stack: int = DEFAULT_STARTING_STACK
    action_time_ms: int = DEFAULT_ACTION_TIME_MS
    timebank_total_ms: int = DEFAULT_TIMEBANK_TOTAL_MS
    timebank_refill_amount_ms: int = DEFAULT_TIMEBANK_REFILL_AMOUNT_MS
    timebank_refill_every_hands: int = DEFAULT_TIMEBANK_REFILL_EVERY_HANDS
    seating_approval_required: bool = True
    stand_up_enabled: bool = False
    stand_up_penalty_per_recipient_chips: int = DEFAULT_STAND_UP_PENALTY_PER_RECIPIENT_CHIPS
    max_seats: int = SIX_MAX_CAPACITY

    def __post_init__(self) -> None:
        object.__setattr__(self, "room_name", normalize_room_name(self.room_name))
        if not _strict_positive_int(self.small_blind):
            raise InvalidRoomSettingsError("The small blind must be a positive integer.")
        if not _strict_positive_int(self.big_blind) or self.big_blind <= self.small_blind:
            raise InvalidRoomSettingsError("The big blind must be greater than the small blind.")
        if (
            not _strict_positive_int(self.default_starting_stack)
            or self.default_starting_stack < self.big_blind
            or self.default_starting_stack > MAX_INITIAL_STACK
        ):
            raise InvalidRoomSettingsError(
                "The default starting stack must be at least the big blind and six-max safe."
            )
        if (
            not _strict_positive_int(self.action_time_ms)
            or not MIN_ACTION_TIME_MS <= self.action_time_ms <= MAX_ACTION_TIME_MS
        ):
            raise InvalidRoomSettingsError("The action time must be between 5 and 120 seconds.")
        if (
            not isinstance(self.timebank_total_ms, int)
            or isinstance(self.timebank_total_ms, bool)
            or not 0 <= self.timebank_total_ms <= MAX_TIMEBANK_MS
        ):
            raise InvalidRoomSettingsError("The time bank must be between 0 and 300 seconds.")
        if (
            not isinstance(self.timebank_refill_amount_ms, int)
            or isinstance(self.timebank_refill_amount_ms, bool)
            or not 0 <= self.timebank_refill_amount_ms <= self.timebank_total_ms
        ):
            raise InvalidRoomSettingsError(
                "The time-bank refill must be between 0 and the configured total."
            )
        if (
            not _strict_positive_int(self.timebank_refill_every_hands)
            or self.timebank_refill_every_hands > MAX_TIMEBANK_REFILL_EVERY_HANDS
        ):
            raise InvalidRoomSettingsError(
                "The time-bank refill cadence must be between 1 and 100 hands."
            )
        if not isinstance(self.seating_approval_required, bool):
            raise InvalidRoomSettingsError("Seating approval must be a boolean.")
        if not isinstance(self.stand_up_enabled, bool):
            raise InvalidRoomSettingsError("Stand-Up enablement must be a boolean.")
        if (
            not _strict_positive_int(self.stand_up_penalty_per_recipient_chips)
            or self.stand_up_penalty_per_recipient_chips > MAX_STAND_UP_PENALTY_PER_RECIPIENT_CHIPS
        ):
            raise InvalidRoomSettingsError(
                "Stand-Up penalty must be positive JavaScript-safe whole chips."
            )
        if (
            not isinstance(self.max_seats, int)
            or isinstance(self.max_seats, bool)
            or self.max_seats != SIX_MAX_CAPACITY
        ):
            raise InvalidRoomSettingsError("Phase 8 rooms must have exactly six seats.")


@dataclass(frozen=True, slots=True)
class RoomSettingsUpdate:
    """Partial host settings update; all resulting settings are validated together."""

    room_name: str | SettingNotProvided = SETTING_NOT_PROVIDED
    small_blind: int | SettingNotProvided = SETTING_NOT_PROVIDED
    big_blind: int | SettingNotProvided = SETTING_NOT_PROVIDED
    default_starting_stack: int | SettingNotProvided = SETTING_NOT_PROVIDED
    action_time_ms: int | SettingNotProvided = SETTING_NOT_PROVIDED
    timebank_total_ms: int | SettingNotProvided = SETTING_NOT_PROVIDED
    timebank_refill_amount_ms: int | SettingNotProvided = SETTING_NOT_PROVIDED
    timebank_refill_every_hands: int | SettingNotProvided = SETTING_NOT_PROVIDED
    seating_approval_required: bool | SettingNotProvided = SETTING_NOT_PROVIDED
    stand_up_enabled: bool | SettingNotProvided = SETTING_NOT_PROVIDED
    stand_up_penalty_per_recipient_chips: int | SettingNotProvided = SETTING_NOT_PROVIDED
    password: str | SettingNotProvided | None = SETTING_NOT_PROVIDED


@dataclass(frozen=True, slots=True)
class _PasswordRecord:
    salt: bytes
    digest: bytes
    iterations: int

    def __post_init__(self) -> None:
        if not isinstance(self.salt, bytes) or not self.salt:
            raise InvalidRoomStateError("A password record requires a nonempty byte salt.")
        if not isinstance(self.digest, bytes) or not self.digest:
            raise InvalidRoomStateError("A password record requires a nonempty byte digest.")
        if (
            not isinstance(self.iterations, int)
            or isinstance(self.iterations, bool)
            or self.iterations <= 0
        ):
            raise InvalidRoomStateError("A password record requires positive iterations.")


@dataclass(frozen=True, slots=True)
class _RoomMember:
    guest_id: GuestId
    player_id: PlayerId
    nickname: str
    nickname_key: str
    status: RoomMemberStatus
    retained_stack: ChipStack | None
    join_order: int


@dataclass(frozen=True, slots=True)
class _SeatRequest:
    guest_id: GuestId
    seat_index: SeatIndex


@dataclass(frozen=True, slots=True)
class _SessionAccount:
    starting_stack: int
    external_added: int = 0
    external_removed: int = 0
    hands_played: int = 0


@dataclass(frozen=True, slots=True)
class _StackAdjustmentLedgerEntry:
    sequence: int
    adjustment_type: StackAdjustmentType
    target_nickname: str
    target_seat_index: int | None
    delta: int
    resulting_stack: int
    initiated_by_host: bool
    initiator_seat_index: int | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class _StackAdjustmentReceipt:
    target_guest_id: GuestId
    adjustment_type: StackAdjustmentType
    amount: int
    reason: str | None
    expected_next_hand_number: int
    expected_ledger_sequence: int
    ledger_sequence: int


@dataclass(frozen=True, slots=True)
class RoomSettingsSnapshot:
    """Public immutable settings without password verification material."""

    room_name: str
    small_blind: int
    big_blind: int
    default_starting_stack: int
    action_time_ms: int
    timebank_total_ms: int
    timebank_refill_amount_ms: int
    timebank_refill_every_hands: int
    seating_approval_required: bool
    stand_up_enabled: bool
    stand_up_penalty_per_recipient_chips: int
    max_seats: int
    password_protected: bool


@dataclass(frozen=True, slots=True)
class MemberSnapshot:
    """Public immutable state for one current room member."""

    guest_id: GuestId
    nickname: str
    status: RoomMemberStatus
    is_host: bool
    stack: int | None


@dataclass(frozen=True, slots=True)
class SeatSnapshot:
    """Public immutable seat projection keyed by guest identity, not PlayerId."""

    seat_index: int
    guest_id: GuestId | None
    nickname: str | None
    stack: int | None


@dataclass(frozen=True, slots=True)
class SeatRequestSnapshot:
    """Public immutable pending request for one specific seat."""

    guest_id: GuestId
    nickname: str
    seat_index: int


@dataclass(frozen=True, slots=True)
class StackAdjustmentSnapshot:
    """One immutable public adjustment entry without internal identity values."""

    sequence: int
    adjustment_type: StackAdjustmentType
    target_nickname: str
    target_seat_index: int | None
    delta: int
    resulting_stack: int
    initiated_by_host: bool
    initiator_seat_index: int | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class PlayerSessionSummarySnapshot:
    """Public per-player session accounting derived from current state."""

    nickname: str
    seat_index: int | None
    current_stack: int
    starting_stack: int
    external_added: int
    external_removed: int
    poker_net: int
    hands_played: int


@dataclass(frozen=True, slots=True)
class SessionAccountingSnapshot:
    """Room-local adjustment history and current session summaries."""

    ledger_sequence: int
    adjustments: tuple[StackAdjustmentSnapshot, ...]
    players: tuple[PlayerSessionSummarySnapshot, ...]


@dataclass(frozen=True, slots=True)
class StandUpParticipantSnapshot:
    """Seat-only public state for one frozen Stand-Up participant."""

    seat_index: int
    is_cleared: bool


@dataclass(frozen=True, slots=True)
class StandUpRoundSnapshot:
    """Public active-round state without internal player identities."""

    start_hand_number: int
    last_processed_hand_number: int
    penalty_per_recipient_chips: int
    participants: tuple[StandUpParticipantSnapshot, ...]


@dataclass(frozen=True, slots=True)
class StandUpTransferSnapshot:
    """One public seat-to-seat transfer from a resolved round."""

    from_seat_index: int
    to_seat_index: int
    chips: int


@dataclass(frozen=True, slots=True)
class StandUpResolutionSnapshot:
    """Seat-oriented terminal resolution suitable for public transport."""

    start_hand_number: int
    hand_number: int
    participant_seat_indexes: tuple[int, ...]
    squid_seat_index: int
    penalty_per_recipient_chips: int
    intended_total: int
    actual_total: int
    shortfall: int
    transfers: tuple[StandUpTransferSnapshot, ...]


@dataclass(frozen=True, slots=True)
class StandUpCancellationSnapshot:
    """Seat-oriented terminal cancellation suitable for public transport."""

    start_hand_number: int
    last_processed_hand_number: int
    participants: tuple[StandUpParticipantSnapshot, ...]
    reason: StandUpCancelReason


StandUpResultSnapshot = StandUpResolutionSnapshot | StandUpCancellationSnapshot


@dataclass(frozen=True, slots=True)
class StandUpStateSnapshot:
    """Current and most recent terminal Stand-Up state."""

    active_round: StandUpRoundSnapshot | None
    last_result: StandUpResultSnapshot | None


@dataclass(frozen=True, slots=True)
class RoomSnapshot:
    """Transport-safe immutable room state without poker or password secrets."""

    room_id: RoomId
    room_code: str
    status: RoomStatus
    host_guest_id: GuestId
    is_paused: bool
    settings: RoomSettingsSnapshot
    members: tuple[MemberSnapshot, ...]
    seats: tuple[SeatSnapshot, ...]
    seat_requests: tuple[SeatRequestSnapshot, ...]
    stand_up: StandUpStateSnapshot
    session: SessionAccountingSnapshot


class _Room:
    """Private mutable candidate aggregate; only RoomService returns snapshots."""

    __slots__ = (
        "active_hand",
        "host_guest_id",
        "is_paused",
        "last_hand",
        "last_stand_up_result",
        "members",
        "next_adjustment_sequence",
        "next_hand_number",
        "next_join_order",
        "password_record",
        "room_code",
        "room_id",
        "seat_requests",
        "session_accounts",
        "settings",
        "stack_adjustment_ledger",
        "stack_adjustment_receipts",
        "stand_up_round",
        "status",
        "table",
        "timebank_balances_ms",
        "timebank_hands_since_refill",
    )

    def __init__(
        self,
        *,
        room_id: RoomId,
        room_code: str,
        host_guest_id: GuestId,
        settings: RoomSettings,
        password_record: _PasswordRecord | None,
        host_player_id: PlayerId,
        host_nickname: str,
    ) -> None:
        normalized_nickname = normalize_nickname(host_nickname)
        self.room_id = room_id
        self.room_code = normalize_room_code(room_code)
        self.host_guest_id = host_guest_id
        self.is_paused = False
        self.settings = settings
        self.password_record = password_record
        self.status = RoomStatus.OPEN
        self.table = TableState.six_max()
        self.active_hand: _ActiveHand | None = None
        self.last_hand: _CompletedHandRecord | None = None
        self.stand_up_round: StandUpRound | None = None
        self.last_stand_up_result: StandUpResolution | StandUpCancellation | None = None
        self.next_hand_number = 1
        self.next_adjustment_sequence = 1
        self.session_accounts: dict[PlayerId, _SessionAccount] = {}
        self.stack_adjustment_ledger: tuple[_StackAdjustmentLedgerEntry, ...] = ()
        self.stack_adjustment_receipts: dict[tuple[GuestId, str], _StackAdjustmentReceipt] = {}
        self.timebank_balances_ms: dict[PlayerId, int] = {}
        self.timebank_hands_since_refill: dict[PlayerId, int] = {}
        self.members = {
            host_guest_id: _RoomMember(
                guest_id=host_guest_id,
                player_id=host_player_id,
                nickname=normalized_nickname,
                nickname_key=normalized_nickname.casefold(),
                status=RoomMemberStatus.IN_ROOM,
                retained_stack=None,
                join_order=0,
            )
        }
        self.seat_requests: dict[GuestId, _SeatRequest] = {}
        self.next_join_order = 1
        self.validate()

    def copy(self) -> _Room:
        """Copy all room and private gameplay execution state independently."""
        candidate = object.__new__(_Room)
        candidate.room_id = self.room_id
        candidate.room_code = self.room_code
        candidate.host_guest_id = self.host_guest_id
        candidate.is_paused = self.is_paused
        candidate.settings = self.settings
        candidate.password_record = self.password_record
        candidate.status = self.status
        candidate.active_hand = None if self.active_hand is None else self.active_hand.copy()
        candidate.last_hand = self.last_hand
        candidate.stand_up_round = self.stand_up_round
        candidate.last_stand_up_result = self.last_stand_up_result
        candidate.next_hand_number = self.next_hand_number
        candidate.next_adjustment_sequence = self.next_adjustment_sequence
        candidate.session_accounts = dict(self.session_accounts)
        candidate.stack_adjustment_ledger = self.stack_adjustment_ledger
        candidate.stack_adjustment_receipts = dict(self.stack_adjustment_receipts)
        candidate.timebank_balances_ms = self.timebank_balances_ms.copy()
        candidate.timebank_hands_since_refill = self.timebank_hands_since_refill.copy()
        candidate.members = dict(self.members)
        candidate.seat_requests = dict(self.seat_requests)
        candidate.next_join_order = self.next_join_order
        candidate.table = self.table.copy()
        return candidate

    def require_actor(self, actor: GuestId) -> _RoomMember:
        try:
            return self.members[actor]
        except KeyError:
            raise NotRoomMemberError(f"Guest {actor.value!r} is not a room member.") from None

    def require_host(self, actor: GuestId) -> None:
        self.require_actor(actor)
        if actor != self.host_guest_id:
            raise NotRoomHostError("Only the room host may perform this operation.")

    def require_not_closed(self) -> None:
        if self.status is RoomStatus.CLOSED:
            raise RoomClosedError("The room is closed and terminal.")

    def add_member(self, *, guest_id: GuestId, player_id: PlayerId, nickname: str) -> None:
        self.require_not_closed()
        if guest_id in self.members:
            raise DuplicateMembershipError("The guest is already a current room member.")
        normalized = normalize_nickname(nickname)
        nickname_key = normalized.casefold()
        if any(member.nickname_key == nickname_key for member in self.members.values()):
            raise DuplicateNicknameError("That nickname is already in use in this room.")
        if any(member.player_id == player_id for member in self.members.values()):
            raise InvalidRoomStateError("Room-specific player IDs must be unique.")
        self.members[guest_id] = _RoomMember(
            guest_id=guest_id,
            player_id=player_id,
            nickname=normalized,
            nickname_key=nickname_key,
            status=RoomMemberStatus.IN_ROOM,
            retained_stack=None,
            join_order=self.next_join_order,
        )
        self.next_join_order += 1

    def request_seat(self, *, actor: GuestId, seat_index: int) -> None:
        self.require_not_closed()
        member = self.require_actor(actor)
        requested = self._checked_seat_index(seat_index)
        if member.status is RoomMemberStatus.SEATED:
            raise MemberAlreadySeatedError("A seated member cannot request another seat.")
        if actor in self.seat_requests:
            raise DuplicateSeatRequestError("A member may have only one pending seat request.")
        if self.table.seat_at(requested).occupant is not None:
            raise RoomSeatOccupiedError(f"Seat {requested.value} is occupied.")
        if any(request.seat_index == requested for request in self.seat_requests.values()):
            raise RoomSeatAlreadyRequestedError(
                f"Seat {requested.value} already has a pending request."
            )
        if self.settings.seating_approval_required:
            self.seat_requests[actor] = _SeatRequest(actor, requested)
            return
        if self.status is RoomStatus.HAND_IN_PROGRESS:
            raise ActiveHandMutationError(operation="seat a member automatically")
        self._seat_member(member=member, seat_index=requested)

    def approve_seat_request(self, *, target: GuestId) -> None:
        self.require_not_closed()
        if self.status is RoomStatus.HAND_IN_PROGRESS:
            raise ActiveHandMutationError(operation="approve a seat request")
        try:
            request = self.seat_requests[target]
        except KeyError:
            raise SeatRequestNotFoundError("The member has no pending seat request.") from None
        try:
            member = self.members[target]
        except KeyError:
            raise MemberNotFoundError("The requested member is no longer in the room.") from None
        if member.status is RoomMemberStatus.SEATED:
            raise MemberAlreadySeatedError("The requesting member is already seated.")
        if self.table.seat_at(request.seat_index).occupant is not None:
            raise RoomSeatOccupiedError(f"Seat {request.seat_index.value} is occupied.")
        self._seat_member(member=member, seat_index=request.seat_index)
        self.seat_requests.pop(target)

    def reject_seat_request(self, *, target: GuestId) -> None:
        self.require_not_closed()
        if target not in self.seat_requests:
            raise SeatRequestNotFoundError("The member has no pending seat request.")
        self.seat_requests.pop(target)

    def stand_up(self, *, actor: GuestId) -> None:
        self.require_not_closed()
        member = self.require_actor(actor)
        if member.status is not RoomMemberStatus.SEATED:
            raise MemberNotSeatedError("The member is not seated.")
        if self.status is RoomStatus.HAND_IN_PROGRESS:
            raise ActiveHandMutationError(operation="stand up")
        removed = self._leave_table(member)
        self.cancel_stand_up_for(member.player_id, StandUpCancelReason.PARTICIPANT_VACATED_SEAT)
        self.members[actor] = replace(
            member,
            status=RoomMemberStatus.IN_ROOM,
            retained_stack=removed.stack,
        )

    def leave(self, *, actor: GuestId) -> None:
        self.require_not_closed()
        member = self.require_actor(actor)
        if actor == self.host_guest_id:
            raise HostCannotLeaveRoomError("The host must close the room instead of leaving.")
        if member.status is RoomMemberStatus.SEATED:
            if self.status is RoomStatus.HAND_IN_PROGRESS:
                raise ActiveHandMutationError(operation="leave while seated")
            self._leave_table(member)
        self.seat_requests.pop(actor, None)
        self.members.pop(actor)
        self.session_accounts.pop(member.player_id, None)
        self.timebank_balances_ms.pop(member.player_id, None)
        self.timebank_hands_since_refill.pop(member.player_id, None)
        self.cancel_stand_up_for(member.player_id, StandUpCancelReason.PARTICIPANT_LEFT)

    def kick(self, *, target: GuestId) -> None:
        self.require_not_closed()
        if target == self.host_guest_id:
            raise CannotKickHostError("The room host cannot be kicked.")
        try:
            member = self.members[target]
        except KeyError:
            raise MemberNotFoundError("The kick target is not a current room member.") from None
        if member.status is RoomMemberStatus.SEATED:
            if self.status is RoomStatus.HAND_IN_PROGRESS:
                raise ActiveHandMutationError(operation="kick a seated member")
            self._leave_table(member)
        self.seat_requests.pop(target, None)
        self.members.pop(target)
        self.session_accounts.pop(member.player_id, None)
        self.timebank_balances_ms.pop(member.player_id, None)
        self.timebank_hands_since_refill.pop(member.player_id, None)
        self.cancel_stand_up_for(member.player_id, StandUpCancelReason.PARTICIPANT_KICKED)

    def update_settings(
        self,
        *,
        settings: RoomSettings,
        password_record: _PasswordRecord | SettingNotProvided | None,
    ) -> None:
        self.require_not_closed()
        between_hand_changed = (
            settings.small_blind != self.settings.small_blind
            or settings.big_blind != self.settings.big_blind
            or settings.default_starting_stack != self.settings.default_starting_stack
            or settings.action_time_ms != self.settings.action_time_ms
            or settings.timebank_total_ms != self.settings.timebank_total_ms
            or settings.timebank_refill_amount_ms != self.settings.timebank_refill_amount_ms
            or settings.timebank_refill_every_hands != self.settings.timebank_refill_every_hands
        )
        if self.status is RoomStatus.HAND_IN_PROGRESS and between_hand_changed:
            raise ActiveHandMutationError(operation="change gameplay settings")
        if (
            self.settings.stand_up_enabled
            and not settings.stand_up_enabled
            and self.stand_up_round is not None
        ):
            self.last_stand_up_result = self.stand_up_round.cancel(StandUpCancelReason.DISABLED)
            self.stand_up_round = None
        timer_settings_changed = (
            settings.timebank_total_ms != self.settings.timebank_total_ms
            or settings.timebank_refill_amount_ms != self.settings.timebank_refill_amount_ms
            or settings.timebank_refill_every_hands != self.settings.timebank_refill_every_hands
        )
        self.settings = settings
        if timer_settings_changed:
            player_ids = {member.player_id for member in self.members.values()}
            self.timebank_balances_ms = {
                player_id: settings.timebank_total_ms for player_id in player_ids
            }
            self.timebank_hands_since_refill = {player_id: 0 for player_id in player_ids}
        if password_record is not SETTING_NOT_PROVIDED:
            self.password_record = password_record

    def close(self) -> None:
        self.require_not_closed()
        if self.status is RoomStatus.HAND_IN_PROGRESS:
            raise ActiveHandMutationError(operation="close the room")
        self.seat_requests.clear()
        if self.stand_up_round is not None:
            self.last_stand_up_result = self.stand_up_round.cancel(StandUpCancelReason.ROOM_CLOSED)
            self.stand_up_round = None
        self.status = RoomStatus.CLOSED

    def adjust_stack(
        self,
        *,
        actor: GuestId,
        target: GuestId,
        adjustment_type: StackAdjustmentType,
        amount: int,
        reason: str | None,
        command_id: str,
        expected_next_hand_number: int,
        expected_ledger_sequence: int,
    ) -> bool:
        """Apply one external stack adjustment, returning false for an exact replay."""
        self.require_host(actor)
        self.require_not_closed()
        normalized_command_id = command_id.strip() if isinstance(command_id, str) else ""
        if not normalized_command_id or len(normalized_command_id) > 64:
            raise InvalidStackAdjustmentError("A valid command ID is required.")
        if not isinstance(adjustment_type, StackAdjustmentType):
            raise InvalidStackAdjustmentError("The adjustment type is invalid.")
        if not isinstance(amount, int) or isinstance(amount, bool):
            raise InvalidStackAdjustmentError("An adjustment amount must be an integer.")
        normalized_reason = normalize_adjustment_reason(reason)
        proposed_receipt = _StackAdjustmentReceipt(
            target_guest_id=target,
            adjustment_type=adjustment_type,
            amount=amount,
            reason=normalized_reason,
            expected_next_hand_number=expected_next_hand_number,
            expected_ledger_sequence=expected_ledger_sequence,
            ledger_sequence=0,
        )
        receipt_key = (actor, normalized_command_id)
        existing_receipt = self.stack_adjustment_receipts.get(receipt_key)
        if existing_receipt is not None:
            if replace(existing_receipt, ledger_sequence=0) == proposed_receipt:
                return False
            raise StackAdjustmentCommandConflictError(
                "That stack-adjustment command ID was already used."
            )
        if self.status is RoomStatus.HAND_IN_PROGRESS or self.active_hand is not None:
            raise ActiveHandMutationError(operation="adjust a player stack")
        if self.is_paused:
            raise InvalidStackAdjustmentError("A paused room cannot adjust player stacks.")
        if (
            not isinstance(expected_next_hand_number, int)
            or isinstance(expected_next_hand_number, bool)
            or expected_next_hand_number != self.next_hand_number
            or not isinstance(expected_ledger_sequence, int)
            or isinstance(expected_ledger_sequence, bool)
            or expected_ledger_sequence != self.next_adjustment_sequence - 1
        ):
            raise StaleHandVersionError("The room accounting state changed.")
        if adjustment_type in (StackAdjustmentType.REBUY, StackAdjustmentType.CASH_OUT):
            if amount <= 0:
                raise InvalidStackAdjustmentError("Rebuy and cash-out amounts must be positive.")
            delta = amount if adjustment_type is StackAdjustmentType.REBUY else -amount
        else:
            if amount == 0:
                raise InvalidStackAdjustmentError("A correction must be nonzero.")
            delta = amount
        if abs(delta) > MAX_CHIP_STACK:
            raise InvalidStackAdjustmentError("The adjustment amount exceeds the chip limit.")
        try:
            member = self.members[target]
        except KeyError:
            raise MemberNotFoundError("The adjustment target is not a current member.") from None
        account = self.session_accounts.get(member.player_id)
        if account is None:
            raise StackAdjustmentTargetError(
                "The adjustment target has not received a session stack."
            )
        current_stack, target_seat = self._member_stack_and_seat(member)
        resulting_stack = current_stack + delta
        if not 0 <= resulting_stack <= MAX_CHIP_STACK:
            raise InvalidStackAdjustmentError(
                "The resulting stack must stay within the safe chip range."
            )
        current_total = sum(
            self._member_stack_and_seat(current)[0]
            for current in self.members.values()
            if current.player_id in self.session_accounts
        )
        if not 0 <= current_total + delta <= MAX_SAFE_INTEGER:
            raise InvalidStackAdjustmentError(
                "The adjustment would exceed the room's safe chip total."
            )
        external_added = account.external_added + max(delta, 0)
        external_removed = account.external_removed + max(-delta, 0)
        poker_net = resulting_stack - account.starting_stack - (external_added - external_removed)
        if (
            external_added > MAX_SAFE_INTEGER
            or external_removed > MAX_SAFE_INTEGER
            or abs(poker_net) > MAX_SAFE_INTEGER
        ):
            raise InvalidStackAdjustmentError(
                "The session accounting totals would exceed safe integer bounds."
            )
        if member.status is RoomMemberStatus.SEATED:
            seat_index = self.table.find_player(member.player_id)
            if seat_index is None:
                raise InvalidRoomStateError("A seated adjustment target has no table seat.")
            self.table.leave_seat(player_id=member.player_id)
            self.table.seat_player(
                seat_index=seat_index,
                player_id=member.player_id,
                stack=ChipStack(resulting_stack),
                status=(
                    ParticipationStatus.SITTING_IN
                    if resulting_stack > 0
                    else ParticipationStatus.SITTING_OUT
                ),
            )
        else:
            self.members[target] = replace(member, retained_stack=ChipStack(resulting_stack))
        self.session_accounts[member.player_id] = replace(
            account,
            external_added=external_added,
            external_removed=external_removed,
        )
        initiator = self.members[actor]
        initiator_seat = self.table.find_player(initiator.player_id)
        sequence = self.next_adjustment_sequence
        self.stack_adjustment_ledger += (
            _StackAdjustmentLedgerEntry(
                sequence=sequence,
                adjustment_type=adjustment_type,
                target_nickname=member.nickname,
                target_seat_index=None if target_seat is None else target_seat.value,
                delta=delta,
                resulting_stack=resulting_stack,
                initiated_by_host=True,
                initiator_seat_index=(None if initiator_seat is None else initiator_seat.value),
                reason=normalized_reason,
            ),
        )
        self.stack_adjustment_receipts[receipt_key] = replace(
            proposed_receipt,
            ledger_sequence=sequence,
        )
        self.next_adjustment_sequence += 1
        return True

    def record_completed_hand(self, player_ids: set[PlayerId]) -> None:
        """Advance per-player hand counts after a reconciled settlement."""
        for player_id in player_ids:
            account = self.session_accounts.get(player_id)
            if account is None:
                raise InvalidRoomStateError("A hand participant lacks session accounting.")
            if account.hands_played >= MAX_SAFE_INTEGER:
                raise InvalidRoomStateError("A session hand count exceeded safe bounds.")
            self.session_accounts[player_id] = replace(
                account,
                hands_played=account.hands_played + 1,
            )

    def _member_stack_and_seat(self, member: _RoomMember) -> tuple[int, SeatIndex | None]:
        if member.status is RoomMemberStatus.SEATED:
            seat_index = self.table.find_player(member.player_id)
            if seat_index is None:
                raise InvalidRoomStateError("A seated member has no table seat.")
            occupant = self.table.seat_at(seat_index).occupant
            if occupant is None:
                raise InvalidRoomStateError("A seated member has no table occupant.")
            return occupant.stack.chips, seat_index
        if member.retained_stack is None:
            raise StackAdjustmentTargetError("The member has no session stack.")
        return member.retained_stack.chips, None

    def cancel_stand_up_for(self, player_id: PlayerId, reason: StandUpCancelReason) -> None:
        round_state = self.stand_up_round
        if round_state is not None and any(
            participant.player_id == player_id for participant in round_state.participants
        ):
            self.last_stand_up_result = round_state.cancel(reason)
            self.stand_up_round = None

    @staticmethod
    def _stand_up_participants(
        participants: tuple[StandUpParticipant, ...],
        cleared_player_ids: frozenset[PlayerId],
    ) -> tuple[StandUpParticipantSnapshot, ...]:
        return tuple(
            StandUpParticipantSnapshot(
                seat_index=participant.seat_index.value,
                is_cleared=participant.player_id in cleared_player_ids,
            )
            for participant in participants
        )

    def _stand_up_snapshot(self) -> StandUpStateSnapshot:
        active = self.stand_up_round
        active_snapshot = (
            None
            if active is None
            else StandUpRoundSnapshot(
                start_hand_number=active.start_hand_number,
                last_processed_hand_number=active.last_processed_hand_number,
                penalty_per_recipient_chips=active.penalty_per_recipient_chips,
                participants=self._stand_up_participants(
                    active.participants, active.cleared_player_ids
                ),
            )
        )
        result = self.last_stand_up_result
        result_snapshot: StandUpResultSnapshot | None
        if isinstance(result, StandUpResolution):
            seats_by_player = {
                participant.player_id: participant.seat_index.value
                for participant in result.participants
            }
            result_snapshot = StandUpResolutionSnapshot(
                start_hand_number=result.start_hand_number,
                hand_number=result.hand_number,
                participant_seat_indexes=tuple(
                    participant.seat_index.value for participant in result.participants
                ),
                squid_seat_index=seats_by_player[result.squid],
                penalty_per_recipient_chips=result.penalty_per_recipient_chips,
                intended_total=result.intended_total,
                actual_total=result.actual_total,
                shortfall=result.shortfall,
                transfers=tuple(
                    StandUpTransferSnapshot(
                        from_seat_index=seats_by_player[transfer.from_player_id],
                        to_seat_index=seats_by_player[transfer.to_player_id],
                        chips=transfer.chips,
                    )
                    for transfer in result.transfers
                ),
            )
        elif isinstance(result, StandUpCancellation):
            result_snapshot = StandUpCancellationSnapshot(
                start_hand_number=result.start_hand_number,
                last_processed_hand_number=result.last_processed_hand_number,
                participants=self._stand_up_participants(
                    result.participants, result.cleared_player_ids
                ),
                reason=result.reason,
            )
        else:
            result_snapshot = None
        return StandUpStateSnapshot(active_round=active_snapshot, last_result=result_snapshot)

    def _session_snapshot(self) -> SessionAccountingSnapshot:
        ordered_members = sorted(self.members.values(), key=lambda member: member.join_order)
        summaries: list[PlayerSessionSummarySnapshot] = []
        for member in ordered_members:
            account = self.session_accounts.get(member.player_id)
            if account is None:
                continue
            current_stack, seat_index = self._member_stack_and_seat(member)
            summaries.append(
                PlayerSessionSummarySnapshot(
                    nickname=member.nickname,
                    seat_index=None if seat_index is None else seat_index.value,
                    current_stack=current_stack,
                    starting_stack=account.starting_stack,
                    external_added=account.external_added,
                    external_removed=account.external_removed,
                    poker_net=(
                        current_stack
                        - account.starting_stack
                        - (account.external_added - account.external_removed)
                    ),
                    hands_played=account.hands_played,
                )
            )
        return SessionAccountingSnapshot(
            ledger_sequence=self.next_adjustment_sequence - 1,
            adjustments=tuple(
                StackAdjustmentSnapshot(
                    sequence=entry.sequence,
                    adjustment_type=entry.adjustment_type,
                    target_nickname=entry.target_nickname,
                    target_seat_index=entry.target_seat_index,
                    delta=entry.delta,
                    resulting_stack=entry.resulting_stack,
                    initiated_by_host=entry.initiated_by_host,
                    initiator_seat_index=entry.initiator_seat_index,
                    reason=entry.reason,
                )
                for entry in self.stack_adjustment_ledger
            ),
            players=tuple(summaries),
        )

    def snapshot(self) -> RoomSnapshot:
        player_to_member = {member.player_id: member for member in self.members.values()}
        stack_by_guest: dict[GuestId, int] = {}
        seat_snapshots: list[SeatSnapshot] = []
        for seat in self.table.seats:
            occupant = seat.occupant
            if occupant is None:
                seat_snapshots.append(SeatSnapshot(seat.index.value, None, None, None))
                continue
            member = player_to_member.get(occupant.player_id)
            if member is None:
                raise InvalidRoomStateError("A table player has no room-member mapping.")
            stack_by_guest[member.guest_id] = occupant.stack.chips
            seat_snapshots.append(
                SeatSnapshot(
                    seat.index.value,
                    member.guest_id,
                    member.nickname,
                    occupant.stack.chips,
                )
            )
        ordered_members = sorted(self.members.values(), key=lambda member: member.join_order)
        member_snapshots = tuple(
            MemberSnapshot(
                guest_id=member.guest_id,
                nickname=member.nickname,
                status=member.status,
                is_host=member.guest_id == self.host_guest_id,
                stack=(
                    stack_by_guest.get(member.guest_id)
                    if member.status is RoomMemberStatus.SEATED
                    else (None if member.retained_stack is None else member.retained_stack.chips)
                ),
            )
            for member in ordered_members
        )
        request_snapshots = tuple(
            SeatRequestSnapshot(
                guest_id=request.guest_id,
                nickname=self.members[request.guest_id].nickname,
                seat_index=request.seat_index.value,
            )
            for request in sorted(
                self.seat_requests.values(),
                key=lambda item: (item.seat_index.value, item.guest_id.value),
            )
        )
        return RoomSnapshot(
            room_id=self.room_id,
            room_code=self.room_code,
            status=self.status,
            host_guest_id=self.host_guest_id,
            is_paused=self.is_paused,
            settings=RoomSettingsSnapshot(
                room_name=self.settings.room_name,
                small_blind=self.settings.small_blind,
                big_blind=self.settings.big_blind,
                default_starting_stack=self.settings.default_starting_stack,
                action_time_ms=self.settings.action_time_ms,
                timebank_total_ms=self.settings.timebank_total_ms,
                timebank_refill_amount_ms=self.settings.timebank_refill_amount_ms,
                timebank_refill_every_hands=self.settings.timebank_refill_every_hands,
                seating_approval_required=self.settings.seating_approval_required,
                stand_up_enabled=self.settings.stand_up_enabled,
                stand_up_penalty_per_recipient_chips=(
                    self.settings.stand_up_penalty_per_recipient_chips
                ),
                max_seats=self.settings.max_seats,
                password_protected=self.password_record is not None,
            ),
            members=member_snapshots,
            seats=tuple(seat_snapshots),
            seat_requests=request_snapshots,
            stand_up=self._stand_up_snapshot(),
            session=self._session_snapshot(),
        )

    def validate(self) -> None:
        if not isinstance(self.room_id, RoomId):
            raise InvalidRoomStateError("A room requires a RoomId.")
        if normalize_room_code(self.room_code) != self.room_code:
            raise InvalidRoomStateError("A room must retain its canonical room code.")
        if not isinstance(self.settings, RoomSettings) or not isinstance(self.status, RoomStatus):
            raise InvalidRoomStateError("A room requires validated settings and status.")
        if not isinstance(self.is_paused, bool):
            raise InvalidRoomStateError("Room pause state must be boolean.")
        if self.password_record is not None and not isinstance(
            self.password_record, _PasswordRecord
        ):
            raise InvalidRoomStateError("Room password state must be a password record or None.")
        if self.table.capacity != SIX_MAX_CAPACITY or self.settings.max_seats != SIX_MAX_CAPACITY:
            raise InvalidRoomStateError("Room and table capacity must both equal six.")
        if (
            not isinstance(self.next_hand_number, int)
            or isinstance(self.next_hand_number, bool)
            or self.next_hand_number < 1
        ):
            raise InvalidRoomStateError("The next hand number must be a positive integer.")
        if (
            not isinstance(self.next_adjustment_sequence, int)
            or isinstance(self.next_adjustment_sequence, bool)
            or not 1 <= self.next_adjustment_sequence <= MAX_SAFE_INTEGER + 1
        ):
            raise InvalidRoomStateError("The next adjustment sequence is invalid.")
        if len(self.stack_adjustment_ledger) != self.next_adjustment_sequence - 1 or any(
            entry.sequence != sequence
            for sequence, entry in enumerate(self.stack_adjustment_ledger, start=1)
        ):
            raise InvalidRoomStateError("Adjustment ledger sequences must be contiguous.")
        for entry in self.stack_adjustment_ledger:
            if (
                not isinstance(entry, _StackAdjustmentLedgerEntry)
                or not isinstance(entry.adjustment_type, StackAdjustmentType)
                or entry.target_nickname != normalize_nickname(entry.target_nickname)
                or (
                    entry.target_seat_index is not None
                    and not 0 <= entry.target_seat_index < SIX_MAX_CAPACITY
                )
                or not isinstance(entry.delta, int)
                or isinstance(entry.delta, bool)
                or entry.delta == 0
                or abs(entry.delta) > MAX_CHIP_STACK
                or not 0 <= entry.resulting_stack <= MAX_CHIP_STACK
                or entry.initiated_by_host is not True
                or (
                    entry.initiator_seat_index is not None
                    and not 0 <= entry.initiator_seat_index < SIX_MAX_CAPACITY
                )
                or entry.reason != normalize_adjustment_reason(entry.reason)
            ):
                raise InvalidRoomStateError("The adjustment ledger contains invalid data.")
        for key, receipt in self.stack_adjustment_receipts.items():
            actor, command_id = key
            if (
                not isinstance(actor, GuestId)
                or not isinstance(command_id, str)
                or not command_id
                or len(command_id) > 64
                or not isinstance(receipt, _StackAdjustmentReceipt)
                or not isinstance(receipt.target_guest_id, GuestId)
                or not isinstance(receipt.adjustment_type, StackAdjustmentType)
                or not isinstance(receipt.amount, int)
                or isinstance(receipt.amount, bool)
                or receipt.reason != normalize_adjustment_reason(receipt.reason)
                or not 1 <= receipt.ledger_sequence < self.next_adjustment_sequence
            ):
                raise InvalidRoomStateError("Stack-adjustment replay state is invalid.")
        if (self.status is RoomStatus.HAND_IN_PROGRESS) != (self.active_hand is not None):
            raise InvalidRoomStateError(
                "HAND_IN_PROGRESS status must correspond exactly to one active hand."
            )
        if self.status is RoomStatus.CLOSED and self.active_hand is not None:
            raise InvalidRoomStateError("A closed room cannot own an active hand.")
        if self.host_guest_id not in self.members:
            raise InvalidRoomStateError("The room host must be a current member.")
        if sum(member.guest_id == self.host_guest_id for member in self.members.values()) != 1:
            raise InvalidRoomStateError("A room must have exactly one host member.")
        if any(key != member.guest_id for key, member in self.members.items()):
            raise InvalidRoomStateError("Member dictionary keys must match member identities.")
        if any(
            not isinstance(member.guest_id, GuestId)
            or not isinstance(member.player_id, PlayerId)
            or not isinstance(member.status, RoomMemberStatus)
            or not isinstance(member.join_order, int)
            or isinstance(member.join_order, bool)
            or member.join_order < 0
            for member in self.members.values()
        ):
            raise InvalidRoomStateError("Room members contain invalid identity or status values.")
        player_ids = [member.player_id for member in self.members.values()]
        nickname_keys = [member.nickname_key for member in self.members.values()]
        join_orders = [member.join_order for member in self.members.values()]
        if len(set(player_ids)) != len(player_ids):
            raise InvalidRoomStateError("Room-specific player IDs must be unique.")
        if len(set(nickname_keys)) != len(nickname_keys):
            raise InvalidRoomStateError("Normalized room nicknames must be unique.")
        if len(set(join_orders)) != len(join_orders):
            raise InvalidRoomStateError("Member join ordering must be unique.")
        if (
            not isinstance(self.next_join_order, int)
            or isinstance(self.next_join_order, bool)
            or self.next_join_order < 0
        ):
            raise InvalidRoomStateError("The next member join order must be nonnegative.")
        if self.next_join_order <= max(join_orders, default=-1):
            raise InvalidRoomStateError("The next member join order must follow current members.")
        if any(
            member.nickname != normalize_nickname(member.nickname)
            or member.nickname_key != member.nickname.casefold()
            for member in self.members.values()
        ):
            raise InvalidRoomStateError("Member nicknames must remain canonical.")

        table_by_player = {
            seat.occupant.player_id: seat for seat in self.table.seats if seat.occupant is not None
        }
        if len(table_by_player) != self.table.occupied_count:
            raise InvalidRoomStateError("Table occupants must have unique player identities.")
        member_by_player = {member.player_id: member for member in self.members.values()}
        stack_owning_players = {
            member.player_id
            for member in self.members.values()
            if member.status is RoomMemberStatus.SEATED or member.retained_stack is not None
        }
        if set(self.session_accounts) != stack_owning_players:
            raise InvalidRoomStateError(
                "Every current session stack requires exactly one accounting baseline."
            )
        total_session_chips = 0
        for player_id, account in self.session_accounts.items():
            member = member_by_player.get(player_id)
            if member is None or not isinstance(account, _SessionAccount):
                raise InvalidRoomStateError("Session accounts must belong to current members.")
            current_stack, _ = self._member_stack_and_seat(member)
            poker_net = (
                current_stack
                - account.starting_stack
                - (account.external_added - account.external_removed)
            )
            if (
                not 0 <= account.starting_stack <= MAX_CHIP_STACK
                or not 0 <= account.external_added <= MAX_SAFE_INTEGER
                or not 0 <= account.external_removed <= MAX_SAFE_INTEGER
                or not 0 <= account.hands_played <= MAX_SAFE_INTEGER
                or abs(poker_net) > MAX_SAFE_INTEGER
            ):
                raise InvalidRoomStateError("Session accounting exceeded safe bounds.")
            total_session_chips += current_stack
        if total_session_chips > MAX_SAFE_INTEGER:
            raise InvalidRoomStateError("Room chip totals must remain JavaScript-safe.")
        if not set(self.timebank_balances_ms) <= set(member_by_player) or any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 <= value <= self.settings.timebank_total_ms
            for value in self.timebank_balances_ms.values()
        ):
            raise InvalidRoomStateError("Stored time-bank balances must belong to room members.")
        if set(self.timebank_hands_since_refill) != set(self.timebank_balances_ms) or any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 <= value <= self.settings.timebank_refill_every_hands
            for value in self.timebank_hands_since_refill.values()
        ):
            raise InvalidRoomStateError("Stored time-bank refill counters are inconsistent.")
        if not set(table_by_player) <= set(member_by_player):
            raise InvalidRoomStateError("Every table player must map to a current member.")
        for member in self.members.values():
            is_on_table = member.player_id in table_by_player
            if member.status is RoomMemberStatus.SEATED:
                if not is_on_table or member.retained_stack is not None:
                    raise InvalidRoomStateError(
                        "A seated member requires one table seat and no retained stack."
                    )
            elif is_on_table:
                raise InvalidRoomStateError("An unseated member cannot occupy a table seat.")
            elif member.retained_stack is not None and not isinstance(
                member.retained_stack, ChipStack
            ):
                raise InvalidRoomStateError("A retained stack must be a ChipStack.")

        if any(key != request.guest_id for key, request in self.seat_requests.items()):
            raise InvalidRoomStateError("Seat-request keys must match guest identities.")
        requested_seats: set[SeatIndex] = set()
        for request in self.seat_requests.values():
            if not isinstance(request, _SeatRequest) or not isinstance(
                request.seat_index, SeatIndex
            ):
                raise InvalidRoomStateError("Pending seat requests contain invalid values.")
            requested_member = self.members.get(request.guest_id)
            if requested_member is None:
                raise InvalidRoomStateError("Pending requests require current room members.")
            if requested_member.status is RoomMemberStatus.SEATED:
                raise InvalidRoomStateError("A seated member cannot retain a pending request.")
            if not 0 <= request.seat_index.value < SIX_MAX_CAPACITY:
                raise InvalidRoomStateError("Pending seat indexes must be between zero and five.")
            if request.seat_index in requested_seats:
                raise InvalidRoomStateError("Pending seat indexes must be unique.")
            if self.table.seat_at(request.seat_index).occupant is not None:
                raise InvalidRoomStateError("A pending request cannot target an occupied seat.")
            requested_seats.add(request.seat_index)
        if self.table.occupied_count > SIX_MAX_CAPACITY:
            raise InvalidRoomStateError("A room cannot exceed six occupied seats.")
        if self.status is RoomStatus.CLOSED and self.seat_requests:
            raise InvalidRoomStateError("Closed rooms cannot retain pending seat requests.")
        self._validate_gameplay_state(member_by_player, table_by_player)
        self._validate_stand_up_state(member_by_player, table_by_player)

    def _validate_stand_up_state(
        self,
        member_by_player: dict[PlayerId, _RoomMember],
        table_by_player: dict[PlayerId, Seat],
    ) -> None:
        result = self.last_stand_up_result
        if result is not None and not isinstance(result, StandUpResolution | StandUpCancellation):
            raise InvalidRoomStateError("The last Stand-Up result must be terminal and immutable.")
        if result is not None:
            result_hand = (
                result.hand_number
                if isinstance(result, StandUpResolution)
                else result.last_processed_hand_number
            )
            if result_hand >= self.next_hand_number:
                raise InvalidRoomStateError("A Stand-Up result cannot refer to a future hand.")
        round_state = self.stand_up_round
        if round_state is None:
            return
        if not isinstance(round_state, StandUpRound) or not self.settings.stand_up_enabled:
            raise InvalidRoomStateError("An active Stand-Up round requires enabled room rules.")
        if self.status is RoomStatus.CLOSED:
            raise InvalidRoomStateError("A closed room cannot retain an active Stand-Up round.")
        if result is not None and result_hand >= round_state.start_hand_number:
            raise InvalidRoomStateError("A previous Stand-Up result must precede the active round.")
        expected_processed = (
            self.next_hand_number - 2 if self.active_hand is not None else self.next_hand_number - 1
        )
        if round_state.last_processed_hand_number != expected_processed:
            raise InvalidRoomStateError("The Stand-Up round is out of step with poker hands.")
        hand_ids = (
            None
            if self.active_hand is None
            else {item.player_id for item in self.active_hand.hand.snapshot.participants}
        )
        for participant in round_state.participants:
            member = member_by_player.get(participant.player_id)
            seat = table_by_player.get(participant.player_id)
            if (
                member is None
                or member.status is not RoomMemberStatus.SEATED
                or seat is None
                or seat.index != participant.seat_index
                or seat.occupant is None
                or seat.occupant.stack.chips == 0
                or (hand_ids is not None and participant.player_id not in hand_ids)
            ):
                raise InvalidRoomStateError("Stand-Up cohort members must retain eligible seats.")

    def _validate_gameplay_state(
        self,
        member_by_player: dict[PlayerId, _RoomMember],
        table_by_player: dict[PlayerId, Seat],
    ) -> None:
        active = self.active_hand
        if active is None:
            if self.is_paused:
                raise InvalidRoomStateError("A room without an active hand cannot be paused.")
            if self.last_hand is not None and self.last_hand.hand_number >= self.next_hand_number:
                raise InvalidRoomStateError("A completed hand number must already be consumed.")
            return
        if active.hand_number != self.next_hand_number - 1:
            raise InvalidRoomStateError("The active hand must be the most recently consumed hand.")
        if active.action_sequence < 0:
            raise InvalidRoomStateError("An action sequence cannot be negative.")
        if active.deadline is None:
            raise InvalidRoomStateError("An active hand requires a turn deadline.")
        if self.is_paused != (active.frozen_remaining_ms is not None):
            raise InvalidRoomStateError("Paused rooms must own exactly one frozen timer value.")
        if active.frozen_remaining_ms is not None and active.frozen_remaining_ms < 0:
            raise InvalidRoomStateError("Frozen timer time cannot be negative.")
        snapshot = active.hand.snapshot
        if snapshot.terminal:
            raise InvalidRoomStateError("A terminal hand cannot remain active in a room.")
        participant_ids = {item.player_id for item in snapshot.participants}
        identity_ids = {item.player_id for item in active.identities}
        identity_guests = {item.guest_id for item in active.identities}
        if (
            participant_ids != identity_ids
            or len(identity_ids) != len(active.identities)
            or len(identity_guests) != len(active.identities)
        ):
            raise InvalidRoomStateError(
                "Active-hand participant mappings must be exact and unique."
            )
        if set(active.timebank_remaining_ms) != participant_ids or any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 <= value <= active.timebank_total_ms
            for value in active.timebank_remaining_ms.values()
        ):
            raise InvalidRoomStateError("Active-hand timebank balances must match participants.")
        if set(active.timebank_hands_until_refill) != participant_ids or any(
            value is not None
            and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 1
                or value > self.settings.timebank_refill_every_hands
            )
            for value in active.timebank_hands_until_refill.values()
        ):
            raise InvalidRoomStateError("Active-hand refill cadence must match participants.")
        if (
            active.base_action_time_ms != self.settings.action_time_ms
            or active.timebank_total_ms != self.settings.timebank_total_ms
            or active.timebank_refill_amount_ms != self.settings.timebank_refill_amount_ms
        ):
            raise InvalidRoomStateError("Active-hand timer rules must match room settings.")
        for identity in active.identities:
            member = member_by_player.get(identity.player_id)
            seat = table_by_player.get(identity.player_id)
            participant = active.hand.participant(identity.player_id)
            if (
                member is None
                or member.guest_id != identity.guest_id
                or member.status is not RoomMemberStatus.SEATED
                or seat is None
                or seat.index != participant.seat_index
            ):
                raise InvalidRoomStateError(
                    "Every active participant must retain their member and table identity."
                )

    @staticmethod
    def _checked_seat_index(value: int) -> SeatIndex:
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 <= value < SIX_MAX_CAPACITY
        ):
            raise InvalidRoomSeatError("A room seat must be an integer from zero through five.")
        return SeatIndex(value)

    def _seat_member(self, *, member: _RoomMember, seat_index: SeatIndex) -> None:
        stack = (
            member.retained_stack
            if member.retained_stack is not None
            else ChipStack(self.settings.default_starting_stack)
        )
        if member.player_id not in self.session_accounts:
            current_total = sum(
                self._member_stack_and_seat(current)[0]
                for current in self.members.values()
                if current.player_id in self.session_accounts
            )
            if current_total + stack.chips > MAX_SAFE_INTEGER:
                raise RoomChipLimitError(
                    "The first session stack would exceed the room's safe chip total."
                )
            self.session_accounts[member.player_id] = _SessionAccount(starting_stack=stack.chips)
        try:
            self.table.seat_player(
                seat_index=seat_index,
                player_id=member.player_id,
                stack=stack,
                status=(
                    ParticipationStatus.SITTING_IN
                    if stack.chips > 0
                    else ParticipationStatus.SITTING_OUT
                ),
            )
        except (SeatOccupiedError, SeatOutOfRangeError) as error:
            raise RoomSeatOccupiedError(f"Seat {seat_index.value} cannot be occupied.") from error
        except PlayerAlreadySeatedError as error:
            raise MemberAlreadySeatedError("The room member is already seated.") from error
        self.members[member.guest_id] = replace(
            member,
            status=RoomMemberStatus.SEATED,
            retained_stack=None,
        )

    def _leave_table(self, member: _RoomMember) -> SeatedPlayer:
        try:
            return self.table.leave_seat(player_id=member.player_id)
        except PlayerNotSeatedError as error:
            raise MemberNotSeatedError("The room member is not seated.") from error
