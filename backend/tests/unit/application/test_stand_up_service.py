"""Pure room lifecycle integration for the optional Stand-Up side game."""

from collections.abc import Callable
from dataclasses import replace

import pytest

from streetpoker.application import (
    GuestId,
    InMemoryRoomRepository,
    RoomId,
    RoomService,
    RoomSettings,
)
from streetpoker.application import room_service as room_service_module
from streetpoker.application.errors import (
    GameplaySettlementError,
    InvalidRoomStateError,
    NoActiveHandError,
)
from streetpoker.application.gameplay import project_room_view
from streetpoker.application.rooms import _Room
from streetpoker.domain import (
    ActionKind,
    ChipStack,
    ParticipationStatus,
    PlayerId,
    SeededRandomSource,
    StandUpCancellation,
    StandUpCancelReason,
    StandUpResolution,
    StandUpRound,
    StandUpTransfer,
)

HOST = GuestId("host")
ALICE = GuestId("alice")
BOB = GuestId("bob")
OUTSIDER = GuestId("outsider")


def make_room(
    *,
    penalty: int | None = 10,
    guests: tuple[GuestId, ...] = (HOST, ALICE, BOB),
    seats: tuple[int, ...] = (0, 2, 5),
    stack: int = 1_000,
    seed: int = 1,
) -> tuple[RoomService, InMemoryRoomRepository, RoomId]:
    repository = InMemoryRoomRepository()
    next_player = 0

    def player_id() -> PlayerId:
        nonlocal next_player
        next_player += 1
        return PlayerId(f"player-{next_player}")

    service = RoomService(
        repository=repository,
        room_id_factory=lambda: RoomId("room"),
        player_id_factory=player_id,
        random_source_factory=lambda: SeededRandomSource(seed),
    )
    created = service.create_room(
        actor=HOST,
        nickname="Host",
        settings=RoomSettings(
            room_name="Side Game",
            default_starting_stack=stack,
            seating_approval_required=False,
        ),
        stand_up_penalty_per_recipient_chips=penalty,
    )
    for guest in guests:
        if guest != HOST:
            service.join_room(
                room_code=created.room_code,
                actor=guest,
                nickname=guest.value.title(),
            )
    for guest, seat in zip(guests, seats, strict=True):
        service.request_seat(room_id=created.room_id, actor=guest, seat_index=seat)
    return service, repository, created.room_id


def start(service: RoomService, room_id: RoomId, number: int) -> None:
    service.start_hand(room_id=room_id, actor=HOST, expected_hand_number=number)


def finish_with_winner(service: RoomService, room_id: RoomId, winner: GuestId) -> None:
    while True:
        hand = service.get_room_view(room_id=room_id, viewer=HOST).active_hand
        if hand is None:
            return
        assert hand.current_actor is not None
        args = {
            "room_id": room_id,
            "actor": hand.current_actor,
            "hand_number": hand.hand_number,
            "expected_action_sequence": hand.action_sequence,
        }
        if hand.current_actor != winner:
            service.fold(**args)
        elif ActionKind.CHECK in hand.legal_actions.kinds:
            service.check(**args)
        else:
            service.call(**args)


def finish_passively(service: RoomService, room_id: RoomId) -> None:
    while True:
        hand = service.get_room_view(room_id=room_id, viewer=HOST).active_hand
        if hand is None:
            return
        assert hand.current_actor is not None
        args = {
            "room_id": room_id,
            "actor": hand.current_actor,
            "hand_number": hand.hand_number,
            "expected_action_sequence": hand.action_sequence,
        }
        if ActionKind.CHECK in hand.legal_actions.kinds:
            service.check(**args)
        else:
            service.call(**args)


def prepare_terminal_fold(service: RoomService, room_id: RoomId) -> tuple[GuestId, int]:
    hand = service.get_room_view(room_id=room_id, viewer=HOST).active_hand
    assert hand is not None and hand.current_actor == HOST
    service.call(
        room_id=room_id,
        actor=HOST,
        hand_number=hand.hand_number,
        expected_action_sequence=hand.action_sequence,
    )
    hand = service.get_room_view(room_id=room_id, viewer=HOST).active_hand
    assert hand is not None and hand.current_actor == ALICE
    return ALICE, hand.action_sequence


def player_for(repository: InMemoryRoomRepository, room_id: RoomId, guest: GuestId) -> PlayerId:
    return repository.get_by_id(room_id).members[guest].player_id


