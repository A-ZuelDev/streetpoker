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


class StackError(PokerDomainError):
    """Base class for expected chip-stack failures."""


class InvalidChipCountError(StackError):
    """Raised when a chip stack is constructed with an invalid count."""


class PlayerError(PokerDomainError):
    """Base class for expected player failures."""


class InvalidPlayerIdError(PlayerError):
    """Raised when a player identifier is invalid."""


class InvalidPlayerStateError(PlayerError):
    """Raised when a seated player's state violates a domain invariant."""


class PlayerAlreadySeatedError(PlayerError):
    """Raised when a player already occupies a seat at the table."""

    def __init__(self, *, player_id: str, seat_index: int) -> None:
        self.player_id = player_id
        self.seat_index = seat_index
        super().__init__(f"Player {player_id!r} already occupies seat {seat_index}.")


class PlayerNotSeatedError(PlayerError):
    """Raised when an operation requires a player who is not seated."""

    def __init__(self, *, player_id: str) -> None:
        self.player_id = player_id
        super().__init__(f"Player {player_id!r} is not seated.")


class TableError(PokerDomainError):
    """Base class for expected table-state failures."""


class SeatError(TableError):
    """Base class for expected seat failures."""


class InvalidSeatIndexError(SeatError):
    """Raised when a seat index is not a nonnegative integer."""


class SeatOutOfRangeError(SeatError):
    """Raised when a seat index is outside a table's capacity."""

    def __init__(self, *, seat_index: int, capacity: int) -> None:
        self.seat_index = seat_index
        self.capacity = capacity
        super().__init__(f"Seat index {seat_index} is outside table capacity {capacity}.")


class SeatOccupiedError(SeatError):
    """Raised when a player is assigned to an occupied seat."""

    def __init__(self, *, seat_index: int, occupant_id: str) -> None:
        self.seat_index = seat_index
        self.occupant_id = occupant_id
        super().__init__(f"Seat {seat_index} is occupied by player {occupant_id!r}.")


class ButtonError(TableError):
    """Base class for expected dealer-button failures."""


class NoEligibleButtonSeatError(ButtonError):
    """Raised when no seated player is eligible to receive the button."""
