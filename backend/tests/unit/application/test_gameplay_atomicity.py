from collections.abc import Callable

import pytest

from streetpoker.application import (
    GuestId,
    InMemoryRoomRepository,
    NotHandParticipantError,
    NotRoomMemberError,
    RoomId,
    RoomService,
    RoomSettings,
    StaleHandVersionError,
)
from streetpoker.application import room_service as room_service_module
from streetpoker.application.gameplay import project_room_view
from streetpoker.domain import (
    HandSettlementResult,
    HoldemHandSnapshot,
    IllegalCheckError,
    PlayerId,
    SeededRandomSource,
    settle_holdem_hand,
)

HOST = GuestId("host")
ALICE = GuestId("alice")
BOB = GuestId("bob")
SPECTATOR = GuestId("spectator")


class FailingRepository(InMemoryRoomRepository):
    def __init__(self) -> None:
        super().__init__()
        self.fail_replace = False

    def replace(self, room):  # type: ignore[no-untyped-def]
        if self.fail_replace:
            raise RuntimeError("forced repository replacement failure")
        super().replace(room)


class FailingSettler:
    def __init__(self) -> None:
        self.fail_next = False

    def __call__(self, snapshot: HoldemHandSnapshot) -> HandSettlementResult:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("forced settlement failure")
        return settle_holdem_hand(snapshot)


def built_service(
    *,
    repository: InMemoryRoomRepository | None = None,
    settler: Callable[[HoldemHandSnapshot], HandSettlementResult] = settle_holdem_hand,
) -> tuple[RoomService, RoomId]:
    index = 0

    def player_id() -> PlayerId:
        nonlocal index
        index += 1
        return PlayerId(f"player-{index}")

    service = RoomService(
        repository=repository,
        room_id_factory=lambda: RoomId("room"),
        player_id_factory=player_id,
        random_source_factory=lambda: SeededRandomSource(71),
        settler=settler,
    )
    created = service.create_room(
        actor=HOST,
        nickname="Host",
        settings=RoomSettings(
            room_name="Atomic",
            default_starting_stack=1_000,
            seating_approval_required=False,
        ),
    )
    for guest, nickname in ((ALICE, "Alice"), (BOB, "Bob"), (SPECTATOR, "Spectator")):
        service.join_room(room_code=created.room_code, actor=guest, nickname=nickname)
    for guest, seat in ((HOST, 0), (ALICE, 2), (BOB, 5)):
        service.request_seat(room_id=created.room_id, actor=guest, seat_index=seat)
    service.start_hand(room_id=created.room_id, actor=HOST, expected_hand_number=1)
    return service, created.room_id


def current(service: RoomService, room_id: RoomId):
    hand = service.get_room_view(room_id=room_id, viewer=HOST).active_hand
    assert hand is not None
    return hand


@pytest.mark.parametrize("wrong_version", [(-1, 0), (2, 0), (1, 99)])
def test_stale_versions_leave_full_view_unchanged(
    wrong_version: tuple[int, int],
) -> None:
    service, room_id = built_service()
    before = service.get_room_view(room_id=room_id, viewer=HOST)
    hand = current(service, room_id)

    with pytest.raises(StaleHandVersionError):
        service.call(
            room_id=room_id,
            actor=hand.current_actor,
            hand_number=wrong_version[0],
            expected_action_sequence=wrong_version[1],
        )

    assert service.get_room_view(room_id=room_id, viewer=HOST) == before


def test_nonparticipant_and_illegal_domain_action_are_atomic() -> None:
    service, room_id = built_service()
    before = service.get_room_view(room_id=room_id, viewer=HOST)

    with pytest.raises(NotHandParticipantError):
        service.call(
            room_id=room_id,
            actor=SPECTATOR,
            hand_number=1,
            expected_action_sequence=0,
        )
    with pytest.raises(NotRoomMemberError):
        service.call(
            room_id=room_id,
            actor=GuestId("outsider"),
            hand_number=1,
            expected_action_sequence=0,
        )
    hand = current(service, room_id)
    with pytest.raises(IllegalCheckError):
        service.check(
            room_id=room_id,
            actor=hand.current_actor,
            hand_number=1,
            expected_action_sequence=0,
        )

    assert service.get_room_view(room_id=room_id, viewer=HOST) == before


def test_projection_failure_discards_successfully_mutated_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, room_id = built_service()
    before = service.get_room_view(room_id=room_id, viewer=HOST)
    hand = current(service, room_id)
    failed = False

    def fail_once(*args):  # type: ignore[no-untyped-def]
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("forced projection failure")
        return project_room_view(*args)

    monkeypatch.setattr(room_service_module, "project_room_view", fail_once)

    with pytest.raises(RuntimeError, match="projection"):
        service.call(
            room_id=room_id,
            actor=hand.current_actor,
            hand_number=1,
            expected_action_sequence=0,
        )

    assert service.get_room_view(room_id=room_id, viewer=HOST) == before


def test_terminal_settlement_failure_restores_pre_action_hand_and_sequence() -> None:
    settler = FailingSettler()
    service, room_id = built_service(settler=settler)
    hand = current(service, room_id)
    service.fold(
        room_id=room_id,
        actor=hand.current_actor,
        hand_number=1,
        expected_action_sequence=0,
    )
    before = service.get_room_view(room_id=room_id, viewer=HOST)
    hand = current(service, room_id)
    settler.fail_next = True

    with pytest.raises(RuntimeError, match="settlement"):
        service.fold(
            room_id=room_id,
            actor=hand.current_actor,
            hand_number=1,
            expected_action_sequence=1,
        )

    assert service.get_room_view(room_id=room_id, viewer=HOST) == before


def test_repository_replacement_failure_does_not_install_candidate_hand() -> None:
    repository = FailingRepository()
    service, room_id = built_service(repository=repository)
    before = service.get_room_view(room_id=room_id, viewer=HOST)
    hand = current(service, room_id)
    repository.fail_replace = True

    with pytest.raises(RuntimeError, match="repository"):
        service.call(
            room_id=room_id,
            actor=hand.current_actor,
            hand_number=1,
            expected_action_sequence=0,
        )

    repository.fail_replace = False
    assert service.get_room_view(room_id=room_id, viewer=HOST) == before
