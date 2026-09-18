from dataclasses import FrozenInstanceError, replace

import pytest

from streetpoker.domain import (
    InvalidStandUpOutcomeError,
    InvalidStandUpStateError,
    PlayerId,
    SeatIndex,
    StaleStandUpOutcomeError,
    StandUpCancellation,
    StandUpCancelReason,
    StandUpHandOutcome,
    StandUpParticipant,
    StandUpResolution,
    StandUpRound,
    StandUpTransfer,
)


def player(name: str) -> PlayerId:
    return PlayerId(name)


def cohort(*seats: tuple[str, int]) -> tuple[StandUpParticipant, ...]:
    return tuple(StandUpParticipant(player(name), SeatIndex(seat)) for name, seat in seats)


def round_for(*seats: tuple[str, int], hand: int = 10, penalty: int = 5) -> StandUpRound:
    return StandUpRound.start(
        start_hand_number=hand,
        participants=cohort(*seats),
        penalty_per_recipient_chips=penalty,
    )


def outcome(
    hand: int,
    participants: tuple[str, ...],
    winners: tuple[str, ...],
    *,
    button: int = 0,
) -> StandUpHandOutcome:
    return StandUpHandOutcome(
        hand_number=hand,
        participants=tuple(player(name) for name in participants),
        main_pot_winners=tuple(player(name) for name in winners),
        button_seat=SeatIndex(button),
    )


def three_player_round() -> StandUpRound:
    return round_for(("A", 0), ("B", 2), ("C", 4))


def four_player_before_resolution() -> StandUpRound:
    started = round_for(("A", 0), ("B", 2), ("C", 4), ("D", 5))
    first = started.apply_hand(outcome(10, ("A", "B", "C", "D"), ("A",)))
    assert isinstance(first, StandUpRound)
    second = first.apply_hand(outcome(11, ("A", "B", "C", "D"), ("B",)))
    assert isinstance(second, StandUpRound)
    return second


def test_round_starts_with_frozen_cohort_and_every_player_at_risk() -> None:
    original = list(cohort(("C", 4), ("A", 0), ("B", 2)))
    active = StandUpRound.start(
        start_hand_number=10, participants=original, penalty_per_recipient_chips=5
    )
    original.clear()

    assert tuple(item.player_id for item in active.participants) == (
        player("A"),
        player("B"),
        player("C"),
    )
    assert active.at_risk_player_ids == {player("A"), player("B"), player("C")}
    assert active.cleared_player_ids == frozenset()
    assert active.last_processed_hand_number == 9
    assert active.penalty_per_recipient_chips == 5
    with pytest.raises(FrozenInstanceError):
        active.penalty_per_recipient_chips = 6  # type: ignore[misc]


def test_sole_main_pot_winner_clears_and_nonwinners_remain_at_risk() -> None:
    active = three_player_round()
    advanced = active.apply_hand(outcome(10, ("A", "B", "C"), ("A",)))

    assert isinstance(advanced, StandUpRound)
    assert advanced.cleared_player_ids == {player("A")}
    assert advanced.at_risk_player_ids == {player("B"), player("C")}
    assert advanced.last_processed_hand_number == 10
    assert active.cleared_player_ids == frozenset()


def test_uncontested_sole_main_pot_win_clears_without_source_specific_input() -> None:
    advanced = three_player_round().apply_hand(outcome(10, ("A", "B", "C"), ("B",)))
    assert isinstance(advanced, StandUpRound)
    assert advanced.cleared_player_ids == {player("B")}


def test_chopped_main_pot_does_not_clear_even_when_both_receive_chips() -> None:
    active = three_player_round()
    advanced = active.apply_hand(outcome(10, ("A", "B", "C"), ("A", "B")))

    assert isinstance(advanced, StandUpRound)
    assert advanced.cleared_player_ids == frozenset()
    assert advanced.at_risk_player_ids == active.at_risk_player_ids
    assert advanced.last_processed_hand_number == 10


def test_side_pot_only_winner_does_not_clear_and_main_winner_does() -> None:
    # The adapter supplies only main-pot winners. Side-pot awards cannot change this rule.
    active = three_player_round()
    advanced = active.apply_hand(outcome(10, ("A", "B", "C"), ("A",)))
    assert isinstance(advanced, StandUpRound)
    assert player("B") in advanced.at_risk_player_ids  # B won a side pot in the poker result.
    assert player("A") in advanced.cleared_player_ids  # A lost that side pot but won main.
    assert not hasattr(StandUpHandOutcome, "side_pot_winners")


