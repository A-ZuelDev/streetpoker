from dataclasses import FrozenInstanceError

import pytest
from hypothesis import given
from hypothesis import strategies as st

from streetpoker.domain import (
    ActionKind,
    BettingParticipantStatus,
    BettingRound,
    BettingRoundCompleteError,
    BettingRoundPlayer,
    Call,
    ChipStack,
    DuplicateBettingPlayerError,
    DuplicateBettingSeatError,
    IllegalBetError,
    IllegalCallError,
    IllegalCheckError,
    IllegalRaiseError,
    InvalidActionTypeError,
    InvalidBettingParticipantError,
    InvalidBettingRoundStateError,
    InvalidMinimumBetError,
    InvalidWagerAmountError,
    OutOfTurnError,
    PlayerId,
    PlayerNotInBettingRoundError,
    RaiseNotReopenedError,
    SeatIndex,
    WagerBelowMinimumError,
    WagerClassification,
    WagerExceedsStackError,
)


def player_id(name: str) -> PlayerId:
    return PlayerId(name)


def player(name: str, seat: int, stack: int = 1_000) -> BettingRoundPlayer:
    return BettingRoundPlayer(
        player_id=player_id(name),
        seat_index=SeatIndex(seat),
        stack=ChipStack(stack),
    )


def betting_round(
    *players: BettingRoundPlayer,
    first: str = "A",
    minimum_bet: int = 100,
) -> BettingRound:
    selected = players or (player("A", 0), player("B", 1), player("C", 2))
    return BettingRound.start(
        players=selected,
        first_to_act=player_id(first),
        minimum_bet=minimum_bet,
    )


def assert_round_invariants(round_state: BettingRound) -> None:
    snapshot = round_state.snapshot
    participants = snapshot.participants

    assert len({participant.player_id for participant in participants}) == len(participants)
    assert len({participant.seat_index for participant in participants}) == len(participants)
    assert [participant.seat_index.value for participant in participants] == sorted(
        participant.seat_index.value for participant in participants
    )
    assert snapshot.current_wager == max(participant.committed for participant in participants)
    assert snapshot.minimum_bet > 0
    assert snapshot.minimum_raise_increment >= snapshot.minimum_bet
    assert sum(participant.starting_stack.chips for participant in participants) == sum(
        participant.remaining_stack.chips + participant.committed for participant in participants
    )

    for participant in participants:
        assert participant.remaining_stack.chips >= 0
        assert participant.committed >= 0
        assert (
            participant.starting_stack.chips
            == participant.remaining_stack.chips + participant.committed
        )
        if participant.status is BettingParticipantStatus.ACTIVE:
            assert participant.remaining_stack.chips > 0
        if participant.status is BettingParticipantStatus.ALL_IN:
            assert participant.remaining_stack.chips == 0

    active_ids = {
        participant.player_id
        for participant in participants
        if participant.status is BettingParticipantStatus.ACTIVE
    }
    assert snapshot.pending_players <= active_ids
    if snapshot.complete:
        assert snapshot.current_player is None
        assert not snapshot.pending_players
    else:
        assert snapshot.current_player in snapshot.pending_players


def test_round_starts_with_zero_commitments_and_explicit_first_actor() -> None:
    round_state = betting_round(player("A", 4, 300), player("B", 1, 200), first="A")

    assert [participant.player_id.value for participant in round_state.participants] == ["B", "A"]
    assert round_state.current_player == player_id("A")
    assert round_state.current_wager == 0
    assert round_state.minimum_raise_increment == 100
    assert all(participant.committed == 0 for participant in round_state.participants)
    assert_round_invariants(round_state)


@pytest.mark.parametrize("minimum_bet", [0, -1, True, False, 1.5, "100", None])
def test_invalid_minimum_bets_are_rejected(minimum_bet: object) -> None:
    with pytest.raises(InvalidMinimumBetError):
        betting_round(player("A", 0), player("B", 1), minimum_bet=minimum_bet)  # type: ignore[arg-type]


def test_round_requires_two_unique_players_and_seats() -> None:
    with pytest.raises(InvalidBettingRoundStateError):
        betting_round(player("A", 0))
    with pytest.raises(DuplicateBettingPlayerError):
        betting_round(player("A", 0), player("A", 1))
    with pytest.raises(DuplicateBettingSeatError):
        betting_round(player("A", 0), player("B", 0))


