from dataclasses import FrozenInstanceError

import pytest
from hypothesis import given
from hypothesis import strategies as st

from streetpoker.domain import (
    SIX_MAX_CAPACITY,
    ChipStack,
    InvalidPlayerStateError,
    InvalidSeatIndexError,
    NoEligibleButtonSeatError,
    ParticipationStatus,
    PlayerAlreadySeatedError,
    PlayerId,
    PlayerNotSeatedError,
    PokerDomainError,
    Seat,
    SeatedPlayer,
    SeatIndex,
    SeatOccupiedError,
    SeatOutOfRangeError,
    TableState,
)


def player_id(number: int) -> PlayerId:
    return PlayerId(f"player-{number}")


def seat_player(
    table: TableState,
    seat_index: int,
    player_number: int,
    *,
    chips: int = 100,
    status: ParticipationStatus = ParticipationStatus.SITTING_IN,
) -> None:
    table.seat_player(
        seat_index=SeatIndex(seat_index),
        player_id=player_id(player_number),
        stack=ChipStack(chips),
        status=status,
    )


def observable_state(table: TableState) -> tuple[tuple[Seat, ...], SeatIndex | None]:
    return table.seats, table.button_position


def assert_table_invariants(table: TableState) -> None:
    assert table.capacity == SIX_MAX_CAPACITY
    assert len(table.seats) == SIX_MAX_CAPACITY
    assert [seat.index.value for seat in table.seats] == list(range(SIX_MAX_CAPACITY))
    assert len({seat.index for seat in table.seats}) == SIX_MAX_CAPACITY

    occupants = [seat.occupant for seat in table.seats if seat.occupant is not None]
    assert table.occupied_count == len(occupants)
    assert len({occupant.player_id for occupant in occupants}) == len(occupants)
    assert all(occupant.stack.chips >= 0 for occupant in occupants)
    assert all(
        occupant.stack.chips > 0
        for occupant in occupants
        if occupant.status is ParticipationStatus.SITTING_IN
    )
    assert table.button_position is None or 0 <= table.button_position.value < table.capacity


def test_new_six_max_table_has_six_ordered_empty_seats_and_no_button() -> None:
    table = TableState.six_max()

    assert table.capacity == 6
    assert [seat.index.value for seat in table.seats] == [0, 1, 2, 3, 4, 5]
    assert all(seat.is_empty for seat in table.seats)
    assert table.occupied_count == 0
    assert table.button_position is None


@pytest.mark.parametrize("value", [-1, -100, True, False, 1.5, "1", None])
def test_seat_index_rejects_negative_bool_and_non_integer_values(value: object) -> None:
    with pytest.raises(InvalidSeatIndexError):
        SeatIndex(value)  # type: ignore[arg-type]


def test_seat_index_is_capacity_independent() -> None:
    assert SeatIndex(6).value == 6
    assert SeatIndex(10).value == 10


@given(value=st.integers(min_value=0))
def test_generated_nonnegative_seat_indexes_are_valid_values(value: int) -> None:
    assert SeatIndex(value).value == value


@given(value=st.integers(max_value=-1))
def test_generated_negative_seat_indexes_are_rejected(value: int) -> None:
    with pytest.raises(InvalidSeatIndexError):
        SeatIndex(value)


@pytest.mark.parametrize("value", [6, 7, 100])
def test_six_max_table_rejects_out_of_range_seat_indexes(value: int) -> None:
    table = TableState.six_max()
    before = observable_state(table)

    with pytest.raises(SeatOutOfRangeError) as error:
        table.seat_at(SeatIndex(value))

    assert error.value.seat_index == value
    assert error.value.capacity == 6
    assert observable_state(table) == before


def test_table_operations_require_a_seat_index_value() -> None:
    table = TableState.six_max()

    with pytest.raises(InvalidSeatIndexError):
        table.seat_at(0)  # type: ignore[arg-type]


