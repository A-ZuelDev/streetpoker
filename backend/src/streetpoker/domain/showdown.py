"""Pure terminal Hold'em showdown resolution and contestable-pot settlement."""

from dataclasses import dataclass
from enum import StrEnum

from streetpoker.domain.cards import Card
from streetpoker.domain.chips import ChipStack
from streetpoker.domain.errors import (
    AwardReconciliationError,
    DuplicateSettlementParticipantError,
    HandNotTerminalError,
    InvalidEligiblePlayerError,
    InvalidOddChipOrderingError,
    InvalidSettlementInputError,
    InvalidSettlementResultError,
    InvalidShowdownBoardError,
    MissingPotResultError,
    NoEligibleWinnerError,
)
from streetpoker.domain.hand_evaluation import EvaluatedHand, evaluate_holdem_hand
from streetpoker.domain.holdem_hand import (
    HoldemHandParticipant,
    HoldemHandPhase,
    HoldemHandSnapshot,
    HoldemParticipantStatus,
)
from streetpoker.domain.players import PlayerId
from streetpoker.domain.pots import Pot, PotConstructionResult, PotEligibilityStatus
from streetpoker.domain.table import SeatIndex


class SettlementSource(StrEnum):
    """The terminal lifecycle path that produced a settlement."""

    SHOWDOWN = "showdown_ready"
    UNCONTESTED = "complete_by_fold"


@dataclass(frozen=True, slots=True)
class PlayerHandEvaluation:
    """One live participant's private, authoritative showdown evaluation."""

    player_id: PlayerId
    evaluated_hand: EvaluatedHand

    def __post_init__(self) -> None:
        if not isinstance(self.player_id, PlayerId) or not isinstance(
            self.evaluated_hand, EvaluatedHand
        ):
            raise InvalidSettlementResultError(
                "A player hand evaluation requires PlayerId and EvaluatedHand values."
            )


@dataclass(frozen=True, slots=True)
class WinnerShare:
    """One tied or sole winner's exact share of one pot."""

    player_id: PlayerId
    seat_index: SeatIndex
    chips: int
    receives_odd_chip: bool

    def __post_init__(self) -> None:
        if not isinstance(self.player_id, PlayerId) or not isinstance(self.seat_index, SeatIndex):
            raise InvalidSettlementResultError("A winner share requires a player and seat.")
        if not _is_strict_int(self.chips) or self.chips <= 0:
            raise InvalidSettlementResultError("A winner share must contain positive chips.")
        if not isinstance(self.receives_odd_chip, bool):
            raise InvalidSettlementResultError("Odd-chip status must be boolean.")


