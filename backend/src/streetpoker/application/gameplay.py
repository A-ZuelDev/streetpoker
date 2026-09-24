"""Private gameplay ownership and transport-safe viewer projections."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from streetpoker.domain import (
    ActionKind,
    Card,
    HandSettlementResult,
    HoldemHand,
    HoldemHandPhase,
    HoldemParticipantStatus,
    PlayerId,
    Rank,
    SettlementSource,
    Suit,
)

if TYPE_CHECKING:
    from streetpoker.application.rooms import GuestId, RoomId, RoomSnapshot


@dataclass(frozen=True, slots=True)
class CardSnapshot:
    rank: Rank
    suit: Suit


@dataclass(frozen=True, slots=True)
class CallSnapshot:
    chips: int
    total: int
    is_all_in: bool


@dataclass(frozen=True, slots=True)
class WagerBoundsSnapshot:
    minimum_full_to: int
    maximum_to: int
    short_all_in_to: int | None


@dataclass(frozen=True, slots=True)
class LegalActionSnapshot:
    actor: GuestId
    kinds: frozenset[ActionKind]
    amount_to_call: int
    call: CallSnapshot | None
    bet_to: WagerBoundsSnapshot | None
    raise_to: WagerBoundsSnapshot | None
    raise_reopened: bool


@dataclass(frozen=True, slots=True)
class ActiveHandPlayerSnapshot:
    guest_id: GuestId
    nickname: str
    seat_index: int
    status: HoldemParticipantStatus
    current_stack: int
    gross_committed: int
    street_committed: int
    hole_cards: tuple[CardSnapshot, CardSnapshot] | None


@dataclass(frozen=True, slots=True)
class ActiveHandSnapshot:
    hand_number: int
    action_sequence: int
    action_deadline_unix_ms: int | None
    action_timer_remaining_ms: int
    current_actor_timebank_ms: int
    current_actor_timebank_total_ms: int
    current_actor_using_timebank: bool
    timebank_refill_amount_ms: int
    timebank_refill_hands_remaining: int | None
    phase: HoldemHandPhase
    button_seat: int
    small_blind_seat: int
    big_blind_seat: int
    board: tuple[CardSnapshot, ...]
    pot_chips: int
    players: tuple[ActiveHandPlayerSnapshot, ...]
    current_actor: GuestId
    legal_actions: LegalActionSnapshot


@dataclass(frozen=True, slots=True)
class WinnerShareSnapshot:
    guest_id: GuestId
    chips: int
    receives_odd_chip: bool


@dataclass(frozen=True, slots=True)
class CompletedPotSnapshot:
    pot_index: int
    amount: int
    winners: tuple[WinnerShareSnapshot, ...]


@dataclass(frozen=True, slots=True)
class CompletedHandPlayerSnapshot:
    guest_id: GuestId
    nickname: str
    seat_index: int
    folded: bool
    final_stack: int
    total_award: int
    gross_committed: int
    returned_excess: int
    hole_cards: tuple[CardSnapshot, CardSnapshot] | None


@dataclass(frozen=True, slots=True)
class CompletedHandSnapshot:
    hand_number: int
    final_action_sequence: int
    phase: HoldemHandPhase
    source: SettlementSource
    button_seat: int
    board: tuple[CardSnapshot, ...]
    players: tuple[CompletedHandPlayerSnapshot, ...]
    pots: tuple[CompletedPotSnapshot, ...]


@dataclass(frozen=True, slots=True)
class RoomViewSnapshot:
    room: RoomSnapshot
    next_hand_number: int
    active_hand: ActiveHandSnapshot | None
    last_hand: CompletedHandSnapshot | None


@dataclass(frozen=True, slots=True)
class TurnDeadline:
    room_id: RoomId
    hand_number: int
    action_sequence: int
    actor: GuestId
    revision: int
    monotonic_ms: int
    unix_ms: int


@dataclass(frozen=True, slots=True)
class _HandIdentity:
    guest_id: GuestId
    player_id: PlayerId
    nickname: str


@dataclass(slots=True)
class _ActiveHand:
    hand_number: int
    action_sequence: int
    hand: HoldemHand
    identities: tuple[_HandIdentity, ...]
    timebank_remaining_ms: dict[PlayerId, int]
    timebank_hands_until_refill: dict[PlayerId, int | None]
    base_action_time_ms: int
    timebank_total_ms: int
    timebank_refill_amount_ms: int
    deadline: TurnDeadline | None = None
    using_timebank: bool = False
    frozen_remaining_ms: int | None = None

    def copy(self) -> _ActiveHand:
        return _ActiveHand(
            hand_number=self.hand_number,
            action_sequence=self.action_sequence,
            hand=self.hand.copy(),
            identities=self.identities,
            timebank_remaining_ms=self.timebank_remaining_ms.copy(),
            timebank_hands_until_refill=self.timebank_hands_until_refill.copy(),
            base_action_time_ms=self.base_action_time_ms,
            timebank_total_ms=self.timebank_total_ms,
            timebank_refill_amount_ms=self.timebank_refill_amount_ms,
            deadline=self.deadline,
            using_timebank=self.using_timebank,
            frozen_remaining_ms=self.frozen_remaining_ms,
        )

    def identity_for_guest(self, guest_id: GuestId) -> _HandIdentity | None:
        return next((item for item in self.identities if item.guest_id == guest_id), None)

    def identity_for_player(self, player_id: PlayerId) -> _HandIdentity:
        return next(item for item in self.identities if item.player_id == player_id)


@dataclass(frozen=True, slots=True)
class _CompletedPlayerRecord:
    guest_id: GuestId
    nickname: str
    seat_index: int
    folded: bool
    final_stack: int
    total_award: int
    gross_committed: int
    returned_excess: int
    hole_cards: tuple[Card, Card]


@dataclass(frozen=True, slots=True)
class _CompletedHandRecord:
    hand_number: int
    final_action_sequence: int
    phase: HoldemHandPhase
    source: SettlementSource
    button_seat: int
    board: tuple[Card, ...]
    players: tuple[_CompletedPlayerRecord, ...]
    pots: tuple[CompletedPotSnapshot, ...]


def completed_hand_record(
    active: _ActiveHand,
    settlement: HandSettlementResult,
) -> _CompletedHandRecord:
    hand_snapshot = active.hand.snapshot
    settlement_by_player = {item.player_id: item for item in settlement.player_settlements}
    players = tuple(
        _CompletedPlayerRecord(
            guest_id=active.identity_for_player(participant.player_id).guest_id,
            nickname=active.identity_for_player(participant.player_id).nickname,
            seat_index=participant.seat_index.value,
            folded=participant.status is HoldemParticipantStatus.FOLDED,
            final_stack=settlement_by_player[participant.player_id].final_stack.chips,
            total_award=settlement_by_player[participant.player_id].total_award,
            gross_committed=participant.gross_committed,
            returned_excess=participant.returned_excess,
            hole_cards=participant.hole_cards,
        )
        for participant in hand_snapshot.participants
    )
    pots = tuple(
        CompletedPotSnapshot(
            pot_index=award.pot_index,
            amount=award.pot.amount,
            winners=tuple(
                WinnerShareSnapshot(
                    guest_id=active.identity_for_player(share.player_id).guest_id,
                    chips=share.chips,
                    receives_odd_chip=share.receives_odd_chip,
                )
                for share in award.winner_shares
            ),
        )
        for award in settlement.pot_awards
    )
    return _CompletedHandRecord(
        hand_number=active.hand_number,
        final_action_sequence=active.action_sequence,
        phase=hand_snapshot.phase,
        source=settlement.source,
        button_seat=hand_snapshot.button_position.value,
        board=hand_snapshot.board,
        players=players,
        pots=pots,
    )


def _card(card: Card) -> CardSnapshot:
    return CardSnapshot(card.rank, card.suit)


def _active_projection(
    active: _ActiveHand,
    viewer: GuestId,
    *,
    paused: bool,
) -> ActiveHandSnapshot:
    snapshot = active.hand.snapshot
    assert active.deadline is not None
    legal = active.hand.legal_actions()
    actor = active.identity_for_player(legal.player_id).guest_id
    remaining_ms = (
        active.frozen_remaining_ms
        if paused
        else (
            active.timebank_remaining_ms[legal.player_id]
            if active.using_timebank
            else active.base_action_time_ms
        )
    )
    assert remaining_ms is not None
    timebank_remaining_ms = active.timebank_remaining_ms[legal.player_id]
    if active.using_timebank and paused:
        timebank_remaining_ms = min(timebank_remaining_ms, remaining_ms)
    players = tuple(
        ActiveHandPlayerSnapshot(
            guest_id=(identity := active.identity_for_player(participant.player_id)).guest_id,
            nickname=identity.nickname,
            seat_index=participant.seat_index.value,
            status=participant.status,
            current_stack=participant.current_stack.chips,
            gross_committed=participant.gross_committed,
            street_committed=participant.street_committed,
            hole_cards=(
                tuple(_card(card) for card in participant.hole_cards)  # type: ignore[arg-type]
                if identity.guest_id == viewer
                else None
            ),
        )
        for participant in snapshot.participants
    )
    return ActiveHandSnapshot(
        hand_number=active.hand_number,
        action_sequence=active.action_sequence,
        action_deadline_unix_ms=None if paused else active.deadline.unix_ms,
        action_timer_remaining_ms=remaining_ms,
        current_actor_timebank_ms=timebank_remaining_ms,
        current_actor_timebank_total_ms=active.timebank_total_ms,
        current_actor_using_timebank=active.using_timebank,
        timebank_refill_amount_ms=active.timebank_refill_amount_ms,
        timebank_refill_hands_remaining=active.timebank_hands_until_refill[legal.player_id],
        phase=snapshot.phase,
        button_seat=snapshot.button_position.value,
        small_blind_seat=snapshot.small_blind_position.value,
        big_blind_seat=snapshot.big_blind_position.value,
        board=tuple(_card(card) for card in snapshot.board),
        pot_chips=sum(item.gross_committed for item in snapshot.participants),
        players=players,
        current_actor=actor,
        legal_actions=LegalActionSnapshot(
            actor=actor,
            kinds=legal.kinds,
            amount_to_call=legal.amount_to_call,
            call=(
                None
                if legal.call is None
                else CallSnapshot(legal.call.chips, legal.call.total, legal.call.is_all_in)
            ),
            bet_to=(
                None
                if legal.bet is None
                else WagerBoundsSnapshot(
                    legal.bet.minimum_full_to,
                    legal.bet.maximum_to,
                    legal.bet.short_all_in_to,
                )
            ),
            raise_to=(
                None
                if legal.raise_to is None
                else WagerBoundsSnapshot(
                    legal.raise_to.minimum_full_to,
                    legal.raise_to.maximum_to,
                    legal.raise_to.short_all_in_to,
                )
            ),
            raise_reopened=legal.raise_reopened,
        ),
    )


def _completed_projection(
    completed: _CompletedHandRecord,
    viewer: GuestId,
) -> CompletedHandSnapshot:
    return CompletedHandSnapshot(
        hand_number=completed.hand_number,
        final_action_sequence=completed.final_action_sequence,
        phase=completed.phase,
        source=completed.source,
        button_seat=completed.button_seat,
        board=tuple(_card(card) for card in completed.board),
        players=tuple(
            CompletedHandPlayerSnapshot(
                guest_id=player.guest_id,
                nickname=player.nickname,
                seat_index=player.seat_index,
                folded=player.folded,
                final_stack=player.final_stack,
                total_award=player.total_award,
                gross_committed=player.gross_committed,
                returned_excess=player.returned_excess,
                hole_cards=(
                    tuple(_card(card) for card in player.hole_cards)  # type: ignore[arg-type]
                    if player.guest_id == viewer
                    else None
                ),
            )
            for player in completed.players
        ),
        pots=completed.pots,
    )


def project_current_room_snapshot(
    room_snapshot: RoomSnapshot,
    active_hand: _ActiveHand | None,
) -> RoomSnapshot:
    """Overlay public live stacks without mutating between-hand table state."""
    if active_hand is None:
        return room_snapshot
    live_stack_by_guest = {
        active_hand.identity_for_player(participant.player_id).guest_id: (
            participant.current_stack.chips
        )
        for participant in active_hand.hand.snapshot.participants
    }
    live_stack_by_seat = {
        participant.seat_index.value: participant.current_stack.chips
        for participant in active_hand.hand.snapshot.participants
    }

    def current_stack(guest_id: GuestId | None, existing: int | None) -> int | None:
        if guest_id is None:
            return existing
        live_stack = live_stack_by_guest.get(guest_id)
        return existing if live_stack is None else live_stack

    return replace(
        room_snapshot,
        members=tuple(
            replace(
                member,
                stack=current_stack(member.guest_id, member.stack),
            )
            for member in room_snapshot.members
        ),
        seats=tuple(
            replace(
                seat,
                stack=current_stack(seat.guest_id, seat.stack),
            )
            for seat in room_snapshot.seats
        ),
        session=replace(
            room_snapshot.session,
            players=tuple(
                replace(
                    player,
                    current_stack=live_stack,
                    poker_net=(
                        live_stack
                        - player.starting_stack
                        - (player.external_added - player.external_removed)
                    ),
                )
                if player.seat_index is not None
                and (live_stack := live_stack_by_seat.get(player.seat_index)) is not None
                else player
                for player in room_snapshot.session.players
            ),
        ),
    )


def project_room_view(
    room_snapshot: RoomSnapshot,
    next_hand_number: int,
    active_hand: _ActiveHand | None,
    last_hand: _CompletedHandRecord | None,
    viewer: GuestId,
    *,
    paused: bool,
) -> RoomViewSnapshot:
    return RoomViewSnapshot(
        room=project_current_room_snapshot(room_snapshot, active_hand),
        next_hand_number=next_hand_number,
        active_hand=(
            None
            if active_hand is None
            else _active_projection(
                active_hand,
                viewer,
                paused=paused,
            )
        ),
        last_hand=(None if last_hand is None else _completed_projection(last_hand, viewer)),
    )
