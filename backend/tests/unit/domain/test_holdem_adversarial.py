from collections.abc import Callable

import pytest
from backend.tests.support.holdem_simulation import (
    ActionIntent,
    HandScenario,
    run_simulation,
    start_scenario,
)
from hypothesis import event, given, settings
from hypothesis import strategies as st

from streetpoker.domain import (
    ActionKind,
    HandAlreadyTerminalError,
    HoldemHandPhase,
    IllegalBetError,
    IllegalCallError,
    IllegalCheckError,
    IllegalRaiseError,
    OutOfTurnError,
    PlayerId,
    WagerBelowMinimumError,
    WagerClassification,
    WagerExceedsStackError,
)

PASSIVE = (ActionIntent.PASSIVE,) * 32


@pytest.mark.parametrize("button", [1, 4])
def test_heads_up_sparse_positions_and_both_buttons_check_down(button: int) -> None:
    scenario = HandScenario(
        seats=(1, 4),
        starting_stacks=(1_000, 1_000),
        button=button,
        small_blind=50,
        big_blind=100,
        deck_seed=button,
        action_intents=PASSIVE,
    )

    result = run_simulation(scenario)

    assert result.snapshots[0].small_blind_position.value == button
    assert result.actions[0].player_id == PlayerId("P0" if button == 1 else "P1")
    flop = next(snapshot for snapshot in result.snapshots if snapshot.phase is HoldemHandPhase.FLOP)
    assert flop.betting_round is not None
    expected_bb = PlayerId("P1" if button == 1 else "P0")
    assert flop.betting_round.current_player == expected_bb
    assert result.snapshots[-1].phase is HoldemHandPhase.SHOWDOWN_READY


@pytest.mark.parametrize(
    ("stacks", "intents", "expected_phase"),
    [
        ((1, 1_000), PASSIVE, HoldemHandPhase.SHOWDOWN_READY),
        ((1_000, 30), PASSIVE, HoldemHandPhase.SHOWDOWN_READY),
        ((1_000, 60), (ActionIntent.PASSIVE,), HoldemHandPhase.SHOWDOWN_READY),
        ((1_000, 60), (ActionIntent.FOLD,), HoldemHandPhase.COMPLETE_BY_FOLD),
        ((50, 100), PASSIVE, HoldemHandPhase.SHOWDOWN_READY),
        (
            (150, 1_000),
            (ActionIntent.SHORT_ALL_IN, ActionIntent.PASSIVE),
            HoldemHandPhase.SHOWDOWN_READY,
        ),
        ((1_000, 1_000), (ActionIntent.MIN_RAISE, *PASSIVE), HoldemHandPhase.SHOWDOWN_READY),
        ((1_000, 1_000), (ActionIntent.FOLD,), HoldemHandPhase.COMPLETE_BY_FOLD),
    ],
)
def test_heads_up_short_blinds_all_ins_raises_and_terminal_paths(
    stacks: tuple[int, int],
    intents: tuple[ActionIntent, ...],
    expected_phase: HoldemHandPhase,
) -> None:
    scenario = HandScenario(
        seats=(0, 5),
        starting_stacks=stacks,
        button=0,
        small_blind=50,
        big_blind=100,
        deck_seed=sum(stacks),
        action_intents=intents,
    )

    result = run_simulation(scenario)

    assert result.snapshots[-1].phase is expected_phase


@pytest.mark.parametrize(
    "boundary_stack",
    [1, 49, 50, 51, 75, 99, 100, 101, 199, 200, 201, 1_000, 100_000],
)
@pytest.mark.parametrize("player_count", range(2, 7))
def test_blind_and_minimum_raise_stack_boundaries_complete_and_settle(
    boundary_stack: int,
    player_count: int,
) -> None:
    seats = tuple(range(player_count))
    scenario = HandScenario(
        seats=seats,
        starting_stacks=(boundary_stack, *((1_000,) * (player_count - 1))),
        button=seats[-1],
        small_blind=50,
        big_blind=100,
        deck_seed=boundary_stack * 10 + player_count,
        action_intents=(
            ActionIntent.MAX_RAISE,
            ActionIntent.SHORT_ALL_IN,
            *PASSIVE,
        ),
    )

    result = run_simulation(scenario)

    assert result.snapshots[-1].terminal