def table_stacks(repository: InMemoryRoomRepository, room_id: RoomId) -> dict[PlayerId, int]:
    return {
        seat.occupant.player_id: seat.occupant.stack.chips
        for seat in repository.get_by_id(room_id).table.seats
        if seat.occupant is not None
    }


def active_round(repository: InMemoryRoomRepository, room_id: RoomId) -> StandUpRound:
    value = repository.get_by_id(room_id).stand_up_round
    assert isinstance(value, StandUpRound)
    return value


def test_disabled_room_preserves_poker_only_settlement() -> None:
    service, repository, room_id = make_room(penalty=None)
    start(service, room_id, 1)
    finish_with_winner(service, room_id, HOST)
    room = repository.get_by_id(room_id)
    view = service.get_room_view(room_id=room_id, viewer=HOST)
    assert room.stand_up_round is None and room.last_stand_up_result is None
    assert view.last_hand is not None
    assert {player.guest_id: player.final_stack for player in view.last_hand.players} == {
        seat.guest_id: seat.stack for seat in view.room.seats if seat.guest_id is not None
    }


def test_round_freezes_hand_player_ids_seats_and_penalty_without_public_projection() -> None:
    service, repository, room_id = make_room(penalty=13)
    start(service, room_id, 1)
    round_state = active_round(repository, room_id)
    assert round_state.start_hand_number == 1
    assert round_state.penalty_per_recipient_chips == 13
    assert [(item.player_id, item.seat_index.value) for item in round_state.participants] == [
        (player_for(repository, room_id, HOST), 0),
        (player_for(repository, room_id, ALICE), 2),
        (player_for(repository, room_id, BOB), 5),
    ]
    public = service.get_room_view(room_id=room_id, viewer=ALICE)
    assert "stand_up" not in repr(public)
    assert all(item.player_id.value not in repr(public) for item in round_state.participants)
    assert public.active_hand is not None
    assert all(
        player.hole_cards is None
        for player in public.active_hand.players
        if player.guest_id != ALICE
    )


def test_sole_main_winners_progress_then_resolve_with_separate_conserved_transfers() -> None:
    service, repository, room_id = make_room(penalty=10)
    start(service, room_id, 1)
    finish_with_winner(service, room_id, HOST)
    first = active_round(repository, room_id)
    assert first.cleared_player_ids == {player_for(repository, room_id, HOST)}
    assert first.at_risk_player_ids == {
        player_for(repository, room_id, ALICE),
        player_for(repository, room_id, BOB),
    }
    start(service, room_id, 2)
    assert active_round(repository, room_id) == first
    finish_with_winner(service, room_id, ALICE)
    room = repository.get_by_id(room_id)
    resolved = room.last_stand_up_result
    assert room.stand_up_round is None
    assert isinstance(resolved, StandUpResolution)
    assert resolved.squid == player_for(repository, room_id, BOB)
    assert resolved.actual_total == resolved.intended_total == 20
    assert {item.to_player_id: item.chips for item in resolved.transfers} == {
        player_for(repository, room_id, HOST): 10,
        player_for(repository, room_id, ALICE): 10,
    }
    view = service.get_room_view(room_id=room_id, viewer=HOST)
    assert view.last_hand is not None
    poker_stacks = {
        player_for(repository, room_id, player.guest_id): player.final_stack
        for player in view.last_hand.players
    }
    final = table_stacks(repository, room_id)
    assert final[resolved.squid] == poker_stacks[resolved.squid] - 20
    for transfer in resolved.transfers:
        assert final[transfer.to_player_id] == poker_stacks[transfer.to_player_id] + 10
    assert sum(final.values()) == sum(poker_stacks.values()) == 3_000
    assert all(item.player_id.value not in repr(view) for item in resolved.participants)
    start(service, room_id, 3)
    next_round = active_round(repository, room_id)
    assert next_round.start_hand_number == 3
    assert next_round.cleared_player_ids == frozenset()


def test_uncontested_winner_and_outsider_main_pot_win() -> None:
    service, repository, room_id = make_room()
    start(service, room_id, 1)
    finish_with_winner(service, room_id, HOST)
    first = active_round(repository, room_id)
    service.join_room(
        room_code=service.get_room_snapshot(room_id).room_code,
        actor=OUTSIDER,
        nickname="Outsider",
    )
    service.request_seat(room_id=room_id, actor=OUTSIDER, seat_index=3)
    start(service, room_id, 2)
    assert {item.player_id for item in active_round(repository, room_id).participants} == {
        item.player_id for item in first.participants
    }
    finish_with_winner(service, room_id, OUTSIDER)
    after = active_round(repository, room_id)
    assert after.cleared_player_ids == first.cleared_player_ids
    assert after.last_processed_hand_number == 2


