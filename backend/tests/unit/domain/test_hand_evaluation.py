from dataclasses import FrozenInstanceError
from itertools import combinations, pairwise

import pytest
from hypothesis import given
from hypothesis import strategies as st

from streetpoker.domain import (
    Card,
    DuplicateEvaluationCardError,
    EvaluatedHand,
    HandCategory,
    HandRank,
    InvalidEvaluatedHandError,
    InvalidHandRankError,
    InvalidHoldemCardCountError,
    InvalidHoldemEvaluationInputError,
    Rank,
    Suit,
    evaluate_holdem_hand,
)
from streetpoker.domain.hand_evaluation import _evaluate_five_card_hand

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


def evaluated(hole: str, board: str) -> EvaluatedHand:
    return evaluate_holdem_hand(cards(hole), cards(board))


@pytest.mark.parametrize(
    ("hole", "board", "category", "tiebreak", "best_five"),
    [
        ("As Ks", "Qs Js Ts 2d 3c", HandCategory.STRAIGHT_FLUSH, (14,), "As Ks Qs Js Ts"),
        ("9h 8h", "7h 6h 5h Ac Kd", HandCategory.STRAIGHT_FLUSH, (9,), "9h 8h 7h 6h 5h"),
        ("Ac Ad", "Ah As Kd 7c 2h", HandCategory.FOUR_OF_A_KIND, (14, 13), "Ac Ad Ah As Kd"),
        ("Kh Kd", "Kc 7s 7h 2c 3d", HandCategory.FULL_HOUSE, (13, 7), "Kc Kd Kh 7h 7s"),
        ("Ah 8h", "Kh Jh 4h 2c 9d", HandCategory.FLUSH, (14, 13, 11, 8, 4), "Ah Kh Jh 8h 4h"),
        ("Ac Kd", "Qh Js Tc 4s 2d", HandCategory.STRAIGHT, (14,), "Ac Kd Qh Js Tc"),
        ("Ac 2d", "3h 4s 5c Kd Qh", HandCategory.STRAIGHT, (5,), "5c 4s 3h 2d Ac"),
        ("Qc Qd", "Qh As 9c 4d 2s", HandCategory.THREE_OF_A_KIND, (12, 14, 9), "Qc Qd Qh As 9c"),
        ("Jc Jd", "8h 8s Ac 4d 2c", HandCategory.TWO_PAIR, (11, 8, 14), "Jc Jd 8h 8s Ac"),
        ("Ac Ad", "Ks Qh Jc 7d 2s", HandCategory.ONE_PAIR, (14, 13, 12, 11), "Ac Ad Ks Qh Jc"),
        ("Ac 9d", "Kh Qs 7c 4d 2h", HandCategory.HIGH_CARD, (14, 13, 12, 9, 7), "Ac Kh Qs 9d 7c"),
        ("2c 3d", "As Kd Qh Jc Ts", HandCategory.STRAIGHT, (14,), "As Kd Qh Jc Ts"),
        ("Ah Kh", "Qh 9h 4h 2c 3d", HandCategory.FLUSH, (14, 13, 12, 9, 4), "Ah Kh Qh 9h 4h"),
        ("Ah 2d", "Kh Qh 9h 4h 3c", HandCategory.FLUSH, (14, 13, 12, 9, 4), "Ah Kh Qh 9h 4h"),
        ("9c 9d", "9h Ks Kc 4d 2s", HandCategory.FULL_HOUSE, (9, 13), "9c 9d 9h Kc Ks"),
        ("Ac Qd", "Kh Kc 8s 8d 2h", HandCategory.TWO_PAIR, (13, 8, 14), "Kc Kh 8d 8s Ac"),
        ("Ah 7c", "Kh Qh 9h 4h 2d", HandCategory.FLUSH, (14, 13, 12, 9, 4), "Ah Kh Qh 9h 4h"),
        ("9c 2d", "8h 7s 6c 5d Kh", HandCategory.STRAIGHT, (9,), "9c 8h 7s 6c 5d"),
        ("Qc Qh", "As Ah Ks Kh Jd", HandCategory.TWO_PAIR, (14, 13, 12), "Ah As Kh Ks Qc"),
        ("Ac Ad", "Kh Qh Tc 7d 2s", HandCategory.ONE_PAIR, (14, 13, 12, 10), "Ac Ad Kh Qh Tc"),
    ],
)
def test_approved_holdem_examples(
    hole: str,
    board: str,
    category: HandCategory,
    tiebreak: tuple[int, ...],
    best_five: str,
) -> None:
    result = evaluated(hole, board)

    assert result.hand_rank == HandRank(category, tiebreak)
    assert result.best_five == cards(best_five)


