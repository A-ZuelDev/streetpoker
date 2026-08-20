from dataclasses import FrozenInstanceError

import pytest
from hypothesis import given
from hypothesis import strategies as st

from streetpoker.domain import ChipStack, InvalidChipCountError


def test_chip_stack_accepts_zero() -> None:
    assert ChipStack(0).chips == 0


def test_chip_stack_accepts_positive_integers() -> None:
    assert ChipStack(125).chips == 125


def test_chip_stack_is_immutable_and_hashable() -> None:
    stack = ChipStack(50)

    assert {stack, ChipStack(50)} == {stack}
    with pytest.raises(FrozenInstanceError):
        stack.chips = 40  # type: ignore[misc]


@pytest.mark.parametrize("chips", [-1, -100])
def test_negative_chip_stacks_are_rejected(chips: int) -> None:
    with pytest.raises(InvalidChipCountError):
        ChipStack(chips)


@pytest.mark.parametrize("chips", [True, False, 1.0, "1", None, object()])
def test_non_integer_chip_counts_are_rejected(chips: object) -> None:
    with pytest.raises(InvalidChipCountError):
        ChipStack(chips)  # type: ignore[arg-type]


@given(chips=st.integers(min_value=0))
def test_generated_nonnegative_chip_stacks_preserve_their_value(chips: int) -> None:
    assert ChipStack(chips).chips == chips


@given(chips=st.integers(max_value=-1))
def test_generated_negative_chip_stacks_are_rejected(chips: int) -> None:
    with pytest.raises(InvalidChipCountError):
        ChipStack(chips)
