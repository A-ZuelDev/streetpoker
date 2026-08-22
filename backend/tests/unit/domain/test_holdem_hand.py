from dataclasses import FrozenInstanceError

import pytest
from hypothesis import given
from hypothesis import strategies as st

from streetpoker.domain import (
    ActionKind,
    BettingRoundReconciliationError,
    ChipStack,
    Deck,
    HandAlreadyTerminalError,
    HoldemHand,
    HoldemHandPhase,
    HoldemParticipantStatus,
    InsufficientEligibleParticipantsError,
    InvalidBlindStructureError,
    InvalidHandButtonError,
    OutOfTurnError,
    ParticipationStatus,
    PlayerId,
    PotEligibilityStatus,
    SeatIndex,
    SeededRandomSource,
    TableState,
)


def pid(name: str) -> PlayerId:
    return PlayerId(name)


def table_with_players(
    rows: tuple[tuple[int, str, int, ParticipationStatus], ...],
    *,
    button: int | None,
) -> TableState:
    table = TableState.six_max()
    for seat, name, stack, status in rows:
        table.seat_player(
            seat_index=SeatIndex(seat),
            player_id=pid(name),
            stack=ChipStack(stack),
            status=status,
        )
    if button is not None:
        for _ in range(table.capacity + 1):
            if table.move_button() == SeatIndex(button):
                break
        else:  # pragma: no cover - helper contract
            raise AssertionError("Requested button is not eligible.")
    return table


def active_table(
    stacks: tuple[int, ...],
    *,
    seats: tuple[int, ...] | None = None,
    button: int | None = None,
) -> TableState:
    selected_seats = tuple(range(len(stacks))) if seats is None else seats
    rows = tuple(
        (seat, chr(ord("A") + index), stack, ParticipationStatus.SITTING_IN)
        for index, (seat, stack) in enumerate(zip(selected_seats, stacks, strict=True))
    )
    return table_with_players(
        rows,
        button=(selected_seats[-1] if button is None else button),
    )


def start_hand(
    stacks: tuple[int, ...],
    *,
    seats: tuple[int, ...] | None = None,
    button: int | None = None,
    seed: int = 1,
) -> HoldemHand:
    return HoldemHand.start(
        table=active_table(stacks, seats=seats, button=button),
        small_blind=50,
        big_blind=100,
        random_source=SeededRandomSource(seed),
    )


def assert_chip_equation(hand: HoldemHand) -> None:
    participants = hand.snapshot.participants
    for participant in participants:
        assert participant.starting_stack.chips == (
            participant.current_stack.chips
            + participant.gross_committed
            - participant.returned_excess
        )
    assert sum(participant.starting_stack.chips for participant in participants) == sum(
        participant.current_stack.chips + participant.gross_committed - participant.returned_excess
        for participant in participants
    )


def check_around(hand: HoldemHand) -> None:
    phase = hand.current_phase
    while not hand.snapshot.terminal and hand.current_phase is phase:
        actions = hand.legal_actions()
        if ActionKind.CHECK in actions.kinds:
            hand.check(player_id=actions.player_id)
        else:
            hand.call(player_id=actions.player_id)


def play_decision(hand: HoldemHand, decision: int) -> None:
    actions = hand.legal_actions()
    actor = actions.player_id
    if decision == 0:
        hand.fold(player_id=actor)
    elif decision == 1 and ActionKind.CHECK in actions.kinds:
        hand.check(player_id=actor)
    elif decision == 1 and ActionKind.CALL in actions.kinds:
        hand.call(player_id=actor)
    elif decision in (2, 3) and actions.bet is not None:
        total = (
            actions.bet.maximum_to
            if decision == 3 or actions.bet.maximum_to < actions.bet.minimum_full_to
            else actions.bet.minimum_full_to
        )
        hand.bet_to(player_id=actor, total=total)
    elif decision in (2, 3) and actions.raise_to is not None:
        total = (
            actions.raise_to.maximum_to
            if decision == 3 or actions.raise_to.maximum_to < actions.raise_to.minimum_full_to
            else actions.raise_to.minimum_full_to
        )
        hand.raise_to(player_id=actor, total=total)
    elif ActionKind.CALL in actions.kinds:
        hand.call(player_id=actor)
    else:
        hand.check(player_id=actor)


