"""Pure deterministic best-five evaluation for standard Texas Hold'em."""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum
from functools import total_ordering
from itertools import combinations, pairwise
from typing import Any

from streetpoker.domain.cards import Card, Rank, Suit
from streetpoker.domain.errors import (
    DuplicateEvaluationCardError,
    InvalidEvaluatedHandError,
    InvalidHandRankError,
    InvalidHoldemCardCountError,
    InvalidHoldemEvaluationInputError,
)


class HandCategory(IntEnum):
    """Explicit standard high-hand category strengths from weakest to strongest."""

    HIGH_CARD = 0
    ONE_PAIR = 1
    TWO_PAIR = 2
    THREE_OF_A_KIND = 3
    STRAIGHT = 4
    FLUSH = 5
    FULL_HOUSE = 6
    FOUR_OF_A_KIND = 7
    STRAIGHT_FLUSH = 8


_RANK_VALUE = {
    Rank.TWO: 2,
    Rank.THREE: 3,
    Rank.FOUR: 4,
    Rank.FIVE: 5,
    Rank.SIX: 6,
    Rank.SEVEN: 7,
    Rank.EIGHT: 8,
    Rank.NINE: 9,
    Rank.TEN: 10,
    Rank.JACK: 11,
    Rank.QUEEN: 12,
    Rank.KING: 13,
    Rank.ACE: 14,
}

_SUIT_CANONICAL_VALUE = {
    Suit.CLUBS: 0,
    Suit.DIAMONDS: 1,
    Suit.HEARTS: 2,
    Suit.SPADES: 3,
}


@total_ordering
@dataclass(frozen=True, slots=True)
class HandRank:
    """A total, suit-independent ordering key for one standard five-card hand."""

    category: HandCategory
    tiebreak_values: tuple[int, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.category, HandCategory):
            raise InvalidHandRankError("A hand rank requires a HandCategory.")
        if not isinstance(self.tiebreak_values, tuple):
            raise InvalidHandRankError("Hand-rank tiebreak values must be a tuple.")
        if any(not _is_strict_rank(value) for value in self.tiebreak_values):
            raise InvalidHandRankError("Hand-rank values must be integers from 2 through 14.")

        values = self.tiebreak_values
        expected_lengths = {
            HandCategory.HIGH_CARD: 5,
            HandCategory.ONE_PAIR: 4,
            HandCategory.TWO_PAIR: 3,
            HandCategory.THREE_OF_A_KIND: 3,
            HandCategory.STRAIGHT: 1,
            HandCategory.FLUSH: 5,
            HandCategory.FULL_HOUSE: 2,
            HandCategory.FOUR_OF_A_KIND: 2,
            HandCategory.STRAIGHT_FLUSH: 1,
        }
        if len(values) != expected_lengths[self.category]:
            raise InvalidHandRankError("Tiebreak shape does not match the hand category.")

        if self.category in {HandCategory.STRAIGHT, HandCategory.STRAIGHT_FLUSH}:
            if not 5 <= values[0] <= 14:
                raise InvalidHandRankError("A straight high card must be from five through ace.")
            return

        if self.category in {HandCategory.HIGH_CARD, HandCategory.FLUSH}:
            _require_strictly_descending(values, "High-card and flush ranks")
            return

        if self.category is HandCategory.FOUR_OF_A_KIND:
            if values[0] == values[1]:
                raise InvalidHandRankError("A four-of-a-kind kicker must have another rank.")
            return

        if self.category is HandCategory.FULL_HOUSE:
            if values[0] == values[1]:
                raise InvalidHandRankError("Full-house trip and pair ranks must differ.")
            return

        if self.category is HandCategory.THREE_OF_A_KIND:
            if values[0] in values[1:]:
                raise InvalidHandRankError("Trip and kicker ranks must differ.")
            _require_strictly_descending(values[1:], "Three-of-a-kind kickers")
            return

        if self.category is HandCategory.TWO_PAIR:
            if values[0] <= values[1] or values[2] in values[:2]:
                raise InvalidHandRankError(
                    "Two pair requires descending pair ranks and a distinct kicker."
                )
            return

        if values[0] in values[1:]:
            raise InvalidHandRankError("Pair and kicker ranks must differ.")
        _require_strictly_descending(values[1:], "Pair kickers")

    @property
    def comparison_key(self) -> tuple[int, tuple[int, ...]]:
        """Return the explicit total-ordering key."""
        return (int(self.category), self.tiebreak_values)

    def __lt__(self, other: Any) -> bool:
        if not isinstance(other, HandRank):
            return NotImplemented
        return self.comparison_key < other.comparison_key