def test_round_rejects_a_noniterable_player_collection() -> None:
    with pytest.raises(InvalidBettingRoundStateError):
        BettingRound.start(
            players=None,  # type: ignore[arg-type]
            first_to_act=player_id("A"),
            minimum_bet=100,
        )


def test_betting_player_requires_typed_identity_seat_and_positive_stack() -> None:
    with pytest.raises(InvalidBettingParticipantError):
        BettingRoundPlayer("A", SeatIndex(0), ChipStack(10))  # type: ignore[arg-type]
    with pytest.raises(InvalidBettingParticipantError):
        BettingRoundPlayer(player_id("A"), 0, ChipStack(10))  # type: ignore[arg-type]
    with pytest.raises(InvalidBettingParticipantError):
        BettingRoundPlayer(player_id("A"), SeatIndex(0), ChipStack(0))


def test_first_actor_must_be_a_participant() -> None:
    with pytest.raises(PlayerNotInBettingRoundError):
        betting_round(player("A", 0), player("B", 1), first="C")


def test_snapshots_and_participants_are_immutable() -> None:
    round_state = betting_round()
    snapshot = round_state.snapshot

    with pytest.raises(FrozenInstanceError):
        snapshot.current_wager = 10  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        snapshot.participants[0].committed = 10  # type: ignore[misc]

    assert round_state.snapshot == snapshot


def test_normal_check_around_completes_in_clockwise_order() -> None:
    round_state = betting_round()

    round_state.check(player_id=player_id("A"))
    assert round_state.current_player == player_id("B")
    round_state.check(player_id=player_id("B"))
    assert round_state.current_player == player_id("C")
    result = round_state.check(player_id=player_id("C"))

    assert result.round_complete
    assert round_state.complete
    assert all(participant.committed == 0 for participant in round_state.participants)


def test_heads_up_bet_and_call_complete_with_equal_commitments() -> None:
    round_state = betting_round(player("A", 0), player("B", 1))

    bet_result = round_state.bet_to(player_id=player_id("A"), total=200)
    call_result = round_state.call(player_id=player_id("B"))

    assert bet_result.classification is WagerClassification.FULL_BET
    assert bet_result.committed_chips == 200
    assert call_result.committed_chips == 200
    assert call_result.round_complete
    assert [participant.committed for participant in round_state.participants] == [200, 200]


def test_heads_up_bet_and_fold_complete_and_preserve_commitments() -> None:
    round_state = betting_round(player("A", 0), player("B", 1))
    round_state.bet_to(player_id=player_id("A"), total=200)

    result = round_state.fold(player_id=player_id("B"))

    folded = round_state.participant(player_id("B"))
    assert result.round_complete
    assert folded.status is BettingParticipantStatus.FOLDED
    assert folded.committed == 0
    assert folded.remaining_stack == ChipStack(1_000)


def test_fold_after_a_raise_preserves_chips_already_committed() -> None:
    round_state = betting_round()
    round_state.bet_to(player_id=player_id("A"), total=100)
    round_state.call(player_id=player_id("B"))
    round_state.raise_to(player_id=player_id("C"), total=200)

    round_state.fold(player_id=player_id("A"))

    folded = round_state.participant(player_id("A"))
    assert folded.status is BettingParticipantStatus.FOLDED
    assert folded.committed == 100
    assert folded.remaining_stack == ChipStack(900)


def test_normal_raise_updates_minimum_and_calls_complete() -> None:
    round_state = betting_round()

    round_state.bet_to(player_id=player_id("A"), total=100)
    result = round_state.raise_to(player_id=player_id("B"), total=300)
    round_state.call(player_id=player_id("C"))
    round_state.call(player_id=player_id("A"))

    assert result.classification is WagerClassification.FULL_RAISE
    assert result.committed_chips == 300
    assert round_state.minimum_raise_increment == 200
    assert round_state.complete
    assert {participant.committed for participant in round_state.participants} == {300}


def test_short_stack_call_commits_exact_remaining_stack_and_is_skipped() -> None:
    round_state = betting_round(player("A", 0), player("B", 1, 120), player("C", 2))
    round_state.bet_to(player_id=player_id("A"), total=300)

    result = round_state.call(player_id=player_id("B"))

    short_player = round_state.participant(player_id("B"))
    assert result.committed_chips == 120
    assert result.is_all_in
    assert short_player.committed == 120
    assert short_player.remaining_stack == ChipStack(0)
    assert short_player.status is BettingParticipantStatus.ALL_IN
    assert round_state.current_player == player_id("C")

    round_state.call(player_id=player_id("C"))
    assert round_state.complete


