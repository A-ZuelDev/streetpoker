from collections import Counter

import pytest
from hypothesis import event, given, settings
from hypothesis import strategies as st
from tests.stress import holdem_stress
from tests.support.holdem_simulation import (
    ActionIntent,
    HandScenario,
    deterministic_scenario,
    replay_resolved_actions,
    run_simulation,
    start_scenario,
)

from streetpoker.domain import ActionKind, HoldemHandPhase, WagerClassification

NORMAL_DETERMINISTIC_HANDS = 3_000


@st.composite
def action_intents(draw: st.DrawFn) -> tuple[ActionIntent, ...]:
    return tuple(
        draw(
            st.lists(
                st.sampled_from(tuple(ActionIntent)),
                min_size=0,
                max_size=32,
            )
        )
    )


@st.composite
def general_hand_scenarios(draw: st.DrawFn) -> HandScenario:
    player_count = draw(st.integers(min_value=2, max_value=6))
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
    button = draw(st.sampled_from(seats))
    small_blind = draw(st.integers(min_value=1, max_value=50))
    big_blind = draw(st.integers(min_value=small_blind + 1, max_value=100))
    boundaries = {
        1,
        max(1, small_blind - 1),
        small_blind,
        small_blind + 1,
        big_blind - 1,
        big_blind,
        big_blind + 1,
        2 * big_blind - 1,
        2 * big_blind,
        2 * big_blind + 1,
        10 * big_blind,
        1000 * big_blind,
    }
    if small_blind + 1 < big_blind:
        boundaries.add(draw(st.integers(min_value=small_blind + 1, max_value=big_blind - 1)))
    stack_strategy = st.one_of(
        st.sampled_from(tuple(sorted(boundaries))),
        st.integers(min_value=1, max_value=100 * big_blind),
    )
    stacks = tuple(
        draw(
            st.lists(
                stack_strategy,
                min_size=player_count,
                max_size=player_count,
            )
        )
    )
    return HandScenario(
        seats=seats,
        starting_stacks=stacks,
        button=button,
        small_blind=small_blind,
        big_blind=big_blind,
        deck_seed=draw(st.integers(min_value=0, max_value=2**64 - 1)),
        action_intents=draw(action_intents()),
    )


def test_deterministic_normal_sweep_completes_and_settles_every_hand() -> None:
    player_counts: Counter[int] = Counter()
    terminal_paths: Counter[HoldemHandPhase] = Counter()
    classifications: Counter[WagerClassification] = Counter()
    action_matrix: Counter[tuple[HoldemHandPhase, ActionKind]] = Counter()
    terminals_by_player_count: dict[int, set[HoldemHandPhase]] = {
        count: set() for count in range(2, 7)
    }
    button_positions: set[int] = set()
    sparse_layouts = 0

    for case_index in range(NORMAL_DETERMINISTIC_HANDS):
        _, _, scenario = deterministic_scenario(7_2026, case_index)
        result = run_simulation(scenario)
        player_counts[len(scenario.seats)] += 1
        terminal_paths[result.snapshots[-1].phase] += 1
        terminals_by_player_count[len(scenario.seats)].add(result.snapshots[-1].phase)
        button_positions.add(scenario.button)
        sparse_layouts += scenario.seats != tuple(
            range(scenario.seats[0], scenario.seats[0] + len(scenario.seats))
        )
        classifications.update(
            action.transition.betting_result.classification for action in result.actions
        )
        action_matrix.update((action.phase_before, action.action_kind) for action in result.actions)

    assert set(player_counts) == {2, 3, 4, 5, 6}
    assert terminal_paths[HoldemHandPhase.SHOWDOWN_READY] > 0
    assert terminal_paths[HoldemHandPhase.COMPLETE_BY_FOLD] > 0
    assert all(
        paths
        == {
            HoldemHandPhase.SHOWDOWN_READY,
            HoldemHandPhase.COMPLETE_BY_FOLD,
        }
        for paths in terminals_by_player_count.values()
    )
    assert button_positions == set(range(6))
    assert sparse_layouts > 0
    assert all(
        classifications[classification] > 0
        for classification in (
            WagerClassification.FULL_BET,
            WagerClassification.SHORT_ALL_IN_BET,
            WagerClassification.FULL_RAISE,
            WagerClassification.SHORT_ALL_IN_RAISE,
        )
    )
    for phase in (
        HoldemHandPhase.FLOP,
        HoldemHandPhase.TURN,
        HoldemHandPhase.RIVER,
    ):
        assert all(action_matrix[phase, action_kind] > 0 for action_kind in ActionKind)
    assert all(
        action_matrix[HoldemHandPhase.PREFLOP, action_kind] > 0
        for action_kind in (
            ActionKind.CHECK,
            ActionKind.CALL,
            ActionKind.RAISE,
            ActionKind.FOLD,
        )
    )


@settings(max_examples=100, deadline=None)
@given(scenario=general_hand_scenarios())
def test_generated_hands_reach_terminal_settlement(scenario: HandScenario) -> None:
    result = run_simulation(scenario)

    event(f"players={len(scenario.seats)}")
    event(f"terminal={result.snapshots[-1].phase.value}")
    event(f"side_pots={len(result.snapshots[-1].pot_result.side_pots)}")  # type: ignore[union-attr]
    for action in result.actions:
        event(f"action={action.action_kind.value}")
        event(f"classification={action.transition.betting_result.classification.value}")

    assert result.snapshots[-1].terminal


def test_same_scenario_and_intents_replay_every_transition_exactly() -> None:
    for case_index in (0, 7, 23, 51, 99):
        _, _, scenario = deterministic_scenario(91_337, case_index)
        first = run_simulation(scenario)
        second = run_simulation(scenario)

        assert first == second
        assert first.actions == second.actions
        assert first.snapshots == second.snapshots
        assert first.settlement == second.settlement


def test_resolved_action_trace_replays_exactly_through_public_api() -> None:
    _, _, scenario = deterministic_scenario(44_001, 17)
    original = run_simulation(scenario)

    replayed = replay_resolved_actions(scenario, original.actions)

    assert replayed == original


def test_separately_started_hands_do_not_alias_mutable_state() -> None:
    _, _, scenario = deterministic_scenario(88_100, 4)
    first = start_scenario(scenario)
    second = start_scenario(scenario)
    untouched = second.snapshot

    actor = first.legal_actions().player_id
    first.fold(player_id=actor)

    assert second.snapshot == untouched
    second.fold(player_id=second.legal_actions().player_id)
    assert first.snapshot == second.snapshot


def test_stress_runner_failure_reports_exact_replay(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_simulation(*args: object, **kwargs: object) -> object:
        raise AssertionError("forced invariant failure")

    monkeypatch.setattr(holdem_stress, "run_simulation", fail_simulation)

    exit_code = holdem_stress.main(["--seed", "123", "--case-index", "7"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "master_seed=123" in captured.err
    assert "case_index=7" in captured.err
    assert "case_seed=" in captured.err
    assert "action_seed=" in captured.err
    assert "deck_seed=" in captured.err
    assert "scenario=" in captured.err
    assert "resolved_actions=[]" in captured.err
    assert "--seed 123 --case-index 7" in captured.err
