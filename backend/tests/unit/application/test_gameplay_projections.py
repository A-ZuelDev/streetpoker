from dataclasses import fields, is_dataclass

from streetpoker.application import (
    GuestId,
    InMemoryRoomRepository,
    RoomId,
    RoomService,
    RoomSettings,
    RoomViewSnapshot,
)
from streetpoker.domain import (
    ActionKind,
    HandSettlementResult,
    HoldemHand,
    HoldemHandSnapshot,
    ParticipationStatus,
    PlayerId,
    SeededRandomSource,
    TableState,
)

HOST = GuestId("host")
ALICE = GuestId("alice")
BOB = GuestId("bob")
SPECTATOR = GuestId("spectator")


def built_room(
    *,
    host_seated: bool = True,
    repository: InMemoryRoomRepository | None = None,
) -> tuple[RoomService, RoomId]:
    player_index = 0

    def player_id() -> PlayerId:
        nonlocal player_index
        player_index += 1
        return PlayerId(f"private-player-{player_index}")

    service = RoomService(
        repository=repository,
        room_id_factory=lambda: RoomId("room"),
        player_id_factory=player_id,
        random_source_factory=lambda: SeededRandomSource(47),
    )
    created = service.create_room(
        actor=HOST,
        nickname="Host",
        settings=RoomSettings(
            room_name="Projection",
            default_starting_stack=1_000,
            seating_approval_required=False,
        ),
    )
    for guest, nickname in ((ALICE, "Alice"), (BOB, "Bob")):
        service.join_room(room_code=created.room_code, actor=guest, nickname=nickname)
    seated = ((HOST, 0), (ALICE, 2), (BOB, 5)) if host_seated else ((ALICE, 2), (BOB, 5))
    for guest, seat in seated:
        service.request_seat(room_id=created.room_id, actor=guest, seat_index=seat)
    return service, created.room_id


def repository_stacks(
    repository: InMemoryRoomRepository,
    room_id: RoomId,
) -> dict[GuestId, int]:
    room = repository.get_by_id(room_id)
    guest_by_player = {member.player_id: guest for guest, member in room.members.items()}
    return {
        guest_by_player[seat.occupant.player_id]: seat.occupant.stack.chips
        for seat in room.table.seats
        if seat.occupant is not None
    }


def assert_active_public_stacks_are_current(
    view: RoomViewSnapshot,
    expected_total: int,
) -> None:
    hand = view.active_hand
    room = view.room
    assert hand is not None
    live_stacks = {player.guest_id: player.current_stack for player in hand.players}
    seated_stacks = {
        seat.guest_id: seat.stack for seat in room.seats if seat.guest_id in live_stacks
    }
    member_stacks = {
        member.guest_id: member.stack for member in room.members if member.guest_id in live_stacks
    }
    assert seated_stacks == live_stacks
    assert member_stacks == live_stacks
    assert hand.pot_chips == sum(player.gross_committed for player in hand.players)
    assert sum(live_stacks.values()) + hand.pot_chips == expected_total


def finish_showdown(service: RoomService, room_id: RoomId) -> None:
    while True:
        view = service.get_room_view(room_id=room_id, viewer=HOST)
        hand = view.active_hand
        if hand is None:
            return
        common = {
            "room_id": room_id,
            "actor": hand.current_actor,
            "hand_number": hand.hand_number,
            "expected_action_sequence": hand.action_sequence,
        }
        if ActionKind.CHECK in hand.legal_actions.kinds:
            service.check(**common)
        else:
            service.call(**common)


def test_active_room_snapshots_use_live_stacks_after_blinds_and_wagers() -> None:
    repository = InMemoryRoomRepository()
    service, room_id = built_room(repository=repository)
    table_stacks = repository_stacks(repository, room_id)

    started = service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=1)

    assert_active_public_stacks_are_current(started, expected_total=3_000)
    assert repository_stacks(repository, room_id) == table_stacks
    assert service.get_room_snapshot(room_id) == started.room
    assert service.get_room_snapshot_by_code(started.room.room_code) == started.room
    hand = started.active_hand
    assert hand is not None

    joined = service.join_room(
        room_code=started.room.room_code,
        actor=SPECTATOR,
        nickname="Spectator",
    )
    assert {
        member.guest_id: member.stack
        for member in joined.members
        if member.guest_id in {HOST, ALICE, BOB}
    } == {player.guest_id: player.current_stack for player in hand.players}

    assert hand.legal_actions.raise_to is not None
    raised = service.raise_to(
        room_id=room_id,
        actor=hand.current_actor,
        hand_number=hand.hand_number,
        expected_action_sequence=hand.action_sequence,
        total=hand.legal_actions.raise_to.minimum_full_to,
    )
    assert_active_public_stacks_are_current(raised, expected_total=3_000)
    hand = raised.active_hand
    assert hand is not None and ActionKind.CALL in hand.legal_actions.kinds
    called = service.call(
        room_id=room_id,
        actor=hand.current_actor,
        hand_number=hand.hand_number,
        expected_action_sequence=hand.action_sequence,
    )

    assert_active_public_stacks_are_current(called, expected_total=3_000)
    assert service.get_room_snapshot(room_id) == called.room
    assert service.get_room_snapshot_by_code(called.room.room_code) == called.room
    assert repository_stacks(repository, room_id) == table_stacks
    assert called.active_hand is not None
    assert {
        player.guest_id for player in called.active_hand.players if player.hole_cards is not None
    } == {hand.current_actor}