def test_seating_a_player_occupies_only_the_requested_seat() -> None:
    table = TableState.six_max()

    seat_player(table, 3, 1, chips=250)

    occupant = table.seat_at(SeatIndex(3)).occupant
    assert occupant == SeatedPlayer(
        player_id=player_id(1),
        stack=ChipStack(250),
        status=ParticipationStatus.SITTING_IN,
    )
    assert table.occupied_count == 1
    assert all(table.seats[index].is_empty for index in [0, 1, 2, 4, 5])


def test_occupied_seat_rejects_another_player_without_mutation() -> None:
    table = TableState.six_max()
    seat_player(table, 2, 1)
    before = observable_state(table)

    with pytest.raises(SeatOccupiedError) as error:
        seat_player(table, 2, 2)

    assert error.value.seat_index == 2
    assert error.value.occupant_id == "player-1"
    assert observable_state(table) == before


def test_player_cannot_occupy_two_seats() -> None:
    table = TableState.six_max()
    seat_player(table, 1, 1)
    before = observable_state(table)

    with pytest.raises(PlayerAlreadySeatedError) as error:
        seat_player(table, 4, 1)

    assert error.value.player_id == "player-1"
    assert error.value.seat_index == 1
    assert observable_state(table) == before


def test_empty_seat_is_distinct_from_sitting_out_player() -> None:
    table = TableState.six_max()
    seat_player(table, 0, 1, chips=0, status=ParticipationStatus.SITTING_OUT)

    occupied_seat = table.seat_at(SeatIndex(0))
    empty_seat = table.seat_at(SeatIndex(1))

    assert not occupied_seat.is_empty
    assert occupied_seat.occupant is not None
    assert occupied_seat.occupant.status is ParticipationStatus.SITTING_OUT
    assert occupied_seat.occupant.stack == ChipStack(0)
    assert empty_seat.is_empty
    assert empty_seat.occupant is None


def test_find_player_returns_their_seat_or_none() -> None:
    table = TableState.six_max()
    seat_player(table, 4, 1)

    assert table.find_player(player_id(1)) == SeatIndex(4)
    assert table.find_player(player_id(2)) is None


def test_leaving_returns_player_and_empties_seat_without_moving_button() -> None:
    table = TableState.six_max()
    seat_player(table, 2, 1)
    table.move_button()

    removed = table.leave_seat(player_id=player_id(1))

    assert removed.player_id == player_id(1)
    assert table.seat_at(SeatIndex(2)).is_empty
    assert table.find_player(player_id(1)) is None
    assert table.button_position == SeatIndex(2)


@pytest.mark.parametrize("operation", ["leave", "sit_in", "sit_out"])
def test_missing_player_operations_fail_without_mutation(operation: str) -> None:
    table = TableState.six_max()
    before = observable_state(table)

    with pytest.raises(PlayerNotSeatedError):
        if operation == "leave":
            table.leave_seat(player_id=player_id(1))
        elif operation == "sit_in":
            table.sit_in(player_id=player_id(1))
        else:
            table.sit_out(player_id=player_id(1))

    assert observable_state(table) == before


def test_sit_out_and_sit_in_preserve_seat_identity_and_stack() -> None:
    table = TableState.six_max()
    seat_player(table, 3, 1, chips=75)

    table.sit_out(player_id=player_id(1))
    sitting_out = table.seat_at(SeatIndex(3)).occupant
    table.sit_in(player_id=player_id(1))
    sitting_in = table.seat_at(SeatIndex(3)).occupant

    assert sitting_out == SeatedPlayer(
        player_id=player_id(1),
        stack=ChipStack(75),
        status=ParticipationStatus.SITTING_OUT,
    )
    assert sitting_in == SeatedPlayer(
        player_id=player_id(1),
        stack=ChipStack(75),
        status=ParticipationStatus.SITTING_IN,
    )


