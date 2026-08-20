"""Chip-stack value types without betting semantics."""

from dataclasses import dataclass

from streetpoker.domain.errors import InvalidChipCountError


@dataclass(frozen=True, slots=True)
class ChipStack:
    """An immutable, nonnegative integer chip count."""

    chips: int

    def __post_init__(self) -> None:
        if not isinstance(self.chips, int) or isinstance(self.chips, bool):
            raise InvalidChipCountError("A chip stack requires an integer chip count.")
        if self.chips < 0:
            raise InvalidChipCountError("A chip stack cannot be negative.")
