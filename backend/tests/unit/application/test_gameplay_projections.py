from dataclasses import fields, is_dataclass

from streetpoker.application import (
    GuestId,
    RoomId,
    RoomService,
    RoomSettings,
)
from streetpoker.domain import (
    ActionKind,
    HandSettlementResult,
    HoldemHand,
    HoldemHandSnapshot,
    PlayerId,
    SeededRandomSource,
    TableState,
)

HOST = GuestId("host")
ALICE = GuestId("alice")
BOB = GuestId("bob")
SPECTATOR = GuestId("spectator")


def built_room(*, host_seated: bool = True) -> tuple[RoomService, RoomId]:
    player_index = 0

    def player_id() -> PlayerId:
        nonlocal player_index
        player_index += 1
        return PlayerId(f"private-player-{player_index}")

    service = RoomService(
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