def test_multiple_short_all_ins_cumulatively_reopen_in_complete_hand() -> None:
    scenario = HandScenario(
        seats=(0, 1, 2, 3, 4),
        starting_stacks=(1_000, 1_000, 1_000, 250, 300),
        button=4,
        small_blind=50,
        big_blind=100,
        deck_seed=772,
        action_intents=(
            ActionIntent.MIN_RAISE,
            ActionIntent.SHORT_ALL_IN,
            ActionIntent.SHORT_ALL_IN,
            ActionIntent.PASSIVE,
            ActionIntent.PASSIVE,
            ActionIntent.MIN_RAISE,
        ),
    )

    result = run_simulation(scenario)
    classifications = tuple(
        action.transition.betting_result.classification for action in result.actions
    )

    assert classifications[:3] == (
        WagerClassification.FULL_RAISE,
        WagerClassification.SHORT_ALL_IN_RAISE,
        WagerClassification.SHORT_ALL_IN_RAISE,
    )
    assert classifications[5] is WagerClassification.FULL_RAISE
    assert result.snapshots[-1].terminal


def test_distinct_all_in_depths_create_multiple_pots_and_unique_excess() -> None:
    scenario = HandScenario(
        seats=(0, 1, 2, 3, 5),
        starting_stacks=(40, 100, 150, 1_000, 300),
        button=5,
        small_blind=50,
        big_blind=100,
        deck_seed=5_555,
        action_intents=(ActionIntent.MAX_RAISE,) * 12,
    )

    result = run_simulation(scenario)
    terminal = result.snapshots[-1]

    assert terminal.phase is HoldemHandPhase.SHOWDOWN_READY
    assert terminal.pot_result is not None
    assert len(terminal.pot_result.pots) == 4
    assert terminal.pot_result.uncalled_excess is not None
    assert terminal.pot_result.uncalled_excess.chips == 700


@pytest.mark.parametrize(
    ("seats", "seed", "winner_count", "board_plays"),
    [
        ((1, 5), 0, 1, False),
        ((1, 5), 38, 2, False),
        ((0, 2, 5), 68, 3, True),
        ((0, 1, 3, 5), 79, 4, True),
    ],
)
def test_pinned_lifecycle_seeds_cover_clear_winners_and_multiway_ties(
    seats: tuple[int, ...],
    seed: int,
    winner_count: int,
    board_plays: bool,
) -> None:
    scenario = HandScenario(
        seats=seats,
        starting_stacks=(1_000,) * len(seats),
        button=seats[-1],
        small_blind=50,
        big_blind=100,
        deck_seed=seed,
        action_intents=PASSIVE,
    )

    result = run_simulation(scenario)
    terminal = result.snapshots[-1]

    assert len(result.settlement.pot_awards[0].winners) == winner_count
    if board_plays:
        board = set(terminal.board)
        assert all(
            set(evaluation.evaluated_hand.best_five) <= board
            for evaluation in result.settlement.evaluations
        )


@pytest.mark.parametrize(
    ("seed", "expected_winner_counts"),
    [
        (0, (2, 1, 1, 1)),
        (8, (1, 4, 3, 2)),
    ],
)
def test_pinned_side_pot_seeds_cover_main_and_side_pot_ties(
    seed: int,
    expected_winner_counts: tuple[int, ...],
) -> None:
    scenario = HandScenario(
        seats=(0, 1, 2, 3, 5),
        starting_stacks=(40, 100, 150, 1_000, 300),
        button=5,
        small_blind=50,
        big_blind=100,
        deck_seed=seed,
        action_intents=(ActionIntent.MAX_RAISE,) * 12,
    )

    result = run_simulation(scenario)

    assert (
        tuple(len(award.winners) for award in result.settlement.pot_awards)
        == expected_winner_counts
    )


