"""Playing-card value types."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TypedDict

from streetpoker.domain.errors import CardDataError, InvalidCardError


class Suit(StrEnum):
    """The four suits in a standard deck."""

    CLUBS = "clubs"
    DIAMONDS = "diamonds"
    HEARTS = "hearts"
    SPADES = "spades"


class Rank(StrEnum):
    """The thirteen ranks in a standard deck, without evaluation semantics."""

    TWO = "two"
    THREE = "three"
    FOUR = "four"
    FIVE = "five"
    SIX = "six"
    SEVEN = "seven"
    EIGHT = "eight"
    NINE = "nine"
    TEN = "ten"
    JACK = "jack"
    QUEEN = "queen"
    KING = "king"
    ACE = "ace"


class CardData(TypedDict):
    """JSON-compatible primitive representation of a card."""

    rank: str
    suit: str


@dataclass(frozen=True, slots=True, repr=False)
class Card:
    """An immutable playing-card value object."""

    rank: Rank
    suit: Suit

    def __post_init__(self) -> None:
        if not isinstance(self.rank, Rank) or not isinstance(self.suit, Suit):
            raise InvalidCardError("Cards require Rank and Suit values.")

    def to_data(self) -> CardData:
        """Return stable primitive data for an explicitly disclosed card."""
        return {"rank": self.rank.value, "suit": self.suit.value}

    @classmethod
    def from_data(cls, data: Mapping[str, object]) -> Card:
        """Construct a card from validated primitive data."""
        if set(data) != {"rank", "suit"}:
            raise CardDataError("Card data must contain exactly rank and suit.")

        rank_value = data["rank"]
        suit_value = data["suit"]
        if not isinstance(rank_value, str) or not isinstance(suit_value, str):
            raise CardDataError("Card rank and suit must be strings.")

        try:
            return cls(rank=Rank(rank_value), suit=Suit(suit_value))
        except ValueError:
            raise CardDataError("Card data contains an unknown rank or suit.") from None

    def __repr__(self) -> str:
        return "Card(<redacted>)"

    __str__ = __repr__
