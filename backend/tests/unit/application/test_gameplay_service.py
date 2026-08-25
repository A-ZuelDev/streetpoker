from collections import deque

import pytest

from streetpoker.application import (
    GuestId,
    HandAlreadyActiveError,
    InMemoryRoomRepository,
    InsufficientEligiblePlayersError,
    NotCurrentActorError,
    NotRoomHostError,
    RoomClosedError,
    RoomId,
    RoomService,
    RoomSettings,
    RoomStatus,
    StaleHandVersionError,
)
from streetpoker.application.room_service import RoomCodeSource
from streetpoker.domain import (
    ActionKind,
    ChipStack,
    ParticipationStatus,
    PlayerId,
    SeededRandomSource,
)

HOST = GuestId("host")
ALICE = GuestId("alice")
BOB = GuestId("bob")


class Codes(RoomCodeSource):
    def __init__(self) -> None:
        self.values = deque(("ABCD2345", "BCDE2345"))

    def next_code(self) -> str:
        return self.values.popleft()


def service_for(
    seed: int = 1,
    repository: InMemoryRoomRepository | None = None,
) -> RoomService:
    next_player = 0

    def player_id() -> PlayerId:
        nonlocal next_player
        next_player += 1
        return PlayerId(f"player-{next_player}")

    return RoomService(
        repository=repository,
        code_source=Codes(),
        room_id_factory=lambda: RoomId("room-1"),
        player_id_factory=player_id,
        random_source_factory=lambda: SeededRandomSource(seed),
    )


def create_and_seat(
    service: RoomService,
    guests: tuple[GuestId, ...] = (HOST, ALICE, BOB),
    *,
    stack: int = 1_000,
    seats: tuple[int, ...] = (0, 2, 5),
) -> RoomId:
    created = service.create_room(
        actor=HOST,
        nickname="Host",
        settings=RoomSettings(
            room_name="Game",
            default_starting_stack=stack,
            seating_approval_required=False,
        ),
    )
    nicknames = {ALICE: "Alice", BOB: "Bob"}
    for guest in guests:
        if guest != HOST:
            service.join_room(
                room_code=created.room_code,
                actor=guest,
                nickname=nicknames[guest],
            )
    for guest, seat in zip(guests, seats, strict=True):
        service.request_seat(room_id=created.room_id, actor=guest, seat_index=seat)
    return created.room_id


def replace_table_stacks(
    repository: InMemoryRoomRepository,
    room_id: RoomId,
    stacks_by_seat: dict[int, int],
) -> None:
    candidate = repository.get_by_id(room_id).copy()
    for seat in candidate.table.seats:
        occupant = seat.occupant
        if occupant is None or seat.index.value not in stacks_by_seat:
            continue
        candidate.table.leave_seat(player_id=occupant.player_id)
        chips = stacks_by_seat[seat.index.value]
        candidate.table.seat_player(
            seat_index=seat.index,
            player_id=occupant.player_id,
            stack=ChipStack(chips),
            status=occupant.status,
        )
    repository.replace(candidate)


def passive_action(service: RoomService, room_id: RoomId, viewer: GuestId = HOST):
    view = service.get_room_view(room_id=room_id, viewer=viewer)
    hand = view.active_hand
    assert hand is not None
    actor = hand.current_actor
    common = {
        "room_id": room_id,
        "actor": actor,
        "hand_number": hand.hand_number,
        "expected_action_sequence": hand.action_sequence,
    }
    if ActionKind.CHECK in hand.legal_actions.kinds:
        return service.check(**common)
    return service.call(**common)


def finish_passively(service: RoomService, room_id: RoomId) -> None:
    while service.get_room_view(room_id=room_id, viewer=HOST).active_hand is not None:
        passive_action(service, room_id)


def test_host_starts_first_sparse_hand_and_number_is_consumed() -> None:
    service = service_for()
    room_id = create_and_seat(service)

    view = service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=1)

    assert view.room.status is RoomStatus.HAND_IN_PROGRESS
    assert view.next_hand_number == 2
    assert view.active_hand is not None
    assert view.active_hand.hand_number == 1
    assert view.active_hand.action_sequence == 0
    assert view.active_hand.button_seat == 0
    assert {player.seat_index for player in view.active_hand.players} == {0, 2, 5}

    with pytest.raises(HandAlreadyActiveError):
        service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=2)