@dataclass(frozen=True, slots=True)
class PotAward:
    """A complete, independently reconciled award of one constructed pot."""

    pot_index: int
    pot: Pot
    button_position: SeatIndex
    participant_seats: tuple[SeatIndex, ...]
    base_share: int
    winner_shares: tuple[WinnerShare, ...]

    def __post_init__(self) -> None:
        if not _is_strict_int(self.pot_index) or self.pot_index < 0:
            raise InvalidSettlementResultError("A pot index must be a nonnegative integer.")
        if not isinstance(self.pot, Pot):
            raise InvalidSettlementResultError("A pot award requires a Pot.")
        _validate_seat_ring(self.participant_seats, self.button_position)
        if not _is_strict_int(self.base_share) or self.base_share <= 0:
            raise InvalidSettlementResultError("A pot award requires a positive base share.")
        if not isinstance(self.winner_shares, tuple) or not self.winner_shares:
            raise InvalidSettlementResultError("A pot award requires at least one winner.")
        if any(not isinstance(share, WinnerShare) for share in self.winner_shares):
            raise InvalidSettlementResultError("Pot winners must be WinnerShare values.")

        winner_ids = tuple(share.player_id for share in self.winner_shares)
        winner_seats = tuple(share.seat_index for share in self.winner_shares)
        if len(set(winner_ids)) != len(winner_ids) or len(set(winner_seats)) != len(winner_seats):
            raise InvalidSettlementResultError("Pot winners and their seats must be unique.")
        if not set(winner_ids) <= self.pot.eligible_players:
            raise InvalidEligiblePlayerError("Every pot winner must be eligible for that pot.")
        if not set(winner_seats) <= set(self.participant_seats):
            raise InvalidSettlementResultError("Every winner seat must belong to the hand ring.")

        expected_order = _clockwise_winner_order(
            participant_seats=self.participant_seats,
            button_position=self.button_position,
            winner_seats=frozenset(winner_seats),
        )
        if winner_seats != expected_order:
            raise InvalidOddChipOrderingError(
                "Pot winners must follow the hand ring clockwise after the button."
            )

        winner_count = len(self.winner_shares)
        expected_base = self.pot.amount // winner_count
        odd_count = self.pot.amount % winner_count
        if self.base_share != expected_base:
            raise AwardReconciliationError("Pot base share does not match integer division.")
        for index, share in enumerate(self.winner_shares):
            should_receive_odd = index < odd_count
            if share.receives_odd_chip is not should_receive_odd:
                raise InvalidOddChipOrderingError(
                    "Odd chips must form the clockwise prefix of tied winners."
                )
            if share.chips != expected_base + int(should_receive_odd):
                raise AwardReconciliationError("A winner share does not match its split amount.")
        if sum(share.chips for share in self.winner_shares) != self.pot.amount:
            raise AwardReconciliationError("Winner shares must award the complete pot.")

    @property
    def winners(self) -> tuple[PlayerId, ...]:
        return tuple(share.player_id for share in self.winner_shares)

    @property
    def odd_chip_recipients(self) -> tuple[PlayerId, ...]:
        return tuple(share.player_id for share in self.winner_shares if share.receives_odd_chip)

    @property
    def awards_by_player(self) -> tuple[tuple[PlayerId, int], ...]:
        return tuple((share.player_id, share.chips) for share in self.winner_shares)


@dataclass(frozen=True, slots=True)
class PlayerSettlement:
    """One participant's pre-award stack, total award, and calculated final stack."""

    player_id: PlayerId
    seat_index: SeatIndex
    folded: bool
    stack_before_awards: ChipStack
    total_award: int
    final_stack: ChipStack

    def __post_init__(self) -> None:
        if not isinstance(self.player_id, PlayerId) or not isinstance(self.seat_index, SeatIndex):
            raise InvalidSettlementResultError("A player settlement requires a player and seat.")
        if not isinstance(self.folded, bool):
            raise InvalidSettlementResultError("A settlement fold flag must be boolean.")
        if not isinstance(self.stack_before_awards, ChipStack) or not isinstance(
            self.final_stack, ChipStack
        ):
            raise InvalidSettlementResultError("Settlement stacks must be ChipStack values.")
        if not _is_strict_int(self.total_award) or self.total_award < 0:
            raise InvalidSettlementResultError("A total award must be nonnegative chips.")
        if self.folded and self.total_award != 0:
            raise InvalidSettlementResultError("A folded participant cannot receive an award.")
        if self.final_stack.chips != self.stack_before_awards.chips + self.total_award:
            raise AwardReconciliationError("A final stack must equal its prior stack plus awards.")