def test_chopped_main_pot_clears_nobody() -> None:
    service, repository, room_id = make_room(seed=63, seats=(0, 1, 2))
    start(service, room_id, 1)
    finish_passively(service, room_id)
    view = service.get_room_view(room_id=room_id, viewer=HOST)
    assert view.last_hand is not None
    assert len(view.last_hand.pots[0].winners) > 1
    assert active_round(repository, room_id).cleared_player_ids == frozenset()


def test_settlement_adapter_uses_only_main_pot_winners_with_side_pots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository, room_id = make_room(seed=1)
    candidate = repository.get_by_id(room_id).copy()
    for seat in candidate.table.seats:
        occupant = seat.occupant
        if occupant is None:
            continue
        candidate.table.leave_seat(player_id=occupant.player_id)
        candidate.table.seat_player(
            seat_index=seat.index,
            player_id=occupant.player_id,
            stack=ChipStack({0: 100, 2: 200, 5: 300}[seat.index.value]),
            status=ParticipationStatus.SITTING_IN,
        )
    repository.replace(candidate)
    original = RoomService._stand_up_outcome
    observed: list[tuple[PlayerId, ...]] = []

    def capture(active, settlement):  # type: ignore[no-untyped-def]
        result = original(active, settlement)
        observed.append(result.main_pot_winners)
        return result

    monkeypatch.setattr(RoomService, "_stand_up_outcome", staticmethod(capture))
    start(service, room_id, 1)
    hand = service.get_room_view(room_id=room_id, viewer=HOST).active_hand
    assert hand is not None and hand.current_actor is not None
    service.call(
        room_id=room_id, actor=hand.current_actor, hand_number=1, expected_action_sequence=0
    )
    hand = service.get_room_view(room_id=room_id, viewer=HOST).active_hand
    assert hand is not None and hand.current_actor is not None
    service.raise_to(
        room_id=room_id,
        actor=hand.current_actor,
        hand_number=1,
        expected_action_sequence=1,
        total=200,
    )
    hand = service.get_room_view(room_id=room_id, viewer=HOST).active_hand
    assert hand is not None and hand.current_actor is not None
    service.call(
        room_id=room_id, actor=hand.current_actor, hand_number=1, expected_action_sequence=2
    )
    view = service.get_room_view(room_id=room_id, viewer=HOST)
    assert view.last_hand is not None
    assert len(view.last_hand.pots) == 2
    assert view.last_hand.pots[0].winners[0].guest_id == HOST
    assert view.last_hand.pots[1].winners[0].guest_id == BOB
    assert observed == [(player_for(repository, room_id, HOST),)]
    assert isinstance(repository.get_by_id(room_id).last_stand_up_result, StandUpCancellation)


def test_partial_payout_uses_post_poker_squid_stack() -> None:
    service, repository, room_id = make_room(guests=(HOST, ALICE), seats=(0, 4), penalty=5_000)
    start(service, room_id, 1)
    finish_with_winner(service, room_id, HOST)
    result = repository.get_by_id(room_id).last_stand_up_result
    assert isinstance(result, StandUpResolution)
    view = service.get_room_view(room_id=room_id, viewer=HOST)
    assert view.last_hand is not None
    squid_poker_stack = next(
        player.final_stack for player in view.last_hand.players if player.guest_id == ALICE
    )
    assert squid_poker_stack > 0
    assert result.squid_available_stack == squid_poker_stack
    assert result.actual_total == squid_poker_stack
    assert table_stacks(repository, room_id)[result.squid] == 0
    assert result.shortfall == result.intended_total - squid_poker_stack


def test_bust_cancels_before_side_game_resolution() -> None:
    service, repository, room_id = make_room(guests=(HOST, ALICE), seats=(0, 4))
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
            status=ParticipationStatus.SITTING_IN,
        )
    repository.replace(candidate)
    start(service, room_id, 1)
    result = repository.get_by_id(room_id).last_stand_up_result
    assert isinstance(result, StandUpCancellation)
    assert result.reason is StandUpCancelReason.PARTICIPANT_BUSTED
    assert repository.get_by_id(room_id).stand_up_round is None
    assert sum(table_stacks(repository, room_id).values()) == 60