def test_start_requires_host_matching_version_and_two_eligible_players() -> None:
    service = service_for()
    room_id = create_and_seat(service, guests=(HOST,), seats=(0,))
    created = service.get_room_snapshot(room_id)
    service.join_room(room_code=created.room_code, actor=ALICE, nickname="Alice")

    with pytest.raises(NotRoomHostError):
        service.start_hand(room_id=room_id, actor=ALICE, expected_hand_number=1)
    with pytest.raises(StaleHandVersionError):
        service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=2)
    with pytest.raises(InsufficientEligiblePlayersError):
        service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=1)

    assert service.get_room_view(room_id=room_id, viewer=HOST).next_hand_number == 1


def test_closed_room_cannot_start_and_pending_request_survives_start() -> None:
    closed_service = service_for()
    closed_room = create_and_seat(closed_service, guests=(HOST,), seats=(0,))
    closed_service.close_room(room_id=closed_room, actor=HOST)
    with pytest.raises(RoomClosedError):
        closed_service.start_hand(
            room_id=closed_room,
            actor=HOST,
            expected_hand_number=1,
        )

    service = service_for()
    created = service.create_room(
        actor=HOST,
        nickname="Host",
        settings=RoomSettings(room_name="Pending", seating_approval_required=True),
    )
    for guest, nickname in ((ALICE, "Alice"), (BOB, "Bob")):
        service.join_room(room_code=created.room_code, actor=guest, nickname=nickname)
    for guest, seat in ((HOST, 0), (ALICE, 2)):
        service.request_seat(room_id=created.room_id, actor=guest, seat_index=seat)
        service.approve_seat_request(room_id=created.room_id, actor=HOST, target=guest)
    pending = service.request_seat(room_id=created.room_id, actor=BOB, seat_index=5)

    started = service.start_hand(
        room_id=created.room_id,
        actor=HOST,
        expected_hand_number=1,
    )

    assert started.room.seat_requests == pending.seat_requests


def test_action_sequence_increments_once_and_duplicate_is_rejected() -> None:
    service = service_for()
    room_id = create_and_seat(service)
    started = service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=1)
    assert started.active_hand is not None
    actor = started.active_hand.current_actor

    acted = service.call(
        room_id=room_id,
        actor=actor,
        hand_number=1,
        expected_action_sequence=0,
    )
    assert acted.active_hand is not None
    assert acted.active_hand.action_sequence == 1

    with pytest.raises(StaleHandVersionError):
        service.call(
            room_id=room_id,
            actor=actor,
            hand_number=1,
            expected_action_sequence=0,
        )
    assert service.get_room_view(room_id=room_id, viewer=HOST) == acted


def test_wrong_participant_cannot_act_for_current_guest() -> None:
    service = service_for()
    room_id = create_and_seat(service)
    view = service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=1)
    assert view.active_hand is not None
    wrong = next(
        player.guest_id
        for player in view.active_hand.players
        if player.guest_id != view.active_hand.current_actor
    )

    with pytest.raises(NotCurrentActorError):
        service.call(
            room_id=room_id,
            actor=wrong,
            hand_number=1,
            expected_action_sequence=0,
        )


def test_fold_terminal_settles_table_and_returns_open() -> None:
    service = service_for()
    room_id = create_and_seat(service)
    service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=1)

    for _ in range(2):
        view = service.get_room_view(room_id=room_id, viewer=HOST)
        assert view.active_hand is not None
        service.fold(
            room_id=room_id,
            actor=view.active_hand.current_actor,
            hand_number=1,
            expected_action_sequence=view.active_hand.action_sequence,
        )

    completed = service.get_room_view(room_id=room_id, viewer=HOST)
    assert completed.room.status is RoomStatus.OPEN
    assert completed.active_hand is None
    assert completed.last_hand is not None
    assert completed.last_hand.final_action_sequence == 2
    assert sum(seat.stack or 0 for seat in completed.room.seats) == 3_000
    assert any(player.returned_excess > 0 for player in completed.last_hand.players)


def test_passive_showdown_settles_and_carries_stacks_into_next_hand() -> None:
    service = service_for(seed=9)
    room_id = create_and_seat(service)
    service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=1)
    finish_passively(service, room_id)
    after_first = service.get_room_view(room_id=room_id, viewer=HOST)
    stacks = tuple(seat.stack for seat in after_first.room.seats)
    assert after_first.last_hand is not None

    second = service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=2)

    assert second.next_hand_number == 3
    assert second.active_hand is not None
    assert second.active_hand.hand_number == 2
    assert second.active_hand.button_seat == 2
    assert tuple(seat.stack for seat in second.room.seats) == stacks


def test_tied_showdown_projects_all_winner_shares_without_private_cards() -> None:
    service = service_for(seed=63)
    room_id = create_and_seat(service, seats=(0, 1, 2))
    service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=1)
    finish_passively(service, room_id)

    completed = service.get_room_view(room_id=room_id, viewer=HOST).last_hand

    assert completed is not None
    assert any(len(pot.winners) > 1 for pot in completed.pots)
    assert all(player.hole_cards is None for player in completed.players if player.guest_id != HOST)