@dataclass(frozen=True, slots=True)
class EvaluatedHand:
    """One canonical rank and the deterministic exact five cards that make it."""

    hand_rank: HandRank
    best_five: tuple[Card, Card, Card, Card, Card]

    def __post_init__(self) -> None:
        if not isinstance(self.hand_rank, HandRank):
            raise InvalidEvaluatedHandError("An evaluated hand requires a HandRank.")
        if not isinstance(self.best_five, tuple) or len(self.best_five) != 5:
            raise InvalidEvaluatedHandError("An evaluated hand requires exactly five cards.")
        if any(not isinstance(card, Card) for card in self.best_five):
            raise InvalidEvaluatedHandError("Best-five values must all be Cards.")
        if len(set(self.best_five)) != 5:
            raise InvalidEvaluatedHandError("Best-five cards must be unique.")
        actual_rank = _evaluate_five_card_hand(self.best_five)
        if actual_rank != self.hand_rank:
            raise InvalidEvaluatedHandError("Best-five cards do not produce the declared rank.")
        if _canonicalize_five(self.best_five, actual_rank) != self.best_five:
            raise InvalidEvaluatedHandError("Best-five cards are not canonically ordered.")


def evaluate_holdem_hand(
    hole_cards: Iterable[Card],
    board: Iterable[Card],
) -> EvaluatedHand:
    """Evaluate exactly two hole cards and five board cards as standard Hold'em."""
    copied_hole = _copy_cards(hole_cards, label="Hole cards")
    copied_board = _copy_cards(board, label="The board")
    if len(copied_hole) != 2 or len(copied_board) != 5:
        raise InvalidHoldemCardCountError(
            "Hold'em evaluation requires exactly two hole cards and five board cards."
        )
    all_cards = copied_hole + copied_board
    if any(not isinstance(card, Card) for card in all_cards):
        raise InvalidHoldemEvaluationInputError("Hold'em evaluation accepts only Card values.")
    if len(set(all_cards)) != 7:
        raise DuplicateEvaluationCardError("Hold'em evaluation cards must be unique.")

    best_rank: HandRank | None = None
    best_five: tuple[Card, Card, Card, Card, Card] | None = None
    best_signature: tuple[tuple[int, int], ...] | None = None
    for candidate in combinations(all_cards, 5):
        rank = _evaluate_five_card_hand(candidate)
        canonical = _canonicalize_five(candidate, rank)
        signature = _exact_card_signature(canonical)
        if (
            best_rank is None
            or rank > best_rank
            or (rank == best_rank and best_signature is not None and signature < best_signature)
        ):
            best_rank = rank
            best_five = canonical
            best_signature = signature

    if best_rank is None or best_five is None:  # pragma: no cover - 21 combinations are guaranteed
        raise InvalidHoldemEvaluationInputError("Hold'em evaluation produced no candidate hand.")
    return EvaluatedHand(hand_rank=best_rank, best_five=best_five)