@pytest.mark.parametrize("count", range(2, 7))
def test_start_snapshots_two_through_six_eligible_participants(count: int) -> None:
    table = active_table((1_000,) * count)
    before = (table.seats, table.button_position)

    hand = HoldemHand.start(
        table=table,
        small_blind=50,
        big_blind=100,
        random_source=SeededRandomSource(11),
    )

    assert len(hand.snapshot.participants) == count
    assert (table.seats, table.button_position) == before
    assert all(len(participant.hole_cards) == 2 for participant in hand.snapshot.participants)
    assert_chip_equation(hand)


def test_hand_snapshot_does_not_follow_later_table_mutations() -> None:
    table = active_table((1_000, 1_000, 1_000), button=2)
    hand = HoldemHand.start(
        table=table,
        small_blind=50,
        big_blind=100,
        random_source=SeededRandomSource(11),
    )
    before = hand.snapshot

    table.sit_out(player_id=pid("A"))
    table.leave_seat(player_id=pid("B"))

    assert hand.snapshot == before


def test_ineligible_occupants_are_excluded_and_fewer_than_two_are_rejected() -> None:
    table = table_with_players(
        (
            (0, "A", 1_000, ParticipationStatus.SITTING_IN),
            (1, "B", 0, ParticipationStatus.SITTING_OUT),
            (2, "C", 1_000, ParticipationStatus.SITTING_OUT),
        ),
        button=0,
    )

    with pytest.raises(InsufficientEligibleParticipantsError):
        HoldemHand.start(table=table, small_blind=50, big_blind=100)


@pytest.mark.parametrize(
    ("small", "big"),
    [(0, 100), (-1, 100), (100, 100), (101, 100), (50, 0), (True, 100), (50, 1.5)],
)
def test_invalid_blind_structures_are_rejected(small: object, big: object) -> None:
    with pytest.raises(InvalidBlindStructureError):
        HoldemHand.start(
            table=active_table((1_000, 1_000)),
            small_blind=small,  # type: ignore[arg-type]
            big_blind=big,  # type: ignore[arg-type]
        )


def test_unset_or_ineligible_button_is_rejected() -> None:
    unset = table_with_players(
        (
            (0, "A", 1_000, ParticipationStatus.SITTING_IN),
            (1, "B", 1_000, ParticipationStatus.SITTING_IN),
        ),
        button=None,
    )
    with pytest.raises(InvalidHandButtonError):
        HoldemHand.start(table=unset, small_blind=50, big_blind=100)

    stale = active_table((1_000, 1_000, 1_000), button=0)
    stale.sit_out(player_id=pid("A"))
    with pytest.raises(InvalidHandButtonError):
        HoldemHand.start(table=stale, small_blind=50, big_blind=100)


def test_snapshot_is_immutable_and_exposes_no_mutable_deck_or_round() -> None:
    hand = start_hand((1_000, 1_000, 1_000))
    snapshot = hand.snapshot

    with pytest.raises(FrozenInstanceError):
        snapshot.board = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        snapshot.participants[0].gross_committed = 0  # type: ignore[misc]

    assert not hasattr(hand, "deck")
    assert not hasattr(hand, "current_betting_round")
    assert not hasattr(snapshot, "burn_cards")
    assert snapshot.burn_count == 0


def test_seeded_hole_dealing_is_one_card_at_a_time_clockwise_after_button() -> None:
    hand = start_hand((1_000,) * 6, button=5, seed=29)
    expected_deck = Deck.shuffled_standard(random_source=SeededRandomSource(29))
    expected: dict[int, list[object]] = {seat: [] for seat in range(6)}
    for _ in range(2):
        for seat in (0, 1, 2, 3, 4, 5):
            expected[seat].append(expected_deck.draw()[0])

    for participant in hand.snapshot.participants:
        assert list(participant.hole_cards) == expected[participant.seat_index.value]
    assert (
        len({card for participant in hand.snapshot.participants for card in participant.hole_cards})
        == 12
    )
    assert hand.snapshot.deck_remaining_count == 40