@dataclass(frozen=True, slots=True)
class HandSettlementResult:
    """Canonical immutable settlement of every participant and contestable pot."""

    source: SettlementSource
    evaluations: tuple[PlayerHandEvaluation, ...]
    pot_awards: tuple[PotAward, ...]
    player_settlements: tuple[PlayerSettlement, ...]
    uncontested_winner: PlayerId | None

    def __post_init__(self) -> None:
        if not isinstance(self.source, SettlementSource):
            raise InvalidSettlementResultError("A settlement result requires a source.")
        if not isinstance(self.evaluations, tuple) or any(
            not isinstance(item, PlayerHandEvaluation) for item in self.evaluations
        ):
            raise InvalidSettlementResultError("Evaluations must be an immutable tuple.")
        if (
            not isinstance(self.pot_awards, tuple)
            or not self.pot_awards
            or any(not isinstance(item, PotAward) for item in self.pot_awards)
        ):
            raise InvalidSettlementResultError(
                "Settlement requires an immutable nonempty pot tuple."
            )
        if not isinstance(self.player_settlements, tuple) or any(
            not isinstance(item, PlayerSettlement) for item in self.player_settlements
        ):
            raise InvalidSettlementResultError("Player settlements must be an immutable tuple.")

        settlements = self.player_settlements
        player_ids = tuple(item.player_id for item in settlements)
        seats = tuple(item.seat_index for item in settlements)
        if len(set(player_ids)) != len(player_ids) or len(set(seats)) != len(seats):
            raise DuplicateSettlementParticipantError(
                "Player settlements must have unique identities and seats."
            )
        if tuple(sorted(seats, key=lambda seat: seat.value)) != seats:
            raise InvalidSettlementResultError("Player settlements must be seat ordered.")
        if tuple(award.pot_index for award in self.pot_awards) != tuple(
            range(len(self.pot_awards))
        ):
            raise InvalidSettlementResultError("Pot-award indexes must be unique and contiguous.")
        for award in self.pot_awards:
            if award.participant_seats != seats:
                raise InvalidSettlementResultError(
                    "Every pot award must retain the full hand ring."
                )
        if len({award.button_position for award in self.pot_awards}) != 1:
            raise InvalidSettlementResultError(
                "Every pot award must use the same snapshotted dealer button."
            )

        settlement_by_id = {item.player_id: item for item in settlements}
        evaluation_ids = tuple(item.player_id for item in self.evaluations)
        if len(set(evaluation_ids)) != len(evaluation_ids):
            raise InvalidSettlementResultError("A player may be evaluated only once.")
        live_ids = tuple(item.player_id for item in settlements if not item.folded)
        if self.source is SettlementSource.SHOWDOWN:
            if evaluation_ids != live_ids:
                raise InvalidSettlementResultError(
                    "Showdown must evaluate every live player in seat order."
                )
            if self.uncontested_winner is not None:
                raise InvalidSettlementResultError("Showdown cannot name an uncontested winner.")
        else:
            if self.evaluations:
                raise InvalidSettlementResultError("Fold completion cannot contain evaluations.")
            if len(live_ids) != 1 or self.uncontested_winner != live_ids[0]:
                raise InvalidSettlementResultError(
                    "Fold completion requires its sole live player as winner."
                )

        awards_by_id = {player_id: 0 for player_id in player_ids}
        evaluation_by_id = {
            evaluation.player_id: evaluation.evaluated_hand for evaluation in self.evaluations
        }
        for pot_award in self.pot_awards:
            contributor_settlements = tuple(
                settlement_by_id.get(player_id) for player_id in pot_award.pot.contributors
            )
            if any(item is None for item in contributor_settlements):
                raise InvalidSettlementResultError(
                    "Every pot contributor must have exactly one player settlement."
                )
            expected_eligible = frozenset(
                item.player_id
                for item in contributor_settlements
                if item is not None and not item.folded
            )
            if pot_award.pot.eligible_players != expected_eligible:
                raise InvalidEligiblePlayerError(
                    "Pot eligibility must equal its non-folded contributor subset."
                )
            if self.source is SettlementSource.SHOWDOWN:
                try:
                    maximum = max(
                        evaluation_by_id[player_id].hand_rank
                        for player_id in pot_award.pot.eligible_players
                    )
                except KeyError:
                    raise InvalidSettlementResultError(
                        "Every eligible showdown player must have an evaluation."
                    ) from None
                expected_winners = {
                    player_id
                    for player_id in pot_award.pot.eligible_players
                    if evaluation_by_id[player_id].hand_rank == maximum
                }
                if set(pot_award.winners) != expected_winners:
                    raise InvalidSettlementResultError(
                        "Pot winners must be every eligible player tied at maximum rank."
                    )
            elif set(pot_award.winners) != {self.uncontested_winner}:
                raise InvalidSettlementResultError(
                    "Every uncontested pot must be awarded to the sole live player."
                )
            for share in pot_award.winner_shares:
                settlement = settlement_by_id.get(share.player_id)
                if settlement is None:
                    raise InvalidSettlementResultError("A pot winner must have a settlement.")
                if settlement.folded:
                    raise InvalidSettlementResultError("A folded player cannot win a pot.")
                if settlement.seat_index != share.seat_index:
                    raise InvalidSettlementResultError(
                        "A winner share seat must match that player's settlement seat."
                    )
                awards_by_id[share.player_id] += share.chips
        for settlement in settlements:
            if settlement.total_award != awards_by_id[settlement.player_id]:
                raise AwardReconciliationError(
                    "A player's total award must equal all of that player's pot shares."
                )

        if self.total_awarded != self.total_pot_chips:
            raise AwardReconciliationError(
                "All contestable pot chips must be awarded exactly once."
            )
        prior_total = sum(item.stack_before_awards.chips for item in settlements)
        final_total = sum(item.final_stack.chips for item in settlements)
        if prior_total + self.total_pot_chips != final_total:
            raise AwardReconciliationError("Settlement does not conserve chips globally.")

    @property
    def total_pot_chips(self) -> int:
        return sum(award.pot.amount for award in self.pot_awards)

    @property
    def total_awarded(self) -> int:
        return sum(share.chips for award in self.pot_awards for share in award.winner_shares)