def test_short_all_in_raise_increases_wager_without_reopening_prior_actors() -> None:
    round_state = betting_round(player("A", 0), player("B", 1), player("C", 2, 150))
    round_state.bet_to(player_id=player_id("A"), total=100)
    round_state.call(player_id=player_id("B"))

    result = round_state.raise_to(player_id=player_id("C"), total=150)

    assert result.classification is WagerClassification.SHORT_ALL_IN_RAISE
    assert round_state.current_wager == 150
    assert round_state.minimum_raise_increment == 100
    assert ActionKind.RAISE not in round_state.legal_actions().kinds

    before = round_state.snapshot
    with pytest.raises(RaiseNotReopenedError):
        round_state.raise_to(player_id=player_id("A"), total=300)
    assert round_state.snapshot == before

    round_state.call(player_id=player_id("A"))
    assert ActionKind.RAISE not in round_state.legal_actions().kinds
    round_state.call(player_id=player_id("B"))
    assert round_state.complete


def test_full_raise_reopens_action_for_prior_actors() -> None:
    round_state = betting_round()
    round_state.bet_to(player_id=player_id("A"), total=100)
    round_state.call(player_id=player_id("B"))
    round_state.raise_to(player_id=player_id("C"), total=200)

    a_actions = round_state.legal_actions()
    assert a_actions.raise_reopened
    assert a_actions.raise_to is not None
    assert a_actions.raise_to.minimum_full_to == 300

    round_state.call(player_id=player_id("A"))
    assert ActionKind.RAISE in round_state.legal_actions().kinds


def test_multiple_short_all_ins_use_player_relative_reopening_baselines() -> None:
    round_state = betting_round(
        player("A", 0),
        player("B", 1, 125),
        player("C", 2),
        player("D", 3, 200),
        player("E", 4),
    )
    round_state.bet_to(player_id=player_id("A"), total=100)
    round_state.raise_to(player_id=player_id("B"), total=125)
    round_state.call(player_id=player_id("C"))
    round_state.raise_to(player_id=player_id("D"), total=200)
    round_state.call(player_id=player_id("E"))

    a_actions = round_state.legal_actions()
    assert a_actions.player_id == player_id("A")
    assert a_actions.raise_reopened
    assert a_actions.raise_to is not None
    assert a_actions.raise_to.minimum_full_to == 300

    round_state.call(player_id=player_id("A"))
    c_actions = round_state.legal_actions()
    assert c_actions.player_id == player_id("C")
    assert c_actions.amount_to_call == 75
    assert not c_actions.raise_reopened
    assert ActionKind.RAISE not in c_actions.kinds


def test_unacted_player_can_raise_over_a_short_all_in() -> None:
    round_state = betting_round(player("A", 0), player("B", 1, 150), player("C", 2))
    round_state.bet_to(player_id=player_id("A"), total=100)
    round_state.raise_to(player_id=player_id("B"), total=150)

    actions = round_state.legal_actions()

    assert actions.player_id == player_id("C")
    assert actions.raise_reopened
    assert actions.raise_to is not None
    assert actions.raise_to.minimum_full_to == 250


def test_calling_updates_a_players_baseline_before_a_later_short_raise() -> None:
    round_state = betting_round(
        player("A", 0),
        player("B", 1, 250),
        player("C", 2),
    )
    round_state.bet_to(player_id=player_id("A"), total=100)
    round_state.call(player_id=player_id("B"))
    round_state.raise_to(player_id=player_id("C"), total=200)
    round_state.call(player_id=player_id("A"))
    round_state.raise_to(player_id=player_id("B"), total=250)

    assert round_state.current_player == player_id("C")
    assert ActionKind.RAISE not in round_state.legal_actions().kinds
    round_state.call(player_id=player_id("C"))
    assert round_state.current_player == player_id("A")
    assert ActionKind.RAISE not in round_state.legal_actions().kinds


def test_prior_checker_is_not_reopened_by_short_opening_all_in() -> None:
    round_state = betting_round(player("A", 0), player("B", 1, 60), player("C", 2))
    round_state.check(player_id=player_id("A"))
    result = round_state.bet_to(player_id=player_id("B"), total=60)

    assert result.classification is WagerClassification.SHORT_ALL_IN_BET
    c_actions = round_state.legal_actions()
    assert c_actions.raise_to is not None
    assert c_actions.raise_to.minimum_full_to == 160

    round_state.call(player_id=player_id("C"))
    a_actions = round_state.legal_actions()
    assert a_actions.amount_to_call == 60
    assert ActionKind.RAISE not in a_actions.kinds


