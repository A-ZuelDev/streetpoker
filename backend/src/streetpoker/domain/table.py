"""Six-max table seating and dealer-button state."""

from dataclasses import dataclass
from typing import Self

from streetpoker.domain.chips import ChipStack
from streetpoker.domain.errors import (
    InvalidPlayerIdError,
    InvalidPlayerStateError,
    InvalidSeatIndexError,
    NoEligibleButtonSeatError,
    PlayerAlreadySeatedError,
    PlayerNotSeatedError,
    SeatOccupiedError,
    SeatOutOfRangeError,
)
from streetpoker.domain.players import ParticipationStatus, PlayerId, SeatedPlayer

SIX_MAX_CAPACITY = 6


@dataclass(frozen=True, slots=True)
class SeatIndex:
    """A capacity-independent, zero-based seat index."""

    value: int

    def __post_init__(self) -> None:
        if not isinstance(self.value, int) or isinstance(self.value, bool) or self.value < 0:
            raise InvalidSeatIndexError("A seat index must be a nonnegative integer.")


@dataclass(frozen=True, slots=True)
class Seat:
    """An immutable snapshot of one position and its optional occupant."""

    index: SeatIndex
    occupant: SeatedPlayer | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.index, SeatIndex):
            raise InvalidSeatIndexError("A seat requires a SeatIndex.")
        if self.occupant is not None and not isinstance(self.occupant, SeatedPlayer):
            raise InvalidPlayerStateError("A seat occupant must be a SeatedPlayer or None.")

    @property
    def is_empty(self) -> bool:
        return self.occupant is None


class TableState:
    """The mutable between-hand seating and button state for a six-max table."""

    __slots__ = ("_button_position", "_seats")

    def __init__(self) -> None:
        self._seats = tuple(Seat(index=SeatIndex(index)) for index in range(SIX_MAX_CAPACITY))
        self._button_position: SeatIndex | None = None

    @classmethod
    def six_max(cls) -> Self:
        """Create an empty six-max table."""
        return cls()

    @property
    def capacity(self) -> int:
        return len(self._seats)

    @property
    def seats(self) -> tuple[Seat, ...]:
        return self._seats

    @property
    def button_position(self) -> SeatIndex | None:
        return self._button_position

    @property
    def occupied_count(self) -> int:
        return sum(not seat.is_empty for seat in self._seats)

    def seat_at(self, seat_index: SeatIndex) -> Seat:
        """Return the immutable snapshot at a position within this table."""
        index = self._checked_index(seat_index)
        return self._seats[index]

    def find_player(self, player_id: PlayerId) -> SeatIndex | None:
        """Return the position occupied by a player, if present."""
        checked_player_id = self._checked_player_id(player_id)
        for seat in self._seats:
            if seat.occupant is not None and seat.occupant.player_id == checked_player_id:
                return seat.index
        return None

    def seat_player(
        self,
        *,
        seat_index: SeatIndex,
        player_id: PlayerId,
        stack: ChipStack,
        status: ParticipationStatus,
    ) -> None:
        """Seat a unique player at an empty position after validating all state."""
        index = self._checked_index(seat_index)
        seated_player = SeatedPlayer(player_id=player_id, stack=stack, status=status)
        target_seat = self._seats[index]

        if target_seat.occupant is not None:
            raise SeatOccupiedError(
                seat_index=index,
                occupant_id=target_seat.occupant.player_id.value,
            )

        existing_index = self.find_player(player_id)
        if existing_index is not None:
            raise PlayerAlreadySeatedError(
                player_id=player_id.value,
                seat_index=existing_index.value,
            )

        self._replace_seat(Seat(index=seat_index, occupant=seated_player))

    def leave_seat(self, *, player_id: PlayerId) -> SeatedPlayer:
        """Remove and return a seated player without moving the button."""
        seat_index = self.find_player(player_id)
        if seat_index is None:
            raise PlayerNotSeatedError(player_id=player_id.value)

        seat = self._seats[seat_index.value]
        occupant = seat.occupant
        if occupant is None:  # pragma: no cover - protected by find_player
            raise PlayerNotSeatedError(player_id=player_id.value)

        self._replace_seat(Seat(index=seat_index))
        return occupant

    def sit_in(self, *, player_id: PlayerId) -> None:
        """Mark a positive-stack seated player as sitting in."""
        self._set_participation(player_id=player_id, status=ParticipationStatus.SITTING_IN)

    def sit_out(self, *, player_id: PlayerId) -> None:
        """Mark a seated player as sitting out without moving the button."""
        self._set_participation(player_id=player_id, status=ParticipationStatus.SITTING_OUT)

    def move_button(self) -> SeatIndex:
        """Move the button clockwise to the next sitting-in player in bounded time."""
        current_value = -1 if self._button_position is None else self._button_position.value

        for offset in range(1, self.capacity + 1):
            candidate_value = (current_value + offset) % self.capacity
            candidate = self._seats[candidate_value]
            if (
                candidate.occupant is not None
                and candidate.occupant.status is ParticipationStatus.SITTING_IN
            ):
                self._button_position = candidate.index
                return candidate.index

        raise NoEligibleButtonSeatError("No seated player is eligible to receive the button.")

    def _set_participation(
        self,
        *,
        player_id: PlayerId,
        status: ParticipationStatus,
    ) -> None:
        seat_index = self.find_player(player_id)
        if seat_index is None:
            raise PlayerNotSeatedError(player_id=player_id.value)

        seat = self._seats[seat_index.value]
        occupant = seat.occupant
        if occupant is None:  # pragma: no cover - protected by find_player
            raise PlayerNotSeatedError(player_id=player_id.value)
        if occupant.status is status:
            return

        updated_player = SeatedPlayer(
            player_id=occupant.player_id,
            stack=occupant.stack,
            status=status,
        )
        self._replace_seat(Seat(index=seat.index, occupant=updated_player))

    def _replace_seat(self, replacement: Seat) -> None:
        self._seats = tuple(
            replacement if seat.index == replacement.index else seat for seat in self._seats
        )

    def _checked_index(self, seat_index: SeatIndex) -> int:
        if not isinstance(seat_index, SeatIndex):
            raise InvalidSeatIndexError("Table operations require a SeatIndex.")
        if seat_index.value >= self.capacity:
            raise SeatOutOfRangeError(seat_index=seat_index.value, capacity=self.capacity)
        return seat_index.value

    @staticmethod
    def _checked_player_id(player_id: PlayerId) -> PlayerId:
        if not isinstance(player_id, PlayerId):
            raise InvalidPlayerIdError("Table operations require a PlayerId.")
        return player_id