@dataclass(frozen=True, slots=True)
class _SettlementParticipant:
    player_id: PlayerId
    seat_index: SeatIndex
    stack_before_awards: ChipStack
    folded: bool
    hole_cards: tuple[Card, Card] | None


@dataclass(frozen=True, slots=True)
class _HandSettlementInput:
    source: SettlementSource
    participants: tuple[_SettlementParticipant, ...]
    button_position: SeatIndex
    board: tuple[Card, ...]
    pot_result: PotConstructionResult
    uncontested_player: PlayerId | None


def settle_holdem_hand(snapshot: HoldemHandSnapshot) -> HandSettlementResult:
    """Validate and settle one terminal authoritative Hold'em snapshot without mutation."""
    settlement_input = _validated_settlement_input(snapshot)
    participants = settlement_input.participants
    participant_seats = tuple(participant.seat_index for participant in participants)

    evaluations: tuple[PlayerHandEvaluation, ...]
    evaluated_by_id: dict[PlayerId, EvaluatedHand]
    if settlement_input.source is SettlementSource.SHOWDOWN:
        evaluated: list[PlayerHandEvaluation] = []
        for participant in participants:
            if participant.folded:
                continue
            if participant.hole_cards is None:  # pragma: no cover - protected by adapter
                raise InvalidSettlementInputError("A live showdown player requires hole cards.")
            item = PlayerHandEvaluation(
                player_id=participant.player_id,
                evaluated_hand=evaluate_holdem_hand(
                    participant.hole_cards,
                    settlement_input.board,
                ),
            )
            evaluated.append(item)
        evaluations = tuple(evaluated)
        evaluated_by_id = {item.player_id: item.evaluated_hand for item in evaluations}
    else:
        evaluations = ()
        evaluated_by_id = {}

    pot_awards: list[PotAward] = []
    total_by_id = {participant.player_id: 0 for participant in participants}
    for pot_index, pot in enumerate(settlement_input.pot_result.pots):
        if settlement_input.source is SettlementSource.UNCONTESTED:
            winner = settlement_input.uncontested_player
            if winner is None or pot.eligible_players != frozenset({winner}):
                raise NoEligibleWinnerError(
                    "Every uncontested pot must be eligible only to the surviving player."
                )
            winner_ids = frozenset({winner})
        else:
            try:
                eligible_evaluations = {
                    player_id: evaluated_by_id[player_id] for player_id in pot.eligible_players
                }
            except KeyError:
                raise NoEligibleWinnerError(
                    "Every eligible showdown player must have an evaluation."
                ) from None
            if not eligible_evaluations:
                raise NoEligibleWinnerError("A contestable pot requires an eligible winner.")
            maximum = max(item.hand_rank for item in eligible_evaluations.values())
            winner_ids = frozenset(
                player_id
                for player_id, item in eligible_evaluations.items()
                if item.hand_rank == maximum
            )

        ordered_winners = _ordered_winner_participants(
            participants=participants,
            button_position=settlement_input.button_position,
            winner_ids=winner_ids,
        )
        if not ordered_winners:
            raise NoEligibleWinnerError("A contestable pot produced no winner.")
        base_share, odd_count = divmod(pot.amount, len(ordered_winners))
        shares = tuple(
            WinnerShare(
                player_id=participant.player_id,
                seat_index=participant.seat_index,
                chips=base_share + int(index < odd_count),
                receives_odd_chip=index < odd_count,
            )
            for index, participant in enumerate(ordered_winners)
        )
        award = PotAward(
            pot_index=pot_index,
            pot=pot,
            button_position=settlement_input.button_position,
            participant_seats=participant_seats,
            base_share=base_share,
            winner_shares=shares,
        )
        pot_awards.append(award)
        for share in shares:
            total_by_id[share.player_id] += share.chips

    player_settlements = tuple(
        PlayerSettlement(
            player_id=participant.player_id,
            seat_index=participant.seat_index,
            folded=participant.folded,
            stack_before_awards=participant.stack_before_awards,
            total_award=total_by_id[participant.player_id],
            final_stack=ChipStack(
                participant.stack_before_awards.chips + total_by_id[participant.player_id]
            ),
        )
        for participant in participants
    )
    return HandSettlementResult(
        source=settlement_input.source,
        evaluations=evaluations,
        pot_awards=tuple(pot_awards),
        player_settlements=player_settlements,
        uncontested_winner=settlement_input.uncontested_player,
    )


