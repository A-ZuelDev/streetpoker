"""Strict WebSocket messages and explicit viewer-safe snapshot serialization."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator

from streetpoker.application import (
    MAX_ACTION_TIME_MS,
    MAX_ADJUSTMENT_REASON_LENGTH,
    MAX_CHIP_STACK,
    MAX_STAND_UP_PENALTY_PER_RECIPIENT_CHIPS,
    MAX_TIMEBANK_MS,
    MAX_TIMEBANK_REFILL_EVERY_HANDS,
    MIN_ACTION_TIME_MS,
    ActiveHandPlayerSnapshot,
    ActiveHandSnapshot,
    CallSnapshot,
    CardSnapshot,
    CompletedHandPlayerSnapshot,
    CompletedHandSnapshot,
    CompletedPotSnapshot,
    LegalActionSnapshot,
    MemberSnapshot,
    PlayerSessionSummarySnapshot,
    RoomSettingsSnapshot,
    RoomSnapshot,
    RoomViewSnapshot,
    SeatRequestSnapshot,
    SeatSnapshot,
    SessionAccountingSnapshot,
    StackAdjustmentSnapshot,
    StandUpCancellationSnapshot,
    StandUpParticipantSnapshot,
    StandUpResolutionSnapshot,
    StandUpRoundSnapshot,
    StandUpStateSnapshot,
    StandUpTransferSnapshot,
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
StandUpPenalty = Annotated[
    int,
    Field(strict=True, ge=1, le=MAX_STAND_UP_PENALTY_PER_RECIPIENT_CHIPS),
]
ActionTime = Annotated[
    int,
    Field(strict=True, ge=MIN_ACTION_TIME_MS, le=MAX_ACTION_TIME_MS),
]
TimebankMilliseconds = Annotated[int, Field(strict=True, ge=0, le=MAX_TIMEBANK_MS)]
TimebankRefillHands = Annotated[
    int,
    Field(strict=True, ge=1, le=MAX_TIMEBANK_REFILL_EVERY_HANDS),
]
AdjustmentAmount = Annotated[int, Field(strict=True, ge=-MAX_CHIP_STACK, le=MAX_CHIP_STACK)]
AdjustmentReason = Annotated[
    str,
    StringConstraints(max_length=MAX_ADJUSTMENT_REASON_LENGTH),
]


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


class PauseGameCommand(_Command):
    type: Literal["pause_game"]


class ResumeGameCommand(_Command):
    type: Literal["resume_game"]


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


class AdjustStackCommand(_Command):
    type: Literal["adjust_stack"]
    target_guest_id: GuestIdText
    adjustment_type: Literal["rebuy", "cash_out", "correction"]
    amount: AdjustmentAmount
    reason: AdjustmentReason | None = None
    expected_next_hand_number: PositiveInt
    expected_ledger_sequence: NonNegativeInt

    @model_validator(mode="after")
    def validate_amount_semantics(self) -> AdjustStackCommand:
        if self.adjustment_type in ("rebuy", "cash_out") and self.amount <= 0:
            raise ValueError("Rebuy and cash-out amounts must be positive.")
        if self.adjustment_type == "correction" and self.amount == 0:
            raise ValueError("A correction must be nonzero.")
        return self


class UpdateSettingsCommand(_Command):
    type: Literal["update_settings"]
    room_name: str | None = None
    small_blind: PositiveInt | None = None
    big_blind: PositiveInt | None = None
    default_starting_stack: PositiveInt | None = None
    action_time_ms: ActionTime | None = None
    timebank_total_ms: TimebankMilliseconds | None = None
    timebank_refill_amount_ms: TimebankMilliseconds | None = None
    timebank_refill_every_hands: TimebankRefillHands | None = None
    seating_approval_required: bool | None = None
    stand_up_enabled: bool | None = None
    stand_up_penalty_per_recipient_chips: StandUpPenalty | None = None
    password: PasswordText | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> UpdateSettingsCommand:
        setting_fields = {
            "room_name",
            "small_blind",
            "big_blind",
            "default_starting_stack",
            "action_time_ms",
            "timebank_total_ms",
            "timebank_refill_amount_ms",
            "timebank_refill_every_hands",
            "seating_approval_required",
            "stand_up_enabled",
            "stand_up_penalty_per_recipient_chips",
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
    | PauseGameCommand
    | ResumeGameCommand
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
    | AdjustStackCommand
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
    action_deadline_unix_ms: int | None
    action_timer_remaining_ms: NonNegativeInt
    current_actor_timebank_ms: NonNegativeInt
    current_actor_timebank_total_ms: NonNegativeInt
    current_actor_using_timebank: bool
    timebank_refill_amount_ms: NonNegativeInt
    timebank_refill_hands_remaining: PositiveInt | None
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
    action_time_ms: int
    timebank_total_ms: int
    timebank_refill_amount_ms: int
    timebank_refill_every_hands: int
    seating_approval_required: bool
    stand_up_enabled: bool
    stand_up_penalty_per_recipient_chips: int
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


class StackAdjustmentDto(_Outbound):
    sequence: int
    adjustment_type: str
    target_nickname: str
    target_seat_index: int | None
    delta: int
    resulting_stack: int
    initiated_by_host: bool
    initiator_seat_index: int | None
    reason: str | None


class PlayerSessionSummaryDto(_Outbound):
    nickname: str
    seat_index: int | None
    current_stack: int
    starting_stack: int
    external_added: int
    external_removed: int
    poker_net: int
    hands_played: int


class SessionAccountingDto(_Outbound):
    ledger_sequence: int
    adjustments: list[StackAdjustmentDto]
    players: list[PlayerSessionSummaryDto]


class StandUpParticipantDto(_Outbound):
    seat_index: int
    is_cleared: bool


class StandUpRoundDto(_Outbound):
    start_hand_number: int
    last_processed_hand_number: int
    penalty_per_recipient_chips: int
    participants: list[StandUpParticipantDto]


class StandUpTransferDto(_Outbound):
    from_seat_index: int
    to_seat_index: int
    chips: int


class StandUpResolutionDto(_Outbound):
    type: Literal["resolution"] = "resolution"
    start_hand_number: int
    hand_number: int
    participant_seat_indexes: list[int]
    squid_seat_index: int
    penalty_per_recipient_chips: int
    intended_total: int
    actual_total: int
    shortfall: int
    transfers: list[StandUpTransferDto]


class StandUpCancellationDto(_Outbound):
    type: Literal["cancellation"] = "cancellation"
    start_hand_number: int
    last_processed_hand_number: int
    participants: list[StandUpParticipantDto]
    reason: str


class StandUpStateDto(_Outbound):
    active_round: StandUpRoundDto | None
    last_result: StandUpResolutionDto | StandUpCancellationDto | None


class RoomDto(_Outbound):
    room_id: str
    room_code: str
    status: str
    host_guest_id: str
    is_paused: bool
    settings: RoomSettingsDto
    members: list[MemberDto]
    seats: list[SeatDto]
    seat_requests: list[SeatRequestDto]
    stand_up: StandUpStateDto
    session: SessionAccountingDto


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
        action_timer_remaining_ms=snapshot.action_timer_remaining_ms,
        current_actor_timebank_ms=snapshot.current_actor_timebank_ms,
        current_actor_timebank_total_ms=snapshot.current_actor_timebank_total_ms,
        current_actor_using_timebank=snapshot.current_actor_using_timebank,
        timebank_refill_amount_ms=snapshot.timebank_refill_amount_ms,
        timebank_refill_hands_remaining=snapshot.timebank_refill_hands_remaining,
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
        action_time_ms=snapshot.action_time_ms,
        timebank_total_ms=snapshot.timebank_total_ms,
        timebank_refill_amount_ms=snapshot.timebank_refill_amount_ms,
        timebank_refill_every_hands=snapshot.timebank_refill_every_hands,
        seating_approval_required=snapshot.seating_approval_required,
        stand_up_enabled=snapshot.stand_up_enabled,
        stand_up_penalty_per_recipient_chips=(snapshot.stand_up_penalty_per_recipient_chips),
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


def _stack_adjustment(snapshot: StackAdjustmentSnapshot) -> StackAdjustmentDto:
    return StackAdjustmentDto(
        sequence=snapshot.sequence,
        adjustment_type=snapshot.adjustment_type.value,
        target_nickname=snapshot.target_nickname,
        target_seat_index=snapshot.target_seat_index,
        delta=snapshot.delta,
        resulting_stack=snapshot.resulting_stack,
        initiated_by_host=snapshot.initiated_by_host,
        initiator_seat_index=snapshot.initiator_seat_index,
        reason=snapshot.reason,
    )


def _session_player(snapshot: PlayerSessionSummarySnapshot) -> PlayerSessionSummaryDto:
    return PlayerSessionSummaryDto(
        nickname=snapshot.nickname,
        seat_index=snapshot.seat_index,
        current_stack=snapshot.current_stack,
        starting_stack=snapshot.starting_stack,
        external_added=snapshot.external_added,
        external_removed=snapshot.external_removed,
        poker_net=snapshot.poker_net,
        hands_played=snapshot.hands_played,
    )


def _session(snapshot: SessionAccountingSnapshot) -> SessionAccountingDto:
    return SessionAccountingDto(
        ledger_sequence=snapshot.ledger_sequence,
        adjustments=[_stack_adjustment(entry) for entry in snapshot.adjustments],
        players=[_session_player(player) for player in snapshot.players],
    )


def _stand_up_participant(snapshot: StandUpParticipantSnapshot) -> StandUpParticipantDto:
    return StandUpParticipantDto(
        seat_index=snapshot.seat_index,
        is_cleared=snapshot.is_cleared,
    )


def _stand_up_round(snapshot: StandUpRoundSnapshot | None) -> StandUpRoundDto | None:
    if snapshot is None:
        return None
    return StandUpRoundDto(
        start_hand_number=snapshot.start_hand_number,
        last_processed_hand_number=snapshot.last_processed_hand_number,
        penalty_per_recipient_chips=snapshot.penalty_per_recipient_chips,
        participants=[_stand_up_participant(item) for item in snapshot.participants],
    )


def _stand_up_transfer(snapshot: StandUpTransferSnapshot) -> StandUpTransferDto:
    return StandUpTransferDto(
        from_seat_index=snapshot.from_seat_index,
        to_seat_index=snapshot.to_seat_index,
        chips=snapshot.chips,
    )


def _stand_up_resolution(snapshot: StandUpResolutionSnapshot) -> StandUpResolutionDto:
    return StandUpResolutionDto(
        start_hand_number=snapshot.start_hand_number,
        hand_number=snapshot.hand_number,
        participant_seat_indexes=list(snapshot.participant_seat_indexes),
        squid_seat_index=snapshot.squid_seat_index,
        penalty_per_recipient_chips=snapshot.penalty_per_recipient_chips,
        intended_total=snapshot.intended_total,
        actual_total=snapshot.actual_total,
        shortfall=snapshot.shortfall,
        transfers=[_stand_up_transfer(item) for item in snapshot.transfers],
    )


def _stand_up_cancellation(
    snapshot: StandUpCancellationSnapshot,
) -> StandUpCancellationDto:
    return StandUpCancellationDto(
        start_hand_number=snapshot.start_hand_number,
        last_processed_hand_number=snapshot.last_processed_hand_number,
        participants=[_stand_up_participant(item) for item in snapshot.participants],
        reason=snapshot.reason.value,
    )


def _stand_up_state(snapshot: StandUpStateSnapshot) -> StandUpStateDto:
    result = snapshot.last_result
    if isinstance(result, StandUpResolutionSnapshot):
        last_result: StandUpResolutionDto | StandUpCancellationDto | None = _stand_up_resolution(
            result
        )
    elif isinstance(result, StandUpCancellationSnapshot):
        last_result = _stand_up_cancellation(result)
    else:
        last_result = None
    return StandUpStateDto(
        active_round=_stand_up_round(snapshot.active_round),
        last_result=last_result,
    )


def _room(snapshot: RoomSnapshot) -> RoomDto:
    return RoomDto(
        room_id=snapshot.room_id.value,
        room_code=snapshot.room_code,
        status=snapshot.status.value,
        host_guest_id=snapshot.host_guest_id.value,
        is_paused=snapshot.is_paused,
        settings=_settings(snapshot.settings),
        members=[_member(member) for member in snapshot.members],
        seats=[_seat(seat) for seat in snapshot.seats],
        seat_requests=[_seat_request(request) for request in snapshot.seat_requests],
        stand_up=_stand_up_state(snapshot.stand_up),
        session=_session(snapshot.session),
    )


def room_view_dto(snapshot: RoomViewSnapshot) -> RoomViewDto:
    """Explicitly map one already-authorized application projection to JSON-safe data."""
    return RoomViewDto(
        room=_room(snapshot.room),
        next_hand_number=snapshot.next_hand_number,
        active_hand=_active_hand(snapshot.active_hand),
        last_hand=_completed_hand(snapshot.last_hand),
    )