def test_repeated_sit_in_and_sit_out_are_idempotent() -> None:
    table = TableState.six_max()
    seat_player(table, 1, 1)

    table.sit_in(player_id=player_id(1))
    after_sit_in = observable_state(table)
    table.sit_in(player_id=player_id(1))
    assert observable_state(table) == after_sit_in

    table.sit_out(player_id=player_id(1))
    after_sit_out = observable_state(table)
    table.sit_out(player_id=player_id(1))
    assert observable_state(table) == after_sit_out


def test_zero_stack_player_cannot_sit_in_and_failure_is_atomic() -> None:
    table = TableState.six_max()
    seat_player(table, 1, 1, chips=0, status=ParticipationStatus.SITTING_OUT)
    before = observable_state(table)

    with pytest.raises(InvalidPlayerStateError):
        table.sit_in(player_id=player_id(1))

    assert observable_state(table) == before


def test_initial_button_placement_uses_lowest_eligible_seat() -> None:
    table = TableState.six_max()
    seat_player(table, 4, 1)
    seat_player(table, 2, 2)

    assert table.move_button() == SeatIndex(2)


def test_button_moves_clockwise_and_wraps() -> None:
    table = TableState.six_max()
    seat_player(table, 1, 1)
    seat_player(table, 5, 2)

    assert table.move_button() == SeatIndex(1)
    assert table.move_button() == SeatIndex(5)
    assert table.move_button() == SeatIndex(1)


def test_button_skips_empty_and_sitting_out_seats() -> None:
    table = TableState.six_max()
    seat_player(table, 0, 1)
    seat_player(table, 1, 2, status=ParticipationStatus.SITTING_OUT)
    seat_player(table, 4, 3)

    assert table.move_button() == SeatIndex(0)
    assert table.move_button() == SeatIndex(4)


def test_button_movement_with_one_eligible_player_returns_same_seat() -> None:
    table = TableState.six_max()
    seat_player(table, 3, 1)

    assert table.move_button() == SeatIndex(3)
    assert table.move_button() == SeatIndex(3)


def test_no_eligible_player_preserves_an_unset_button() -> None:
    table = TableState.six_max()
    seat_player(table, 1, 1, chips=0, status=ParticipationStatus.SITTING_OUT)

    with pytest.raises(NoEligibleButtonSeatError):
        table.move_button()

    assert table.button_position is None


def test_no_eligible_player_preserves_an_existing_button() -> None:
    table = TableState.six_max()
    seat_player(table, 2, 1)
    table.move_button()
    table.sit_out(player_id=player_id(1))

    with pytest.raises(NoEligibleButtonSeatError):
        table.move_button()

    assert table.button_position == SeatIndex(2)


@pytest.mark.parametrize("vacate_operation", ["leave", "sit_out"])
def test_vacated_button_position_remains_the_clockwise_anchor(vacate_operation: str) -> None:
    table = TableState.six_max()
    seat_player(table, 1, 1)
    seat_player(table, 4, 2)
    assert table.move_button() == SeatIndex(1)

    if vacate_operation == "leave":
        table.leave_seat(player_id=player_id(1))
    else:
        table.sit_out(player_id=player_id(1))

    assert table.button_position == SeatIndex(1)
    assert table.move_button() == SeatIndex(4)


def test_exposed_seat_snapshots_are_immutable() -> None:
    table = TableState.six_max()
    seat_player(table, 0, 1)
    seats = table.seats
    first_seat = seats[0]

    with pytest.raises(TypeError):
        seats[0] = seats[1]  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        first_seat.occupant = None  # type: ignore[misc]

    assert table.seat_at(SeatIndex(0)).occupant is not None


def test_out_of_range_seating_fails_without_mutation() -> None:
    table = TableState.six_max()
    before = observable_state(table)

    with pytest.raises(SeatOutOfRangeError):
        seat_player(table, 6, 1)

    assert observable_state(table) == before


