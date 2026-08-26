"""Authoritative pure-domain lifecycle for one no-limit Texas Hold'em hand."""

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Self

from streetpoker.domain.betting_round import (
    ActionResult,
    BettingAction,
    BettingParticipantStatus,
    BettingRound,
    BettingRoundPlayer,
    BettingRoundSnapshot,
    BetTo,
    Call,
    Check,
    Fold,
    LegalActions,
    RaiseTo,
)
from streetpoker.domain.cards import Card
from streetpoker.domain.chips import ChipStack
from streetpoker.domain.deck import Deck
from streetpoker.domain.errors import (
    BettingRoundReconciliationError,
    HandAlreadyTerminalError,
    HandChipAccountingError,
    InsufficientEligibleParticipantsError,
    InvalidBlindStructureError,
    InvalidDealingStateError,
    InvalidHandButtonError,
    InvalidHandInitializationError,
    InvalidHandStateError,
    NoActiveBettingRoundError,
    PlayerNotInHandError,
)
from streetpoker.domain.players import ParticipationStatus, PlayerId
from streetpoker.domain.pots import (
    PotConstructionResult,
    PotContribution,
    PotEligibilityStatus,
    construct_pots,
)
from streetpoker.domain.randomness import RandomSource
from streetpoker.domain.table import SeatIndex, TableState


class HoldemHandPhase(StrEnum):
    """Explicit lifecycle phase for one Hold'em hand."""

    PREFLOP = "preflop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN_READY = "showdown_ready"
    COMPLETE_BY_FOLD = "complete_by_fold"


class HoldemParticipantStatus(StrEnum):
    """Persistent hand-level participation state."""

    ACTIVE = "active"
    FOLDED = "folded"
    ALL_IN = "all_in"


class BlindKind(StrEnum):
    """The two forced blind positions supported in Phase 5."""

    SMALL = "small"
    BIG = "big"


@dataclass(frozen=True, slots=True)
class BlindPost:
    """Immutable record of a nominal blind and the chips actually posted."""

    player_id: PlayerId
    seat_index: SeatIndex
    kind: BlindKind
    required: int
    posted: int


@dataclass(frozen=True, slots=True)
class HoldemHandParticipant:
    """Immutable authoritative state for one snapshotted hand participant."""

    player_id: PlayerId
    seat_index: SeatIndex
    starting_stack: ChipStack
    current_stack: ChipStack
    gross_committed: int
    street_committed: int
    returned_excess: int
    status: HoldemParticipantStatus
    hole_cards: tuple[Card, Card]

    @property
    def contestable_committed(self) -> int:
        return self.gross_committed - self.returned_excess


@dataclass(frozen=True, slots=True)
class HoldemHandSnapshot:
    """Immutable trusted-server snapshot; it is not a client-facing projection."""

    phase: HoldemHandPhase
    participants: tuple[HoldemHandParticipant, ...]
    button_position: SeatIndex
    small_blind_position: SeatIndex
    big_blind_position: SeatIndex
    small_blind: int
    big_blind: int
    blind_posts: tuple[BlindPost, BlindPost]
    board: tuple[Card, ...]
    burn_count: int
    betting_round: BettingRoundSnapshot | None
    deck_remaining_count: int
    pot_result: PotConstructionResult | None
    uncontested_player: PlayerId | None

    @property
    def terminal(self) -> bool:
        return self.phase in {
            HoldemHandPhase.SHOWDOWN_READY,
            HoldemHandPhase.COMPLETE_BY_FOLD,
        }


@dataclass(frozen=True, slots=True)
class HoldemTransitionResult:
    """Facts returned after one successful hand-routed betting action."""

    betting_result: ActionResult
    phase_before: HoldemHandPhase
    phase_after: HoldemHandPhase
    board_cards_dealt: int
    terminal: bool


