"""Small deterministic harness for exercising complete public Hold'em domain APIs."""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from enum import StrEnum

from streetpoker.domain import (
    ActionKind,
    BettingParticipantStatus,
    BettingRoundSnapshot,
    ChipStack,
    HandSettlementResult,
    HoldemHand,
    HoldemHandPhase,
    HoldemHandSnapshot,
    HoldemParticipantStatus,
    HoldemTransitionResult,
    ParticipationStatus,
    PlayerId,
    PotEligibilityStatus,
    SeatIndex,
    SeededRandomSource,
    TableState,
    evaluate_holdem_hand,
    settle_holdem_hand,
)


class ActionIntent(StrEnum):
    """A small domain-neutral preference resolved through ``legal_actions()``."""

    FOLD = "fold"
    PASSIVE = "passive"
    MIN_BET = "min_bet"
    MIN_RAISE = "min_raise"
    MAX_BET = "max_bet"
    MAX_RAISE = "max_raise"
    SHORT_ALL_IN = "short_all_in"
    INTERIOR_WAGER = "interior_wager"


@dataclass(frozen=True, slots=True)
class HandScenario:
    """Complete reproducible input for one simulated hand."""

    seats: tuple[int, ...]
    starting_stacks: tuple[int, ...]
    button: int
    small_blind: int
    big_blind: int
    deck_seed: int
    action_intents: tuple[ActionIntent, ...]

    def __post_init__(self) -> None:
        assert 2 <= len(self.seats) <= 6
        assert len(self.starting_stacks) == len(self.seats)
        assert tuple(sorted(self.seats)) == self.seats
        assert len(set(self.seats)) == len(self.seats)
        assert all(0 <= seat <= 5 for seat in self.seats)
        assert self.button in self.seats
        assert all(stack > 0 for stack in self.starting_stacks)
        assert 0 < self.small_blind < self.big_blind
        assert all(isinstance(intent, ActionIntent) for intent in self.action_intents)


@dataclass(frozen=True, slots=True)
class ResolvedAction:
    """One exact public command and its resulting public transition facts."""

    action_index: int
    phase_before: HoldemHandPhase
    player_id: PlayerId
    action_kind: ActionKind
    total: int | None
    transition: HoldemTransitionResult


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """Complete trace and terminal result of one simulated hand."""

    scenario: HandScenario
    actions: tuple[ResolvedAction, ...]
    snapshots: tuple[HoldemHandSnapshot, ...]
    settlement: HandSettlementResult


def start_scenario(scenario: HandScenario) -> HoldemHand:
    """Create a real table and hand solely through public domain APIs."""
    table = TableState.six_max()
    for index, (seat, stack) in enumerate(
        zip(scenario.seats, scenario.starting_stacks, strict=True)
    ):
        table.seat_player(
            seat_index=SeatIndex(seat),
            player_id=PlayerId(f"P{index}"),
            stack=ChipStack(stack),
            status=ParticipationStatus.SITTING_IN,
        )
    for _ in range(table.capacity + 1):
        if table.move_button() == SeatIndex(scenario.button):
            break
    else:  # pragma: no cover - protected by HandScenario validation
        raise AssertionError("Scenario button could not be placed.")
    return HoldemHand.start(
        table=table,
        small_blind=scenario.small_blind,
        big_blind=scenario.big_blind,
        random_source=SeededRandomSource(scenario.deck_seed),
    )


def run_simulation(
    scenario: HandScenario,
    *,
    trace_sink: list[ResolvedAction] | None = None,
) -> SimulationResult:
    """Resolve intents, drain to terminal, assert invariants, and settle one hand."""
    hand = start_scenario(scenario)
    initial = hand.snapshot
    snapshots = [initial]
    actions: list[ResolvedAction] = []
    starting_total = sum(participant.starting_stack.chips for participant in initial.participants)
    _assert_snapshot_invariants(initial, starting_total=starting_total)

    max_actions = len(scenario.action_intents) + 4 * len(scenario.seats) + 8
    while not hand.snapshot.terminal:
        if len(actions) >= max_actions:
            raise AssertionError(
                f"Hand failed bounded progress after {max_actions} actions: {scenario!r}"
            )
        before = hand.snapshot
        legal = hand.legal_actions()
        intent = (
            scenario.action_intents[len(actions)]
            if len(actions) < len(scenario.action_intents)
            else ActionIntent.PASSIVE
        )
        action_kind, total, transition = _execute_intent(hand, intent)
        resolved = ResolvedAction(
            action_index=len(actions),
            phase_before=before.phase,
            player_id=legal.player_id,
            action_kind=action_kind,
            total=total,
            transition=transition,
        )
        actions.append(resolved)
        if trace_sink is not None:
            trace_sink.append(resolved)
        after = hand.snapshot
        _assert_transition_invariants(
            before,
            after,
            resolved,
            starting_total=starting_total,
        )
        snapshots.append(after)

    settlement = assert_terminal_and_settle(hand.snapshot)
    return SimulationResult(
        scenario=scenario,
        actions=tuple(actions),
        snapshots=tuple(snapshots),
        settlement=settlement,
    )


