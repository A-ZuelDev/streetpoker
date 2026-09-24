from collections import deque
from dataclasses import FrozenInstanceError

import pytest

from streetpoker.application import (
    MAX_CHIP_STACK,
    MAX_INITIAL_STACK,
    ActiveHandMutationError,
    GuestId,
    InMemoryRoomRepository,
    InvalidStackAdjustmentError,
    NotRoomHostError,
    RoomChipLimitError,
    RoomId,
    RoomMemberStatus,
    RoomService,
    RoomSettings,
    StackAdjustmentCommandConflictError,
    StackAdjustmentType,
    StaleHandVersionError,
)
from streetpoker.application.room_service import RoomCodeSource
from streetpoker.domain import ActionKind, PlayerId, SeededRandomSource

HOST = GuestId("host")
ALICE = GuestId("alice")


class Codes(RoomCodeSource):
    def __init__(self) -> None:
        self._values = deque(("ABCD2345",))

    def next_code(self) -> str:
        return self._values.popleft()


def build_room(*, stack: int = 1_000, seat_alice: bool = True) -> tuple[RoomService, RoomId]:
    next_player = 0

    def player_id() -> PlayerId:
        nonlocal next_player
        next_player += 1
        return PlayerId(f"player-{next_player}")

    service = RoomService(
        repository=InMemoryRoomRepository(),
        code_source=Codes(),
        room_id_factory=lambda: RoomId("room"),
        player_id_factory=player_id,
        random_source_factory=lambda: SeededRandomSource(7),
    )
    created = service.create_room(
        actor=HOST,
        nickname="Host",
        settings=RoomSettings(
            room_name="Ledger game",
            default_starting_stack=stack,
            seating_approval_required=False,
        ),
    )
    service.request_seat(room_id=created.room_id, actor=HOST, seat_index=0)
    if seat_alice:
        service.join_room(room_code=created.room_code, actor=ALICE, nickname="Alice")
        service.request_seat(room_id=created.room_id, actor=ALICE, seat_index=2)
    return service, created.room_id


def adjust(
    service: RoomService,
    room_id: RoomId,
    *,
    target: GuestId = ALICE,
    adjustment_type: StackAdjustmentType = StackAdjustmentType.REBUY,
    amount: int = 500,
    command_id: str = "adjust-1",
    expected_next_hand_number: int = 1,
    expected_ledger_sequence: int = 0,
    reason: str | None = None,
    actor: GuestId = HOST,
):
    return service.adjust_player_stack(
        room_id=room_id,
        actor=actor,
        target=target,
        adjustment_type=adjustment_type,
        amount=amount,
        reason=reason,
        command_id=command_id,
        expected_next_hand_number=expected_next_hand_number,
        expected_ledger_sequence=expected_ledger_sequence,
    )


def finish_passively(service: RoomService, room_id: RoomId) -> None:
    while (view := service.get_room_view(room_id=room_id, viewer=HOST)).active_hand:
        hand = view.active_hand
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


def summary_by_name(service: RoomService, room_id: RoomId):
    snapshot = service.get_room_snapshot(room_id)
    return {summary.nickname: summary for summary in snapshot.session.players}


def test_adjustments_are_ledgered_external_chips_and_exact_replay_is_idempotent() -> None:
    service, room_id = build_room()

    first = adjust(service, room_id, reason="  First   top-up  ")
    alice = next(item for item in first.room.session.players if item.nickname == "Alice")
    assert (alice.current_stack, alice.external_added, alice.external_removed, alice.poker_net) == (
        1_500,
        500,
        0,
        0,
    )
    assert first.room.session.adjustments[0].target_nickname == "Alice"
    assert first.room.session.adjustments[0].target_seat_index == 2
    assert first.room.session.adjustments[0].delta == 500
    assert first.room.session.adjustments[0].reason == "First top-up"
    with pytest.raises(FrozenInstanceError):
        first.room.session.adjustments[0].delta = 1  # type: ignore[misc]

    replayed = adjust(service, room_id, reason="First top-up")
    assert replayed.room.session == first.room.session

    with pytest.raises(StackAdjustmentCommandConflictError):
        adjust(service, room_id, amount=501, command_id="adjust-1")
    assert service.get_room_snapshot(room_id).session == first.room.session

    adjust(
        service,
        room_id,
        adjustment_type=StackAdjustmentType.CASH_OUT,
        amount=200,
        command_id="adjust-2",
        expected_ledger_sequence=1,
    )
    corrected = adjust(
        service,
        room_id,
        adjustment_type=StackAdjustmentType.CORRECTION,
        amount=-100,
        command_id="adjust-3",
        expected_ledger_sequence=2,
    )
    alice = next(item for item in corrected.room.session.players if item.nickname == "Alice")
    assert (alice.current_stack, alice.external_added, alice.external_removed, alice.poker_net) == (
        1_200,
        500,
        300,
        0,
    )
    assert [entry.sequence for entry in corrected.room.session.adjustments] == [1, 2, 3]