def test_category_ordering_is_explicit_and_complete() -> None:
    ordered = (
        HandRank(HandCategory.HIGH_CARD, (14, 13, 11, 9, 7)),
        HandRank(HandCategory.ONE_PAIR, (2, 14, 13, 12)),
        HandRank(HandCategory.TWO_PAIR, (3, 2, 14)),
        HandRank(HandCategory.THREE_OF_A_KIND, (2, 14, 13)),
        HandRank(HandCategory.STRAIGHT, (5,)),
        HandRank(HandCategory.FLUSH, (7, 6, 5, 3, 2)),
        HandRank(HandCategory.FULL_HOUSE, (2, 14)),
        HandRank(HandCategory.FOUR_OF_A_KIND, (2, 14)),
        HandRank(HandCategory.STRAIGHT_FLUSH, (5,)),
    )

    assert tuple(sorted(reversed(ordered))) == ordered
    assert all(weaker < stronger for weaker, stronger in pairwise(ordered))


@pytest.mark.parametrize(
    ("hole", "board", "expected"),
    [
        ("Ac 2d", "3h 4s 5c 9d Kh", HandRank(HandCategory.STRAIGHT, (5,))),
        ("2c 3d", "4h 5s 6c 9d Kh", HandRank(HandCategory.STRAIGHT, (6,))),
        ("Ac Kd", "Qh Js Tc 4s 2d", HandRank(HandCategory.STRAIGHT, (14,))),
    ],
)
def test_straight_boundaries(hole: str, board: str, expected: HandRank) -> None:
    assert evaluated(hole, board).hand_rank == expected


def test_queen_king_ace_two_three_is_not_a_straight() -> None:
    result = evaluated("Qc Kd", "Ah 2s 3c 8d 7h")

    assert result.hand_rank == HandRank(HandCategory.HIGH_CARD, (14, 13, 12, 8, 7))


def test_duplicate_rank_in_seven_cards_does_not_hide_a_valid_straight() -> None:
    result = evaluated("9c 9d", "8h 7s 6c 5d 4h")

    assert result.hand_rank == HandRank(HandCategory.STRAIGHT, (9,))
    assert result.best_five == cards("9c 8h 7s 6c 5d")


@pytest.mark.parametrize(
    ("stronger", "weaker"),
    [
        ((14, 13, 12, 11), (13, 14, 12, 11)),
        ((14, 13, 12, 11), (14, 12, 11, 10)),
        ((14, 13, 12, 11), (14, 13, 11, 10)),
        ((14, 13, 12, 11), (14, 13, 12, 10)),
    ],
)
def test_pair_rank_and_every_kicker_position(
    stronger: tuple[int, ...], weaker: tuple[int, ...]
) -> None:
    assert HandRank(HandCategory.ONE_PAIR, stronger) > HandRank(HandCategory.ONE_PAIR, weaker)


@pytest.mark.parametrize(
    ("stronger", "weaker"),
    [
        ((14, 12, 13), (13, 12, 14)),
        ((14, 12, 13), (14, 11, 13)),
        ((14, 12, 13), (14, 12, 11)),
    ],
)
def test_two_pair_uses_both_pair_ranks_then_kicker(
    stronger: tuple[int, ...], weaker: tuple[int, ...]
) -> None:
    assert HandRank(HandCategory.TWO_PAIR, stronger) > HandRank(HandCategory.TWO_PAIR, weaker)


def test_trips_use_both_kickers() -> None:
    assert HandRank(HandCategory.THREE_OF_A_KIND, (12, 14, 10)) > HandRank(
        HandCategory.THREE_OF_A_KIND, (12, 13, 11)
    )
    assert HandRank(HandCategory.THREE_OF_A_KIND, (12, 14, 10)) > HandRank(
        HandCategory.THREE_OF_A_KIND, (12, 14, 9)
    )


def test_full_house_compares_trips_before_pair() -> None:
    assert HandRank(HandCategory.FULL_HOUSE, (13, 2)) > HandRank(HandCategory.FULL_HOUSE, (12, 14))
    assert HandRank(HandCategory.FULL_HOUSE, (13, 12)) > HandRank(HandCategory.FULL_HOUSE, (13, 11))


def test_quads_compare_rank_then_kicker() -> None:
    assert HandRank(HandCategory.FOUR_OF_A_KIND, (13, 2)) > HandRank(
        HandCategory.FOUR_OF_A_KIND, (12, 14)
    )
    assert HandRank(HandCategory.FOUR_OF_A_KIND, (13, 14)) > HandRank(
        HandCategory.FOUR_OF_A_KIND, (13, 12)
    )