def test_already_cleared_and_new_noncohort_winners_do_not_change_cohort() -> None:
    first = three_player_round().apply_hand(outcome(10, ("A", "B", "C"), ("A",)))
    assert isinstance(first, StandUpRound)
    again = first.apply_hand(outcome(11, ("A", "B", "C"), ("A",)))
    assert isinstance(again, StandUpRound)
    outsider = again.apply_hand(outcome(12, ("A", "B", "C", "X"), ("X",)))
    assert isinstance(outsider, StandUpRound)
    assert outsider.cleared_player_ids == {player("A")}
    assert outsider.at_risk_player_ids == {player("B"), player("C")}
    assert outsider.last_processed_hand_number == 12


def test_two_player_round_resolves_after_one_sole_main_pot_win() -> None:
    active = round_for(("A", 0), ("B", 2), penalty=7)
    resolved = active.apply_hand(
        outcome(10, ("A", "B"), ("A",), button=0), squid_available_stack=100
    )

    assert isinstance(resolved, StandUpResolution)
    assert resolved.squid == player("B")
    assert resolved.intended_total == resolved.actual_total == 7
    assert resolved.shortfall == 0
    assert resolved.transfers == (StandUpTransfer(player("B"), player("A"), 7),)
    assert not hasattr(resolved, "apply_hand")


def test_multi_player_round_resolves_only_when_one_at_risk_remains() -> None:
    active = four_player_before_resolution()
    assert active.at_risk_player_ids == {player("C"), player("D")}
    resolved = active.apply_hand(
        outcome(12, ("A", "B", "C", "D"), ("C",), button=4),
        squid_available_stack=20,
    )

    assert isinstance(resolved, StandUpResolution)
    assert resolved.squid == player("D")
    assert resolved.hand_number == 12
    assert resolved.intended_total == resolved.actual_total == 15
    assert resolved.shortfall == 0
    assert resolved.transfers == (
        StandUpTransfer(player("D"), player("A"), 5),
        StandUpTransfer(player("D"), player("B"), 5),
        StandUpTransfer(player("D"), player("C"), 5),
    )


@pytest.mark.parametrize(
    ("button", "expected"),
    [
        (4, (("A", 3), ("B", 3), ("C", 2))),
        (5, (("A", 3), ("B", 3), ("C", 2))),
        (2, (("C", 3), ("A", 3), ("B", 2))),
        (0, (("B", 3), ("C", 3), ("A", 2))),
    ],
)
def test_partial_payout_follows_clockwise_button_order(
    button: int, expected: tuple[tuple[str, int], ...]
) -> None:
    active = four_player_before_resolution()
    resolved = active.apply_hand(
        outcome(12, ("A", "B", "C", "D"), ("C",), button=button),
        squid_available_stack=8,
    )

    assert isinstance(resolved, StandUpResolution)
    assert tuple((item.to_player_id.value, item.chips) for item in resolved.transfers) == expected
    assert resolved.intended_total == 15
    assert resolved.actual_total == 8
    assert resolved.shortfall == 7
    assert resolved.actual_total <= resolved.squid_available_stack
    assert len({item.to_player_id for item in resolved.transfers}) == len(resolved.transfers)
    assert all(item.chips <= resolved.penalty_per_recipient_chips for item in resolved.transfers)


def test_wraparound_and_less_than_one_chip_per_recipient_omit_zero_transfers() -> None:
    active = four_player_before_resolution()
    resolved = active.apply_hand(
        outcome(12, ("A", "B", "C", "D"), ("C",), button=5),
        squid_available_stack=2,
    )

    assert isinstance(resolved, StandUpResolution)
    assert resolved.transfers == (
        StandUpTransfer(player("D"), player("A"), 1),
        StandUpTransfer(player("D"), player("B"), 1),
    )
    assert resolved.actual_total == 2
    assert resolved.shortfall == 13