def test_six_max_normal_checkdown_deals_exact_board_and_burn_counts() -> None:
    hand = start_hand((1_000,) * 6, button=5)
    for name in ("C", "D", "E", "F", "A"):
        hand.call(player_id=pid(name))
    hand.check(player_id=pid("B"))
    assert hand.current_phase is HoldemHandPhase.FLOP
    assert hand.legal_actions().player_id == pid("A")

    check_around(hand)
    assert hand.current_phase is HoldemHandPhase.TURN
    check_around(hand)
    assert hand.current_phase is HoldemHandPhase.RIVER
    check_around(hand)

    assert hand.current_phase is HoldemHandPhase.SHOWDOWN_READY
    assert len(hand.snapshot.board) == 5
    assert hand.snapshot.burn_count == 3
    assert hand.snapshot.deck_remaining_count == 32
    assert hand.snapshot.pot_result is not None
    assert hand.snapshot.pot_result.total_pot_chips == 600
    assert_chip_equation(hand)


def test_heads_up_blinds_deal_and_action_order() -> None:
    hand = start_hand((1_000, 1_000), seats=(1, 4), button=1, seed=41)
    expected_deck = Deck.shuffled_standard(random_source=SeededRandomSource(41))
    expected_b = (expected_deck.draw()[0],)
    expected_a = (expected_deck.draw()[0],)
    expected_b += (expected_deck.draw()[0],)
    expected_a += (expected_deck.draw()[0],)

    assert hand.snapshot.small_blind_position == SeatIndex(1)
    assert hand.snapshot.big_blind_position == SeatIndex(4)
    assert hand.participant(pid("B")).hole_cards == expected_b
    assert hand.participant(pid("A")).hole_cards == expected_a
    assert hand.legal_actions().player_id == pid("A")
    hand.call(player_id=pid("A"))
    hand.check(player_id=pid("B"))
    assert hand.current_phase is HoldemHandPhase.FLOP
    assert hand.legal_actions().player_id == pid("B")


def test_three_handed_and_sparse_seat_orders_use_participant_ring() -> None:
    hand = start_hand((1_000, 1_000, 1_000), seats=(0, 2, 5), button=5)
    assert hand.snapshot.small_blind_position == SeatIndex(0)
    assert hand.snapshot.big_blind_position == SeatIndex(2)
    assert hand.legal_actions().player_id == pid("C")

    other = start_hand((1_000, 1_000, 1_000), seats=(0, 2, 5), button=2)
    assert other.snapshot.small_blind_position == SeatIndex(5)
    assert other.snapshot.big_blind_position == SeatIndex(0)
    assert other.legal_actions().player_id == pid("B")


def test_big_blind_option_after_calls_advances_to_flop() -> None:
    hand = start_hand((1_000, 1_000, 1_000), button=2)
    hand.call(player_id=pid("C"))
    hand.call(player_id=pid("A"))

    actions = hand.legal_actions()
    assert actions.player_id == pid("B")
    assert {ActionKind.CHECK, ActionKind.RAISE} <= actions.kinds
    hand.check(player_id=pid("B"))
    assert hand.current_phase is HoldemHandPhase.FLOP


def test_big_blind_wins_preflop_after_everyone_folds_without_board() -> None:
    hand = start_hand((1_000,) * 6, button=5)
    for name in ("C", "D", "E", "F", "A"):
        hand.fold(player_id=pid(name))

    assert hand.current_phase is HoldemHandPhase.COMPLETE_BY_FOLD
    assert hand.snapshot.uncontested_player == pid("B")
    assert hand.snapshot.board == ()
    assert hand.participant(pid("B")).gross_committed == 100
    assert hand.participant(pid("B")).returned_excess == 50
    assert hand.snapshot.pot_result is not None
    assert hand.snapshot.pot_result.total_pot_chips == 100
    assert_chip_equation(hand)


def test_button_raise_and_blinds_fold_returns_unmatched_raise() -> None:
    hand = start_hand((1_000,) * 6, button=5)
    for name in ("C", "D", "E"):
        hand.fold(player_id=pid(name))
    hand.raise_to(player_id=pid("F"), total=200)
    hand.fold(player_id=pid("A"))
    hand.fold(player_id=pid("B"))

    assert hand.current_phase is HoldemHandPhase.COMPLETE_BY_FOLD
    assert hand.snapshot.uncontested_player == pid("F")
    assert hand.participant(pid("F")).returned_excess == 100
    assert hand.snapshot.pot_result is not None
    assert hand.snapshot.pot_result.total_pot_chips == 250