class HoldemHand:
    """Copy-on-write aggregate orchestrating an authoritative Hold'em hand."""

    __slots__ = ("_betting_round", "_burn_cards", "_deck", "_snapshot")

    _betting_round: BettingRound | None
    _burn_cards: tuple[Card, ...]
    _deck: Deck
    _snapshot: HoldemHandSnapshot

    def __init__(self) -> None:
        raise TypeError("Use HoldemHand.start().")

    @classmethod
    def start(
        cls,
        *,
        table: TableState,
        small_blind: int,
        big_blind: int,
        random_source: RandomSource | None = None,
    ) -> Self:
        """Snapshot a table and start one independently owned Hold'em hand."""
        cls._validate_start_inputs(table, small_blind, big_blind)
        eligible = tuple(
            seat
            for seat in table.seats
            if seat.occupant is not None
            and seat.occupant.status is ParticipationStatus.SITTING_IN
            and seat.occupant.stack.chips > 0
        )
        if len(eligible) < 2:
            raise InsufficientEligibleParticipantsError(
                "A Hold'em hand requires at least two eligible participants."
            )
        button = table.button_position
        eligible_seats = {seat.index for seat in eligible}
        if button is None or button not in eligible_seats:
            raise InvalidHandButtonError(
                "The button must identify an eligible snapshotted participant."
            )

        ordered_seats = tuple(sorted(eligible_seats, key=lambda item: item.value))
        if len(eligible) == 2:
            small_blind_position = button
            big_blind_position = cls._next_seat(button, ordered_seats)
        else:
            small_blind_position = cls._next_seat(button, ordered_seats)
            big_blind_position = cls._next_seat(small_blind_position, ordered_seats)

        occupant_by_seat = {seat.index: seat.occupant for seat in eligible}
        small_blind_player = occupant_by_seat[small_blind_position]
        big_blind_player = occupant_by_seat[big_blind_position]
        if small_blind_player is None or big_blind_player is None:  # pragma: no cover
            raise InvalidHandInitializationError("Blind positions must be occupied.")
        small_posted = min(small_blind, small_blind_player.stack.chips)
        big_posted = min(big_blind, big_blind_player.stack.chips)
        initial_by_seat = {
            small_blind_position: small_posted,
            big_blind_position: big_posted,
        }

        deck = Deck.shuffled_standard(random_source=random_source)
        deal_order = cls._clockwise_seats_after(button, ordered_seats)
        hole_cards: dict[SeatIndex, list[Card]] = {seat: [] for seat in ordered_seats}
        for _ in range(2):
            for seat in deal_order:
                hole_cards[seat].append(deck.draw()[0])

        round_players = tuple(
            BettingRoundPlayer(
                player_id=occupant_by_seat[seat].player_id,  # type: ignore[union-attr]
                seat_index=seat,
                stack=occupant_by_seat[seat].stack,  # type: ignore[union-attr]
                initial_commitment=initial_by_seat.get(seat, 0),
            )
            for seat in ordered_seats
        )
        first_seat = (
            button if len(eligible) == 2 else cls._next_seat(big_blind_position, ordered_seats)
        )
        first_player = occupant_by_seat[first_seat]
        if first_player is None:  # pragma: no cover
            raise InvalidHandInitializationError("The first action position must be occupied.")
        initial_wager = big_blind if len(eligible) >= 3 else max(small_posted, big_posted)
        betting_round = BettingRound.start(
            players=round_players,
            first_to_act=first_player.player_id,
            minimum_bet=big_blind,
            initial_wager=initial_wager,
        )
        round_by_id = {
            participant.player_id: participant for participant in betting_round.participants
        }
        participants = tuple(
            HoldemHandParticipant(
                player_id=occupant_by_seat[seat].player_id,  # type: ignore[union-attr]
                seat_index=seat,
                starting_stack=occupant_by_seat[seat].stack,  # type: ignore[union-attr]
                current_stack=round_by_id[
                    occupant_by_seat[seat].player_id  # type: ignore[union-attr]
                ].remaining_stack,
                gross_committed=initial_by_seat.get(seat, 0),
                street_committed=initial_by_seat.get(seat, 0),
                returned_excess=0,
                status=cls._hand_status(
                    round_by_id[occupant_by_seat[seat].player_id].status  # type: ignore[union-attr]
                ),
                hole_cards=(hole_cards[seat][0], hole_cards[seat][1]),
            )
            for seat in ordered_seats
        )
        snapshot = HoldemHandSnapshot(
            phase=HoldemHandPhase.PREFLOP,
            participants=participants,
            button_position=button,
            small_blind_position=small_blind_position,
            big_blind_position=big_blind_position,
            small_blind=small_blind,
            big_blind=big_blind,
            blind_posts=(
                BlindPost(
                    small_blind_player.player_id,
                    small_blind_position,
                    BlindKind.SMALL,
                    small_blind,
                    small_posted,
                ),
                BlindPost(
                    big_blind_player.player_id,
                    big_blind_position,
                    BlindKind.BIG,
                    big_blind,
                    big_posted,
                ),
            ),
            board=(),
            burn_count=0,
            betting_round=(None if betting_round.complete else betting_round.snapshot),
            deck_remaining_count=deck.remaining_count,
            pot_result=None,
            uncontested_player=None,
        )

        hand = object.__new__(cls)
        hand._snapshot = snapshot
        hand._deck = deck
        hand._burn_cards = ()
        hand._betting_round = None if betting_round.complete else betting_round
        if betting_round.complete:
            candidate_snapshot, candidate_deck, burns, next_round = hand._after_round_complete(
                snapshot,
                deck.copy(),
                (),
            )
            hand._snapshot = candidate_snapshot
            hand._deck = candidate_deck
            hand._burn_cards = burns
            hand._betting_round = next_round
        hand._validate_state(
            hand._snapshot,
            hand._deck,
            hand._burn_cards,
            hand._betting_round,
        )
        return hand

    @property
    def snapshot(self) -> HoldemHandSnapshot:
        return self._snapshot

    def copy(self) -> Self:
        """Return an independent hand preserving all private execution state."""
        copied = object.__new__(type(self))
        copied._snapshot = self._snapshot
        copied._deck = self._deck.copy()
        copied._burn_cards = self._burn_cards
        copied._betting_round = None if self._betting_round is None else self._betting_round.copy()
        return copied

    @property
    def current_phase(self) -> HoldemHandPhase:
        return self._snapshot.phase

    @property
    def betting_round_snapshot(self) -> BettingRoundSnapshot | None:
        return self._snapshot.betting_round

    def participant(self, player_id: PlayerId) -> HoldemHandParticipant:
        if not isinstance(player_id, PlayerId):
            raise PlayerNotInHandError(player_id=str(player_id))
        for participant in self._snapshot.participants:
            if participant.player_id == player_id:
                return participant
        raise PlayerNotInHandError(player_id=player_id.value)

    def legal_actions(self) -> LegalActions:
        self._checked_active_round()
        if self._betting_round is None:  # pragma: no cover - protected above
            raise NoActiveBettingRoundError("The hand has no active betting round.")
        return self._betting_round.legal_actions()

    def act(self, *, player_id: PlayerId, action: BettingAction) -> HoldemTransitionResult:
        current_round = self._checked_active_round()
        before = self._snapshot
        candidate_round = current_round.copy()
        betting_result = candidate_round.act(player_id=player_id, action=action)
        participants = self._reconcile_round(
            before.participants,
            current_round.snapshot,
            candidate_round.snapshot,
        )
        candidate_snapshot = replace(
            before,
            participants=participants,
            betting_round=(None if candidate_round.complete else candidate_round.snapshot),
        )
        candidate_deck = self._deck.copy()
        candidate_burns = self._burn_cards
        next_round: BettingRound | None = None if candidate_round.complete else candidate_round

        if candidate_round.complete:
            candidate_snapshot, candidate_deck, candidate_burns, next_round = (
                self._after_round_complete(
                    candidate_snapshot,
                    candidate_deck,
                    candidate_burns,
                )
            )

        self._validate_state(
            candidate_snapshot,
            candidate_deck,
            candidate_burns,
            next_round,
        )
        self._snapshot = candidate_snapshot
        self._deck = candidate_deck
        self._burn_cards = candidate_burns
        self._betting_round = next_round
        return HoldemTransitionResult(
            betting_result=betting_result,
            phase_before=before.phase,
            phase_after=candidate_snapshot.phase,
            board_cards_dealt=len(candidate_snapshot.board) - len(before.board),
            terminal=candidate_snapshot.terminal,
        )

    def check(self, *, player_id: PlayerId) -> HoldemTransitionResult:
        return self.act(player_id=player_id, action=Check())

    def call(self, *, player_id: PlayerId) -> HoldemTransitionResult:
        return self.act(player_id=player_id, action=Call())

    def bet_to(self, *, player_id: PlayerId, total: int) -> HoldemTransitionResult:
        return self.act(player_id=player_id, action=BetTo(total))

    def raise_to(self, *, player_id: PlayerId, total: int) -> HoldemTransitionResult:
        return self.act(player_id=player_id, action=RaiseTo(total))

    def fold(self, *, player_id: PlayerId) -> HoldemTransitionResult:
        return self.act(player_id=player_id, action=Fold())

    def _checked_active_round(self) -> BettingRound:
        if self._snapshot.terminal:
            raise HandAlreadyTerminalError("The hand is already terminal.")
        if self._betting_round is None:
            raise NoActiveBettingRoundError("The hand has no active betting round.")
        return self._betting_round

    def _after_round_complete(
        self,
        snapshot: HoldemHandSnapshot,
        deck: Deck,
        burn_cards: tuple[Card, ...],
    ) -> tuple[HoldemHandSnapshot, Deck, tuple[Card, ...], BettingRound | None]:
        non_folded = tuple(
            participant
            for participant in snapshot.participants
            if participant.status is not HoldemParticipantStatus.FOLDED
        )
        if len(non_folded) == 1:
            terminal = self._terminalize(
                snapshot,
                phase=HoldemHandPhase.COMPLETE_BY_FOLD,
                uncontested_player=non_folded[0].player_id,
            )
            return terminal, deck, burn_cards, None
        if snapshot.phase is HoldemHandPhase.RIVER:
            terminal = self._terminalize(
                snapshot,
                phase=HoldemHandPhase.SHOWDOWN_READY,
                uncontested_player=None,
            )
            return terminal, deck, burn_cards, None

        next_snapshot, burn_cards = self._deal_next_street(snapshot, deck, burn_cards)
        active = tuple(
            participant
            for participant in next_snapshot.participants
            if participant.status is HoldemParticipantStatus.ACTIVE
        )
        if len(active) >= 2:
            next_round = self._postflop_round(next_snapshot, active)
            next_snapshot = replace(next_snapshot, betting_round=next_round.snapshot)
            return next_snapshot, deck, burn_cards, next_round

        while next_snapshot.phase is not HoldemHandPhase.RIVER:
            next_snapshot, burn_cards = self._deal_next_street(
                next_snapshot,
                deck,
                burn_cards,
            )
        terminal = self._terminalize(
            next_snapshot,
            phase=HoldemHandPhase.SHOWDOWN_READY,
            uncontested_player=None,
        )
        return terminal, deck, burn_cards, None

    @staticmethod
    def _reconcile_round(
        participants: tuple[HoldemHandParticipant, ...],
        prior_round: BettingRoundSnapshot,
        candidate_round: BettingRoundSnapshot,
    ) -> tuple[HoldemHandParticipant, ...]:
        prior_by_id = {
            participant.player_id: participant for participant in prior_round.participants
        }
        candidate_by_id = {
            participant.player_id: participant for participant in candidate_round.participants
        }
        if prior_by_id.keys() != candidate_by_id.keys():
            raise BettingRoundReconciliationError(
                "A betting transition cannot change round participants."
            )
        hand_by_id = {participant.player_id: participant for participant in participants}
        if not candidate_by_id.keys() <= hand_by_id.keys():
            raise BettingRoundReconciliationError(
                "Every betting participant must belong to the hand."
            )

        reconciled: list[HoldemHandParticipant] = []
        for participant in participants:
            candidate = candidate_by_id.get(participant.player_id)
            if candidate is None:
                reconciled.append(participant)
                continue
            prior = prior_by_id[participant.player_id]
            if (
                participant.street_committed != prior.committed
                or participant.current_stack != prior.remaining_stack
            ):
                raise BettingRoundReconciliationError(
                    "Hand state does not match the authoritative prior betting snapshot."
                )
            delta = candidate.committed - prior.committed
            if delta < 0:
                raise BettingRoundReconciliationError(
                    "A betting transition cannot reduce a street commitment."
                )
            reconciled.append(
                replace(
                    participant,
                    current_stack=candidate.remaining_stack,
                    gross_committed=participant.gross_committed + delta,
                    street_committed=candidate.committed,
                    status=HoldemHand._hand_status(candidate.status),
                )
            )
        return tuple(reconciled)

    @staticmethod
    def _postflop_round(
        snapshot: HoldemHandSnapshot,
        active: tuple[HoldemHandParticipant, ...],
    ) -> BettingRound:
        first_seat = HoldemHand._next_seat(
            snapshot.button_position,
            tuple(participant.seat_index for participant in active),
        )
        first_player = next(
            participant for participant in active if participant.seat_index == first_seat
        )
        return BettingRound.start(
            players=tuple(
                BettingRoundPlayer(
                    participant.player_id,
                    participant.seat_index,
                    participant.current_stack,
                )
                for participant in active
            ),
            first_to_act=first_player.player_id,
            minimum_bet=snapshot.big_blind,
        )

    @staticmethod
    def _deal_next_street(
        snapshot: HoldemHandSnapshot,
        deck: Deck,
        burn_cards: tuple[Card, ...],
    ) -> tuple[HoldemHandSnapshot, tuple[Card, ...]]:
        phase_and_count = {
            HoldemHandPhase.PREFLOP: (HoldemHandPhase.FLOP, 3),
            HoldemHandPhase.FLOP: (HoldemHandPhase.TURN, 1),
            HoldemHandPhase.TURN: (HoldemHandPhase.RIVER, 1),
        }
        try:
            next_phase, board_count = phase_and_count[snapshot.phase]
        except KeyError:
            raise InvalidHandStateError("No community street follows the current phase.") from None
        drawn = deck.draw(board_count + 1)
        reset_participants = tuple(
            replace(participant, street_committed=0) for participant in snapshot.participants
        )
        next_snapshot = replace(
            snapshot,
            phase=next_phase,
            participants=reset_participants,
            board=snapshot.board + drawn[1:],
            burn_count=snapshot.burn_count + 1,
            betting_round=None,
            deck_remaining_count=deck.remaining_count,
        )
        return next_snapshot, (*burn_cards, drawn[0])

    @staticmethod
    def _terminalize(
        snapshot: HoldemHandSnapshot,
        *,
        phase: HoldemHandPhase,
        uncontested_player: PlayerId | None,
    ) -> HoldemHandSnapshot:
        result = construct_pots(
            PotContribution(
                participant.player_id,
                participant.gross_committed,
                (
                    PotEligibilityStatus.FOLDED
                    if participant.status is HoldemParticipantStatus.FOLDED
                    else PotEligibilityStatus.LIVE
                ),
            )
            for participant in snapshot.participants
        )
        refund_by_id = (
            {}
            if result.uncalled_excess is None
            else {result.uncalled_excess.player_id: result.uncalled_excess.chips}
        )
        participants = tuple(
            replace(
                participant,
                current_stack=ChipStack(
                    participant.current_stack.chips + refund_by_id.get(participant.player_id, 0)
                ),
                returned_excess=refund_by_id.get(participant.player_id, 0),
            )
            for participant in snapshot.participants
        )
        return replace(
            snapshot,
            phase=phase,
            participants=participants,
            betting_round=None,
            pot_result=result,
            uncontested_player=uncontested_player,
        )

    @staticmethod
    def _validate_start_inputs(table: TableState, small_blind: int, big_blind: int) -> None:
        if not isinstance(table, TableState):
            raise InvalidHandInitializationError("A Hold'em hand requires a TableState.")
        if (
            not isinstance(small_blind, int)
            or isinstance(small_blind, bool)
            or small_blind <= 0
            or not isinstance(big_blind, int)
            or isinstance(big_blind, bool)
            or big_blind <= 0
            or small_blind >= big_blind
        ):
            raise InvalidBlindStructureError(
                "Blinds must be positive integers with small blind below big blind."
            )

    @staticmethod
    def _hand_status(status: BettingParticipantStatus) -> HoldemParticipantStatus:
        return {
            BettingParticipantStatus.ACTIVE: HoldemParticipantStatus.ACTIVE,
            BettingParticipantStatus.FOLDED: HoldemParticipantStatus.FOLDED,
            BettingParticipantStatus.ALL_IN: HoldemParticipantStatus.ALL_IN,
        }[status]

    @staticmethod
    def _next_seat(anchor: SeatIndex, candidates: tuple[SeatIndex, ...]) -> SeatIndex:
        ordered = tuple(sorted(candidates, key=lambda item: item.value))
        return next((seat for seat in ordered if seat.value > anchor.value), ordered[0])

    @staticmethod
    def _clockwise_seats_after(
        anchor: SeatIndex,
        candidates: tuple[SeatIndex, ...],
    ) -> tuple[SeatIndex, ...]:
        ordered = tuple(sorted(candidates, key=lambda item: item.value))
        return tuple(seat for seat in ordered if seat.value > anchor.value) + tuple(
            seat for seat in ordered if seat.value <= anchor.value
        )

    @staticmethod
    def _validate_state(
        snapshot: HoldemHandSnapshot,
        deck: Deck,
        burn_cards: tuple[Card, ...],
        betting_round: BettingRound | None,
    ) -> None:
        participants = snapshot.participants
        if not 2 <= len(participants) <= 6:
            raise InvalidHandStateError("A Hold'em hand requires two through six participants.")
        if tuple(sorted(participants, key=lambda item: item.seat_index.value)) != participants:
            raise InvalidHandStateError("Hand participants must be seat ordered.")
        if len({participant.player_id for participant in participants}) != len(participants):
            raise InvalidHandStateError("Hand participant IDs must be unique.")
        if len({participant.seat_index for participant in participants}) != len(participants):
            raise InvalidHandStateError("Hand participant seats must be unique.")

        total_starting = 0
        total_reconciled = 0
        for participant in participants:
            if participant.gross_committed < 0 or not (
                0 <= participant.returned_excess <= participant.gross_committed
            ):
                raise HandChipAccountingError("Hand commitments or refunds are invalid.")
            if not 0 <= participant.street_committed <= participant.gross_committed:
                raise HandChipAccountingError("A street commitment is outside hand commitment.")
            if participant.starting_stack.chips != (
                participant.current_stack.chips
                + participant.gross_committed
                - participant.returned_excess
            ):
                raise HandChipAccountingError(
                    "Participant stack, gross commitment, and refund do not reconcile."
                )
            if (
                participant.status is HoldemParticipantStatus.ACTIVE
                and participant.current_stack.chips == 0
            ):
                raise InvalidHandStateError("An active hand participant requires chips.")
            if (
                participant.status is HoldemParticipantStatus.ALL_IN
                and participant.current_stack.chips != participant.returned_excess
            ):
                raise InvalidHandStateError(
                    "An all-in participant can retain only returned uncalled excess."
                )
            total_starting += participant.starting_stack.chips
            total_reconciled += (
                participant.current_stack.chips
                + participant.gross_committed
                - participant.returned_excess
            )
        if total_starting != total_reconciled:
            raise HandChipAccountingError("Global hand chip accounting does not reconcile.")

        board_sizes = {
            HoldemHandPhase.PREFLOP: {0},
            HoldemHandPhase.FLOP: {3},
            HoldemHandPhase.TURN: {4},
            HoldemHandPhase.RIVER: {5},
            HoldemHandPhase.SHOWDOWN_READY: {5},
            HoldemHandPhase.COMPLETE_BY_FOLD: {0, 3, 4, 5},
        }
        if len(snapshot.board) not in board_sizes[snapshot.phase]:
            raise InvalidDealingStateError("Board size does not match the hand phase.")
        expected_burns = {0: 0, 3: 1, 4: 2, 5: 3}[len(snapshot.board)]
        if snapshot.burn_count != expected_burns or len(burn_cards) != expected_burns:
            raise InvalidDealingStateError("Burn count does not match the dealt board.")
        dealt = (
            tuple(card for participant in participants for card in participant.hole_cards)
            + snapshot.board
            + burn_cards
        )
        if len(dealt) != len(set(dealt)):
            raise InvalidDealingStateError("A card cannot be dealt or burned twice.")
        if deck.remaining_count + len(dealt) != 52:
            raise InvalidDealingStateError("Deck consumption does not reconcile to 52 cards.")
        if snapshot.deck_remaining_count != deck.remaining_count:
            raise InvalidDealingStateError("Snapshot deck count is not authoritative.")

        if snapshot.terminal:
            if betting_round is not None or snapshot.betting_round is not None:
                raise InvalidHandStateError("A terminal hand cannot retain a betting round.")
            if snapshot.pot_result is None:
                raise InvalidHandStateError("A terminal hand requires pot construction.")
            if (
                sum(participant.current_stack.chips for participant in participants)
                + (snapshot.pot_result.total_pot_chips)
                != total_starting
            ):
                raise HandChipAccountingError("Terminal stacks and pots do not reconcile globally.")
            expected_contributions = {
                participant.player_id: (
                    participant.gross_committed,
                    participant.status is HoldemParticipantStatus.FOLDED,
                )
                for participant in participants
            }
            actual_contributions = {
                contribution.player_id: (
                    contribution.committed,
                    contribution.status is PotEligibilityStatus.FOLDED,
                )
                for contribution in snapshot.pot_result.contributions
            }
            if actual_contributions != expected_contributions:
                raise HandChipAccountingError(
                    "Terminal pots do not match authoritative hand contributions."
                )
            non_folded = tuple(
                participant
                for participant in participants
                if participant.status is not HoldemParticipantStatus.FOLDED
            )
            if snapshot.phase is HoldemHandPhase.COMPLETE_BY_FOLD:
                if len(non_folded) != 1 or snapshot.uncontested_player != non_folded[0].player_id:
                    raise InvalidHandStateError("Fold completion requires one uncontested player.")
            elif len(non_folded) < 2 or snapshot.uncontested_player is not None:
                raise InvalidHandStateError(
                    "Showdown readiness requires at least two non-folded players."
                )
            return

        if snapshot.pot_result is not None or snapshot.uncontested_player is not None:
            raise InvalidHandStateError("An active hand cannot contain terminal results.")
        if any(participant.returned_excess != 0 for participant in participants):
            raise HandChipAccountingError("Refunds can be applied only at terminal state.")
        if betting_round is None or snapshot.betting_round != betting_round.snapshot:
            raise InvalidHandStateError("An active hand requires its authoritative betting round.")
        if betting_round.complete:
            raise InvalidHandStateError("An active hand cannot retain a complete betting round.")
        hand_by_id = {participant.player_id: participant for participant in participants}
        for round_participant in betting_round.participants:
            hand_participant = hand_by_id.get(round_participant.player_id)
            if hand_participant is None:
                raise BettingRoundReconciliationError(
                    "A betting participant must belong to the hand."
                )
            if (
                hand_participant.current_stack != round_participant.remaining_stack
                or hand_participant.street_committed != round_participant.committed
                or hand_participant.status is not HoldemHand._hand_status(round_participant.status)
            ):
                raise BettingRoundReconciliationError(
                    "The active betting round does not match hand participant state."
                )
