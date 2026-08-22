from dataclasses import FrozenInstanceError, replace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from streetpoker.domain import (
    AwardReconciliationError,
    BlindKind,
    BlindPost,
    Card,
    ChipStack,
    DuplicateSettlementParticipantError,
    HandNotTerminalError,
    HandSettlementResult,
    HoldemHandParticipant,
    HoldemHandPhase,
    HoldemHandSnapshot,
    HoldemParticipantStatus,
    InvalidEligiblePlayerError,
    InvalidOddChipOrderingError,
    InvalidSettlementInputError,
    InvalidSettlementResultError,
    InvalidShowdownBoardError,
    MissingPotResultError,
    PlayerHandEvaluation,
    PlayerId,
    PotAward,
    PotContribution,
    PotEligibilityStatus,
    Rank,
    SeatIndex,
    SettlementSource,
    Suit,
    WinnerShare,
    construct_pots,
    settle_holdem_hand,
)

RANK_BY_SYMBOL = {
    "2": Rank.TWO,
    "3": Rank.THREE,
    "4": Rank.FOUR,
    "5": Rank.FIVE,
    "6": Rank.SIX,
    "7": Rank.SEVEN,
    "8": Rank.EIGHT,
    "9": Rank.NINE,
    "T": Rank.TEN,
    "J": Rank.JACK,
    "Q": Rank.QUEEN,
    "K": Rank.KING,
    "A": Rank.ACE,
}
SUIT_BY_SYMBOL = {
    "c": Suit.CLUBS,
    "d": Suit.DIAMONDS,
    "h": Suit.HEARTS,
    "s": Suit.SPADES,
}


def cards(notation: str) -> tuple[Card, ...]:
    return tuple(
        Card(rank=RANK_BY_SYMBOL[token[0]], suit=SUIT_BY_SYMBOL[token[1]])
        for token in notation.split()
    )


def pid(name: str) -> PlayerId:
    return PlayerId(name)


Row = tuple[str, int, int, int, HoldemParticipantStatus, str]


def terminal_snapshot(
    rows: tuple[Row, ...],
    *,
    button: int,
    board: str,
    phase: HoldemHandPhase = HoldemHandPhase.SHOWDOWN_READY,
    uncontested: str | None = None,
) -> HoldemHandSnapshot:
    pot_result = construct_pots(
        PotContribution(
            player_id=pid(name),
            committed=committed,
            status=(
                PotEligibilityStatus.FOLDED
                if status is HoldemParticipantStatus.FOLDED
                else PotEligibilityStatus.LIVE
            ),
        )
        for name, _, _, committed, status, _ in rows
    )
    refund_by_id = (
        {}
        if pot_result.uncalled_excess is None
        else {pot_result.uncalled_excess.player_id: pot_result.uncalled_excess.chips}
    )
    participants = tuple(
        HoldemHandParticipant(
            player_id=pid(name),
            seat_index=SeatIndex(seat),
            starting_stack=ChipStack(current_stack + committed - refund_by_id.get(pid(name), 0)),
            current_stack=ChipStack(current_stack),
            gross_committed=committed,
            street_committed=committed,
            returned_excess=refund_by_id.get(pid(name), 0),
            status=status,
            hole_cards=cards(hole),  # type: ignore[arg-type]
        )
        for name, seat, current_stack, committed, status, hole in rows
    )
    board_cards = cards(board)
    burn_count = {0: 0, 3: 1, 4: 2, 5: 3}[len(board_cards)]
    first, second = participants[:2]
    return HoldemHandSnapshot(
        phase=phase,
        participants=participants,
        button_position=SeatIndex(button),
        small_blind_position=first.seat_index,
        big_blind_position=second.seat_index,
        small_blind=50,
        big_blind=100,
        blind_posts=(
            BlindPost(first.player_id, first.seat_index, BlindKind.SMALL, 50, 50),
            BlindPost(second.player_id, second.seat_index, BlindKind.BIG, 100, 100),
        ),
        board=board_cards,
        burn_count=burn_count,
        betting_round=None,
        deck_remaining_count=52 - 2 * len(participants) - len(board_cards) - burn_count,
        pot_result=pot_result,
        uncontested_player=None if uncontested is None else pid(uncontested),
    )