def replay_resolved_actions(
    scenario: HandScenario,
    expected_actions: tuple[ResolvedAction, ...],
) -> SimulationResult:
    """Replay an exact recorded command trace through the public hand API."""
    hand = start_scenario(scenario)
    initial = hand.snapshot
    snapshots = [initial]
    replayed: list[ResolvedAction] = []
    starting_total = sum(participant.starting_stack.chips for participant in initial.participants)
    _assert_snapshot_invariants(initial, starting_total=starting_total)

    for expected in expected_actions:
        assert not hand.snapshot.terminal
        before = hand.snapshot
        legal = hand.legal_actions()
        assert legal.player_id == expected.player_id
        transition = _execute_resolved(hand, expected)
        actual = ResolvedAction(
            action_index=len(replayed),
            phase_before=before.phase,
            player_id=legal.player_id,
            action_kind=expected.action_kind,
            total=expected.total,
            transition=transition,
        )
        assert actual == expected
        after = hand.snapshot
        _assert_transition_invariants(
            before,
            after,
            actual,
            starting_total=starting_total,
        )
        replayed.append(actual)
        snapshots.append(after)

    assert hand.snapshot.terminal
    settlement = assert_terminal_and_settle(hand.snapshot)
    return SimulationResult(
        scenario=scenario,
        actions=tuple(replayed),
        snapshots=tuple(snapshots),
        settlement=settlement,
    )


def assert_terminal_and_settle(snapshot: HoldemHandSnapshot) -> HandSettlementResult:
    """Assert terminal pot invariants, settle, and assert award invariants."""
    assert snapshot.phase in {
        HoldemHandPhase.SHOWDOWN_READY,
        HoldemHandPhase.COMPLETE_BY_FOLD,
    }
    assert snapshot.terminal
    assert snapshot.betting_round is None
    pot_result = snapshot.pot_result
    assert pot_result is not None

    participant_by_id = {
        participant.player_id: participant for participant in snapshot.participants
    }
    contribution_by_id = {
        contribution.player_id: contribution for contribution in pot_result.contributions
    }
    assert set(contribution_by_id) == set(participant_by_id)
    for player_id, participant in participant_by_id.items():
        contribution = contribution_by_id[player_id]
        assert contribution.committed == participant.gross_committed
        assert (contribution.status is PotEligibilityStatus.FOLDED) is (
            participant.status is HoldemParticipantStatus.FOLDED
        )

    gross_total = sum(participant.gross_committed for participant in snapshot.participants)
    returned_total = sum(participant.returned_excess for participant in snapshot.participants)
    assert gross_total == pot_result.total_pot_chips + returned_total
    assert returned_total == pot_result.total_returned_chips

    descending = sorted(
        (participant.gross_committed for participant in snapshot.participants),
        reverse=True,
    )
    highest, second_highest = descending[:2]
    maximum_participants = tuple(
        participant
        for participant in snapshot.participants
        if participant.gross_committed == highest
    )
    expected_excess = highest - second_highest if len(maximum_participants) == 1 else 0
    if expected_excess:
        assert pot_result.uncalled_excess is not None
        assert pot_result.uncalled_excess.player_id == maximum_participants[0].player_id
        assert pot_result.uncalled_excess.chips == expected_excess
    else:
        assert pot_result.uncalled_excess is None
    for participant in snapshot.participants:
        expected_return = (
            expected_excess if expected_excess and participant is maximum_participants[0] else 0
        )
        assert participant.returned_excess == expected_return

    for pot in pot_result.pots:
        assert pot.eligible_players
        assert pot.eligible_players <= pot.contributors
        assert all(
            participant_by_id[player_id].status is not HoldemParticipantStatus.FOLDED
            for player_id in pot.eligible_players
        )
    starting_total = sum(participant.starting_stack.chips for participant in snapshot.participants)
    current_total = sum(participant.current_stack.chips for participant in snapshot.participants)
    assert current_total + pot_result.total_pot_chips == starting_total

    before = snapshot
    settlement = settle_holdem_hand(snapshot)
    assert snapshot == before
    assert len(settlement.pot_awards) == len(pot_result.pots)
    assert tuple(award.pot_index for award in settlement.pot_awards) == tuple(
        range(len(pot_result.pots))
    )
    assert tuple(award.pot for award in settlement.pot_awards) == pot_result.pots
    assert len({award.pot for award in settlement.pot_awards}) == len(pot_result.pots)

    awarded_by_id = {player_id: 0 for player_id in participant_by_id}
    for award in settlement.pot_awards:
        assert set(award.winners) <= award.pot.eligible_players
        assert sum(share.chips for share in award.winner_shares) == award.pot.amount
        for share in award.winner_shares:
            awarded_by_id[share.player_id] += share.chips
    assert settlement.total_awarded == pot_result.total_pot_chips

    settlements_by_id = {item.player_id: item for item in settlement.player_settlements}
    assert set(settlements_by_id) == set(participant_by_id)
    for player_id, item in settlements_by_id.items():
        participant = participant_by_id[player_id]
        assert item.stack_before_awards == participant.current_stack
        assert item.total_award == awarded_by_id[player_id]
        assert item.final_stack.chips == item.stack_before_awards.chips + item.total_award
        if participant.status is HoldemParticipantStatus.FOLDED:
            assert item.total_award == 0
    assert sum(item.final_stack.chips for item in settlement.player_settlements) == starting_total

    if snapshot.phase is HoldemHandPhase.SHOWDOWN_READY:
        live = tuple(
            participant
            for participant in snapshot.participants
            if participant.status is not HoldemParticipantStatus.FOLDED
        )
        assert tuple(item.player_id for item in settlement.evaluations) == tuple(
            participant.player_id for participant in live
        )
        evaluation_by_id = {item.player_id: item.evaluated_hand for item in settlement.evaluations}
        for participant in live:
            assert evaluation_by_id[participant.player_id] == evaluate_holdem_hand(
                participant.hole_cards,
                snapshot.board,
            )
        for award in settlement.pot_awards:
            maximum = max(
                evaluation_by_id[player_id].hand_rank for player_id in award.pot.eligible_players
            )
            expected_winners = {
                player_id
                for player_id in award.pot.eligible_players
                if evaluation_by_id[player_id].hand_rank == maximum
            }
            assert set(award.winners) == expected_winners
    else:
        assert not settlement.evaluations

    return settlement


