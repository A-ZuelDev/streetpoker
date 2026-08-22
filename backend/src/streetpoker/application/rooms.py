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
    MemberAlreadySeatedError,
    MemberNotFoundError,
    MemberNotSeatedError,
    NotRoomHostError,
    NotRoomMemberError,
    RoomClosedError,
    RoomSeatAlreadyRequestedError,
    RoomSeatOccupiedError,
    SeatRequestNotFoundError,
)
from streetpoker.domain import (
    SIX_MAX_CAPACITY,
    ChipStack,
    ParticipationStatus,
    PlayerAlreadySeatedError,
    PlayerId,
    PlayerNotSeatedError,
    SeatedPlayer,
    SeatIndex,
    SeatOccupiedError,
    SeatOutOfRangeError,
    TableState,
)

ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ROOM_CODE_LENGTH = 8
MAX_NICKNAME_LENGTH = 24
MAX_ROOM_NAME_LENGTH = 60
DEFAULT_SMALL_BLIND = 50
DEFAULT_BIG_BLIND = 100
DEFAULT_STARTING_STACK = 10_000


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


def _strict_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


@dataclass(frozen=True, slots=True)
class RoomSettings:
    """Complete validated settings for one six-max room."""

    room_name: str
    small_blind: int = DEFAULT_SMALL_BLIND
    big_blind: int = DEFAULT_BIG_BLIND
    default_starting_stack: int = DEFAULT_STARTING_STACK
    seating_approval_required: bool = True
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
        ):
            raise InvalidRoomSettingsError(
                "The default starting stack must be at least the big blind."
            )
        if not isinstance(self.seating_approval_required, bool):
            raise InvalidRoomSettingsError("Seating approval must be a boolean.")
        if self.max_seats != SIX_MAX_CAPACITY:
            raise InvalidRoomSettingsError("Phase 8 rooms must have exactly six seats.")


@dataclass(frozen=True, slots=True)
class RoomSettingsUpdate:
    """Partial host settings update; all resulting settings are validated together."""

    room_name: str | SettingNotProvided = SETTING_NOT_PROVIDED
    small_blind: int | SettingNotProvided = SETTING_NOT_PROVIDED
    big_blind: int | SettingNotProvided = SETTING_NOT_PROVIDED
    default_starting_stack: int | SettingNotProvided = SETTING_NOT_PROVIDED
    seating_approval_required: bool | SettingNotProvided = SETTING_NOT_PROVIDED
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
class RoomSettingsSnapshot:
    """Public immutable settings without password verification material."""

    room_name: str
    small_blind: int
    big_blind: int
    default_starting_stack: int
    seating_approval_required: bool
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
class RoomSnapshot:
    """Transport-safe immutable room state without poker or password secrets."""

    room_id: RoomId
    room_code: str
    status: RoomStatus
    host_guest_id: GuestId
    settings: RoomSettingsSnapshot
    members: tuple[MemberSnapshot, ...]
    seats: tuple[SeatSnapshot, ...]
    seat_requests: tuple[SeatRequestSnapshot, ...]


class _Room:
    """Private mutable candidate aggregate; only RoomService returns snapshots."""

    __slots__ = (
        "host_guest_id",
        "members",
        "next_join_order",
        "password_record",
        "room_code",
        "room_id",
        "seat_requests",
        "settings",
        "status",
        "table",
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
        self.settings = settings
        self.password_record = password_record
        self.status = RoomStatus.OPEN
        self.table = TableState.six_max()
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
        """Copy valid Phase 8 room state using only public TableState operations."""
        if self.table.button_position is not None:
            raise InvalidRoomStateError(
                "Phase 8 room tables cannot have a dealer button before gameplay orchestration."
            )
        candidate = object.__new__(_Room)
        candidate.room_id = self.room_id
        candidate.room_code = self.room_code
        candidate.host_guest_id = self.host_guest_id
        candidate.settings = self.settings
        candidate.password_record = self.password_record
        candidate.status = self.status
        candidate.members = dict(self.members)
        candidate.seat_requests = dict(self.seat_requests)
        candidate.next_join_order = self.next_join_order
        candidate.table = TableState.six_max()
        for seat in self.table.seats:
            occupant = seat.occupant
            if occupant is not None:
                candidate.table.seat_player(
                    seat_index=seat.index,
                    player_id=occupant.player_id,
                    stack=occupant.stack,
                    status=occupant.status,
                )
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
        )
        if self.status is RoomStatus.HAND_IN_PROGRESS and between_hand_changed:
            raise ActiveHandMutationError(operation="change blind or starting-stack settings")
        self.settings = settings
        if password_record is not SETTING_NOT_PROVIDED:
            self.password_record = password_record

    def close(self) -> None:
        self.require_not_closed()
        if self.status is RoomStatus.HAND_IN_PROGRESS:
            raise ActiveHandMutationError(operation="close the room")
        self.seat_requests.clear()
        self.status = RoomStatus.CLOSED

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
            settings=RoomSettingsSnapshot(
                room_name=self.settings.room_name,
                small_blind=self.settings.small_blind,
                big_blind=self.settings.big_blind,
                default_starting_stack=self.settings.default_starting_stack,
                seating_approval_required=self.settings.seating_approval_required,
                max_seats=self.settings.max_seats,
                password_protected=self.password_record is not None,
            ),
            members=member_snapshots,
            seats=tuple(seat_snapshots),
            seat_requests=request_snapshots,
        )

    def validate(self) -> None:
        if not isinstance(self.room_id, RoomId):
            raise InvalidRoomStateError("A room requires a RoomId.")
        if normalize_room_code(self.room_code) != self.room_code:
            raise InvalidRoomStateError("A room must retain its canonical room code.")
        if not isinstance(self.settings, RoomSettings) or not isinstance(self.status, RoomStatus):
            raise InvalidRoomStateError("A room requires validated settings and status.")
        if self.password_record is not None and not isinstance(
            self.password_record, _PasswordRecord
        ):
            raise InvalidRoomStateError("Room password state must be a password record or None.")
        if self.table.capacity != SIX_MAX_CAPACITY or self.settings.max_seats != SIX_MAX_CAPACITY:
            raise InvalidRoomStateError("Room and table capacity must both equal six.")
        if self.table.button_position is not None:
            raise InvalidRoomStateError("Phase 8 room tables cannot have a dealer button.")
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
        try:
            self.table.seat_player(
                seat_index=seat_index,
                player_id=member.player_id,
                stack=stack,
                status=ParticipationStatus.SITTING_IN,
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