def award_totals(result: HandSettlementResult) -> dict[str, int]:
    return {
        settlement.player_id.value: settlement.total_award
        for settlement in result.player_settlements
    }


def assert_conserved(result: HandSettlementResult) -> None:
    assert result.total_awarded == result.total_pot_chips
    assert sum(item.stack_before_awards.chips for item in result.player_settlements) + (
        result.total_pot_chips
    ) == sum(item.final_stack.chips for item in result.player_settlements)
    for award in result.pot_awards:
        assert sum(share.chips for share in award.winner_shares) == award.pot.amount
        assert set(award.winners) <= award.pot.eligible_players
    for settlement in result.player_settlements:
        assert settlement.final_stack.chips == (
            settlement.stack_before_awards.chips + settlement.total_award
        )
        if settlement.folded:
            assert settlement.total_award == 0


def test_heads_up_single_pot_has_clear_winner() -> None:
    snapshot = terminal_snapshot(
        (
            ("A", 0, 900, 100, HoldemParticipantStatus.ACTIVE, "Ah Ad"),
            ("B", 3, 900, 100, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
        ),
        button=3,
        board="2c 7d 9h Js Qd",
    )

    result = settle_holdem_hand(snapshot)

    assert result.pot_awards[0].pot.amount == 200
    assert result.pot_awards[0].winners == (pid("A"),)
    assert result.pot_awards[0].base_share == 200
    assert result.pot_awards[0].odd_chip_recipients == ()
    assert award_totals(result) == {"A": 200, "B": 0}
    assert_conserved(result)


def test_heads_up_board_lock_tie_splits_evenly() -> None:
    snapshot = terminal_snapshot(
        (
            ("A", 0, 900, 100, HoldemParticipantStatus.ACTIVE, "2c 3c"),
            ("B", 3, 900, 100, HoldemParticipantStatus.ACTIVE, "2s 3s"),
        ),
        button=3,
        board="As Kd Qh Jc Ts",
    )

    result = settle_holdem_hand(snapshot)

    assert result.evaluations[0].evaluated_hand == result.evaluations[1].evaluated_hand
    assert result.pot_awards[0].base_share == 100
    assert award_totals(result) == {"A": 100, "B": 100}


def test_three_way_single_pot_can_have_two_tied_winners() -> None:
    snapshot = terminal_snapshot(
        (
            ("A", 0, 900, 100, HoldemParticipantStatus.ACTIVE, "Ah Ad"),
            ("B", 3, 900, 100, HoldemParticipantStatus.ACTIVE, "As Ac"),
            ("C", 5, 900, 100, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
        ),
        button=5,
        board="2c 7d 9h Js Qd",
    )

    result = settle_holdem_hand(snapshot)

    assert result.pot_awards[0].winners == (pid("A"), pid("B"))
    assert result.pot_awards[0].base_share == 150
    assert award_totals(result) == {"A": 150, "B": 150, "C": 0}


def test_three_way_pot_with_two_tied_winners_assigns_odd_chip_clockwise() -> None:
    snapshot = terminal_snapshot(
        (
            ("A", 0, 899, 101, HoldemParticipantStatus.ACTIVE, "Ah Ad"),
            ("B", 3, 899, 101, HoldemParticipantStatus.ACTIVE, "As Ac"),
            ("C", 5, 899, 101, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
        ),
        button=5,
        board="2c 7d 9h Js Qd",
    )

    result = settle_holdem_hand(snapshot)
    award = result.pot_awards[0]

    assert award.pot.amount == 303
    assert award.winners == (pid("A"), pid("B"))
    assert award.base_share == 151
    assert award.odd_chip_recipients == (pid("A"),)
    assert award_totals(result) == {"A": 152, "B": 151, "C": 0}


def test_short_all_in_wins_main_but_is_ineligible_for_side_pot() -> None:
    snapshot = terminal_snapshot(
        (
            ("A", 0, 0, 100, HoldemParticipantStatus.ALL_IN, "6c 7c"),
            ("B", 2, 700, 300, HoldemParticipantStatus.ACTIVE, "9d 9h"),
            ("C", 5, 700, 300, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
        ),
        button=5,
        board="2c 3d 4h 5s 9c",
    )

    result = settle_holdem_hand(snapshot)

    assert [award.pot.amount for award in result.pot_awards] == [300, 400]
    assert result.pot_awards[0].winners == (pid("A"),)
    assert pid("A") not in result.pot_awards[1].pot.eligible_players
    assert result.pot_awards[1].winners == (pid("B"),)
    assert award_totals(result) == {"A": 300, "B": 400, "C": 0}


def test_main_pot_tie_and_side_pot_clear_winner_are_independent() -> None:
    snapshot = terminal_snapshot(
        (
            ("A", 0, 0, 101, HoldemParticipantStatus.ALL_IN, "6c 7c"),
            ("B", 3, 799, 201, HoldemParticipantStatus.ACTIVE, "6d 7d"),
            ("C", 5, 799, 201, HoldemParticipantStatus.ACTIVE, "9d 9h"),
        ),
        button=5,
        board="2c 3d 4h 5s 9c",
    )

    result = settle_holdem_hand(snapshot)

    assert result.pot_awards[0].winners == (pid("A"), pid("B"))
    assert result.pot_awards[0].odd_chip_recipients == (pid("A"),)
    assert result.pot_awards[1].winners == (pid("B"),)
    assert award_totals(result) == {"A": 152, "B": 351, "C": 0}


def test_multiple_side_pots_can_have_different_winner_sets() -> None:
    snapshot = terminal_snapshot(
        (
            ("A", 0, 900, 100, HoldemParticipantStatus.ACTIVE, "Ah Ad"),
            ("B", 2, 800, 200, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
            ("C", 3, 700, 300, HoldemParticipantStatus.ACTIVE, "Ks Kc"),
            ("D", 5, 700, 300, HoldemParticipantStatus.ACTIVE, "Th Td"),
        ),
        button=5,
        board="2c 7d 9h Js Qd",
    )

    result = settle_holdem_hand(snapshot)

    assert [award.pot.amount for award in result.pot_awards] == [400, 300, 200]
    assert [award.winners for award in result.pot_awards] == [
        (pid("A"),),
        (pid("B"), pid("C")),
        (pid("C"),),
    ]
    assert award_totals(result) == {"A": 400, "B": 150, "C": 350, "D": 0}


def test_folded_player_with_strongest_cards_is_not_evaluated_or_awarded() -> None:
    snapshot = terminal_snapshot(
        (
            ("A", 0, 900, 100, HoldemParticipantStatus.ACTIVE, "Ah Ad"),
            ("B", 2, 900, 100, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
            ("F", 5, 900, 100, HoldemParticipantStatus.FOLDED, "As Ks"),
        ),
        button=5,
        board="Ts Js Qs 2d 3c",
    )

    result = settle_holdem_hand(snapshot)

    assert tuple(item.player_id for item in result.evaluations) == (pid("A"), pid("B"))
    assert pid("F") not in result.pot_awards[0].pot.eligible_players
    assert result.pot_awards[0].winners == (pid("A"),)
    assert award_totals(result) == {"A": 300, "B": 0, "F": 0}


def test_showdown_evaluates_each_live_player_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = terminal_snapshot(
        (
            ("A", 0, 900, 100, HoldemParticipantStatus.ACTIVE, "Ah Ad"),
            ("B", 2, 900, 100, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
            ("F", 5, 900, 100, HoldemParticipantStatus.FOLDED, "As Ks"),
        ),
        button=5,
        board="Ts Js Qs 2d 3c",
    )
    from streetpoker.domain import showdown

    original = showdown.evaluate_holdem_hand
    evaluated_holes: list[tuple[Card, ...]] = []

    def recording_evaluator(hole_cards: tuple[Card, ...], board: tuple[Card, ...]) -> object:
        evaluated_holes.append(hole_cards)
        return original(hole_cards, board)

    monkeypatch.setattr(showdown, "evaluate_holdem_hand", recording_evaluator)

    settle_holdem_hand(snapshot)

    assert evaluated_holes == [
        snapshot.participants[0].hole_cards,
        snapshot.participants[1].hole_cards,
    ]


def test_complete_by_fold_awards_every_pot_without_calling_evaluator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = terminal_snapshot(
        (
            ("A", 0, 900, 100, HoldemParticipantStatus.ACTIVE, "Ah Ad"),
            ("B", 2, 900, 100, HoldemParticipantStatus.FOLDED, "Kh Kd"),
            ("C", 5, 950, 50, HoldemParticipantStatus.FOLDED, "Qh Qd"),
        ),
        button=5,
        board="",
        phase=HoldemHandPhase.COMPLETE_BY_FOLD,
        uncontested="A",
    )

    def fail_evaluation(*args: object, **kwargs: object) -> object:
        raise AssertionError("Fold completion must not evaluate cards.")

    monkeypatch.setattr("streetpoker.domain.showdown.evaluate_holdem_hand", fail_evaluation)
    result = settle_holdem_hand(snapshot)

    assert result.source is SettlementSource.UNCONTESTED
    assert result.evaluations == ()
    assert all(award.winners == (pid("A"),) for award in result.pot_awards)
    assert award_totals(result) == {"A": 250, "B": 0, "C": 0}
    assert_conserved(result)


@pytest.mark.parametrize(
    ("rows", "button", "expected_order"),
    [
        (
            (
                ("A", 0, 899, 101, HoldemParticipantStatus.ACTIVE, "Ah Ad"),
                ("C", 2, 899, 101, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
                ("B", 3, 899, 101, HoldemParticipantStatus.ACTIVE, "As Ac"),
                ("D", 5, 899, 101, HoldemParticipantStatus.FOLDED, "Th Td"),
            ),
            5,
            ("A", "B"),
        ),
        (
            (
                ("A", 0, 899, 101, HoldemParticipantStatus.ACTIVE, "Ah Ad"),
                ("C", 2, 899, 101, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
                ("B", 5, 899, 101, HoldemParticipantStatus.ACTIVE, "As Ac"),
            ),
            2,
            ("B", "A"),
        ),
        (
            (
                ("A", 0, 899, 101, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
                ("C", 2, 899, 101, HoldemParticipantStatus.ACTIVE, "Ah Ad"),
                ("B", 5, 899, 101, HoldemParticipantStatus.ACTIVE, "As Ac"),
            ),
            2,
            ("B", "C"),
        ),
    ],
)
def test_sparse_actual_hand_ring_orders_tied_winners_after_button(
    rows: tuple[Row, ...], button: int, expected_order: tuple[str, ...]
) -> None:
    snapshot = terminal_snapshot(rows, button=button, board="2c 7d 9h Js Qd")

    result = settle_holdem_hand(snapshot)

    assert tuple(player.value for player in result.pot_awards[0].winners) == expected_order
    odd_count = result.pot_awards[0].pot.amount % len(expected_order)
    assert result.pot_awards[0].odd_chip_recipients == tuple(
        pid(name) for name in expected_order[:odd_count]
    )


def test_same_player_can_win_every_eligible_pot() -> None:
    snapshot = terminal_snapshot(
        (
            ("A", 0, 800, 200, HoldemParticipantStatus.ACTIVE, "6c 7c"),
            ("B", 2, 800, 200, HoldemParticipantStatus.ACTIVE, "9d 9h"),
            ("C", 5, 900, 100, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
        ),
        button=5,
        board="2c 3d 4h 5s 9c",
    )

    result = settle_holdem_hand(snapshot)

    assert [award.winners for award in result.pot_awards] == [(pid("A"),), (pid("A"),)]
    assert award_totals(result) == {"A": 500, "B": 0, "C": 0}


def test_three_separate_pots_can_have_three_different_winners() -> None:
    snapshot = terminal_snapshot(
        (
            ("A", 0, 900, 100, HoldemParticipantStatus.ACTIVE, "Ah Ad"),
            ("B", 2, 800, 200, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
            ("C", 3, 700, 300, HoldemParticipantStatus.ACTIVE, "Qh Qc"),
            ("D", 5, 700, 300, HoldemParticipantStatus.FOLDED, "Th Td"),
        ),
        button=5,
        board="2c 7d 9h Js 3d",
    )

    result = settle_holdem_hand(snapshot)

    assert [award.pot.amount for award in result.pot_awards] == [400, 300, 200]
    assert [award.winners for award in result.pot_awards] == [
        (pid("A"),),
        (pid("B"),),
        (pid("C"),),
    ]
    assert award_totals(result) == {"A": 400, "B": 300, "C": 200, "D": 0}


def test_uncalled_excess_is_in_prior_stack_and_never_awarded_again() -> None:
    snapshot = terminal_snapshot(
        (
            ("A", 0, 850, 200, HoldemParticipantStatus.ACTIVE, "Ah Ad"),
            ("B", 3, 900, 100, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
        ),
        button=3,
        board="2c 7d 9h Js Qd",
    )
    assert snapshot.pot_result is not None
    assert snapshot.pot_result.uncalled_excess is not None
    assert snapshot.participants[0].returned_excess == 100

    result = settle_holdem_hand(snapshot)

    assert result.total_pot_chips == 200
    assert result.player_settlements[0].stack_before_awards == ChipStack(850)
    assert award_totals(result) == {"A": 200, "B": 0}
    assert result.player_settlements[0].final_stack == ChipStack(1_050)


def test_settlement_is_deterministic_and_does_not_mutate_snapshot() -> None:
    snapshot = terminal_snapshot(
        (
            ("A", 0, 899, 101, HoldemParticipantStatus.ACTIVE, "Ah Ad"),
            ("B", 3, 899, 101, HoldemParticipantStatus.ACTIVE, "As Ac"),
            ("C", 5, 899, 101, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
        ),
        button=5,
        board="2c 7d 9h Js Qd",
    )
    before = snapshot

    first = settle_holdem_hand(snapshot)
    second = settle_holdem_hand(snapshot)

    assert first == second
    assert snapshot == before
    with pytest.raises(FrozenInstanceError):
        first.uncontested_winner = pid("A")  # type: ignore[misc]


def test_snapshot_boundary_rejects_nonterminal_missing_pot_and_bad_board() -> None:
    valid = terminal_snapshot(
        (
            ("A", 0, 900, 100, HoldemParticipantStatus.ACTIVE, "Ah Ad"),
            ("B", 3, 900, 100, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
        ),
        button=3,
        board="2c 7d 9h Js Qd",
    )
    with pytest.raises(HandNotTerminalError):
        settle_holdem_hand(replace(valid, phase=HoldemHandPhase.RIVER))
    with pytest.raises(MissingPotResultError):
        settle_holdem_hand(replace(valid, pot_result=None))
    with pytest.raises(InvalidShowdownBoardError):
        settle_holdem_hand(replace(valid, board=cards("2c 7d 9h Js"), burn_count=2))


def test_snapshot_boundary_reconciles_participants_contributions_refunds_and_cards() -> None:
    valid = terminal_snapshot(
        (
            ("A", 0, 850, 200, HoldemParticipantStatus.ACTIVE, "Ah Ad"),
            ("B", 3, 900, 100, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
        ),
        button=3,
        board="2c 7d 9h Js Qd",
    )
    first = valid.participants[0]
    with pytest.raises(AwardReconciliationError):
        settle_holdem_hand(
            replace(
                valid,
                participants=(
                    replace(first, current_stack=ChipStack(849)),
                    *valid.participants[1:],
                ),
            )
        )
    with pytest.raises(InvalidSettlementInputError):
        settle_holdem_hand(
            replace(
                valid,
                participants=(
                    replace(first, gross_committed=199),
                    *valid.participants[1:],
                ),
            )
        )
    with pytest.raises(InvalidSettlementInputError):
        settle_holdem_hand(
            replace(
                valid,
                participants=(
                    replace(first, starting_stack=ChipStack(951), returned_excess=99),
                    *valid.participants[1:],
                ),
            )
        )
    with pytest.raises(InvalidSettlementInputError):
        settle_holdem_hand(replace(valid, board=(*valid.board[:-1], first.hole_cards[0])))


def test_snapshot_boundary_rejects_duplicate_identity_seat_and_fold_eligibility() -> None:
    valid = terminal_snapshot(
        (
            ("A", 0, 900, 100, HoldemParticipantStatus.ACTIVE, "Ah Ad"),
            ("B", 3, 900, 100, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
        ),
        button=3,
        board="2c 7d 9h Js Qd",
    )
    first, second = valid.participants
    with pytest.raises(DuplicateSettlementParticipantError):
        settle_holdem_hand(
            replace(valid, participants=(first, replace(second, player_id=pid("A"))))
        )
    with pytest.raises(DuplicateSettlementParticipantError):
        settle_holdem_hand(
            replace(valid, participants=(first, replace(second, seat_index=SeatIndex(0))))
        )
    with pytest.raises(InvalidSettlementInputError):
        settle_holdem_hand(
            replace(
                valid,
                participants=(
                    first,
                    replace(second, status=HoldemParticipantStatus.FOLDED),
                ),
            )
        )


def test_exported_result_types_reject_invalid_direct_construction() -> None:
    valid = settle_holdem_hand(
        terminal_snapshot(
            (
                ("A", 0, 899, 101, HoldemParticipantStatus.ACTIVE, "Ah Ad"),
                ("B", 3, 899, 101, HoldemParticipantStatus.ACTIVE, "As Ac"),
                ("C", 5, 899, 101, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
            ),
            button=5,
            board="2c 7d 9h Js Qd",
        )
    )
    award = valid.pot_awards[0]
    first_share = award.winner_shares[0]
    second_share = award.winner_shares[1]

    with pytest.raises(InvalidSettlementResultError):
        replace(award, winner_shares=())
    with pytest.raises(InvalidSettlementResultError):
        replace(award, winner_shares=(first_share, first_share))
    outsider = WinnerShare(pid("outsider"), SeatIndex(5), 151, False)
    with pytest.raises(InvalidEligiblePlayerError):
        replace(award, winner_shares=(first_share, outsider))
    with pytest.raises(AwardReconciliationError):
        replace(
            award,
            winner_shares=(replace(first_share, chips=151), second_share),
        )
    with pytest.raises(InvalidOddChipOrderingError):
        replace(
            award,
            winner_shares=(
                replace(first_share, chips=151, receives_odd_chip=False),
                replace(second_share, chips=152, receives_odd_chip=True),
            ),
        )
    with pytest.raises(InvalidOddChipOrderingError):
        replace(award, winner_shares=tuple(reversed(award.winner_shares)))


def test_top_level_result_rejects_cross_record_reconciliation_errors() -> None:
    valid = settle_holdem_hand(
        terminal_snapshot(
            (
                ("A", 0, 800, 200, HoldemParticipantStatus.ACTIVE, "6c 7c"),
                ("B", 2, 800, 200, HoldemParticipantStatus.ACTIVE, "9d 9h"),
                ("C", 5, 900, 100, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
            ),
            button=5,
            board="2c 3d 4h 5s 9c",
        )
    )
    first = valid.player_settlements[0]
    with pytest.raises(AwardReconciliationError):
        replace(first, final_stack=ChipStack(first.final_stack.chips - 1))
    with pytest.raises(InvalidSettlementResultError):
        replace(first, folded=True)
    with pytest.raises(InvalidSettlementResultError):
        replace(valid, evaluations=valid.evaluations[:-1])
    folded_evaluation = PlayerHandEvaluation(
        valid.player_settlements[-1].player_id,
        valid.evaluations[0].evaluated_hand,
    )
    folded_settlement = replace(valid.player_settlements[-1], folded=True)
    with pytest.raises(InvalidSettlementResultError):
        HandSettlementResult(
            source=valid.source,
            evaluations=(*valid.evaluations, folded_evaluation),
            pot_awards=valid.pot_awards,
            player_settlements=(*valid.player_settlements[:-1], folded_settlement),
            uncontested_winner=None,
        )
    with pytest.raises(DuplicateSettlementParticipantError):
        replace(
            valid,
            player_settlements=(valid.player_settlements[0], valid.player_settlements[0]),
        )
    duplicate_index = replace(valid.pot_awards[1], pot_index=0)
    with pytest.raises(InvalidSettlementResultError):
        replace(valid, pot_awards=(valid.pot_awards[0], duplicate_index))
    wrong_winner = PotAward(
        pot_index=0,
        pot=valid.pot_awards[0].pot,
        button_position=valid.pot_awards[0].button_position,
        participant_seats=valid.pot_awards[0].participant_seats,
        base_share=valid.pot_awards[0].pot.amount,
        winner_shares=(
            WinnerShare(
                player_id=valid.evaluations[1].player_id,
                seat_index=valid.player_settlements[1].seat_index,
                chips=valid.pot_awards[0].pot.amount,
                receives_odd_chip=False,
            ),
        ),
    )
    with pytest.raises(InvalidSettlementResultError):
        replace(valid, pot_awards=(wrong_winner, *valid.pot_awards[1:]))
    with pytest.raises(AwardReconciliationError):
        replace(
            valid,
            player_settlements=(
                replace(
                    first,
                    total_award=first.total_award - 1,
                    final_stack=ChipStack(first.final_stack.chips - 1),
                ),
                *valid.player_settlements[1:],
            ),
        )


def test_direct_result_rejects_live_contributor_omitted_from_eligibility() -> None:
    valid = settle_holdem_hand(
        terminal_snapshot(
            (
                ("A", 0, 900, 100, HoldemParticipantStatus.ACTIVE, "Ah Ad"),
                ("B", 3, 900, 100, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
            ),
            button=3,
            board="2c 7d 9h Js Qd",
        )
    )
    original_award = valid.pot_awards[0]
    invalid_pot = replace(original_award.pot, eligible_players=frozenset({pid("A")}))
    invalid_award = replace(original_award, pot=invalid_pot)

    with pytest.raises(InvalidEligiblePlayerError):
        replace(valid, pot_awards=(invalid_award,))


def test_direct_result_allows_folded_contributor_excluded_from_eligibility() -> None:
    valid = settle_holdem_hand(
        terminal_snapshot(
            (
                ("A", 0, 900, 100, HoldemParticipantStatus.ACTIVE, "Ah Ad"),
                ("B", 3, 900, 100, HoldemParticipantStatus.FOLDED, "Kh Kd"),
            ),
            button=3,
            board="",
            phase=HoldemHandPhase.COMPLETE_BY_FOLD,
            uncontested="A",
        )
    )

    reconstructed = HandSettlementResult(
        source=valid.source,
        evaluations=valid.evaluations,
        pot_awards=valid.pot_awards,
        player_settlements=valid.player_settlements,
        uncontested_winner=valid.uncontested_winner,
    )

    assert reconstructed == valid


def test_direct_result_allows_all_live_contributors_as_exact_eligibility() -> None:
    valid = settle_holdem_hand(
        terminal_snapshot(
            (
                ("A", 0, 900, 100, HoldemParticipantStatus.ACTIVE, "Ah Ad"),
                ("B", 3, 900, 100, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
            ),
            button=3,
            board="2c 7d 9h Js Qd",
        )
    )

    reconstructed = HandSettlementResult(
        source=valid.source,
        evaluations=valid.evaluations,
        pot_awards=valid.pot_awards,
        player_settlements=valid.player_settlements,
        uncontested_winner=valid.uncontested_winner,
    )

    assert reconstructed == valid


def test_direct_result_rejects_contributor_without_player_settlement() -> None:
    valid = settle_holdem_hand(
        terminal_snapshot(
            (
                ("A", 0, 900, 100, HoldemParticipantStatus.ACTIVE, "Ah Ad"),
                ("B", 3, 900, 100, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
                ("C", 5, 900, 100, HoldemParticipantStatus.ACTIVE, "Qh Qc"),
            ),
            button=3,
            board="2c 7d 9h Js 4d",
        )
    )
    reduced_award = replace(
        valid.pot_awards[0],
        participant_seats=(SeatIndex(0), SeatIndex(3)),
    )

    with pytest.raises(InvalidSettlementResultError):
        HandSettlementResult(
            source=valid.source,
            evaluations=valid.evaluations[:2],
            pot_awards=(reduced_award,),
            player_settlements=valid.player_settlements[:2],
            uncontested_winner=None,
        )


def test_noncontributor_cannot_be_added_to_pot_eligibility() -> None:
    valid = settle_holdem_hand(
        terminal_snapshot(
            (
                ("A", 0, 900, 100, HoldemParticipantStatus.ACTIVE, "Ah Ad"),
                ("B", 3, 900, 100, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
                ("C", 5, 1_000, 0, HoldemParticipantStatus.ACTIVE, "Qh Qc"),
            ),
            button=3,
            board="2c 7d 9h Js 4d",
        )
    )

    invalid_pot = replace(valid.pot_awards[0].pot)
    object.__setattr__(
        invalid_pot,
        "eligible_players",
        frozenset({pid("A"), pid("B"), pid("C")}),
    )
    invalid_award = replace(valid.pot_awards[0], pot=invalid_pot)

    with pytest.raises(InvalidEligiblePlayerError):
        replace(valid, pot_awards=(invalid_award,))


@given(commitment=st.integers(min_value=1, max_value=500))
def test_generated_tied_pots_reconcile_eligibility_shares_and_global_chips(
    commitment: int,
) -> None:
    snapshot = terminal_snapshot(
        (
            ("A", 0, 1_000, commitment, HoldemParticipantStatus.ACTIVE, "Ah Ad"),
            ("B", 3, 1_000, commitment, HoldemParticipantStatus.ACTIVE, "As Ac"),
            ("C", 5, 1_000, commitment, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
        ),
        button=5,
        board="2c 7d 9h Js Qd",
    )

    result = settle_holdem_hand(snapshot)

    assert_conserved(result)
    shares = tuple(share.chips for share in result.pot_awards[0].winner_shares)
    assert max(shares) - min(shares) <= 1
    odd_count = result.pot_awards[0].pot.amount % len(shares)
    assert result.pot_awards[0].odd_chip_recipients == result.pot_awards[0].winners[:odd_count]


@given(commitment=st.integers(min_value=1, max_value=500))
def test_lexical_player_ids_do_not_change_seat_based_odd_chip_outcome(
    commitment: int,
) -> None:
    first = terminal_snapshot(
        (
            ("Zulu", 0, 1_000, commitment, HoldemParticipantStatus.ACTIVE, "Ah Ad"),
            ("Alpha", 3, 1_000, commitment, HoldemParticipantStatus.ACTIVE, "As Ac"),
            ("Middle", 5, 1_000, commitment, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
        ),
        button=5,
        board="2c 7d 9h Js Qd",
    )
    second = terminal_snapshot(
        (
            ("Alpha", 0, 1_000, commitment, HoldemParticipantStatus.ACTIVE, "Ah Ad"),
            ("Zulu", 3, 1_000, commitment, HoldemParticipantStatus.ACTIVE, "As Ac"),
            ("Middle", 5, 1_000, commitment, HoldemParticipantStatus.ACTIVE, "Kh Kd"),
        ),
        button=5,
        board="2c 7d 9h Js Qd",
    )

    first_award = settle_holdem_hand(first).pot_awards[0]
    second_award = settle_holdem_hand(second).pot_awards[0]

    assert tuple(share.seat_index for share in first_award.winner_shares) == (
        SeatIndex(0),
        SeatIndex(3),
    )
    assert tuple(share.seat_index for share in second_award.winner_shares) == (
        SeatIndex(0),
        SeatIndex(3),
    )
    assert tuple(share.receives_odd_chip for share in first_award.winner_shares) == tuple(
        share.receives_odd_chip for share in second_award.winner_shares
    )
