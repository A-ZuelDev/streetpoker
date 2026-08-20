"""Typed errors raised by the poker domain."""


class PokerDomainError(Exception):
    """Base class for expected poker-domain failures."""


class InvalidCardError(PokerDomainError):
    """Raised when a card is constructed with invalid domain values."""


class CardDataError(PokerDomainError):
    """Raised when primitive card data cannot be decoded."""


class DeckError(PokerDomainError):
    """Base class for expected deck failures."""


class DuplicateCardError(DeckError):
    """Raised when a deck is constructed with duplicate cards."""


class InvalidDrawCountError(DeckError):
    """Raised when a draw count is not a positive integer."""


class InsufficientCardsError(DeckError):
    """Raised when a draw requests more cards than remain."""

    def __init__(self, *, requested: int, remaining: int) -> None:
        self.requested = requested
        self.remaining = remaining
        super().__init__(f"Cannot draw {requested} cards; only {remaining} remain.")


class InvalidRandomSourceError(DeckError):
    """Raised when an injected random source violates its contract."""