def test_zero_available_stack_yields_no_transfers_and_full_shortfall() -> None:
    active = round_for(("A", 0), ("B", 2), penalty=7)
    resolved = active.apply_hand(outcome(10, ("A", "B"), ("A",)), squid_available_stack=0)

    assert isinstance(resolved, StandUpResolution)
    assert resolved.transfers == ()
    assert resolved.actual_total == 0
    assert resolved.intended_total == resolved.shortfall == 7


@pytest.mark.parametrize("reason", tuple(StandUpCancelReason))
def test_cancellation_is_terminal_auditable_and_has_no_payout(reason: StandUpCancelReason) -> None:
    active = three_player_round()
    cancelled = active.cancel(reason)

    assert isinstance(cancelled, StandUpCancellation)
    assert cancelled.reason is reason
    assert cancelled.start_hand_number == 10
    assert cancelled.last_processed_hand_number == 9
    assert cancelled.participants == active.participants
    assert not hasattr(cancelled, "transfers")
    assert not hasattr(cancelled, "apply_hand")
    assert active.cleared_player_ids == frozenset()


def test_duplicate_and_stale_hand_outcomes_raise_without_changing_updated_round() -> None:
    active = three_player_round()
    first_outcome = outcome(10, ("A", "B", "C"), ("A",))
    advanced = active.apply_hand(first_outcome)
    assert isinstance(advanced, StandUpRound)

    with pytest.raises(StaleStandUpOutcomeError):
        advanced.apply_hand(first_outcome)
    with pytest.raises(StaleStandUpOutcomeError):
        advanced.apply_hand(outcome(9, ("A", "B", "C"), ("B",)))
    with pytest.raises(InvalidStandUpOutcomeError):
        advanced.apply_hand(outcome(12, ("A", "B", "C"), ("B",)))
    assert advanced.cleared_player_ids == {player("A")}
    assert advanced.last_processed_hand_number == 10


def test_first_outcome_cannot_skip_starting_hand() -> None:
    active = three_player_round()
    with pytest.raises(InvalidStandUpOutcomeError):
        active.apply_hand(outcome(11, ("A", "B", "C"), ("A",)))
    assert active.last_processed_hand_number == 9


def test_missing_frozen_participant_rejected_but_extra_player_may_win() -> None:
    active = three_player_round()
    with pytest.raises(InvalidStandUpOutcomeError):
        active.apply_hand(outcome(10, ("A", "B", "X"), ("X",)))
    advanced = active.apply_hand(outcome(10, ("A", "B", "C", "X"), ("X",)))
    assert isinstance(advanced, StandUpRound)
    assert advanced.cleared_player_ids == frozenset()


def test_resolving_hand_requires_valid_settled_squid_stack() -> None:
    active = round_for(("A", 0), ("B", 2))
    hand = outcome(10, ("A", "B"), ("A",))
    with pytest.raises(InvalidStandUpOutcomeError):
        active.apply_hand(hand)
    for value in (-1, True, 1.5, "3"):
        with pytest.raises(InvalidStandUpOutcomeError):
            active.apply_hand(hand, squid_available_stack=value)  # type: ignore[arg-type]
    assert active.at_risk_player_ids == {player("A"), player("B")}


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "10"])
def test_invalid_start_hand_number_and_penalty_rejected(bad: object) -> None:
    participants = cohort(("A", 0), ("B", 2))
    with pytest.raises(InvalidStandUpStateError):
        StandUpRound.start(
            start_hand_number=bad,  # type: ignore[arg-type]
            participants=participants,
            penalty_per_recipient_chips=5,
        )
    with pytest.raises(InvalidStandUpStateError):
        StandUpRound.start(
            start_hand_number=10,
            participants=participants,
            penalty_per_recipient_chips=bad,  # type: ignore[arg-type]
        )


def test_invalid_cohort_identity_and_seats_rejected() -> None:
    with pytest.raises(InvalidStandUpStateError):
        round_for(("A", 0))
    with pytest.raises(InvalidStandUpStateError):
        round_for(("A", 0), ("A", 2))
    with pytest.raises(InvalidStandUpStateError):
        round_for(("A", 0), ("B", 0))
    with pytest.raises(InvalidStandUpStateError):
        StandUpParticipant(player("A"), SeatIndex(6))
    with pytest.raises(InvalidStandUpStateError):
        StandUpParticipant("A", SeatIndex(0))  # type: ignore[arg-type]
    with pytest.raises(InvalidStandUpStateError):
        StandUpRound.start(
            start_hand_number=10,
            participants=None,  # type: ignore[arg-type]
            penalty_per_recipient_chips=5,
        )