@pytest.mark.parametrize(
    ("operation", "reason"),
    [
        ("stand", StandUpCancelReason.PARTICIPANT_VACATED_SEAT),
        ("leave", StandUpCancelReason.PARTICIPANT_LEFT),
        ("kick", StandUpCancelReason.PARTICIPANT_KICKED),
        ("close", StandUpCancelReason.ROOM_CLOSED),
    ],
)
def test_cohort_exit_cancels_without_squid_or_payout(
    operation: str, reason: StandUpCancelReason
) -> None:
    service, repository, room_id = make_room()
    start(service, room_id, 1)
    finish_with_winner(service, room_id, HOST)
    before = table_stacks(repository, room_id)
    if operation == "stand":
        service.stand_up(room_id=room_id, actor=ALICE)
    elif operation == "leave":
        service.leave_room(room_id=room_id, actor=ALICE)
    elif operation == "kick":
        service.kick_member(room_id=room_id, actor=HOST, target=ALICE)
    else:
        service.close_room(room_id=room_id, actor=HOST)
    room = repository.get_by_id(room_id)
    assert room.stand_up_round is None
    assert isinstance(room.last_stand_up_result, StandUpCancellation)
    assert room.last_stand_up_result.reason is reason
    assert sum(table_stacks(repository, room_id).values()) <= sum(before.values())


def test_refresh_without_room_command_does_not_cancel_or_reset_round() -> None:
    service, repository, room_id = make_room()
    start(service, room_id, 1)
    before = active_round(repository, room_id)
    deadline = service.current_turn_deadline(room_id)
    service.get_room_view(room_id=room_id, viewer=ALICE)
    service.get_room_view(room_id=room_id, viewer=ALICE)
    assert active_round(repository, room_id) == before
    assert service.current_turn_deadline(room_id) == deadline


def test_deferred_grace_stand_after_settlement_cancels_only_unresolved_round() -> None:
    service, repository, room_id = make_room()
    start(service, room_id, 1)
    finish_with_winner(service, room_id, HOST)
    assert active_round(repository, room_id).last_processed_hand_number == 1
    service.stand_up(room_id=room_id, actor=ALICE)
    result = repository.get_by_id(room_id).last_stand_up_result
    assert isinstance(result, StandUpCancellation)
    assert result.reason is StandUpCancelReason.PARTICIPANT_VACATED_SEAT


def test_deferred_grace_stand_after_resolution_keeps_payout_result() -> None:
    service, repository, room_id = make_room(guests=(HOST, ALICE), seats=(0, 4))
    start(service, room_id, 1)
    finish_with_winner(service, room_id, HOST)
    resolved = repository.get_by_id(room_id).last_stand_up_result
    assert isinstance(resolved, StandUpResolution)
    service.stand_up(room_id=room_id, actor=ALICE)
    assert repository.get_by_id(room_id).last_stand_up_result == resolved


def test_duplicate_terminal_action_does_not_pay_twice() -> None:
    service, repository, room_id = make_room(guests=(HOST, ALICE), seats=(0, 4))
    start(service, room_id, 1)
    actor, sequence = prepare_terminal_fold(service, room_id)
    service.fold(
        room_id=room_id,
        actor=actor,
        hand_number=1,
        expected_action_sequence=sequence,
    )
    before = table_stacks(repository, room_id)
    result = repository.get_by_id(room_id).last_stand_up_result
    with pytest.raises(NoActiveHandError):
        service.fold(
            room_id=room_id,
            actor=actor,
            hand_number=1,
            expected_action_sequence=sequence,
        )
    assert table_stacks(repository, room_id) == before
    assert repository.get_by_id(room_id).last_stand_up_result == result