def test_short_small_blind_all_in_is_skipped_and_board_runs_out() -> None:
    hand = start_hand((30, 1_000, 1_000, 1_000), button=3)
    assert hand.participant(pid("A")).status is HoldemParticipantStatus.ALL_IN
    hand.fold(player_id=pid("C"))
    hand.fold(player_id=pid("D"))

    assert hand.current_phase is HoldemHandPhase.SHOWDOWN_READY
    assert hand.participant(pid("B")).returned_excess == 70
    assert hand.snapshot.burn_count == 3
    assert_chip_equation(hand)


def test_nominal_multiway_short_big_blind_call_fold_chain_runs_out_and_refunds() -> None:
    hand = start_hand((1_000, 60, 1_000, 1_000), button=3)

    actions = hand.legal_actions()
    assert actions.player_id == pid("C")
    assert actions.amount_to_call == 100
    assert actions.raise_to is not None
    assert actions.raise_to.minimum_full_to == 200
    hand.call(player_id=pid("C"))
    hand.fold(player_id=pid("D"))
    hand.fold(player_id=pid("A"))

    assert hand.current_phase is HoldemHandPhase.SHOWDOWN_READY
    assert hand.betting_round_snapshot is None
    assert hand.participant(pid("C")).gross_committed == 100
    assert hand.participant(pid("C")).returned_excess == 40
    assert hand.participant(pid("C")).contestable_committed == 60
    assert hand.snapshot.pot_result is not None
    assert hand.snapshot.pot_result.total_pot_chips == 170
    assert len(hand.snapshot.board) == 5
    with pytest.raises(HandAlreadyTerminalError):
        hand.legal_actions()
    assert_chip_equation(hand)


def test_heads_up_short_big_blind_uses_actual_target_and_disallows_raise() -> None:
    hand = start_hand((1_000, 60), button=0)

    actions = hand.legal_actions()
    assert actions.player_id == pid("A")
    assert actions.amount_to_call == 10
    assert actions.kinds == frozenset({ActionKind.CALL, ActionKind.FOLD})
    hand.call(player_id=pid("A"))

    assert hand.current_phase is HoldemHandPhase.SHOWDOWN_READY
    assert all(participant.returned_excess == 0 for participant in hand.snapshot.participants)
    assert hand.snapshot.pot_result is not None
    assert hand.snapshot.pot_result.total_pot_chips == 120


def test_heads_up_big_blind_shorter_than_small_blind_needs_no_action() -> None:
    hand = start_hand((1_000, 30), button=0)

    assert hand.current_phase is HoldemHandPhase.SHOWDOWN_READY
    assert hand.participant(pid("A")).gross_committed == 50
    assert hand.participant(pid("A")).returned_excess == 20
    assert hand.participant(pid("A")).contestable_committed == 30
    assert hand.snapshot.pot_result is not None
    assert hand.snapshot.pot_result.total_pot_chips == 60
    assert_chip_equation(hand)


def test_preflop_all_in_with_two_players_behind_creates_real_flop_round() -> None:
    hand = start_hand((70, 1_000, 1_000), button=2)
    hand.call(player_id=pid("C"))
    hand.call(player_id=pid("A"))
    hand.check(player_id=pid("B"))

    assert hand.current_phase is HoldemHandPhase.FLOP
    assert hand.participant(pid("A")).status is HoldemParticipantStatus.ALL_IN
    assert hand.legal_actions().player_id == pid("B")
    hand.check(player_id=pid("B"))
    assert hand.legal_actions().player_id == pid("C")
    assert pid("A") not in hand.snapshot.betting_round.pending_players  # type: ignore[union-attr]


def test_flop_all_in_automatically_runs_turn_and_river() -> None:
    hand = start_hand((500, 1_000, 500), button=2)
    hand.call(player_id=pid("C"))
    hand.call(player_id=pid("A"))
    hand.check(player_id=pid("B"))
    hand.bet_to(player_id=pid("A"), total=100)
    hand.fold(player_id=pid("B"))
    hand.raise_to(player_id=pid("C"), total=400)
    hand.call(player_id=pid("A"))

    assert hand.current_phase is HoldemHandPhase.SHOWDOWN_READY
    assert len(hand.snapshot.board) == 5
    assert hand.snapshot.burn_count == 3