def test_larger_opening_bet_sets_the_next_minimum_raise_increment() -> None:
    round_state = betting_round()

    round_state.bet_to(player_id=player_id("A"), total=300)

    assert round_state.minimum_raise_increment == 300
    actions = round_state.legal_actions()
    assert actions.raise_to is not None
    assert actions.raise_to.minimum_full_to == 600


def test_larger_raise_sets_the_next_minimum_raise_increment() -> None:
    round_state = betting_round()
    round_state.bet_to(player_id=player_id("A"), total=300)

    round_state.raise_to(player_id=player_id("B"), total=800)

    assert round_state.minimum_raise_increment == 500
    actions = round_state.legal_actions()
    assert actions.raise_to is not None
    assert actions.raise_to.minimum_full_to == 1_300


def test_short_all_in_is_the_only_legal_below_minimum_bet() -> None:
    round_state = betting_round(player("A", 0, 60), player("B", 1))
    actions = round_state.legal_actions()

    assert actions.bet is not None
    assert actions.bet.minimum_full_to == 100
    assert actions.bet.maximum_to == 60
    assert actions.bet.short_all_in_to == 60

    result = round_state.bet_to(player_id=player_id("A"), total=60)
    assert result.is_all_in
    assert result.classification is WagerClassification.SHORT_ALL_IN_BET


def test_short_all_in_is_the_only_legal_below_minimum_raise() -> None:
    round_state = betting_round(player("A", 0), player("B", 1, 150), player("C", 2))
    round_state.bet_to(player_id=player_id("A"), total=100)
    actions = round_state.legal_actions()

    assert actions.raise_to is not None
    assert actions.raise_to.minimum_full_to == 200
    assert actions.raise_to.maximum_to == 150
    assert actions.raise_to.short_all_in_to == 150


def test_exact_minimum_all_in_raise_is_full_not_short() -> None:
    round_state = betting_round(player("A", 0), player("B", 1, 200), player("C", 2))
    round_state.bet_to(player_id=player_id("A"), total=100)

    result = round_state.raise_to(player_id=player_id("B"), total=200)

    assert result.is_all_in
    assert result.classification is WagerClassification.FULL_RAISE
    assert round_state.minimum_raise_increment == 100


@pytest.mark.parametrize("total", [0, -1, True, False, 1.5, "100", None])
def test_invalid_wager_totals_fail_without_mutation(total: object) -> None:
    round_state = betting_round()
    before = round_state.snapshot

    with pytest.raises(InvalidWagerAmountError):
        round_state.bet_to(player_id=player_id("A"), total=total)  # type: ignore[arg-type]

    assert round_state.snapshot == before


def test_non_all_in_underbet_and_underraise_fail_without_mutation() -> None:
    round_state = betting_round()
    before_bet = round_state.snapshot
    with pytest.raises(WagerBelowMinimumError):
        round_state.bet_to(player_id=player_id("A"), total=99)
    assert round_state.snapshot == before_bet

    round_state.bet_to(player_id=player_id("A"), total=100)
    before_raise = round_state.snapshot
    with pytest.raises(WagerBelowMinimumError):
        round_state.raise_to(player_id=player_id("B"), total=199)
    assert round_state.snapshot == before_raise


def test_wager_beyond_available_stack_fails_without_mutation() -> None:
    round_state = betting_round(player("A", 0, 200), player("B", 1))
    before = round_state.snapshot

    with pytest.raises(WagerExceedsStackError) as error:
        round_state.bet_to(player_id=player_id("A"), total=201)

    assert error.value.maximum_total == 200
    assert round_state.snapshot == before


def test_wrong_action_kind_and_out_of_turn_actions_are_atomic() -> None:
    round_state = betting_round()

    for operation, error_type in (
        (lambda: round_state.call(player_id=player_id("A")), IllegalCallError),
        (lambda: round_state.raise_to(player_id=player_id("A"), total=200), IllegalRaiseError),
        (lambda: round_state.check(player_id=player_id("B")), OutOfTurnError),
    ):
        before = round_state.snapshot
        with pytest.raises(error_type):
            operation()
        assert round_state.snapshot == before

    before = round_state.snapshot
    with pytest.raises(InvalidActionTypeError):
        round_state.act(player_id=player_id("A"), action=object())  # type: ignore[arg-type]
    assert round_state.snapshot == before


