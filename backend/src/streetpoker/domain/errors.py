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


class BettingRoundError(PokerDomainError):
    """Base class for expected betting-round failures."""


class InvalidBettingRoundStateError(BettingRoundError):
    """Raised when betting-round construction or state is invalid."""


class InvalidMinimumBetError(InvalidBettingRoundStateError):
    """Raised when a betting round receives an invalid minimum bet."""


class InvalidBettingParticipantError(InvalidBettingRoundStateError):
    """Raised when a betting-round participant is invalid."""


class DuplicateBettingPlayerError(InvalidBettingParticipantError):
    """Raised when a player appears more than once in a betting round."""


class DuplicateBettingSeatError(InvalidBettingParticipantError):
    """Raised when a seat appears more than once in a betting round."""


class PlayerNotInBettingRoundError(BettingRoundError):
    """Raised when a betting action references a nonparticipant."""

    def __init__(self, *, player_id: str) -> None:
        self.player_id = player_id
        super().__init__(f"Player {player_id!r} is not in this betting round.")


class BettingRoundCompleteError(BettingRoundError):
    """Raised when an action is attempted after betting is complete."""


class BettingActionError(BettingRoundError):
    """Base class for an illegal betting action."""


class OutOfTurnError(BettingActionError):
    """Raised when a participant acts outside the authoritative turn order."""

    def __init__(self, *, player_id: str, expected_player_id: str) -> None:
        self.player_id = player_id
        self.expected_player_id = expected_player_id
        super().__init__(f"Player {player_id!r} cannot act; action is on {expected_player_id!r}.")


class InvalidActionTypeError(BettingActionError):
    """Raised when the aggregate receives an unknown action value."""


class IllegalCheckError(BettingActionError):
    """Raised when a player checks while facing a wager."""


class IllegalCallError(BettingActionError):
    """Raised when a player calls without facing a wager."""


class IllegalBetError(BettingActionError):
    """Raised when a player bets after a wager already exists."""


class IllegalRaiseError(BettingActionError):
    """Raised when a player raises before a wager exists."""


class RaiseNotReopenedError(BettingActionError):
    """Raised when a prior actor attempts to raise without facing a full raise."""


class InvalidWagerAmountError(BettingActionError):
    """Raised when a bet-to or raise-to amount is not a positive integer."""


class WagerBelowMinimumError(BettingActionError):
    """Raised when a non-all-in wager is below the required minimum."""

    def __init__(self, *, requested_total: int, minimum_total: int) -> None:
        self.requested_total = requested_total
        self.minimum_total = minimum_total
        super().__init__(
            f"Wager total {requested_total} is below the required minimum {minimum_total}."
        )


class WagerExceedsStackError(BettingActionError):
    """Raised when a wager exceeds the chips available to its actor."""

    def __init__(self, *, requested_total: int, maximum_total: int) -> None:
        self.requested_total = requested_total
        self.maximum_total = maximum_total
        super().__init__(
            f"Wager total {requested_total} exceeds the available total {maximum_total}."
        )
