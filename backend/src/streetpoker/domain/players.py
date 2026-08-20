"""Player identity and table-participation value types."""

from dataclasses import dataclass
from enum import StrEnum

from streetpoker.domain.chips import ChipStack
from streetpoker.domain.errors import InvalidPlayerIdError, InvalidPlayerStateError


@dataclass(frozen=True, slots=True)
class PlayerId:
    """An opaque player identity whose uniqueness is enforced within a table."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value or self.value.isspace():
            raise InvalidPlayerIdError("A player ID must be a non-blank string.")


class ParticipationStatus(StrEnum):
    """A seated player's between-hand participation preference."""

    SITTING_IN = "sitting_in"
    SITTING_OUT = "sitting_out"


@dataclass(frozen=True, slots=True)
class SeatedPlayer:
    """An immutable player state associated with one table seat."""

    player_id: PlayerId
    stack: ChipStack
    status: ParticipationStatus

    def __post_init__(self) -> None:
        if not isinstance(self.player_id, PlayerId):
            raise InvalidPlayerStateError("A seated player requires a PlayerId.")
        if not isinstance(self.stack, ChipStack):
            raise InvalidPlayerStateError("A seated player requires a ChipStack.")
        if not isinstance(self.status, ParticipationStatus):
            raise InvalidPlayerStateError("A seated player requires a ParticipationStatus.")
        if self.status is ParticipationStatus.SITTING_IN and self.stack.chips == 0:
            raise InvalidPlayerStateError("A sitting-in player requires a positive chip stack.")
