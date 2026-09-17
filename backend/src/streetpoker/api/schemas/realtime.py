"""Strict WebSocket messages and explicit viewer-safe snapshot serialization."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator

from streetpoker.application import (
    ActiveHandPlayerSnapshot,
    ActiveHandSnapshot,
    CallSnapshot,
    CardSnapshot,
    CompletedHandPlayerSnapshot,
    CompletedHandSnapshot,
    CompletedPotSnapshot,
    LegalActionSnapshot,
    MemberSnapshot,
    RoomSettingsSnapshot,
    RoomSnapshot,
    RoomViewSnapshot,
    SeatRequestSnapshot,
    SeatSnapshot,
    WagerBoundsSnapshot,
    WinnerShareSnapshot,
)

CommandId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
GuestToken = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9_-]{43}$"),
]
GuestIdText = Annotated[str, StringConstraints(min_length=1, max_length=128)]
NicknameText = Annotated[str, StringConstraints(max_length=128)]
PasswordText = Annotated[str, StringConstraints(max_length=128)]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
SeatNumber = Annotated[int, Field(strict=True, ge=0, le=5)]


class _StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ConnectRequest(_StrictInput):
    type: Literal["connect"]
    guest_token: GuestToken
    nickname: NicknameText | None = None
    password: PasswordText | None = None


class _Command(_StrictInput):
    command_id: CommandId


class StartHandCommand(_Command):
    type: Literal["start_hand"]
    hand_number: PositiveInt


class _GameplayCommand(_Command):
    hand_number: PositiveInt
    expected_action_sequence: NonNegativeInt


class FoldCommand(_GameplayCommand):
    type: Literal["fold"]


class CheckCommand(_GameplayCommand):
    type: Literal["check"]


class CallCommand(_GameplayCommand):
    type: Literal["call"]


class BetToCommand(_GameplayCommand):
    type: Literal["bet_to"]
    total: PositiveInt


class RaiseToCommand(_GameplayCommand):
    type: Literal["raise_to"]
    total: PositiveInt


class RequestSeatCommand(_Command):
    type: Literal["request_seat"]
    seat_index: SeatNumber


class ApproveSeatCommand(_Command):
    type: Literal["approve_seat"]
    target_guest_id: GuestIdText


class RejectSeatCommand(_Command):
    type: Literal["reject_seat"]
    target_guest_id: GuestIdText


class StandCommand(_Command):
    type: Literal["stand"]


class LeaveCommand(_Command):
    type: Literal["leave"]


class KickCommand(_Command):
    type: Literal["kick"]
    target_guest_id: GuestIdText


class UpdateSettingsCommand(_Command):
    type: Literal["update_settings"]
    room_name: str | None = None
    small_blind: PositiveInt | None = None
    big_blind: PositiveInt | None = None
    default_starting_stack: PositiveInt | None = None
    seating_approval_required: bool | None = None
    password: PasswordText | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> UpdateSettingsCommand:
        setting_fields = {
            "room_name",
            "small_blind",
            "big_blind",
            "default_starting_stack",
            "seating_approval_required",
            "password",
        }
        supplied = setting_fields & self.model_fields_set
        if not supplied:
            raise ValueError("At least one setting must be supplied.")
        nullable_only = {"password"}
        if any(getattr(self, name) is None for name in supplied - nullable_only):
            raise ValueError("Only password may be explicitly null.")
        return self


class CloseRoomCommand(_Command):
    type: Literal["close_room"]


ClientCommand = Annotated[
    StartHandCommand
    | FoldCommand
    | CheckCommand
    | CallCommand
    | BetToCommand
    | RaiseToCommand
    | RequestSeatCommand
    | ApproveSeatCommand
    | RejectSeatCommand
    | StandCommand
    | LeaveCommand
    | KickCommand
    | UpdateSettingsCommand
    | CloseRoomCommand,
    Field(discriminator="type"),
]

connect_request_adapter: TypeAdapter[ConnectRequest] = TypeAdapter(ConnectRequest)
client_command_adapter: TypeAdapter[ClientCommand] = TypeAdapter(ClientCommand)


class _Outbound(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CardDto(_Outbound):
    rank: str
    suit: str


class CallDto(_Outbound):
    chips: int
    total: int
    is_all_in: bool


class WagerBoundsDto(_Outbound):
    minimum_full_to: int
    maximum_to: int
    short_all_in_to: int | None


class LegalActionsDto(_Outbound):
    actor: str
    kinds: list[str]
    amount_to_call: int
    call: CallDto | None
    bet_to: WagerBoundsDto | None
    raise_to: WagerBoundsDto | None
    raise_reopened: bool


class ActiveHandPlayerDto(_Outbound):
    guest_id: str
    nickname: str
    seat_index: int
    status: str
    current_stack: int
    gross_committed: int
    street_committed: int
    hole_cards: list[CardDto] | None


class ActiveHandDto(_Outbound):
    hand_number: int
    action_sequence: int
    action_deadline_unix_ms: int
    phase: str
    button_seat: int
    small_blind_seat: int
    big_blind_seat: int
    board: list[CardDto]
    pot_chips: int
    players: list[ActiveHandPlayerDto]
    current_actor: str
    legal_actions: LegalActionsDto


class WinnerShareDto(_Outbound):
    guest_id: str
    chips: int
    receives_odd_chip: bool


class CompletedPotDto(_Outbound):
    pot_index: int
    amount: int
    winners: list[WinnerShareDto]


class CompletedHandPlayerDto(_Outbound):
    guest_id: str
    nickname: str
    seat_index: int
    folded: bool
    final_stack: int
    total_award: int
    gross_committed: int
    returned_excess: int
    hole_cards: list[CardDto] | None


class CompletedHandDto(_Outbound):
    hand_number: int
    final_action_sequence: int
    phase: str
    source: str
    button_seat: int
    board: list[CardDto]
    players: list[CompletedHandPlayerDto]
    pots: list[CompletedPotDto]


class RoomSettingsDto(_Outbound):
    room_name: str
    small_blind: int
    big_blind: int
    default_starting_stack: int
    seating_approval_required: bool
    max_seats: int
    password_protected: bool


class MemberDto(_Outbound):
    guest_id: str
    nickname: str
    status: str
    is_host: bool
    stack: int | None


class SeatDto(_Outbound):
    seat_index: int
    guest_id: str | None
    nickname: str | None
    stack: int | None


class SeatRequestDto(_Outbound):
    guest_id: str
    nickname: str
    seat_index: int


class RoomDto(_Outbound):
    room_id: str
    room_code: str
    status: str
    host_guest_id: str
    settings: RoomSettingsDto
    members: list[MemberDto]
    seats: list[SeatDto]
    seat_requests: list[SeatRequestDto]


class RoomViewDto(_Outbound):
    room: RoomDto
    next_hand_number: int
    active_hand: ActiveHandDto | None
    last_hand: CompletedHandDto | None


class ConnectedMessage(_Outbound):
    type: Literal["connected"] = "connected"
    guest_id: str
    room_code: str


class CommandAckMessage(_Outbound):
    type: Literal["command_ack"] = "command_ack"
    command_id: str


class StateMessage(_Outbound):
    type: Literal["state"] = "state"
    snapshot: RoomViewDto


class CommandErrorMessage(_Outbound):
    type: Literal["command_error"] = "command_error"
    command_id: str | None
    code: str
    message: str


class ConnectionErrorMessage(_Outbound):
    type: Literal["connection_error"] = "connection_error"
    code: str
    message: str


OutboundMessage = (
    ConnectedMessage
    | CommandAckMessage
    | StateMessage
    | CommandErrorMessage
    | ConnectionErrorMessage
)


def _card(snapshot: CardSnapshot) -> CardDto:
    return CardDto(rank=snapshot.rank.value, suit=snapshot.suit.value)


def _call(snapshot: CallSnapshot | None) -> CallDto | None:
    if snapshot is None:
        return None
    return CallDto(chips=snapshot.chips, total=snapshot.total, is_all_in=snapshot.is_all_in)


def _wager(snapshot: WagerBoundsSnapshot | None) -> WagerBoundsDto | None:
    if snapshot is None:
        return None
    return WagerBoundsDto(
        minimum_full_to=snapshot.minimum_full_to,
        maximum_to=snapshot.maximum_to,
        short_all_in_to=snapshot.short_all_in_to,
    )


def _legal_actions(snapshot: LegalActionSnapshot) -> LegalActionsDto:
    return LegalActionsDto(
        actor=snapshot.actor.value,
        kinds=sorted(kind.value for kind in snapshot.kinds),
        amount_to_call=snapshot.amount_to_call,
        call=_call(snapshot.call),
        bet_to=_wager(snapshot.bet_to),
        raise_to=_wager(snapshot.raise_to),
        raise_reopened=snapshot.raise_reopened,
    )


def _active_player(snapshot: ActiveHandPlayerSnapshot) -> ActiveHandPlayerDto:
    return ActiveHandPlayerDto(
        guest_id=snapshot.guest_id.value,
        nickname=snapshot.nickname,
        seat_index=snapshot.seat_index,
        status=snapshot.status.value,
        current_stack=snapshot.current_stack,
        gross_committed=snapshot.gross_committed,
        street_committed=snapshot.street_committed,
        hole_cards=(
            None if snapshot.hole_cards is None else [_card(card) for card in snapshot.hole_cards]
        ),
    )


def _active_hand(snapshot: ActiveHandSnapshot | None) -> ActiveHandDto | None:
    if snapshot is None:
        return None
    return ActiveHandDto(
        hand_number=snapshot.hand_number,
        action_sequence=snapshot.action_sequence,
        action_deadline_unix_ms=snapshot.action_deadline_unix_ms,
        phase=snapshot.phase.value,
        button_seat=snapshot.button_seat,
        small_blind_seat=snapshot.small_blind_seat,
        big_blind_seat=snapshot.big_blind_seat,
        board=[_card(card) for card in snapshot.board],
        pot_chips=snapshot.pot_chips,
        players=[_active_player(player) for player in snapshot.players],
        current_actor=snapshot.current_actor.value,
        legal_actions=_legal_actions(snapshot.legal_actions),
    )


def _winner(snapshot: WinnerShareSnapshot) -> WinnerShareDto:
    return WinnerShareDto(
        guest_id=snapshot.guest_id.value,
        chips=snapshot.chips,
        receives_odd_chip=snapshot.receives_odd_chip,
    )


def _pot(snapshot: CompletedPotSnapshot) -> CompletedPotDto:
    return CompletedPotDto(
        pot_index=snapshot.pot_index,
        amount=snapshot.amount,
        winners=[_winner(winner) for winner in snapshot.winners],
    )


def _completed_player(snapshot: CompletedHandPlayerSnapshot) -> CompletedHandPlayerDto:
    return CompletedHandPlayerDto(
        guest_id=snapshot.guest_id.value,
        nickname=snapshot.nickname,
        seat_index=snapshot.seat_index,
        folded=snapshot.folded,
        final_stack=snapshot.final_stack,
        total_award=snapshot.total_award,
        gross_committed=snapshot.gross_committed,
        returned_excess=snapshot.returned_excess,
        hole_cards=(
            None if snapshot.hole_cards is None else [_card(card) for card in snapshot.hole_cards]
        ),
    )


def _completed_hand(snapshot: CompletedHandSnapshot | None) -> CompletedHandDto | None:
    if snapshot is None:
        return None
    return CompletedHandDto(
        hand_number=snapshot.hand_number,
        final_action_sequence=snapshot.final_action_sequence,
        phase=snapshot.phase.value,
        source=snapshot.source.value,
        button_seat=snapshot.button_seat,
        board=[_card(card) for card in snapshot.board],
        players=[_completed_player(player) for player in snapshot.players],
        pots=[_pot(pot) for pot in snapshot.pots],
    )


def _settings(snapshot: RoomSettingsSnapshot) -> RoomSettingsDto:
    return RoomSettingsDto(
        room_name=snapshot.room_name,
        small_blind=snapshot.small_blind,
        big_blind=snapshot.big_blind,
        default_starting_stack=snapshot.default_starting_stack,
        seating_approval_required=snapshot.seating_approval_required,
        max_seats=snapshot.max_seats,
        password_protected=snapshot.password_protected,
    )


def _member(snapshot: MemberSnapshot) -> MemberDto:
    return MemberDto(
        guest_id=snapshot.guest_id.value,
        nickname=snapshot.nickname,
        status=snapshot.status.value,
        is_host=snapshot.is_host,
        stack=snapshot.stack,
    )


def _seat(snapshot: SeatSnapshot) -> SeatDto:
    return SeatDto(
        seat_index=snapshot.seat_index,
        guest_id=None if snapshot.guest_id is None else snapshot.guest_id.value,
        nickname=snapshot.nickname,
        stack=snapshot.stack,
    )


def _seat_request(snapshot: SeatRequestSnapshot) -> SeatRequestDto:
    return SeatRequestDto(
        guest_id=snapshot.guest_id.value,
        nickname=snapshot.nickname,
        seat_index=snapshot.seat_index,
    )


def _room(snapshot: RoomSnapshot) -> RoomDto:
    return RoomDto(
        room_id=snapshot.room_id.value,
        room_code=snapshot.room_code,
        status=snapshot.status.value,
        host_guest_id=snapshot.host_guest_id.value,
        settings=_settings(snapshot.settings),
        members=[_member(member) for member in snapshot.members],
        seats=[_seat(seat) for seat in snapshot.seats],
        seat_requests=[_seat_request(request) for request in snapshot.seat_requests],
    )


def room_view_dto(snapshot: RoomViewSnapshot) -> RoomViewDto:
    """Explicitly map one already-authorized application projection to JSON-safe data."""
    return RoomViewDto(
        room=_room(snapshot.room),
        next_hand_number=snapshot.next_hand_number,
        active_hand=_active_hand(snapshot.active_hand),
        last_hand=_completed_hand(snapshot.last_hand),
    )