def test_check_and_bet_are_illegal_when_facing_an_existing_wager() -> None:
    round_state = betting_round()
    round_state.bet_to(player_id=player_id("A"), total=100)

    before = round_state.snapshot
    with pytest.raises(IllegalCheckError):
        round_state.check(player_id=player_id("B"))
    assert round_state.snapshot == before
    with pytest.raises(IllegalBetError):
        round_state.bet_to(player_id=player_id("B"), total=200)
    assert round_state.snapshot == before


def test_clockwise_order_skips_gaps_wraps_and_skips_folded_players() -> None:
    round_state = betting_round(
        player("A", 4),
        player("B", 1),
        player("C", 5),
        first="A",
    )

    round_state.check(player_id=player_id("A"))
    assert round_state.current_player == player_id("C")
    round_state.fold(player_id=player_id("C"))
    assert round_state.current_player == player_id("B")


def test_folding_without_facing_a_wager_is_allowed_and_can_end_heads_up_round() -> None:
    round_state = betting_round(player("A", 0), player("B", 1))

    result = round_state.fold(player_id=player_id("A"))

    assert result.round_complete
    assert round_state.complete


def test_completed_round_rejects_all_further_actions_without_mutation() -> None:
    round_state = betting_round(player("A", 0), player("B", 1))
    round_state.check(player_id=player_id("A"))
    round_state.check(player_id=player_id("B"))
    before = round_state.snapshot

    with pytest.raises(BettingRoundCompleteError):
        round_state.fold(player_id=player_id("A"))
    with pytest.raises(BettingRoundCompleteError):
        round_state.legal_actions()

    assert round_state.snapshot == before


def test_no_player_can_make_an_uncontested_side_wager_against_all_in_opponents() -> None:
    round_state = betting_round(
        player("A", 0),
        player("B", 1, 100),
        player("C", 2),
    )
    round_state.bet_to(player_id=player_id("A"), total=100)
    round_state.call(player_id=player_id("B"))

    round_state.fold(player_id=player_id("C"))

    assert round_state.complete
    with pytest.raises(BettingRoundCompleteError):
        round_state.bet_to(player_id=player_id("A"), total=200)


def test_generic_call_action_uses_the_same_transition_pipeline() -> None:
    round_state = betting_round(player("A", 0), player("B", 1))
    round_state.bet_to(player_id=player_id("A"), total=100)

    result = round_state.act(player_id=player_id("B"), action=Call())

    assert result.action_kind is ActionKind.CALL
    assert result.round_complete


@given(
    minimum_bet=st.integers(min_value=2, max_value=100),
    first_short=st.integers(min_value=1, max_value=99),
    second_short=st.integers(min_value=1, max_value=99),
)
def test_generated_cumulative_short_all_ins_reopen_at_exact_full_raise_threshold(
    minimum_bet: int,
    first_short: int,
    second_short: int,
) -> None:
    first_short %= minimum_bet
    second_short %= minimum_bet
    if first_short == 0 or second_short == 0:
        return

    first_total = minimum_bet + first_short
    second_total = first_total + second_short
    round_state = betting_round(
        player("A", 0, minimum_bet * 10),
        player("B", 1, first_total),
        player("C", 2, minimum_bet * 10),
        player("D", 3, second_total),
        player("E", 4, minimum_bet * 10),
        minimum_bet=minimum_bet,
    )
    round_state.bet_to(player_id=player_id("A"), total=minimum_bet)
    round_state.raise_to(player_id=player_id("B"), total=first_total)
    round_state.call(player_id=player_id("C"))
    round_state.raise_to(player_id=player_id("D"), total=second_total)
    round_state.call(player_id=player_id("E"))

    assert (ActionKind.RAISE in round_state.legal_actions().kinds) is (
        first_short + second_short >= minimum_bet
    )
    assert round_state.minimum_raise_increment == minimum_bet
    assert_round_invariants(round_state)


DECISIONS = st.lists(st.integers(min_value=0, max_value=4), min_size=1, max_size=200)


