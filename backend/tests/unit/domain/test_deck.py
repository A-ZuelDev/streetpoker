import pytest
from hypothesis import given
from hypothesis import strategies as st

from streetpoker.domain import (
    Card,
    Deck,
    DuplicateCardError,
    InsufficientCardsError,
    InvalidDrawCountError,
    Rank,
    Suit,
)


def canonical_cards() -> tuple[Card, ...]:
    return tuple(Card(rank=rank, suit=suit) for suit in Suit for rank in Rank)


def test_standard_deck_is_the_complete_cartesian_product() -> None:
    deck = Deck.standard()

    drawn = deck.draw(52)
    expected = {Card(rank=rank, suit=suit) for rank in Rank for suit in Suit}

    assert len(drawn) == 52
    assert len(set(drawn)) == 52
    assert set(drawn) == expected
    assert deck.remaining_count == 0


def test_duplicate_card_construction_is_rejected() -> None:
    card = Card(rank=Rank.ACE, suit=Suit.SPADES)

    with pytest.raises(DuplicateCardError):
        Deck.from_cards([card, Card(rank=Rank.ACE, suit=Suit.SPADES)])


def test_deck_defensively_copies_construction_input() -> None:
    cards = list(canonical_cards())
    deck = Deck.from_cards(cards)

    cards.clear()

    assert deck.remaining_count == 52


def test_drawing_reduces_remaining_count() -> None:
    deck = Deck.standard()

    drawn = deck.draw(7)

    assert len(drawn) == 7
    assert deck.remaining_count == 45


def test_sequential_draws_are_disjoint() -> None:
    deck = Deck.standard()

    first_draw = deck.draw(17)
    second_draw = deck.draw(19)

    assert set(first_draw).isdisjoint(second_draw)
    assert deck.remaining_count == 16


def test_drawing_the_final_card_empties_the_deck() -> None:
    deck = Deck.standard()
    deck.draw(51)

    final_card = deck.draw()

    assert len(final_card) == 1
    assert deck.remaining_count == 0


@pytest.mark.parametrize("count", [0, -1, True, False, 1.5, "1", None])
def test_invalid_draw_count_fails_without_mutation(count: object) -> None:
    deck = Deck.standard()

    with pytest.raises(InvalidDrawCountError):
        deck.draw(count)  # type: ignore[arg-type]

    assert deck.remaining_count == 52
    assert deck.draw(52) == canonical_cards()


def test_overdraw_fails_without_mutation() -> None:
    deck = Deck.standard()
    first_draw = deck.draw(2)

    with pytest.raises(InsufficientCardsError) as error:
        deck.draw(51)

    assert error.value.requested == 51
    assert error.value.remaining == 50
    assert deck.remaining_count == 50
    assert first_draw + deck.draw(50) == canonical_cards()


@given(count=st.integers(min_value=1, max_value=52))
def test_drawing_any_valid_count_preserves_deck_invariants(count: int) -> None:
    deck = Deck.standard()

    drawn = deck.draw(count)

    assert len(drawn) == count
    assert len(set(drawn)) == count
    assert deck.remaining_count == 52 - count


@given(batch_sizes=st.lists(st.integers(min_value=1, max_value=60), max_size=25))
def test_generated_sequential_draws_are_unique_and_atomic(batch_sizes: list[int]) -> None:
    deck = Deck.standard()
    drawn: list[Card] = []

    for count in batch_sizes:
        remaining_before = deck.remaining_count
        if count <= remaining_before:
            drawn.extend(deck.draw(count))
            assert deck.remaining_count == remaining_before - count
        else:
            with pytest.raises(InsufficientCardsError):
                deck.draw(count)
            assert deck.remaining_count == remaining_before

    assert len(drawn) == len(set(drawn))
    assert len(drawn) + deck.remaining_count == 52


@given(
    rank=st.sampled_from(tuple(Rank)),
    suit=st.sampled_from(tuple(Suit)),
)
def test_generated_duplicate_cards_are_rejected(rank: Rank, suit: Suit) -> None:
    card = Card(rank=rank, suit=suit)

    with pytest.raises(DuplicateCardError):
        Deck.from_cards([card, card])