def _validated_settlement_input(snapshot: HoldemHandSnapshot) -> _HandSettlementInput:
    if not isinstance(snapshot, HoldemHandSnapshot):
        raise InvalidSettlementInputError("Settlement requires a HoldemHandSnapshot.")
    if snapshot.phase not in {
        HoldemHandPhase.SHOWDOWN_READY,
        HoldemHandPhase.COMPLETE_BY_FOLD,
    }:
        raise HandNotTerminalError("Only a terminal Hold'em hand can be settled.")
    if snapshot.betting_round is not None:
        raise InvalidSettlementInputError("A terminal hand cannot retain a betting round.")
    if snapshot.pot_result is None:
        raise MissingPotResultError("A terminal hand requires a pot-construction result.")
    if not isinstance(snapshot.pot_result, PotConstructionResult) or not snapshot.pot_result.pots:
        raise InvalidSettlementInputError("A terminal hand requires at least one constructed pot.")

    participants = snapshot.participants
    if not isinstance(participants, tuple) or not 2 <= len(participants) <= 6:
        raise InvalidSettlementInputError("Settlement requires two through six participants.")
    if any(not isinstance(participant, HoldemHandParticipant) for participant in participants):
        raise InvalidSettlementInputError("Settlement participants must be hand participants.")

    total_starting = 0
    total_current = 0
    all_hole_cards: list[Card] = []
    for participant in participants:
        _validate_terminal_participant(participant)
        total_starting += participant.starting_stack.chips
        total_current += participant.current_stack.chips
        all_hole_cards.extend(participant.hole_cards)

    if tuple(sorted(participants, key=lambda item: item.seat_index.value)) != participants:
        raise InvalidSettlementInputError("Hand participants must be seat ordered.")
    player_ids = tuple(participant.player_id for participant in participants)
    seats = tuple(participant.seat_index for participant in participants)
    if len(set(player_ids)) != len(player_ids) or len(set(seats)) != len(seats):
        raise DuplicateSettlementParticipantError(
            "Settlement participant identities and seats must be unique."
        )
    _validate_seat_ring(seats, snapshot.button_position)

    if not isinstance(snapshot.board, tuple) or any(
        not isinstance(card, Card) for card in snapshot.board
    ):
        raise InvalidShowdownBoardError("A terminal board must be a tuple of Cards.")
    if len(set((*all_hole_cards, *snapshot.board))) != len(all_hole_cards) + len(snapshot.board):
        raise InvalidSettlementInputError("Dealt hole and board cards must be unique.")

    expected_board_sizes = {
        HoldemHandPhase.SHOWDOWN_READY: {5},
        HoldemHandPhase.COMPLETE_BY_FOLD: {0, 3, 4, 5},
    }
    if len(snapshot.board) not in expected_board_sizes[snapshot.phase]:
        raise InvalidShowdownBoardError("Board size does not match the terminal hand phase.")
    expected_burns = {0: 0, 3: 1, 4: 2, 5: 3}[len(snapshot.board)]
    if not _is_strict_int(snapshot.burn_count) or snapshot.burn_count != expected_burns:
        raise InvalidSettlementInputError("Burn count does not match the terminal board.")
    if (
        not _is_strict_int(snapshot.deck_remaining_count)
        or snapshot.deck_remaining_count < 0
        or snapshot.deck_remaining_count
        + len(all_hole_cards)
        + len(snapshot.board)
        + snapshot.burn_count
        != 52
    ):
        raise InvalidSettlementInputError("Terminal deck consumption does not reconcile.")

    contribution_by_id = {
        contribution.player_id: contribution for contribution in snapshot.pot_result.contributions
    }
    if set(contribution_by_id) != set(player_ids):
        raise InvalidSettlementInputError("Pot contributions must match all hand participants.")
    for participant in participants:
        contribution = contribution_by_id[participant.player_id]
        expected_status = (
            PotEligibilityStatus.FOLDED
            if participant.status is HoldemParticipantStatus.FOLDED
            else PotEligibilityStatus.LIVE
        )
        if (
            contribution.committed != participant.gross_committed
            or contribution.status is not expected_status
        ):
            raise InvalidSettlementInputError(
                "Pot contributions must match gross commitments and fold state."
            )

    expected_refund_by_id = (
        {}
        if snapshot.pot_result.uncalled_excess is None
        else {
            snapshot.pot_result.uncalled_excess.player_id: snapshot.pot_result.uncalled_excess.chips
        }
    )
    for participant in participants:
        if participant.returned_excess != expected_refund_by_id.get(participant.player_id, 0):
            raise InvalidSettlementInputError(
                "Returned excess must exactly match the pot-construction result."
            )

    live = tuple(
        participant
        for participant in participants
        if participant.status is not HoldemParticipantStatus.FOLDED
    )
    participant_by_id = {participant.player_id: participant for participant in participants}
    for pot in snapshot.pot_result.pots:
        if any(player_id not in participant_by_id for player_id in pot.eligible_players):
            raise InvalidEligiblePlayerError("Every eligible pot player must belong to the hand.")
        if any(
            participant_by_id[player_id].status is HoldemParticipantStatus.FOLDED
            for player_id in pot.eligible_players
        ):
            raise InvalidEligiblePlayerError("A folded player cannot be eligible for a pot.")

    if total_current + snapshot.pot_result.total_pot_chips != total_starting:
        raise AwardReconciliationError(
            "Pre-award stacks and contestable pots must equal starting chips."
        )

    if snapshot.phase is HoldemHandPhase.SHOWDOWN_READY:
        if len(live) < 2 or snapshot.uncontested_player is not None:
            raise InvalidSettlementInputError(
                "Showdown readiness requires at least two live players and no fold winner."
            )
        source = SettlementSource.SHOWDOWN
        board = snapshot.board
    else:
        if len(live) != 1 or snapshot.uncontested_player != live[0].player_id:
            raise InvalidSettlementInputError(
                "Fold completion requires exactly one matching uncontested player."
            )
        for pot in snapshot.pot_result.pots:
            if pot.eligible_players != frozenset({live[0].player_id}):
                raise InvalidEligiblePlayerError(
                    "Every fold-completion pot must belong only to the survivor."
                )
        source = SettlementSource.UNCONTESTED
        board = ()

    compact_participants = tuple(
        _SettlementParticipant(
            player_id=participant.player_id,
            seat_index=participant.seat_index,
            stack_before_awards=participant.current_stack,
            folded=participant.status is HoldemParticipantStatus.FOLDED,
            hole_cards=(
                participant.hole_cards
                if source is SettlementSource.SHOWDOWN
                and participant.status is not HoldemParticipantStatus.FOLDED
                else None
            ),
        )
        for participant in participants
    )
    return _HandSettlementInput(
        source=source,
        participants=compact_participants,
        button_position=snapshot.button_position,
        board=board,
        pot_result=snapshot.pot_result,
        uncontested_player=snapshot.uncontested_player,
    )