def test_direct_round_construction_rejects_invalid_partition_or_version() -> None:
    active = three_player_round()
    with pytest.raises(InvalidStandUpStateError):
        replace(active, cleared_player_ids=frozenset({player("X")}))
    with pytest.raises(InvalidStandUpStateError):
        replace(active, cleared_player_ids=frozenset({player("A"), player("B")}))
    with pytest.raises(InvalidStandUpStateError):
        replace(active, last_processed_hand_number=8)
    with pytest.raises(InvalidStandUpStateError):
        replace(active, cleared_player_ids=frozenset({player("A")}))
    four = round_for(("A", 0), ("B", 1), ("C", 2), ("D", 3))
    with pytest.raises(InvalidStandUpStateError):
        replace(
            four,
            last_processed_hand_number=10,
            cleared_player_ids=frozenset({player("A"), player("B")}),
        )


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "10"])
def test_invalid_outcome_hand_number_rejected(bad: object) -> None:
    with pytest.raises(InvalidStandUpOutcomeError):
        StandUpHandOutcome(
            hand_number=bad,  # type: ignore[arg-type]
            participants=(player("A"), player("B")),
            main_pot_winners=(player("A"),),
            button_seat=SeatIndex(0),
        )


@pytest.mark.parametrize(
    ("participants", "winners"),
    [
        (("A",), ("A",)),
        (("A", "A"), ("A",)),
        (("A", "B"), ()),
        (("A", "B"), ("X",)),
        (("A", "B"), ("A", "A")),
    ],
)
def test_malformed_hand_participant_or_winner_set_rejected(
    participants: tuple[str, ...], winners: tuple[str, ...]
) -> None:
    with pytest.raises(InvalidStandUpOutcomeError):
        outcome(10, participants, winners)
    with pytest.raises(InvalidStandUpOutcomeError):
        outcome(10, ("A", "B"), ("A",), button=6)


def test_transfer_and_resolution_constructors_reject_forged_payouts() -> None:
    for chips in (0, -1, True):
        with pytest.raises(InvalidStandUpStateError):
            StandUpTransfer(player("A"), player("B"), chips)
    with pytest.raises(InvalidStandUpStateError):
        StandUpTransfer(player("A"), player("A"), 1)

    active = four_player_before_resolution()
    resolved = active.apply_hand(
        outcome(12, ("A", "B", "C", "D"), ("C",), button=4),
        squid_available_stack=8,
    )
    assert isinstance(resolved, StandUpResolution)
    with pytest.raises(InvalidStandUpStateError):
        replace(resolved, squid=player("X"))
    with pytest.raises(InvalidStandUpStateError):
        replace(resolved, transfers=resolved.transfers + resolved.transfers[:1])
    with pytest.raises(InvalidStandUpStateError):
        replace(resolved, transfers=tuple(reversed(resolved.transfers)))
    with pytest.raises(InvalidStandUpStateError):
        replace(resolved, squid_available_stack=1)


def test_cancellation_rejects_arbitrary_reason() -> None:
    active = three_player_round()
    with pytest.raises(InvalidStandUpStateError):
        active.cancel("anything")  # type: ignore[arg-type]


def test_deterministic_replay_and_caller_owned_outcome_collections_are_frozen() -> None:
    participants = [player("A"), player("B"), player("C")]
    winners = [player("A")]
    hand = StandUpHandOutcome(
        hand_number=10,
        participants=participants,  # type: ignore[arg-type]
        main_pot_winners=winners,  # type: ignore[arg-type]
        button_seat=SeatIndex(0),
    )
    participants.clear()
    winners.clear()
    assert hand.participants == (player("A"), player("B"), player("C"))
    assert hand.main_pot_winners == (player("A"),)

    def run() -> StandUpResolution:
        first = three_player_round().apply_hand(hand)
        assert isinstance(first, StandUpRound)
        second = first.apply_hand(
            outcome(11, ("A", "B", "C"), ("B",), button=2),
            squid_available_stack=6,
        )
        assert isinstance(second, StandUpResolution)
        return second

    assert run() == run()