def deterministic_scenario(master_seed: int, case_index: int) -> tuple[int, int, HandScenario]:
    """Derive one independent repeatable stress scenario from seed and case index."""
    case_seed = _derived_seed(master_seed, case_index, "case")
    deck_seed = _derived_seed(master_seed, case_index, "deck")
    action_seed = _derived_seed(master_seed, case_index, "actions")
    scenario_random = random.Random(case_seed)
    action_random = random.Random(action_seed)
    player_count = 2 + case_index % 5
    seats = tuple(sorted(scenario_random.sample(range(6), player_count)))
    button = seats[scenario_random.randrange(player_count)]
    small_blind = scenario_random.randint(1, 50)
    big_blind = scenario_random.randint(small_blind + 1, 100)
    boundary_values = (
        1,
        max(1, small_blind - 1),
        small_blind,
        small_blind + 1,
        max(small_blind + 1, big_blind - 1),
        big_blind,
        big_blind + 1,
        2 * big_blind - 1,
        2 * big_blind,
        2 * big_blind + 1,
        10 * big_blind,
        1000 * big_blind,
    )
    stacks = tuple(
        boundary_values[(case_index + player_index) % len(boundary_values)]
        if player_index == 0 or case_index % 3 == 0
        else scenario_random.randint(1, 100 * big_blind)
        for player_index in range(player_count)
    )
    intents = tuple(
        action_random.choice(tuple(ActionIntent)) for _ in range(action_random.randint(4, 28))
    )
    return (
        case_seed,
        action_seed,
        HandScenario(
            seats=seats,
            starting_stacks=stacks,
            button=button,
            small_blind=small_blind,
            big_blind=big_blind,
            deck_seed=deck_seed,
            action_intents=intents,
        ),
    )


def scenario_as_dict(scenario: HandScenario) -> dict[str, object]:
    """Return a JSON-compatible diagnostic representation."""
    return {
        "seats": list(scenario.seats),
        "starting_stacks": list(scenario.starting_stacks),
        "button": scenario.button,
        "small_blind": scenario.small_blind,
        "big_blind": scenario.big_blind,
        "deck_seed": scenario.deck_seed,
        "action_intents": [intent.value for intent in scenario.action_intents],
    }