def _evaluate_five_card_hand(cards: Iterable[Card]) -> HandRank:
    """Rank one internally supplied five-card candidate."""
    copied = tuple(cards)
    if len(copied) != 5 or any(not isinstance(card, Card) for card in copied):
        raise InvalidEvaluatedHandError("Five-card evaluation requires five Card values.")
    if len(set(copied)) != 5:
        raise InvalidEvaluatedHandError("Five-card evaluation requires unique cards.")

    values = tuple(_RANK_VALUE[card.rank] for card in copied)
    counts = Counter(values)
    groups = sorted(((count, rank) for rank, count in counts.items()), reverse=True)
    flush = len({card.suit for card in copied}) == 1
    straight_high = _straight_high(frozenset(values))

    if flush and straight_high is not None:
        return HandRank(HandCategory.STRAIGHT_FLUSH, (straight_high,))
    if groups[0][0] == 4:
        return HandRank(HandCategory.FOUR_OF_A_KIND, (groups[0][1], groups[1][1]))
    if tuple(count for count, _ in groups) == (3, 2):
        return HandRank(HandCategory.FULL_HOUSE, (groups[0][1], groups[1][1]))
    if flush:
        return HandRank(HandCategory.FLUSH, tuple(sorted(values, reverse=True)))
    if straight_high is not None:
        return HandRank(HandCategory.STRAIGHT, (straight_high,))
    if groups[0][0] == 3:
        kickers = tuple(
            sorted((rank for rank, count in counts.items() if count == 1), reverse=True)
        )
        return HandRank(HandCategory.THREE_OF_A_KIND, (groups[0][1], *kickers))
    pairs = tuple(sorted((rank for rank, count in counts.items() if count == 2), reverse=True))
    if len(pairs) == 2:
        kicker = next(rank for rank, count in counts.items() if count == 1)
        return HandRank(HandCategory.TWO_PAIR, (*pairs, kicker))
    if len(pairs) == 1:
        kickers = tuple(
            sorted((rank for rank, count in counts.items() if count == 1), reverse=True)
        )
        return HandRank(HandCategory.ONE_PAIR, (pairs[0], *kickers))
    return HandRank(HandCategory.HIGH_CARD, tuple(sorted(values, reverse=True)))


def _canonicalize_five(
    cards: Iterable[Card],
    hand_rank: HandRank,
) -> tuple[Card, Card, Card, Card, Card]:
    copied = tuple(cards)
    by_rank: dict[int, list[Card]] = {}
    for card in copied:
        by_rank.setdefault(_RANK_VALUE[card.rank], []).append(card)
    for same_rank in by_rank.values():
        same_rank.sort(key=lambda card: _SUIT_CANONICAL_VALUE[card.suit])

    category = hand_rank.category
    values = hand_rank.tiebreak_values
    ordered_ranks: tuple[int, ...]
    if category in {HandCategory.STRAIGHT, HandCategory.STRAIGHT_FLUSH}:
        high = values[0]
        ordered_ranks = (5, 4, 3, 2, 14) if high == 5 else tuple(range(high, high - 5, -1))
    elif category is HandCategory.FOUR_OF_A_KIND:
        ordered_ranks = (values[0],) * 4 + (values[1],)
    elif category is HandCategory.FULL_HOUSE:
        ordered_ranks = (values[0],) * 3 + (values[1],) * 2
    elif category is HandCategory.THREE_OF_A_KIND:
        ordered_ranks = (values[0],) * 3 + values[1:]
    elif category is HandCategory.TWO_PAIR:
        ordered_ranks = (values[0],) * 2 + (values[1],) * 2 + (values[2],)
    elif category is HandCategory.ONE_PAIR:
        ordered_ranks = (values[0],) * 2 + values[1:]
    else:
        ordered_ranks = values

    consumed: Counter[int] = Counter()
    normalized: list[Card] = []
    for rank in ordered_ranks:
        normalized.append(by_rank[rank][consumed[rank]])
        consumed[rank] += 1
    return tuple(normalized)  # type: ignore[return-value]


def _straight_high(values: frozenset[int]) -> int | None:
    if values == frozenset({14, 2, 3, 4, 5}):
        return 5
    if len(values) == 5 and max(values) - min(values) == 4:
        return max(values)
    return None


def _copy_cards(cards: Iterable[Card], *, label: str) -> tuple[Card, ...]:
    try:
        return tuple(cards)
    except TypeError:
        raise InvalidHoldemEvaluationInputError(f"{label} must be an iterable of Cards.") from None


def _exact_card_signature(cards: tuple[Card, ...]) -> tuple[tuple[int, int], ...]:
    return tuple((_RANK_VALUE[card.rank], _SUIT_CANONICAL_VALUE[card.suit]) for card in cards)


def _require_strictly_descending(values: tuple[int, ...], label: str) -> None:
    if any(left <= right for left, right in pairwise(values)):
        raise InvalidHandRankError(f"{label} must be strictly descending.")


def _is_strict_rank(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 2 <= value <= 14