@given(
    order=st.permutations(tuple(range(SIX_MAX_CAPACITY))),
    count=st.integers(min_value=0, max_value=SIX_MAX_CAPACITY),
)
def test_generated_unique_seating_permutations_preserve_one_to_one_occupancy(
    order: list[int],
    count: int,
) -> None:
    table = TableState.six_max()

    for player_number, seat_index in enumerate(order[:count]):
        seat_player(table, seat_index, player_number)

    assert table.occupied_count == count
    assert {
        table.find_player(player_id(player_number)) for player_number in range(count)
    } == {SeatIndex(seat_index) for seat_index in order[:count]}
    assert_table_invariants(table)


@given(
    first_seat=st.integers(min_value=0, max_value=5),
    second_seat=st.integers(min_value=0, max_value=5),
)
def test_generated_duplicate_player_attempts_are_atomic(
    first_seat: int,
    second_seat: int,
) -> None:
    if first_seat == second_seat:
        return

    table = TableState.six_max()
    seat_player(table, first_seat, 1)
    before = observable_state(table)

    with pytest.raises(PlayerAlreadySeatedError):
        seat_player(table, second_seat, 1)

    assert observable_state(table) == before


@given(
    seat_states=st.lists(
        st.sampled_from(("empty", "sitting_out", "sitting_in")),
        min_size=SIX_MAX_CAPACITY,
        max_size=SIX_MAX_CAPACITY,
    ),
    starting_position=st.one_of(st.none(), st.integers(min_value=0, max_value=5)),
)
def test_generated_button_eligibility_masks_match_bounded_clockwise_search(
    seat_states: list[str],
    starting_position: int | None,
) -> None:
    table = TableState.six_max()

    if starting_position is not None:
        seat_player(table, starting_position, 100)
        assert table.move_button() == SeatIndex(starting_position)
        table.leave_seat(player_id=player_id(100))

    for index, state in enumerate(seat_states):
        if state == "sitting_in":
            seat_player(table, index, index)
        elif state == "sitting_out":
            seat_player(table, index, index, chips=0, status=ParticipationStatus.SITTING_OUT)

    before_button = table.button_position
    eligible = {index for index, state in enumerate(seat_states) if state == "sitting_in"}
    start = -1 if starting_position is None else starting_position
    expected = next(
        (
            (start + offset) % SIX_MAX_CAPACITY
            for offset in range(1, SIX_MAX_CAPACITY + 1)
            if (start + offset) % SIX_MAX_CAPACITY in eligible
        ),
        None,
    )

    if expected is None:
        with pytest.raises(NoEligibleButtonSeatError):
            table.move_button()
        assert table.button_position == before_button
    else:
        assert table.move_button() == SeatIndex(expected)


OPERATION_STRATEGY = st.tuples(
    st.sampled_from(("seat", "leave", "sit_in", "sit_out", "move_button")),
    st.integers(min_value=0, max_value=8),
    st.integers(min_value=0, max_value=8),
    st.integers(min_value=0, max_value=200),
    st.booleans(),
)


@given(operations=st.lists(OPERATION_STRATEGY, max_size=50))
def test_generated_transition_sequences_preserve_table_invariants(
    operations: list[tuple[str, int, int, int, bool]],
) -> None:
    table = TableState.six_max()

    for operation, index, player_number, chips, sitting_in in operations:
        before = observable_state(table)
        try:
            if operation == "seat":
                seat_player(
                    table,
                    index,
                    player_number,
                    chips=chips,
                    status=(
                        ParticipationStatus.SITTING_IN
                        if sitting_in
                        else ParticipationStatus.SITTING_OUT
                    ),
                )
            elif operation == "leave":
                table.leave_seat(player_id=player_id(player_number))
            elif operation == "sit_in":
                table.sit_in(player_id=player_id(player_number))
            elif operation == "sit_out":
                table.sit_out(player_id=player_id(player_number))
            else:
                table.move_button()
        except PokerDomainError:
            assert observable_state(table) == before

        assert_table_invariants(table)