def resolved_action_as_dict(action: ResolvedAction) -> dict[str, object]:
    """Return a JSON-compatible exact action representation for failures."""
    return {
        "action_index": action.action_index,
        "phase_before": action.phase_before.value,
        "player_id": action.player_id.value,
        "action_kind": action.action_kind.value,
        "total": action.total,
        "classification": action.transition.betting_result.classification.value,
        "phase_after": action.transition.phase_after.value,
        "board_cards_dealt": action.transition.board_cards_dealt,
        "terminal": action.transition.terminal,
    }


def _execute_intent(
    hand: HoldemHand,
    intent: ActionIntent,
) -> tuple[ActionKind, int | None, HoldemTransitionResult]:
    legal = hand.legal_actions()
    actor = legal.player_id
    if intent is ActionIntent.FOLD:
        return ActionKind.FOLD, None, hand.fold(player_id=actor)
    if (
        intent is ActionIntent.MIN_BET
        and legal.bet is not None
        and legal.bet.maximum_to >= legal.bet.minimum_full_to
    ):
        total = legal.bet.minimum_full_to
        return ActionKind.BET, total, hand.bet_to(player_id=actor, total=total)
    if (
        intent is ActionIntent.MIN_RAISE
        and legal.raise_to is not None
        and legal.raise_to.maximum_to >= legal.raise_to.minimum_full_to
    ):
        total = legal.raise_to.minimum_full_to
        return ActionKind.RAISE, total, hand.raise_to(player_id=actor, total=total)
    if intent is ActionIntent.MAX_BET and legal.bet is not None:
        total = legal.bet.maximum_to
        return ActionKind.BET, total, hand.bet_to(player_id=actor, total=total)
    if intent is ActionIntent.MAX_RAISE and legal.raise_to is not None:
        total = legal.raise_to.maximum_to
        return ActionKind.RAISE, total, hand.raise_to(player_id=actor, total=total)
    if intent is ActionIntent.SHORT_ALL_IN:
        if legal.bet is not None and legal.bet.short_all_in_to is not None:
            total = legal.bet.short_all_in_to
            return ActionKind.BET, total, hand.bet_to(player_id=actor, total=total)
        if legal.raise_to is not None and legal.raise_to.short_all_in_to is not None:
            total = legal.raise_to.short_all_in_to
            return ActionKind.RAISE, total, hand.raise_to(player_id=actor, total=total)
    if intent is ActionIntent.INTERIOR_WAGER:
        if legal.bet is not None and legal.bet.maximum_to - legal.bet.minimum_full_to >= 2:
            total = (legal.bet.minimum_full_to + legal.bet.maximum_to) // 2
            return ActionKind.BET, total, hand.bet_to(player_id=actor, total=total)
        if (
            legal.raise_to is not None
            and legal.raise_to.maximum_to - legal.raise_to.minimum_full_to >= 2
        ):
            total = (legal.raise_to.minimum_full_to + legal.raise_to.maximum_to) // 2
            return ActionKind.RAISE, total, hand.raise_to(player_id=actor, total=total)
    return _execute_passive(hand)


def _execute_passive(
    hand: HoldemHand,
) -> tuple[ActionKind, None, HoldemTransitionResult]:
    legal = hand.legal_actions()
    if ActionKind.CHECK in legal.kinds:
        return ActionKind.CHECK, None, hand.check(player_id=legal.player_id)
    if ActionKind.CALL in legal.kinds:
        return ActionKind.CALL, None, hand.call(player_id=legal.player_id)
    assert ActionKind.FOLD in legal.kinds
    return ActionKind.FOLD, None, hand.fold(player_id=legal.player_id)


def _execute_resolved(hand: HoldemHand, action: ResolvedAction) -> HoldemTransitionResult:
    if action.action_kind is ActionKind.CHECK:
        return hand.check(player_id=action.player_id)
    if action.action_kind is ActionKind.CALL:
        return hand.call(player_id=action.player_id)
    if action.action_kind is ActionKind.FOLD:
        return hand.fold(player_id=action.player_id)
    assert action.total is not None
    if action.action_kind is ActionKind.BET:
        return hand.bet_to(player_id=action.player_id, total=action.total)
    assert action.action_kind is ActionKind.RAISE
    return hand.raise_to(player_id=action.player_id, total=action.total)