def test_active_projection_keeps_sitting_out_nonparticipant_table_stack() -> None:
    repository = InMemoryRoomRepository()
    service, room_id = built_room(repository=repository)
    candidate = repository.get_by_id(room_id).copy()
    candidate.table.sit_out(player_id=candidate.members[BOB].player_id)
    repository.replace(candidate)

    view = service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=1)

    assert_active_public_stacks_are_current(view, expected_total=2_000)
    assert view.active_hand is not None
    assert {player.guest_id for player in view.active_hand.players} == {HOST, ALICE}
    bob_seat = next(seat for seat in view.room.seats if seat.guest_id == BOB)
    bob_member = next(member for member in view.room.members if member.guest_id == BOB)
    assert bob_seat.stack == 1_000
    assert bob_member.stack == 1_000
    raw_room = repository.get_by_id(room_id)
    bob_occupant = raw_room.table.seats[5].occupant
    assert bob_occupant is not None
    assert bob_occupant.stack.chips == 1_000
    assert bob_occupant.status is ParticipationStatus.SITTING_OUT


def test_terminal_settlement_replaces_between_hand_stacks_after_live_projection() -> None:
    repository = InMemoryRoomRepository()
    service, room_id = built_room(repository=repository)
    before = repository_stacks(repository, room_id)
    started = service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=1)
    assert started.active_hand is not None
    hand = started.active_hand
    assert hand.legal_actions.raise_to is not None
    live = service.raise_to(
        room_id=room_id,
        actor=hand.current_actor,
        hand_number=hand.hand_number,
        expected_action_sequence=hand.action_sequence,
        total=hand.legal_actions.raise_to.minimum_full_to,
    )

    assert_active_public_stacks_are_current(live, expected_total=3_000)
    assert repository_stacks(repository, room_id) == before
    finish_showdown(service, room_id)

    completed = service.get_room_view(room_id=room_id, viewer=HOST)
    assert completed.active_hand is None
    assert completed.last_hand is not None
    final_stacks = {player.guest_id: player.final_stack for player in completed.last_hand.players}
    settled_table_stacks = repository_stacks(repository, room_id)
    assert settled_table_stacks == final_stacks
    assert settled_table_stacks != before
    assert sum(settled_table_stacks.values()) == 3_000
    assert service.get_room_snapshot(room_id) == completed.room
    assert service.get_room_snapshot_by_code(completed.room.room_code) == completed.room
    assert {
        seat.guest_id: seat.stack for seat in completed.room.seats if seat.guest_id is not None
    } == final_stacks
    assert {
        member.guest_id: member.stack
        for member in completed.room.members
        if member.guest_id in final_stacks
    } == final_stacks


def test_active_projection_discloses_only_viewers_own_hole_cards() -> None:
    service, room_id = built_room()
    service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=1)
    room_code = service.get_room_snapshot(room_id).room_code
    service.join_room(room_code=room_code, actor=SPECTATOR, nickname="Spectator")

    for viewer in (HOST, ALICE, BOB):
        hand = service.get_room_view(room_id=room_id, viewer=viewer).active_hand
        assert hand is not None
        visible = {player.guest_id for player in hand.players if player.hole_cards is not None}
        assert visible == {viewer}
        assert (
            len(next(player for player in hand.players if player.guest_id == viewer).hole_cards)
            == 2
        )  # type: ignore[arg-type]
    spectator_hand = service.get_room_view(room_id=room_id, viewer=SPECTATOR).active_hand
    assert spectator_hand is not None
    assert all(player.hole_cards is None for player in spectator_hand.players)


def test_unseated_host_receives_no_special_private_visibility() -> None:
    service, room_id = built_room(host_seated=False)

    view = service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=1)

    assert view.active_hand is not None
    assert all(player.hole_cards is None for player in view.active_hand.players)


def test_completed_showdown_keeps_opponents_and_folded_cards_concealed() -> None:
    service, room_id = built_room()
    service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=1)
    finish_showdown(service, room_id)

    for viewer in (HOST, ALICE, BOB):
        completed = service.get_room_view(room_id=room_id, viewer=viewer).last_hand
        assert completed is not None
        assert len(completed.board) == 5
        assert completed.pots
        assert sum(share.chips for pot in completed.pots for share in pot.winners) == sum(
            pot.amount for pot in completed.pots
        )
        visible = {player.guest_id for player in completed.players if player.hole_cards is not None}
        assert visible == {viewer}


def test_projection_contains_no_trusted_or_mutable_domain_aggregate() -> None:
    service, room_id = built_room()
    view = service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=1)

    forbidden = (HoldemHand, HoldemHandSnapshot, HandSettlementResult, TableState, PlayerId)

    def inspect(value: object) -> None:
        assert not isinstance(value, forbidden)
        if is_dataclass(value):
            for field in fields(value):
                inspect(getattr(value, field.name))
        elif isinstance(value, tuple | frozenset):
            for item in value:
                inspect(item)

    inspect(view)
    assert "private-player" not in repr(view)
    assert "burn" not in repr(view).lower()
    assert "deck" not in repr(view).lower()
