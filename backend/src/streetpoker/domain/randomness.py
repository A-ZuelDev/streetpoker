"""Randomness providers for unbiased deck shuffling."""

import random
import secrets
from typing import Protocol

type Seed = int | float | str | bytes | bytearray


class RandomSource(Protocol):
    """Minimal randomness contract used by the shuffle algorithm."""

    def randbelow(self, upper_bound: int) -> int:
        """Return an integer in the half-open range [0, upper_bound)."""
        ...


class SecureRandomSource:
    """Production random source backed by operating-system entropy."""

    def randbelow(self, upper_bound: int) -> int:
        return secrets.randbelow(upper_bound)


class SeededRandomSource:
    """Isolated deterministic random source for tests and simulations."""

    __slots__ = ("_random",)

    def __init__(self, seed: Seed) -> None:
        self._random = random.Random(seed)

    def randbelow(self, upper_bound: int) -> int:
        return self._random.randrange(upper_bound)