@pytest.mark.parametrize("failure", ["adapter", "transfer", "projection", "validation"])
def test_failed_side_game_candidate_rolls_back_poker_and_transfer(
    failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, room_id = make_room(guests=(HOST, ALICE), seats=(0, 4))
    start(service, room_id, 1)
    actor, sequence = prepare_terminal_fold(service, room_id)
    before_room = repository.get_by_id(room_id)
    before_view = service.get_room_view(room_id=room_id, viewer=HOST)
    original_projection: Callable[..., object] = project_room_view

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("forced side-game integration failure")

    if failure == "adapter":
        monkeypatch.setattr(RoomService, "_stand_up_outcome", staticmethod(fail))
    elif failure == "transfer":
        monkeypatch.setattr(RoomService, "_apply_stand_up_transfers", staticmethod(fail))
    elif failure == "projection":
        monkeypatch.setattr(room_service_module, "project_room_view", fail)
    else:
        original_validate = _Room.validate

        def fail_candidate(self: _Room) -> None:
            if self.last_stand_up_result is not None:
                raise InvalidRoomStateError("forced side-game candidate rejection")
            original_validate(self)

        monkeypatch.setattr(_Room, "validate", fail_candidate)
    with pytest.raises((RuntimeError, InvalidRoomStateError)):
        service.fold(
            room_id=room_id,
            actor=actor,
            hand_number=1,
            expected_action_sequence=sequence,
        )
    if failure == "projection":
        monkeypatch.setattr(room_service_module, "project_room_view", original_projection)
    assert repository.get_by_id(room_id) is before_room
    assert service.get_room_view(room_id=room_id, viewer=HOST) == before_view


def test_invalid_transfer_source_is_rejected_before_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository, room_id = make_room(guests=(HOST, ALICE), seats=(0, 4))
    start(service, room_id, 1)
    actor, sequence = prepare_terminal_fold(service, room_id)
    before = repository.get_by_id(room_id)
    original = StandUpRound.apply_hand

    def forge_transfer(self, outcome, **kwargs):  # type: ignore[no-untyped-def]
        result = original(self, outcome, **kwargs)
        assert isinstance(result, StandUpResolution)
        recipient = result.transfers[0].to_player_id
        object.__setattr__(
            result,
            "transfers",
            (StandUpTransfer(recipient, result.squid, 1),),
        )
        return result

    monkeypatch.setattr(StandUpRound, "apply_hand", forge_transfer)
    with pytest.raises(GameplaySettlementError):
        service.fold(
            room_id=room_id,
            actor=actor,
            hand_number=1,
            expected_action_sequence=sequence,
        )
    assert repository.get_by_id(room_id) is before


def test_failure_after_candidate_side_transfer_discards_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository, room_id = make_room(guests=(HOST, ALICE), seats=(0, 4))
    start(service, room_id, 1)
    actor, sequence = prepare_terminal_fold(service, room_id)
    before = repository.get_by_id(room_id)
    original = RoomService._apply_stand_up_transfers

    def fail_after_transfer(candidate: _Room, result: StandUpResolution) -> None:
        original(candidate, result)
        raise GameplaySettlementError("forced failure after candidate transfer")

    monkeypatch.setattr(RoomService, "_apply_stand_up_transfers", staticmethod(fail_after_transfer))
    with pytest.raises(GameplaySettlementError):
        service.fold(
            room_id=room_id,
            actor=actor,
            hand_number=1,
            expected_action_sequence=sequence,
        )
    assert repository.get_by_id(room_id) is before


def test_invalid_internal_penalty_is_rejected_at_room_creation() -> None:
    for bad in (0, -1, True):
        with pytest.raises(InvalidRoomStateError):
            make_room(penalty=bad)


def test_room_validation_rejects_corrupted_cohort_seat_without_commit() -> None:
    service, repository, room_id = make_room()
    start(service, room_id, 1)
    original = repository.get_by_id(room_id)
    candidate = original.copy()
    participant = active_round(repository, room_id).participants[0]
    candidate.table.leave_seat(player_id=participant.player_id)
    with pytest.raises(InvalidRoomStateError):
        repository.replace(candidate)
    assert repository.get_by_id(room_id) is original


def test_room_validation_rejects_previous_result_overlapping_active_round() -> None:
    service, repository, room_id = make_room(guests=(HOST, ALICE), seats=(0, 4))
    start(service, room_id, 1)
    finish_with_winner(service, room_id, HOST)
    start(service, room_id, 2)
    original = repository.get_by_id(room_id)
    candidate = original.copy()
    previous = candidate.last_stand_up_result
    assert isinstance(previous, StandUpResolution)
    assert candidate.stand_up_round is not None
    candidate.last_stand_up_result = replace(
        previous, hand_number=candidate.stand_up_round.start_hand_number
    )
    with pytest.raises(InvalidRoomStateError, match="precede the active round"):
        repository.replace(candidate)
    assert repository.get_by_id(room_id) is original