def test_everyone_but_one_folds_on_flop_without_future_deal() -> None:
    hand = start_hand((1_000, 1_000, 1_000), button=2)
    hand.call(player_id=pid("C"))
    hand.call(player_id=pid("A"))
    hand.check(player_id=pid("B"))
    hand.bet_to(player_id=pid("A"), total=100)
    hand.fold(player_id=pid("B"))
    hand.fold(player_id=pid("C"))

    assert hand.current_phase is HoldemHandPhase.COMPLETE_BY_FOLD
    assert len(hand.snapshot.board) == 3
    assert hand.snapshot.burn_count == 1
    assert hand.snapshot.uncontested_player == pid("A")


def test_multiple_preflop_all_ins_construct_side_pots_and_refund() -> None:
    hand = start_hand((1_000, 100, 40, 150, 300), button=4)
    hand.call(player_id=pid("C"))
    hand.raise_to(player_id=pid("D"), total=150)
    hand.raise_to(player_id=pid("E"), total=300)
    hand.fold(player_id=pid("A"))

    assert hand.current_phase is HoldemHandPhase.SHOWDOWN_READY
    assert hand.participant(pid("E")).returned_excess == 150
    assert hand.snapshot.pot_result is not None
    assert [pot.amount for pot in hand.snapshot.pot_result.pots] == [200, 40, 150, 100]
    assert_chip_equation(hand)


def test_unique_unmatched_final_contribution_is_returned() -> None:
    hand = start_hand((120, 200, 1_000, 1_000, 1_000, 1_000), button=5)
    for name in ("C", "D", "E"):
        hand.fold(player_id=pid(name))
    hand.raise_to(player_id=pid("F"), total=300)
    hand.call(player_id=pid("A"))
    hand.call(player_id=pid("B"))

    assert hand.current_phase is HoldemHandPhase.SHOWDOWN_READY
    assert hand.participant(pid("F")).gross_committed == 300
    assert hand.participant(pid("F")).returned_excess == 100
    assert hand.participant(pid("F")).contestable_committed == 200
    assert_chip_equation(hand)


def test_multiple_side_pot_levels_accumulate_across_streets() -> None:
    hand = start_hand((300, 600, 1_000, 1_000), button=3)
    hand.raise_to(player_id=pid("C"), total=200)
    hand.call(player_id=pid("D"))
    hand.raise_to(player_id=pid("A"), total=300)
    hand.call(player_id=pid("B"))
    hand.call(player_id=pid("C"))
    hand.call(player_id=pid("D"))
    hand.bet_to(player_id=pid("B"), total=300)
    hand.call(player_id=pid("C"))
    hand.raise_to(player_id=pid("D"), total=700)
    hand.call(player_id=pid("C"))

    assert hand.current_phase is HoldemHandPhase.SHOWDOWN_READY
    assert [hand.participant(pid(name)).gross_committed for name in ("A", "B", "C", "D")] == [
        300,
        600,
        1_000,
        1_000,
    ]
    assert hand.snapshot.pot_result is not None
    assert [pot.amount for pot in hand.snapshot.pot_result.pots] == [1_200, 900, 800]
    assert hand.snapshot.pot_result.uncalled_excess is None
    assert_chip_equation(hand)


def test_folded_large_cumulative_contributor_funds_but_cannot_win_pots() -> None:
    hand = start_hand((1_000, 1_000, 1_000), button=2)
    hand.call(player_id=pid("C"))
    hand.call(player_id=pid("A"))
    hand.check(player_id=pid("B"))
    hand.check(player_id=pid("A"))
    hand.bet_to(player_id=pid("B"), total=200)
    hand.call(player_id=pid("C"))
    hand.fold(player_id=pid("A"))
    assert hand.current_phase is HoldemHandPhase.TURN
    hand.fold(player_id=pid("B"))

    assert hand.current_phase is HoldemHandPhase.COMPLETE_BY_FOLD
    assert [hand.participant(pid(name)).gross_committed for name in ("A", "B", "C")] == [
        100,
        300,
        300,
    ]
    assert hand.participant(pid("B")).status is HoldemParticipantStatus.FOLDED
    assert hand.snapshot.pot_result is not None
    b_funded = [pot for pot in hand.snapshot.pot_result.pots if pid("B") in pot.contributors]
    assert len(b_funded) == 2
    assert all(pid("B") not in pot.eligible_players for pot in b_funded)
    assert all(
        contribution.status is PotEligibilityStatus.FOLDED
        for contribution in hand.snapshot.pot_result.contributions
        if contribution.player_id == pid("B")
    )
    assert_chip_equation(hand)


