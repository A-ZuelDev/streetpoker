import json
from dataclasses import FrozenInstanceError

import pytest
from hypothesis import given
from hypothesis import strategies as st

from streetpoker.domain import Card, CardDataError, Rank, Suit

CARD_STRATEGY = st.builds(
    Card,
    rank=st.sampled_from(tuple(Rank)),
    suit=st.sampled_from(tuple(Suit)),
)


def test_suit_has_four_stable_values() -> None:
    assert [suit.value for suit in Suit] == ["clubs", "diamonds", "hearts", "spades"]


def test_rank_has_thirteen_stable_values() -> None:
    assert [rank.value for rank in Rank] == [
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "jack",
        "queen",
        "king",
        "ace",
    ]


def test_card_is_immutable_and_hashable() -> None:
    card = Card(rank=Rank.ACE, suit=Suit.SPADES)
    equal_card = Card(rank=Rank.ACE, suit=Suit.SPADES)

    assert card == equal_card
    assert {card, equal_card} == {card}
    with pytest.raises(FrozenInstanceError):
        card.rank = Rank.KING  # type: ignore[misc]


def test_card_representation_is_redacted() -> None:
    card = Card(rank=Rank.ACE, suit=Suit.SPADES)

    assert repr(card) == "Card(<redacted>)"
    assert str(card) == "Card(<redacted>)"


def test_card_serialization_is_json_compatible() -> None:
    card = Card(rank=Rank.QUEEN, suit=Suit.HEARTS)

    serialized = json.dumps(card.to_data())

    assert json.loads(serialized) == {"rank": "queen", "suit": "hearts"}


@given(card=CARD_STRATEGY)
def test_card_serialization_round_trip(card: Card) -> None:
    assert Card.from_data(card.to_data()) == card


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"rank": "ace"},
        {"suit": "spades"},
        {"rank": "ace", "suit": "spades", "extra": "value"},
        {"rank": 14, "suit": "spades"},
        {"rank": "ace", "suit": 4},
        {"rank": "unknown", "suit": "spades"},
        {"rank": "ace", "suit": "unknown"},
    ],
)
def test_malformed_card_serialization_is_rejected(data: dict[str, object]) -> None:
    with pytest.raises(CardDataError):
        Card.from_data(data)