def _validate_terminal_participant(participant: HoldemHandParticipant) -> None:
    if not isinstance(participant.player_id, PlayerId) or not isinstance(
        participant.seat_index, SeatIndex
    ):
        raise InvalidSettlementInputError("A hand participant requires a player and seat.")
    if not isinstance(participant.starting_stack, ChipStack) or not isinstance(
        participant.current_stack, ChipStack
    ):
        raise InvalidSettlementInputError("Hand participant stacks must be ChipStack values.")
    if any(
        not _is_strict_int(value)
        for value in (
            participant.gross_committed,
            participant.street_committed,
            participant.returned_excess,
        )
    ):
        raise InvalidSettlementInputError("Commitments and refunds must be integer chips.")
    if participant.gross_committed < 0 or not (
        0 <= participant.returned_excess <= participant.gross_committed
    ):
        raise InvalidSettlementInputError("Gross commitments or returned excess are invalid.")
    if not 0 <= participant.street_committed <= participant.gross_committed:
        raise InvalidSettlementInputError("Street commitment is outside gross commitment.")
    if not isinstance(participant.status, HoldemParticipantStatus):
        raise InvalidSettlementInputError("A participant requires a Hold'em status.")
    if participant.starting_stack.chips != (
        participant.current_stack.chips + participant.gross_committed - participant.returned_excess
    ):
        raise AwardReconciliationError(
            "Participant starting stack, current stack, commitment, and refund do not reconcile."
        )
    if (
        participant.status is HoldemParticipantStatus.ACTIVE
        and participant.current_stack.chips == 0
    ):
        raise InvalidSettlementInputError("An active participant requires chips.")
    if (
        participant.status is HoldemParticipantStatus.ALL_IN
        and participant.current_stack.chips != participant.returned_excess
    ):
        raise InvalidSettlementInputError(
            "An all-in participant can retain only returned uncalled excess."
        )
    if (
        not isinstance(participant.hole_cards, tuple)
        or len(participant.hole_cards) != 2
        or any(not isinstance(card, Card) for card in participant.hole_cards)
        or len(set(participant.hole_cards)) != 2
    ):
        raise InvalidSettlementInputError("Every hand participant requires two unique hole cards.")