def test_adjustment_authority_active_hand_and_cas_failures_are_atomic() -> None:
    service, room_id = build_room()
    before = service.get_room_snapshot(room_id)
    with pytest.raises(NotRoomHostError):
        adjust(service, room_id, actor=ALICE)
    with pytest.raises(StaleHandVersionError):
        adjust(service, room_id, expected_ledger_sequence=1)
    with pytest.raises(StaleHandVersionError):
        adjust(service, room_id, expected_next_hand_number=2)
    assert service.get_room_snapshot(room_id) == before

    service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=1)
    active = service.get_room_snapshot(room_id)
    with pytest.raises(ActiveHandMutationError):
        adjust(
            service,
            room_id,
            expected_next_hand_number=2,
        )
    assert service.get_room_snapshot(room_id) == active


@pytest.mark.parametrize(
    ("adjustment_type", "amount"),
    [
        (StackAdjustmentType.REBUY, 0),
        (StackAdjustmentType.REBUY, -1),
        (StackAdjustmentType.CASH_OUT, 0),
        (StackAdjustmentType.CASH_OUT, -1),
        (StackAdjustmentType.CASH_OUT, 1_001),
        (StackAdjustmentType.CORRECTION, 0),
        (StackAdjustmentType.CORRECTION, -1_001),
        (StackAdjustmentType.CORRECTION, MAX_CHIP_STACK + 1),
    ],
)
def test_invalid_amounts_and_resulting_stack_bounds_are_atomic(
    adjustment_type: StackAdjustmentType,
    amount: int,
) -> None:
    service, room_id = build_room()
    before = service.get_room_snapshot(room_id)
    with pytest.raises(InvalidStackAdjustmentError):
        adjust(service, room_id, adjustment_type=adjustment_type, amount=amount)
    assert service.get_room_snapshot(room_id) == before


def test_safe_upper_bound_and_zero_stack_keep_seat_occupancy_deterministic() -> None:
    service, room_id = build_room(stack=MAX_INITIAL_STACK, seat_alice=False)
    adjust(
        service,
        room_id,
        target=HOST,
        amount=MAX_CHIP_STACK - MAX_INITIAL_STACK,
    )
    with pytest.raises(InvalidStackAdjustmentError):
        adjust(
            service,
            room_id,
            target=HOST,
            amount=1,
            command_id="overflow",
            expected_ledger_sequence=1,
        )

    service, room_id = build_room()
    emptied = adjust(
        service,
        room_id,
        adjustment_type=StackAdjustmentType.CASH_OUT,
        amount=1_000,
    )
    alice_member = next(member for member in emptied.room.members if member.guest_id == ALICE)
    alice_seat = next(seat for seat in emptied.room.seats if seat.guest_id == ALICE)
    assert alice_member.status is RoomMemberStatus.SEATED
    assert alice_member.stack == alice_seat.stack == 0

    service.stand_up(room_id=room_id, actor=ALICE)
    standing = adjust(
        service,
        room_id,
        adjustment_type=StackAdjustmentType.REBUY,
        amount=250,
        command_id="standing-top-up",
        expected_ledger_sequence=1,
    )
    alice = next(item for item in standing.room.session.players if item.nickname == "Alice")
    assert alice.seat_index is None
    assert alice.current_stack == 250
    assert alice.poker_net == 0
    assert any(member.guest_id == ALICE for member in standing.room.members)


def test_first_session_stack_reports_safe_chip_limit_after_large_adjustment() -> None:
    service, room_id = build_room(seat_alice=False)
    adjust(
        service,
        room_id,
        target=HOST,
        amount=MAX_CHIP_STACK - 1_000,
    )
    room_code = service.get_room_snapshot(room_id).room_code
    service.join_room(room_code=room_code, actor=ALICE, nickname="Alice")
    before = service.get_room_snapshot(room_id)

    with pytest.raises(RoomChipLimitError):
        service.request_seat(room_id=room_id, actor=ALICE, seat_index=2)

    assert service.get_room_snapshot(room_id) == before


def test_hand_settlement_updates_independent_poker_net_without_counting_rebuy() -> None:
    service, room_id = build_room()
    adjust(service, room_id, amount=500)
    service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=1)
    finish_passively(service, room_id)

    summaries = summary_by_name(service, room_id)
    assert set(summaries) == {"Host", "Alice"}
    assert summaries["Host"].hands_played == summaries["Alice"].hands_played == 1
    assert summaries["Alice"].poker_net == (summaries["Alice"].current_stack - 1_000 - 500)
    assert summaries["Host"].poker_net == summaries["Host"].current_stack - 1_000
    assert summaries["Host"].poker_net + summaries["Alice"].poker_net == 0

    reconnected = service.get_room_view(room_id=room_id, viewer=ALICE)
    assert reconnected.room.session == service.get_room_snapshot(room_id).session
    assert all(
        not hasattr(item, "guest_id") and not hasattr(item, "player_id")
        for item in (*reconnected.room.session.players, *reconnected.room.session.adjustments)
    )