def test_three_consecutive_hands_advance_button_once_each() -> None:
    service = service_for()
    room_id = create_and_seat(service)
    buttons: list[int] = []

    for hand_number in (1, 2, 3):
        view = service.start_hand(
            room_id=room_id,
            actor=HOST,
            expected_hand_number=hand_number,
        )
        assert view.active_hand is not None
        buttons.append(view.active_hand.button_seat)
        while service.get_room_view(room_id=room_id, viewer=HOST).active_hand is not None:
            current = service.get_room_view(room_id=room_id, viewer=HOST).active_hand
            assert current is not None
            service.fold(
                room_id=room_id,
                actor=current.current_actor,
                hand_number=hand_number,
                expected_action_sequence=current.action_sequence,
            )

    assert buttons == [0, 2, 5]


def test_immediate_terminal_start_consumes_hand_number() -> None:
    repository = InMemoryRoomRepository()
    service = service_for(seed=2, repository=repository)
    room_id = create_and_seat(
        service,
        guests=(HOST, ALICE),
        stack=100,
        seats=(0, 4),
    )
    candidate = repository.get_by_id(room_id).copy()
    for seat in candidate.table.seats:
        occupant = seat.occupant
        if occupant is None:
            continue
        candidate.table.leave_seat(player_id=occupant.player_id)
        candidate.table.seat_player(
            seat_index=seat.index,
            player_id=occupant.player_id,
            stack=ChipStack(30),
            status=occupant.status,
        )
    repository.replace(candidate)

    view = service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=1)

    assert view.room.status is RoomStatus.OPEN
    assert view.active_hand is None
    assert view.last_hand is not None
    assert view.last_hand.hand_number == 1
    assert view.next_hand_number == 2
    assert any(seat.stack == 0 for seat in view.room.seats if seat.guest_id is not None)
    busted = next(player for player in view.last_hand.players if player.final_stack == 0)
    assert (
        next(
            member for member in view.room.members if member.guest_id == busted.guest_id
        ).status.value
        == "seated"
    )
    zero_occupant = next(
        seat.occupant
        for seat in repository.get_by_id(room_id).table.seats
        if seat.occupant is not None and seat.occupant.stack.chips == 0
    )
    assert zero_occupant.status is ParticipationStatus.SITTING_OUT
    with pytest.raises(InsufficientEligiblePlayersError):
        service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=2)
    assert service.get_room_view(room_id=room_id, viewer=HOST).next_hand_number == 2


def test_side_pot_settlement_reconciles_varied_all_in_stacks() -> None:
    repository = InMemoryRoomRepository()
    service = service_for(seed=13, repository=repository)
    room_id = create_and_seat(service)
    replace_table_stacks(repository, room_id, {0: 100, 2: 200, 5: 300})
    view = service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=1)
    assert view.active_hand is not None
    service.call(
        room_id=room_id,
        actor=view.active_hand.current_actor,
        hand_number=1,
        expected_action_sequence=0,
    )
    hand = service.get_room_view(room_id=room_id, viewer=HOST).active_hand
    assert hand is not None and hand.legal_actions.raise_to is not None
    service.raise_to(
        room_id=room_id,
        actor=hand.current_actor,
        hand_number=1,
        expected_action_sequence=1,
        total=200,
    )
    hand = service.get_room_view(room_id=room_id, viewer=HOST).active_hand
    assert hand is not None
    completed = service.call(
        room_id=room_id,
        actor=hand.current_actor,
        hand_number=1,
        expected_action_sequence=2,
    )

    assert completed.active_hand is None
    assert completed.last_hand is not None
    assert [pot.amount for pot in completed.last_hand.pots] == [300, 200]
    assert sum(seat.stack or 0 for seat in completed.room.seats) == 600


def test_vacated_button_anchor_and_multiway_to_heads_up_progression() -> None:
    service = service_for()
    room_id = create_and_seat(service)
    first = service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=1)
    assert first.active_hand is not None and first.active_hand.button_seat == 0
    while service.get_room_view(room_id=room_id, viewer=HOST).active_hand is not None:
        hand = service.get_room_view(room_id=room_id, viewer=HOST).active_hand
        assert hand is not None
        service.fold(
            room_id=room_id,
            actor=hand.current_actor,
            hand_number=1,
            expected_action_sequence=hand.action_sequence,
        )
    service.stand_up(room_id=room_id, actor=HOST)

    second = service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=2)

    assert second.active_hand is not None
    assert second.active_hand.button_seat == 2
    assert second.active_hand.small_blind_seat == 2
    assert second.active_hand.big_blind_seat == 5


