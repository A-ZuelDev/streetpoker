import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

import streetpoker.domain.randomness as randomness_module
from streetpoker.domain import Card, Deck, InvalidRandomSourceError
from streetpoker.domain.randomness import SecureRandomSource, SeededRandomSource


def shuffled_order(seed: int) -> tuple[Card, ...]:
    deck = Deck.shuffled_standard(random_source=SeededRandomSource(seed))
    return deck.draw(52)


def test_secure_random_source_uses_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake_randbelow(upper_bound: int) -> int:
        calls.append(upper_bound)
        return 0

    monkeypatch.setattr(randomness_module.secrets, "randbelow", fake_randbelow)

    assert SecureRandomSource().randbelow(17) == 0
    assert calls == [17]


def test_default_shuffle_uses_secure_random_source(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake_randbelow(upper_bound: int) -> int:
        calls.append(upper_bound)
        return 0

    monkeypatch.setattr(randomness_module.secrets, "randbelow", fake_randbelow)

    Deck.standard().shuffle()

    assert calls == list(range(52, 1, -1))


def test_same_seed_produces_same_shuffled_order() -> None:
    assert shuffled_order(2026) == shuffled_order(2026)


def test_representative_different_seeds_produce_different_orders() -> None:
    assert shuffled_order(2026) != shuffled_order(2027)


def test_shuffle_preserves_the_complete_standard_deck() -> None:
    shuffled = shuffled_order(2026)
    canonical = Deck.standard().draw(52)

    assert len(shuffled) == 52
    assert len(set(shuffled)) == 52
    assert set(shuffled) == set(canonical)


@given(seed=st.integers(min_value=-(2**63), max_value=2**63 - 1))
def test_generated_seeds_are_reproducible(seed: int) -> None:
    assert shuffled_order(seed) == shuffled_order(seed)


def test_seeded_randomness_does_not_modify_global_random_state() -> None:
    original_state = random.getstate()
    try:
        random.seed(918_273)
        state_before_shuffle = random.getstate()

        shuffled_order(4_567)

        assert random.getstate() == state_before_shuffle
    finally:
        random.setstate(original_state)


def test_invalid_injected_random_source_fails_without_partial_mutation() -> None:
    class InvalidAfterOneCall:
        def __init__(self) -> None:
            self.call_count = 0

        def randbelow(self, upper_bound: int) -> int:
            self.call_count += 1
            return 0 if self.call_count == 1 else upper_bound

    deck = Deck.standard()

    with pytest.raises(InvalidRandomSourceError):
        deck.shuffle(random_source=InvalidAfterOneCall())

    assert deck.remaining_count == 52
    assert deck.draw(52) == Deck.standard().draw(52)


def test_raising_injected_random_source_fails_without_partial_mutation() -> None:
    class RaisingSource:
        def randbelow(self, upper_bound: int) -> int:
            raise RuntimeError(upper_bound)

    deck = Deck.standard()

    with pytest.raises(InvalidRandomSourceError):
        deck.shuffle(random_source=RaisingSource())

    assert deck.draw(52) == Deck.standard().draw(52)