def _assert_transition_invariants(
    before: HoldemHandSnapshot,
    after: HoldemHandSnapshot,
    action: ResolvedAction,
    *,
    starting_total: int,
) -> None:
    assert before.betting_round is not None
    assert before.betting_round.current_player == action.player_id
    assert action.transition.phase_before is before.phase
    assert action.transition.phase_after is after.phase
    assert action.transition.terminal is after.terminal
    assert action.transition.betting_result.player_id == action.player_id
    assert action.transition.betting_result.action_kind is action.action_kind
    assert before != after

    before_by_id = {item.player_id: item for item in before.participants}
    after_by_id = {item.player_id: item for item in after.participants}
    assert before_by_id.keys() == after_by_id.keys()
    for player_id, prior in before_by_id.items():
        current = after_by_id[player_id]
        assert current.gross_committed >= prior.gross_committed
        if before.phase is after.phase:
            assert current.street_committed >= prior.street_committed
        if prior.status is HoldemParticipantStatus.FOLDED:
            assert current.status is HoldemParticipantStatus.FOLDED
        if prior.status is HoldemParticipantStatus.ALL_IN:
            assert current.status is HoldemParticipantStatus.ALL_IN
    assert before_by_id[action.player_id].status is HoldemParticipantStatus.ACTIVE
    _assert_snapshot_invariants(after, starting_total=starting_total)


def _assert_snapshot_invariants(
    snapshot: HoldemHandSnapshot,
    *,
    starting_total: int,
) -> None:
    assert (
        sum(participant.starting_stack.chips for participant in snapshot.participants)
        == starting_total
    )
    for participant in snapshot.participants:
        assert participant.current_stack.chips >= 0
        assert participant.gross_committed >= 0
        assert 0 <= participant.returned_excess <= participant.gross_committed
        assert 0 <= participant.street_committed <= participant.gross_committed
        assert participant.starting_stack.chips == (
            participant.current_stack.chips
            + participant.gross_committed
            - participant.returned_excess
        )
        if participant.status is HoldemParticipantStatus.ACTIVE:
            assert participant.current_stack.chips > 0
        if participant.status is HoldemParticipantStatus.ALL_IN:
            assert participant.current_stack.chips == participant.returned_excess

    visible_cards = (
        tuple(card for participant in snapshot.participants for card in participant.hole_cards)
        + snapshot.board
    )
    assert len(visible_cards) == len(set(visible_cards))
    assert (
        snapshot.deck_remaining_count
        + 2 * len(snapshot.participants)
        + len(snapshot.board)
        + snapshot.burn_count
        == 52
    )
    expected_board_sizes = {
        HoldemHandPhase.PREFLOP: {0},
        HoldemHandPhase.FLOP: {3},
        HoldemHandPhase.TURN: {4},
        HoldemHandPhase.RIVER: {5},
        HoldemHandPhase.SHOWDOWN_READY: {5},
        HoldemHandPhase.COMPLETE_BY_FOLD: {0, 3, 4, 5},
    }
    assert len(snapshot.board) in expected_board_sizes[snapshot.phase]
    assert snapshot.burn_count == {0: 0, 3: 1, 4: 2, 5: 3}[len(snapshot.board)]

    if snapshot.terminal:
        assert snapshot.betting_round is None
        assert snapshot.pot_result is not None
        return
    assert snapshot.betting_round is not None
    assert snapshot.pot_result is None
    assert snapshot.uncontested_player is None
    _assert_round_reconciliation(snapshot, snapshot.betting_round)


def _assert_round_reconciliation(
    snapshot: HoldemHandSnapshot,
    betting_round: BettingRoundSnapshot,
) -> None:
    assert not betting_round.complete
    hand_by_id = {participant.player_id: participant for participant in snapshot.participants}
    round_by_id = {participant.player_id: participant for participant in betting_round.participants}
    assert set(round_by_id) <= set(hand_by_id)
    for player_id, round_participant in round_by_id.items():
        hand_participant = hand_by_id[player_id]
        assert round_participant.remaining_stack == hand_participant.current_stack
        assert round_participant.committed == hand_participant.street_committed
        assert round_participant.status.value == hand_participant.status.value
        assert round_participant.remaining_stack.chips >= 0
    active_ids = {
        participant.player_id
        for participant in betting_round.participants
        if participant.status is BettingParticipantStatus.ACTIVE
    }
    assert betting_round.pending_players <= active_ids
    assert betting_round.current_player is not None
    assert betting_round.current_player in betting_round.pending_players
    assert hand_by_id[betting_round.current_player].status is HoldemParticipantStatus.ACTIVE


def _derived_seed(master_seed: int, case_index: int, stream: str) -> int:
    material = f"streetpoker-phase7:{master_seed}:{case_index}:{stream}".encode()
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
