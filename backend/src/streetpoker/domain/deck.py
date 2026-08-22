"""Standard playing-deck construction, shuffling, and drawing."""

from collections.abc import Iterable
from typing import Self

from streetpoker.domain.cards import Card, Rank, Suit
from streetpoker.domain.errors import (
    DuplicateCardError,
    InsufficientCardsError,
    InvalidCardError,
    InvalidDrawCountError,
    InvalidRandomSourceError,
)
from streetpoker.domain.randomness import RandomSource, SecureRandomSource


class Deck:
    """A privately ordered collection that consumes cards without replacement."""

    __slots__ = ("_cards",)

    def __init__(self, cards: Iterable[Card]) -> None:
        copied_cards = list(cards)
        if any(not isinstance(card, Card) for card in copied_cards):
            raise InvalidCardError("Decks can contain only Card values.")
        if len(copied_cards) != len(set(copied_cards)):
            raise DuplicateCardError("A deck cannot contain duplicate cards.")
        self._cards = copied_cards

    @classmethod
    def standard(cls) -> Self:
        """Construct the canonical, unshuffled standard 52-card deck."""
        return cls(Card(rank=rank, suit=suit) for suit in Suit for rank in Rank)

    @classmethod
    def shuffled_standard(cls, *, random_source: RandomSource | None = None) -> Self:
        """Construct and shuffle a standard deck, securely by default."""
        deck = cls.standard()
        deck.shuffle(random_source=random_source)
        return deck

    @classmethod
    def from_cards(cls, cards: Iterable[Card]) -> Self:
        """Construct a deck from an ordered collection of unique cards."""
        return cls(cards)

    @property
    def remaining_count(self) -> int:
        return len(self._cards)

    def copy(self) -> Self:
        """Return an independent deck with the same remaining private order."""
        return type(self)(self._cards)

    def shuffle(self, *, random_source: RandomSource | None = None) -> None:
        """Shuffle remaining cards atomically, securely by default."""
        source = SecureRandomSource() if random_source is None else random_source
        shuffled_cards = self._cards.copy()

        for upper_bound in range(len(shuffled_cards), 1, -1):
            try:
                selected_index = source.randbelow(upper_bound)
            except Exception as error:
                raise InvalidRandomSourceError("The random source failed.") from error

            if (
                not isinstance(selected_index, int)
                or isinstance(selected_index, bool)
                or not 0 <= selected_index < upper_bound
            ):
                raise InvalidRandomSourceError("The random source returned an invalid index.")

            swap_index = upper_bound - 1
            shuffled_cards[swap_index], shuffled_cards[selected_index] = (
                shuffled_cards[selected_index],
                shuffled_cards[swap_index],
            )

        self._cards = shuffled_cards

    def draw(self, count: int = 1) -> tuple[Card, ...]:
        """Remove and return count cards from the top of the deck."""
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            raise InvalidDrawCountError("Draw count must be a positive integer.")
        if count > self.remaining_count:
            raise InsufficientCardsError(requested=count, remaining=self.remaining_count)

        drawn_cards = tuple(self._cards[:count])
        del self._cards[:count]
        return drawn_cards

    def __repr__(self) -> str:
        return f"Deck(remaining_count={self.remaining_count})"

    __str__ = __repr__