def play_decisions(round_state: BettingRound, decisions: list[int]) -> None:
    for decision in decisions:
        if round_state.complete:
            return
        actions = round_state.legal_actions()
        actor = actions.player_id
        if decision == 0:
            round_state.fold(player_id=actor)
        elif decision == 1 and ActionKind.CHECK in actions.kinds:
            round_state.check(player_id=actor)
        elif decision == 1 and ActionKind.CALL in actions.kinds:
            round_state.call(player_id=actor)
        elif decision in (2, 3) and actions.bet is not None:
            if decision == 3 or actions.bet.maximum_to < actions.bet.minimum_full_to:
                total = actions.bet.maximum_to
            else:
                total = actions.bet.minimum_full_to
            round_state.bet_to(player_id=actor, total=total)
        elif decision in (2, 3) and actions.raise_to is not None:
            if decision == 3 or actions.raise_to.maximum_to < actions.raise_to.minimum_full_to:
                total = actions.raise_to.maximum_to
            else:
                total = actions.raise_to.minimum_full_to
            round_state.raise_to(player_id=actor, total=total)
        elif ActionKind.CALL in actions.kinds:
            round_state.call(player_id=actor)
        else:
            round_state.check(player_id=actor)


@given(
    stacks=st.lists(
        st.integers(min_value=1, max_value=500),
        min_size=2,
        max_size=6,
    ),
    minimum_bet=st.integers(min_value=1, max_value=100),
    decisions=DECISIONS,
)
def test_generated_legal_transitions_preserve_all_round_invariants(
    stacks: list[int],
    minimum_bet: int,
    decisions: list[int],
) -> None:
    round_state = betting_round(
        *(player(f"P{index}", index, stack) for index, stack in enumerate(stacks)),
        first="P0",
        minimum_bet=minimum_bet,
    )

    previous_commitments = {
        participant.player_id: participant.committed for participant in round_state.participants
    }
    previous_remaining = {
        participant.player_id: participant.remaining_stack.chips
        for participant in round_state.participants
    }
    previous_wager = round_state.current_wager
    previous_minimum_raise = round_state.minimum_raise_increment

    for decision in decisions:
        if round_state.complete:
            break
        play_decisions(round_state, [decision])
        assert_round_invariants(round_state)
        assert round_state.current_wager >= previous_wager
        assert round_state.minimum_raise_increment >= previous_minimum_raise
        for participant in round_state.participants:
            assert participant.committed >= previous_commitments[participant.player_id]
            assert participant.remaining_stack.chips <= previous_remaining[participant.player_id]
            previous_commitments[participant.player_id] = participant.committed
            previous_remaining[participant.player_id] = participant.remaining_stack.chips
        previous_wager = round_state.current_wager
        previous_minimum_raise = round_state.minimum_raise_increment


@given(
    stacks=st.lists(
        st.integers(min_value=1, max_value=200),
        min_size=2,
        max_size=6,
    ),
    minimum_bet=st.integers(min_value=1, max_value=50),
    decisions=DECISIONS,
)
def test_generated_action_replay_is_deterministic(
    stacks: list[int],
    minimum_bet: int,
    decisions: list[int],
) -> None:
    players = tuple(player(f"P{index}", index, stack) for index, stack in enumerate(stacks))
    first = betting_round(*players, first="P0", minimum_bet=minimum_bet)
    second = betting_round(*players, first="P0", minimum_bet=minimum_bet)

    play_decisions(first, decisions)
    play_decisions(second, decisions)

    assert first.snapshot == second.snapshot


@given(
    stacks=st.lists(
        st.integers(min_value=1, max_value=200),
        min_size=2,
        max_size=6,
    ),
    minimum_bet=st.integers(min_value=1, max_value=50),
    decisions=DECISIONS,
)
def test_generated_rounds_terminate_after_a_finite_legal_drain(
    stacks: list[int],
    minimum_bet: int,
    decisions: list[int],
) -> None:
    round_state = betting_round(
        *(player(f"P{index}", index, stack) for index, stack in enumerate(stacks)),
        first="P0",
        minimum_bet=minimum_bet,
    )
    play_decisions(round_state, decisions)

    drain_actions = 0
    while not round_state.complete:
        actions = round_state.legal_actions()
        if ActionKind.CALL in actions.kinds:
            round_state.call(player_id=actions.player_id)
        else:
            round_state.check(player_id=actions.player_id)
        drain_actions += 1

    assert drain_actions <= len(stacks)
    assert_round_invariants(round_state)


@given(
    stack=st.integers(min_value=101, max_value=10_000),
    requested=st.integers(min_value=1, max_value=99),
)
def test_generated_non_all_in_underbets_are_atomic(stack: int, requested: int) -> None:
    round_state = betting_round(player("A", 0, stack), player("B", 1))
    before = round_state.snapshot

    with pytest.raises(WagerBelowMinimumError):
        round_state.bet_to(player_id=player_id("A"), total=requested)

    assert round_state.snapshot == before