def test_large_contributor_can_fold_later_and_remains_ineligible() -> None:
    scenario = HandScenario(
        seats=(0, 2, 5),
        starting_stacks=(1_000, 1_000, 1_000),
        button=5,
        small_blind=50,
        big_blind=100,
        deck_seed=909,
        action_intents=(
            ActionIntent.PASSIVE,
            ActionIntent.PASSIVE,
            ActionIntent.PASSIVE,
            ActionIntent.PASSIVE,
            ActionIntent.MIN_BET,
            ActionIntent.PASSIVE,
            ActionIntent.FOLD,
            ActionIntent.FOLD,
        ),
    )

    result = run_simulation(scenario)
    terminal = result.snapshots[-1]
    folded = {
        participant.player_id
        for participant in terminal.participants
        if participant.status.value == "folded"
    }

    assert terminal.phase is HoldemHandPhase.COMPLETE_BY_FOLD
    assert terminal.pot_result is not None
    assert any(folded & pot.contributors for pot in terminal.pot_result.pots)
    assert all(not (folded & pot.eligible_players) for pot in terminal.pot_result.pots)


@st.composite
def distinct_depth_scenarios(draw: st.DrawFn) -> HandScenario:
    player_count = draw(st.integers(min_value=3, max_value=6))
    seats = tuple(
        sorted(
            draw(
                st.lists(
                    st.integers(min_value=0, max_value=5),
                    min_size=player_count,
                    max_size=player_count,
                    unique=True,
                )
            )
        )
    )
    depths = tuple(
        sorted(
            draw(
                st.lists(
                    st.integers(min_value=1, max_value=2_000),
                    min_size=player_count,
                    max_size=player_count,
                    unique=True,
                )
            )
        )
    )
    return HandScenario(
        seats=seats,
        starting_stacks=depths,
        button=draw(st.sampled_from(seats)),
        small_blind=50,
        big_blind=100,
        deck_seed=draw(st.integers(min_value=0, max_value=2**32)),
        action_intents=(ActionIntent.MAX_RAISE,) * 18,
    )


@settings(max_examples=75, deadline=None)
@given(scenario=distinct_depth_scenarios())
def test_generated_distinct_depth_multiway_hands_settle(scenario: HandScenario) -> None:
    result = run_simulation(scenario)
    terminal = result.snapshots[-1]

    event(f"players={len(scenario.seats)}")
    event(f"terminal={terminal.phase.value}")
    event(f"pots={len(terminal.pot_result.pots)}")  # type: ignore[union-attr]
    assert terminal.terminal


def test_representative_invalid_actions_are_atomic() -> None:
    scenario = HandScenario(
        seats=(0, 2, 5),
        starting_stacks=(1_000, 1_000, 1_000),
        button=5,
        small_blind=50,
        big_blind=100,
        deck_seed=13,
        action_intents=(),
    )
    hand = start_scenario(scenario)
    actor = hand.legal_actions().player_id

    def assert_atomic(
        expected: type[Exception],
        command: Callable[[], object],
    ) -> None:
        before = hand.snapshot
        with pytest.raises(expected):
            command()
        assert hand.snapshot == before

    assert_atomic(OutOfTurnError, lambda: hand.call(player_id=PlayerId("P0")))
    assert_atomic(IllegalCheckError, lambda: hand.check(player_id=actor))
    assert_atomic(IllegalBetError, lambda: hand.bet_to(player_id=actor, total=200))
    assert_atomic(WagerBelowMinimumError, lambda: hand.raise_to(player_id=actor, total=150))
    assert_atomic(WagerExceedsStackError, lambda: hand.raise_to(player_id=actor, total=2_000))

    hand.call(player_id=actor)
    next_actor = hand.legal_actions().player_id
    hand.call(player_id=next_actor)
    hand.check(player_id=hand.legal_actions().player_id)
    flop_actor = hand.legal_actions().player_id
    assert_atomic(IllegalCallError, lambda: hand.call(player_id=flop_actor))
    assert_atomic(IllegalRaiseError, lambda: hand.raise_to(player_id=flop_actor, total=100))

    hand.check(player_id=flop_actor)
    assert_atomic(OutOfTurnError, lambda: hand.check(player_id=flop_actor))

    while not hand.snapshot.terminal:
        legal = hand.legal_actions()
        if ActionKind.CHECK in legal.kinds:
            hand.check(player_id=legal.player_id)
        else:
            hand.call(player_id=legal.player_id)
    assert_atomic(HandAlreadyTerminalError, lambda: hand.check(player_id=PlayerId("P0")))