def _ordered_winner_participants(
    *,
    participants: tuple[_SettlementParticipant, ...],
    button_position: SeatIndex,
    winner_ids: frozenset[PlayerId],
) -> tuple[_SettlementParticipant, ...]:
    seats = tuple(participant.seat_index for participant in participants)
    ordered_seats = _clockwise_winner_order(
        participant_seats=seats,
        button_position=button_position,
        winner_seats=frozenset(
            participant.seat_index
            for participant in participants
            if participant.player_id in winner_ids
        ),
    )
    participant_by_seat = {participant.seat_index: participant for participant in participants}
    ordered = tuple(participant_by_seat[seat] for seat in ordered_seats)
    if {participant.player_id for participant in ordered} != set(winner_ids):
        raise NoEligibleWinnerError("Every selected winner must belong to the hand ring.")
    return ordered


def _clockwise_winner_order(
    *,
    participant_seats: tuple[SeatIndex, ...],
    button_position: SeatIndex,
    winner_seats: frozenset[SeatIndex],
) -> tuple[SeatIndex, ...]:
    _validate_seat_ring(participant_seats, button_position)
    if not winner_seats <= set(participant_seats):
        raise InvalidOddChipOrderingError("Winner seats must belong to the hand ring.")
    button_index = participant_seats.index(button_position)
    traversal = participant_seats[button_index + 1 :] + participant_seats[: button_index + 1]
    return tuple(seat for seat in traversal if seat in winner_seats)


def _validate_seat_ring(
    participant_seats: tuple[SeatIndex, ...],
    button_position: SeatIndex,
) -> None:
    if (
        not isinstance(participant_seats, tuple)
        or not participant_seats
        or any(not isinstance(seat, SeatIndex) for seat in participant_seats)
    ):
        raise InvalidOddChipOrderingError("The hand seat ring must contain SeatIndex values.")
    if len(set(participant_seats)) != len(participant_seats):
        raise InvalidOddChipOrderingError("The hand seat ring cannot repeat a seat.")
    if tuple(sorted(participant_seats, key=lambda seat: seat.value)) != participant_seats:
        raise InvalidOddChipOrderingError("The hand seat ring must be seat ordered.")
    if not isinstance(button_position, SeatIndex) or button_position not in participant_seats:
        raise InvalidOddChipOrderingError("The dealer button must belong to the hand seat ring.")


def _is_strict_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
