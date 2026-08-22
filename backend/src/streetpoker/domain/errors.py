"""Typed errors raised by the poker domain."""


class PokerDomainError(Exception):
    """Base class for expected poker-domain failures."""


class InvalidCardError(PokerDomainError):
    """Raised when a card is constructed with invalid domain values."""


class CardDataError(PokerDomainError):
    """Raised when primitive card data cannot be decoded."""


class HandEvaluationError(PokerDomainError):
    """Base class for expected poker-hand evaluation failures."""


class InvalidHoldemEvaluationInputError(HandEvaluationError):
    """Raised when cards cannot be used as a seven-card Hold'em holding."""


class InvalidHoldemCardCountError(InvalidHoldemEvaluationInputError):
    """Raised when Hold'em evaluation does not receive exactly two plus five cards."""


class DuplicateEvaluationCardError(InvalidHoldemEvaluationInputError):
    """Raised when the same card appears more than once in evaluation input."""


class InvalidHandRankError(HandEvaluationError):
    """Raised when a canonical comparable hand rank is structurally invalid."""


class InvalidEvaluatedHandError(HandEvaluationError):
    """Raised when exact best-five cards do not match their declared hand rank."""


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


class PotConstructionError(PokerDomainError):
    """Base class for expected pot-construction failures."""


class InvalidPotConstructionInputError(PotConstructionError):
    """Raised when a contribution collection cannot be used to construct pots."""


class InvalidPotContributionError(InvalidPotConstructionInputError):
    """Raised when one participant contribution has invalid domain values."""


class InvalidCommittedChipsError(InvalidPotContributionError):
    """Raised when a cumulative commitment is not a nonnegative integer."""


class DuplicatePotContributorError(InvalidPotConstructionInputError):
    """Raised when a player appears more than once in pot-construction input."""


class NoEligiblePotParticipantError(InvalidPotConstructionInputError):
    """Raised when every supplied participant is folded."""


class UnawardablePotError(InvalidPotConstructionInputError):
    """Raised when a funded positive pot tier has no live eligible contributor."""

    def __init__(self, *, lower_threshold: int, upper_threshold: int) -> None:
        self.lower_threshold = lower_threshold
        self.upper_threshold = upper_threshold
        super().__init__(
            "A funded pot tier has no live eligible contributor: "
            f"({lower_threshold}, {upper_threshold}]."
        )


class InvalidConstructedPotError(PotConstructionError):
    """Raised when a constructed pot value or result violates an invariant."""


class HoldemHandError(PokerDomainError):
    """Base class for expected Hold'em hand lifecycle failures."""


class InvalidHandInitializationError(HoldemHandError):
    """Raised when a hand cannot be started from the supplied table and blinds."""


class InsufficientEligibleParticipantsError(InvalidHandInitializationError):
    """Raised when fewer than two table occupants are eligible for a hand."""


class InvalidBlindStructureError(InvalidHandInitializationError):
    """Raised when blind amounts do not form a valid StreetPoker structure."""


class InvalidHandButtonError(InvalidHandInitializationError):
    """Raised when the snapshotted button is absent or ineligible."""


class InvalidHandStateError(HoldemHandError):
    """Raised when authoritative hand state violates a lifecycle invariant."""


class BettingRoundReconciliationError(InvalidHandStateError):
    """Raised when a betting-round candidate cannot reconcile into hand state."""


class HandChipAccountingError(InvalidHandStateError):
    """Raised when per-player or global hand chip accounting does not reconcile."""


class InvalidDealingStateError(InvalidHandStateError):
    """Raised when authoritative dealt-card state violates Hold'em invariants."""


class NoActiveBettingRoundError(HoldemHandError):
    """Raised when an operation requires an active street betting round."""


class HandAlreadyTerminalError(HoldemHandError):
    """Raised when an action is attempted after the hand reaches a terminal state."""


class PlayerNotInHandError(HoldemHandError):
    """Raised when a hand operation references a nonparticipant."""

    def __init__(self, *, player_id: str) -> None:
        self.player_id = player_id
        super().__init__(f"Player {player_id!r} is not in this hand.")


class SettlementError(PokerDomainError):
    """Base class for expected Hold'em settlement failures."""


class InvalidSettlementInputError(SettlementError):
    """Raised when terminal hand state cannot be settled consistently."""


class HandNotTerminalError(InvalidSettlementInputError):
    """Raised when settlement is requested before a hand is terminal."""


class MissingPotResultError(InvalidSettlementInputError):
    """Raised when terminal hand state has no contestable pot result."""


class InvalidShowdownBoardError(InvalidSettlementInputError):
    """Raised when showdown does not contain exactly five valid board cards."""


class InvalidEligiblePlayerError(InvalidSettlementInputError):
    """Raised when pot eligibility disagrees with terminal participants."""


class DuplicateSettlementParticipantError(InvalidSettlementInputError):
    """Raised when settlement participants repeat an identity or seat."""


class NoEligibleWinnerError(SettlementError):
    """Raised when a contestable pot has no player who can win it."""


class InvalidOddChipOrderingError(SettlementError):
    """Raised when a pot award violates clockwise odd-chip ordering."""


class InvalidSettlementResultError(SettlementError):
    """Raised when an immutable settlement result violates its invariants."""


class AwardReconciliationError(InvalidSettlementResultError):
    """Raised when pot awards or final stacks fail exact chip reconciliation."""