def test_new_player_between_hands_can_receive_next_clockwise_button() -> None:
    service = service_for()
    room_id = create_and_seat(service, guests=(HOST, ALICE), seats=(0, 5))
    service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=1)
    while service.get_room_view(room_id=room_id, viewer=HOST).active_hand is not None:
        hand = service.get_room_view(room_id=room_id, viewer=HOST).active_hand
        assert hand is not None
        service.fold(
            room_id=room_id,
            actor=hand.current_actor,
            hand_number=1,
            expected_action_sequence=hand.action_sequence,
        )
    room_code = service.get_room_snapshot(room_id).room_code
    service.join_room(room_code=room_code, actor=BOB, nickname="Bob")
    service.request_seat(room_id=room_id, actor=BOB, seat_index=2)

    second = service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=2)

    assert second.active_hand is not None
    assert second.active_hand.button_seat == 2


@pytest.mark.parametrize("zero_stack", [False, True])
def test_sitting_out_or_busted_prior_button_is_retained_as_skipped_anchor(
    zero_stack: bool,
) -> None:
    repository = InMemoryRoomRepository()
    service = service_for(repository=repository)
    room_id = create_and_seat(service)
    service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=1)
    while service.get_room_view(room_id=room_id, viewer=HOST).active_hand is not None:
        hand = service.get_room_view(room_id=room_id, viewer=HOST).active_hand
        assert hand is not None
        service.fold(
            room_id=room_id,
            actor=hand.current_actor,
            hand_number=1,
            expected_action_sequence=hand.action_sequence,
        )
    candidate = repository.get_by_id(room_id).copy()
    prior_button = candidate.table.button_position
    assert prior_button is not None
    occupant = candidate.table.seat_at(prior_button).occupant
    assert occupant is not None
    if zero_stack:
        candidate.table.leave_seat(player_id=occupant.player_id)
        candidate.table.seat_player(
            seat_index=prior_button,
            player_id=occupant.player_id,
            stack=ChipStack(0),
            status=ParticipationStatus.SITTING_OUT,
        )
    else:
        candidate.table.sit_out(player_id=occupant.player_id)
    repository.replace(candidate)

    second = service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=2)

    assert second.active_hand is not None
    assert second.active_hand.button_seat == 2


def test_heads_up_continuation_alternates_button() -> None:
    service = service_for()
    room_id = create_and_seat(service, guests=(HOST, ALICE), seats=(0, 4))
    buttons: list[int] = []
    for hand_number in (1, 2, 3):
        view = service.start_hand(
            room_id=room_id,
            actor=HOST,
            expected_hand_number=hand_number,
        )
        assert view.active_hand is not None
        buttons.append(view.active_hand.button_seat)
        if hand_number == 2:
            with pytest.raises(StaleHandVersionError):
                service.fold(
                    room_id=room_id,
                    actor=view.active_hand.current_actor,
                    hand_number=1,
                    expected_action_sequence=0,
                )
        service.fold(
            room_id=room_id,
            actor=view.active_hand.current_actor,
            hand_number=hand_number,
            expected_action_sequence=0,
        )

    assert buttons == [0, 4, 0]


def test_raise_bet_and_check_commands_delegate_to_domain() -> None:
    service = service_for()
    room_id = create_and_seat(service)
    view = service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=1)
    assert view.active_hand is not None
    hand = view.active_hand

    raised = service.raise_to(
        room_id=room_id,
        actor=hand.current_actor,
        hand_number=1,
        expected_action_sequence=0,
        total=hand.legal_actions.raise_to.minimum_full_to,  # type: ignore[union-attr]
    )
    assert raised.active_hand is not None
    while raised.active_hand.phase.value == "preflop":
        raised = passive_action(service, room_id)
        if raised.active_hand is None:
            pytest.fail("hand unexpectedly completed before the flop")
    hand = raised.active_hand
    assert hand.legal_actions.bet_to is not None
    bet = service.bet_to(
        room_id=room_id,
        actor=hand.current_actor,
        hand_number=1,
        expected_action_sequence=hand.action_sequence,
        total=hand.legal_actions.bet_to.minimum_full_to,
    )
    assert bet.active_hand is not None
    while bet.active_hand.legal_actions.amount_to_call:
        bet = passive_action(service, room_id)
        if bet.active_hand is None:
            pytest.fail("hand unexpectedly completed")
    checked = passive_action(service, room_id)
    assert checked.active_hand is not None
