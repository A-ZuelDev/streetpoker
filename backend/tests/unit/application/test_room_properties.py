from collections.abc import Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st

from streetpoker.application import (
    GuestId,
    InMemoryRoomRepository,
    RoomId,
    RoomSeatAlreadyRequestedError,
    RoomService,
    RoomSettings,
)
from streetpoker.application.room_service import RoomCodeSource
from streetpoker.domain import PlayerId

HOST = GuestId("property-host")


class StaticCodeSource(RoomCodeSource):
    def next_code(self) -> str:
        return "ABCDEFGH"


def sequential_player_ids() -> Callable[[], PlayerId]:
    next_value = 0

    def create() -> PlayerId:
        nonlocal next_value
        result = PlayerId(f"property-player-{next_value}")
        next_value += 1
        return result

    return create


def build_service(*, approval: bool) -> tuple[RoomService, str]:
    service = RoomService(
        repository=InMemoryRoomRepository(),
        code_source=StaticCodeSource(),
        room_id_factory=lambda: RoomId("property-room"),
        player_id_factory=sequential_player_ids(),
    )
    snapshot = service.create_room(
        actor=HOST,
        nickname="Host",
        settings=RoomSettings(
            room_name="Property Room",
            seating_approval_required=approval,
        ),
    )
    return service, snapshot.room_code


@given(
    order=st.permutations(tuple(range(6))),
    count=st.integers(min_value=0, max_value=6),
)
def test_generated_sparse_seating_and_stand_reseat_preserve_exact_stacks(
    order: list[int],
    count: int,
) -> None:
    service, code = build_service(approval=False)
    room_id = service.get_room_snapshot_by_code(code).room_id
    guests = [GuestId(f"guest-{index}") for index in range(count)]

    for index, guest in enumerate(guests):
        service.join_room(
            room_code=code,
            actor=guest,
            nickname=f"Player {index}",
        )
        service.request_seat(
            room_id=room_id,
            actor=guest,
            seat_index=order[index],
        )

    seated = service.get_room_snapshot(room_id)
    assert {seat.seat_index for seat in seated.seats if seat.guest_id is not None} == set(
        order[:count]
    )
    assert all(seat.stack == 10_000 for seat in seated.seats if seat.guest_id is not None)

    for guest in guests:
        service.stand_up(room_id=room_id, actor=guest)
    for index, guest in enumerate(reversed(guests)):
        service.request_seat(
            room_id=room_id,
            actor=guest,
            seat_index=order[index],
        )

    reseated = service.get_room_snapshot(room_id)
    assert len({seat.guest_id for seat in reseated.seats if seat.guest_id is not None}) == count
    assert all(seat.stack == 10_000 for seat in reseated.seats if seat.guest_id is not None)


@given(seat_index=st.integers(min_value=0, max_value=5))
def test_generated_pending_seat_conflicts_are_atomic(seat_index: int) -> None:
    service, code = build_service(approval=True)
    room_id = service.get_room_snapshot_by_code(code).room_id
    first = GuestId("first")
    second = GuestId("second")
    service.join_room(room_code=code, actor=first, nickname="First")
    service.join_room(room_code=code, actor=second, nickname="Second")
    service.request_seat(room_id=room_id, actor=first, seat_index=seat_index)
    before = service.get_room_snapshot(room_id)

    with pytest.raises(RoomSeatAlreadyRequestedError):
        service.request_seat(room_id=room_id, actor=second, seat_index=seat_index)

    assert service.get_room_snapshot(room_id) == before