@pytest.mark.parametrize(
    ("stronger", "weaker"),
    [
        ((14, 12, 10, 8, 6), (13, 12, 10, 8, 6)),
        ((14, 12, 10, 8, 6), (14, 11, 10, 8, 6)),
        ((14, 12, 10, 8, 6), (14, 12, 9, 8, 6)),
        ((14, 12, 10, 8, 6), (14, 12, 10, 7, 6)),
        ((14, 12, 10, 8, 6), (14, 12, 10, 8, 5)),
    ],
)
@pytest.mark.parametrize("category", [HandCategory.FLUSH, HandCategory.HIGH_CARD])
def test_flush_and_high_card_compare_every_position(
    category: HandCategory,
    stronger: tuple[int, ...],
    weaker: tuple[int, ...],
) -> None:
    assert HandRank(category, stronger) > HandRank(category, weaker)


def test_broadway_board_is_locked_for_every_player_and_irrelevant_suits() -> None:
    board = cards("As Kd Qh Jc Ts")
    first = evaluate_holdem_hand(cards("2c 3c"), board)
    second = evaluate_holdem_hand(cards("2s 3s"), board)

    expected_rank = HandRank(HandCategory.STRAIGHT, (14,))
    assert first.hand_rank == second.hand_rank == expected_rank
    assert first.best_five == second.best_five == board


def test_interchangeable_same_rank_cards_choose_stable_exact_five_without_affecting_rank() -> None:
    first = evaluated("Qc Qh", "As Ah Ks Kh Jd")
    second = evaluated("Qd Qs", "As Ah Ks Kh Jd")

    assert first.hand_rank == second.hand_rank == HandRank(HandCategory.TWO_PAIR, (14, 13, 12))
    assert first.best_five == cards("Ah As Kh Ks Qc")
    assert second.best_five == cards("Ah As Kh Ks Qd")
    assert not (first.hand_rank < second.hand_rank or second.hand_rank < first.hand_rank)


def test_misleading_lower_combinations_are_ignored() -> None:
    result = evaluated("Ah Ad", "Ac Ks Kd Kh 2c")

    assert result.hand_rank == HandRank(HandCategory.FULL_HOUSE, (14, 13))
    assert result.best_five == cards("Ac Ad Ah Kd Kh")


@pytest.mark.parametrize(
    ("hole", "board"),
    [
        ("Ac", "2c 3c 4c 5c 6c"),
        ("Ac Ad Ah", "2c 3c 4c 5c 6c"),
        ("Ac Ad", "2c 3c 4c 5c"),
        ("Ac Ad", "2c 3c 4c 5c 6c 7c"),
    ],
)
def test_wrong_public_card_counts_are_rejected(hole: str, board: str) -> None:
    with pytest.raises(InvalidHoldemCardCountError):
        evaluate_holdem_hand(cards(hole), cards(board))


def test_invalid_public_elements_and_noniterables_are_rejected() -> None:
    with pytest.raises(InvalidHoldemEvaluationInputError):
        evaluate_holdem_hand((cards("Ac")[0], object()), cards("2c 3c 4c 5c 6c"))  # type: ignore[arg-type]
    with pytest.raises(InvalidHoldemEvaluationInputError):
        evaluate_holdem_hand(None, cards("2c 3c 4c 5c 6c"))  # type: ignore[arg-type]


def test_duplicate_cards_across_hole_and_board_are_rejected() -> None:
    with pytest.raises(DuplicateEvaluationCardError):
        evaluate_holdem_hand(cards("Ac Kd"), cards("Ac 2c 3c 4c 5c"))


@pytest.mark.parametrize(
    ("category", "values"),
    [
        (HandCategory.STRAIGHT, (4,)),
        (HandCategory.STRAIGHT_FLUSH, (15,)),
        (HandCategory.FOUR_OF_A_KIND, (14, 14)),
        (HandCategory.FULL_HOUSE, (13, 13)),
        (HandCategory.FLUSH, (14, 13, 13, 8, 2)),
        (HandCategory.THREE_OF_A_KIND, (12, 14, 14)),
        (HandCategory.TWO_PAIR, (12, 13, 14)),
        (HandCategory.TWO_PAIR, (13, 12, 12)),
        (HandCategory.ONE_PAIR, (14, 13, 11, 12)),
        (HandCategory.HIGH_CARD, (14, 13, 11, 9)),
    ],
)
def test_malformed_hand_ranks_are_rejected(category: HandCategory, values: tuple[int, ...]) -> None:
    with pytest.raises(InvalidHandRankError):
        HandRank(category, values)


def test_hand_rank_rejects_bool_non_tuple_and_invalid_category() -> None:
    with pytest.raises(InvalidHandRankError):
        HandRank(HandCategory.STRAIGHT, (True,))
    with pytest.raises(InvalidHandRankError):
        HandRank(HandCategory.STRAIGHT, [5])  # type: ignore[arg-type]
    with pytest.raises(InvalidHandRankError):
        HandRank(8, (14,))  # type: ignore[arg-type]