def test_invalid_action_and_reconciliation_failure_are_copy_on_write_atomic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hand = start_hand((1_000, 1_000, 1_000), button=2)
    before = hand.snapshot
    with pytest.raises(OutOfTurnError):
        hand.call(player_id=pid("A"))
    assert hand.snapshot == before

    def fail_reconciliation(*args: object) -> tuple[object, ...]:
        raise BettingRoundReconciliationError("forced test failure")

    monkeypatch.setattr(HoldemHand, "_reconcile_round", staticmethod(fail_reconciliation))
    with pytest.raises(BettingRoundReconciliationError):
        hand.call(player_id=pid("C"))
    assert hand.snapshot == before


@given(
    seats=st.sets(st.integers(min_value=0, max_value=5), min_size=2, max_size=6),
    stacks=st.lists(st.integers(min_value=1, max_value=10_000), min_size=6, max_size=6),
    seed=st.integers(),
)
def test_generated_starts_preserve_card_chip_and_sparse_order_invariants(
    seats: set[int],
    stacks: list[int],
    seed: int,
) -> None:
    ordered = tuple(sorted(seats))
    selected_stacks = tuple(stacks[seat] for seat in ordered)
    hand = start_hand(
        selected_stacks,
        seats=ordered,
        button=ordered[-1],
        seed=seed,
    )

    dealt = [card for participant in hand.snapshot.participants for card in participant.hole_cards]
    dealt.extend(hand.snapshot.board)
    assert len(dealt) == len(set(dealt))
    assert hand.snapshot.button_position == SeatIndex(ordered[-1])
    assert_chip_equation(hand)


@given(seed=st.integers())
def test_generated_seeded_start_and_checkdown_replay_is_deterministic(seed: int) -> None:
    first = start_hand((1_000, 1_000, 1_000), button=2, seed=seed)
    second = start_hand((1_000, 1_000, 1_000), button=2, seed=seed)

    for hand in (first, second):
        hand.call(player_id=pid("C"))
        hand.call(player_id=pid("A"))
        hand.check(player_id=pid("B"))
        check_around(hand)
        check_around(hand)
        check_around(hand)

    assert first.snapshot == second.snapshot


@given(
    stacks=st.lists(st.integers(min_value=100, max_value=1_000), min_size=3, max_size=3),
    seed=st.integers(),
    decisions=st.lists(st.integers(min_value=0, max_value=3), min_size=1, max_size=80),
)
def test_generated_legal_hand_sequences_preserve_authoritative_invariants(
    stacks: list[int],
    seed: int,
    decisions: list[int],
) -> None:
    first = start_hand(tuple(stacks), button=2, seed=seed)
    second = start_hand(tuple(stacks), button=2, seed=seed)
    previous_gross = {
        participant.player_id: participant.gross_committed
        for participant in first.snapshot.participants
    }
    folded: set[PlayerId] = set()
    all_in: set[PlayerId] = set()

    for decision in decisions:
        if first.snapshot.terminal:
            break
        assert not ({first.legal_actions().player_id} & (folded | all_in))
        play_decision(first, decision)
        play_decision(second, decision)
        assert_chip_equation(first)
        assert first.snapshot == second.snapshot
        assert (
            first.snapshot.deck_remaining_count
            + 2 * len(first.snapshot.participants)
            + len(first.snapshot.board)
            + first.snapshot.burn_count
            == 52
        )

        current_folded = {
            participant.player_id
            for participant in first.snapshot.participants
            if participant.status is HoldemParticipantStatus.FOLDED
        }
        current_all_in = {
            participant.player_id
            for participant in first.snapshot.participants
            if participant.status is HoldemParticipantStatus.ALL_IN
        }
        assert folded <= current_folded
        assert all_in <= current_all_in
        folded = current_folded
        all_in = current_all_in
        for participant in first.snapshot.participants:
            assert participant.gross_committed >= previous_gross[participant.player_id]
            previous_gross[participant.player_id] = participant.gross_committed

    if first.snapshot.terminal:
        assert first.snapshot.pot_result is not None