def test_broadway_shaped_high_card_rank_is_rejected() -> None:
    with pytest.raises(InvalidHandRankError):
        HandRank(HandCategory.HIGH_CARD, (14, 13, 12, 11, 10))


def test_nine_high_straight_shaped_high_card_rank_is_rejected() -> None:
    with pytest.raises(InvalidHandRankError):
        HandRank(HandCategory.HIGH_CARD, (9, 8, 7, 6, 5))


def test_wheel_shaped_high_card_rank_is_rejected() -> None:
    with pytest.raises(InvalidHandRankError):
        HandRank(HandCategory.HIGH_CARD, (14, 5, 4, 3, 2))


def test_broadway_shaped_flush_rank_is_rejected() -> None:
    with pytest.raises(InvalidHandRankError):
        HandRank(HandCategory.FLUSH, (14, 13, 12, 11, 10))


def test_wheel_shaped_flush_rank_is_rejected() -> None:
    with pytest.raises(InvalidHandRankError):
        HandRank(HandCategory.FLUSH, (14, 5, 4, 3, 2))


def test_nearby_nonstraight_high_card_rank_remains_valid() -> None:
    rank = HandRank(HandCategory.HIGH_CARD, (14, 13, 12, 11, 9))

    assert rank.tiebreak_values == (14, 13, 12, 11, 9)


def test_nearby_nonstraight_flush_rank_remains_valid() -> None:
    rank = HandRank(HandCategory.FLUSH, (14, 13, 11, 8, 4))

    assert rank.tiebreak_values == (14, 13, 11, 8, 4)


def test_evaluated_hand_direct_construction_validates_cards_rank_and_order() -> None:
    result = evaluated("Ac Ad", "Ks Qh Jc 7d 2s")
    with pytest.raises(InvalidEvaluatedHandError):
        EvaluatedHand(result.hand_rank, cards("Ac Ad Ks Qh"))  # type: ignore[arg-type]
    with pytest.raises(InvalidEvaluatedHandError):
        EvaluatedHand(result.hand_rank, cards("Ac Ac Ks Qh Jc"))  # type: ignore[arg-type]
    with pytest.raises(InvalidEvaluatedHandError):
        EvaluatedHand(HandRank(HandCategory.ONE_PAIR, (13, 14, 12, 11)), result.best_five)
    with pytest.raises(InvalidEvaluatedHandError):
        EvaluatedHand(result.hand_rank, tuple(reversed(result.best_five)))  # type: ignore[arg-type]


def test_evaluation_values_are_frozen() -> None:
    result = evaluated("Ac Ad", "Ks Qh Jc 7d 2s")
    with pytest.raises(FrozenInstanceError):
        result.hand_rank = HandRank(HandCategory.HIGH_CARD, (14, 13, 11, 9, 7))  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.hand_rank.tiebreak_values = (2, 3, 4, 5, 6)  # type: ignore[misc]


STANDARD_CARDS = tuple(Card(rank=rank, suit=suit) for rank in Rank for suit in Suit)
SEVEN_UNIQUE_CARDS = st.lists(
    st.sampled_from(STANDARD_CARDS), min_size=7, max_size=7, unique=True
).map(tuple)


@given(seven=SEVEN_UNIQUE_CARDS, data=st.data())
def test_hole_and_board_permutations_are_invariant(
    seven: tuple[Card, ...], data: st.DataObject
) -> None:
    expected = evaluate_holdem_hand(seven[:2], seven[2:])
    hole = tuple(data.draw(st.permutations(seven[:2])))
    board = tuple(data.draw(st.permutations(seven[2:])))

    assert evaluate_holdem_hand(hole, board) == expected


@given(seven=SEVEN_UNIQUE_CARDS)
def test_best_five_is_unique_subset_and_maximal(seven: tuple[Card, ...]) -> None:
    result = evaluate_holdem_hand(seven[:2], seven[2:])
    candidate_ranks = tuple(
        _evaluate_five_card_hand(candidate) for candidate in combinations(seven, 5)
    )

    assert set(result.best_five) <= set(seven)
    assert len(set(result.best_five)) == 5
    assert result.hand_rank >= max(candidate_ranks)
    assert evaluate_holdem_hand(seven[:2], seven[2:]) == result


@given(first=SEVEN_UNIQUE_CARDS, second=SEVEN_UNIQUE_CARDS, third=SEVEN_UNIQUE_CARDS)
def test_hand_rank_comparison_is_total_and_transitive(
    first: tuple[Card, ...],
    second: tuple[Card, ...],
    third: tuple[Card, ...],
) -> None:
    ranks = tuple(
        evaluate_holdem_hand(seven[:2], seven[2:]).hand_rank for seven in (first, second, third)
    )
    left, middle, right = sorted(ranks)

    assert left <= middle <= right
    assert not (left > middle or middle > right)
    if left <= middle and middle <= right:
        assert left <= right
