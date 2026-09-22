import hashlib
import hmac
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields, replace

import pytest

from streetpoker.application import (
    ActiveHandMutationError,
    CannotKickHostError,
    DuplicateMembershipError,
    DuplicateNicknameError,
    DuplicateRoomCodeError,
    DuplicateRoomIdError,
    DuplicateSeatRequestError,
    GuestId,
    HostCannotLeaveRoomError,
    InMemoryRoomRepository,
    InvalidRoomPasswordError,
    InvalidRoomSeatError,
    InvalidRoomSettingsError,
    InvalidRoomStateError,
    MemberAlreadySeatedError,
    MemberNotFoundError,
    MemberNotSeatedError,
    NotRoomHostError,
    NotRoomMemberError,
    Pbkdf2PasswordHasher,
    RoomClosedError,
    RoomCreationCollisionError,
    RoomId,
    RoomMemberStatus,
    RoomNotFoundError,
    RoomSeatAlreadyRequestedError,
    RoomSeatOccupiedError,
    RoomService,
    RoomSettings,
    RoomSettingsUpdate,
    RoomSnapshot,
    RoomStatus,
    SeatRequestNotFoundError,
    WrongRoomPasswordError,
)
from streetpoker.application.room_service import PasswordHasher, RoomCodeSource
from streetpoker.application.rooms import _PasswordRecord, _Room
from streetpoker.domain import ChipStack, ParticipationStatus, PlayerId, SeatIndex, TableState

HOST = GuestId("guest-host")
ALICE = GuestId("guest-alice")
BOB = GuestId("guest-bob")
OUTSIDER = GuestId("guest-outsider")
LONE_SURROGATE_PASSWORD = "\ud800"


class SequenceCodeSource(RoomCodeSource):
    def __init__(self, *codes: str) -> None:
        self._codes = deque(codes)

    def next_code(self) -> str:
        return self._codes.popleft()


class FastPasswordHasher(PasswordHasher):
    def hash_password(self, password: str) -> _PasswordRecord:
        if not isinstance(password, str) or not password or len(password) > 128:
            raise InvalidRoomPasswordError("Invalid test password.")
        encoded = password.encode()
        if len(encoded) > 256:
            raise InvalidRoomPasswordError("Invalid test password.")
        return _PasswordRecord(b"test-salt", b"test:" + encoded, 1)

    def verify_password(self, password: str, record: _PasswordRecord) -> bool:
        if not isinstance(password, str) or not password or len(password) > 128:
            raise InvalidRoomPasswordError("Invalid test password.")
        return hmac.compare_digest(b"test:" + password.encode(), record.digest)


class IdSequence:
    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._next = 0

    def room_id(self) -> RoomId:
        value = RoomId(f"{self._prefix}-room-{self._next}")
        self._next += 1
        return value

    def player_id(self) -> PlayerId:
        value = PlayerId(f"{self._prefix}-player-{self._next}")
        self._next += 1
        return value


def build_service(
    *,
    codes: tuple[str, ...] = ("ABCDEFGH",),
    repository: InMemoryRoomRepository | None = None,
    password_hasher: PasswordHasher | None = None,
) -> tuple[RoomService, InMemoryRoomRepository]:
    room_repository = repository or InMemoryRoomRepository()
    ids = IdSequence("test")
    return (
        RoomService(
            repository=room_repository,
            code_source=SequenceCodeSource(*codes),
            password_hasher=password_hasher or FastPasswordHasher(),
            room_id_factory=ids.room_id,
            player_id_factory=ids.player_id,
        ),
        room_repository,
    )


def create_room(
    service: RoomService,
    *,
    approval: bool = True,
    password: str | None = None,
    stack: int = 10_000,
) -> RoomSnapshot:
    return service.create_room(
        actor=HOST,
        nickname="Host",
        settings=RoomSettings(
            room_name=" Friday Game ",
            default_starting_stack=stack,
            seating_approval_required=approval,
        ),
        password=password,
    )


def join(
    service: RoomService,
    room_code: str,
    guest: GuestId = ALICE,
    nickname: str = "Alice",
    password: str | None = None,
) -> RoomSnapshot:
    return service.join_room(
        room_code=room_code,
        actor=guest,
        nickname=nickname,
        password=password,
    )


def assert_unchanged_after_error(
    service: RoomService,
    room_id: RoomId,
    error_type: type[Exception],
    operation: Callable[[], object],
) -> None:
    before = service.get_room_snapshot(room_id)
    with pytest.raises(error_type):
        operation()
    assert service.get_room_snapshot(room_id) == before


def test_creation_builds_host_member_defaults_and_deterministic_identifiers() -> None:
    service, _ = build_service(codes=("abcd2345",))

    snapshot = create_room(service)

    assert snapshot.room_id == RoomId("test-room-0")
    assert snapshot.room_code == "ABCD2345"
    assert snapshot.host_guest_id == HOST
    assert snapshot.status is RoomStatus.OPEN
    assert snapshot.settings.room_name == "Friday Game"
    assert snapshot.settings.small_blind == 50
    assert snapshot.settings.big_blind == 100
    assert snapshot.settings.default_starting_stack == 10_000
    assert snapshot.settings.max_seats == 6
    assert [(member.guest_id, member.is_host) for member in snapshot.members] == [(HOST, True)]
    assert all(seat.guest_id is None for seat in snapshot.seats)


def test_creation_retries_code_collisions_and_reserves_closed_codes() -> None:
    repository = InMemoryRoomRepository()
    service, _ = build_service(
        codes=("ABCDEFGH", "ABCDEFGH", "BCDEFGHJ"),
        repository=repository,
    )
    first = create_room(service)
    service.close_room(room_id=first.room_id, actor=HOST)

    second = service.create_room(
        actor=ALICE,
        nickname="Alice",
        settings=RoomSettings(room_name="Second"),
    )

    assert first.room_code == "ABCDEFGH"
    assert second.room_code == "BCDEFGHJ"
    assert repository.contains_code("abcdefgh")


def test_creation_collision_retries_are_bounded() -> None:
    repository = InMemoryRoomRepository()
    service, _ = build_service(
        codes=("ABCDEFGH",) + ("ABCDEFGH",) * 32,
        repository=repository,
    )
    create_room(service)

    with pytest.raises(RoomCreationCollisionError):
        service.create_room(
            actor=ALICE,
            nickname="Alice",
            settings=RoomSettings(room_name="Second"),
        )


def test_join_normalizes_nickname_and_code_and_preserves_join_order() -> None:
    service, _ = build_service()
    created = create_room(service)

    joined = join(service, "  abcdefgh ", nickname="  \uff21lice\tSmith ")
    joined = join(service, created.room_code, BOB, "Bob")

    assert [member.nickname for member in joined.members] == ["Host", "Alice Smith", "Bob"]
    assert [member.guest_id for member in joined.members] == [HOST, ALICE, BOB]


@pytest.mark.parametrize(
    ("guest", "nickname", "error_type"),
    [
        (ALICE, "Another", DuplicateMembershipError),
        (BOB, "  \uff41LICE  ", DuplicateNicknameError),
    ],
)
def test_duplicate_membership_and_casefolded_nickname_are_atomic(
    guest: GuestId,
    nickname: str,
    error_type: type[Exception],
) -> None:
    service, _ = build_service()
    created = create_room(service)
    join(service, created.room_code)

    assert_unchanged_after_error(
        service,
        created.room_id,
        error_type,
        lambda: join(service, created.room_code, guest, nickname),
    )


def test_password_protected_join_and_password_secrecy() -> None:
    service, repository = build_service()
    created = create_room(service, password="correct horse")

    assert created.settings.password_protected
    assert "correct horse" not in repr(created)
    assert {field.name for field in fields(created.settings)} == {
        "room_name",
        "small_blind",
        "big_blind",
        "default_starting_stack",
        "action_time_ms",
        "timebank_total_ms",
        "timebank_refill_amount_ms",
        "timebank_refill_every_hands",
        "seating_approval_required",
        "stand_up_enabled",
        "stand_up_penalty_per_recipient_chips",
        "max_seats",
        "password_protected",
    }
    assert repository.get_by_id(created.room_id).password_record is not None

    for password in (None, "wrong"):
        assert_unchanged_after_error(
            service,
            created.room_id,
            WrongRoomPasswordError,
            lambda password=password: join(
                service,
                created.room_code,
                password=password,
            ),
        )
    joined = join(service, created.room_code, password="correct horse")
    assert len(joined.members) == 2


def test_unprotected_room_ignores_an_unneeded_supplied_password() -> None:
    service, _ = build_service()
    created = create_room(service)

    assert len(join(service, created.room_code, password="unused").members) == 2


def test_production_password_hasher_uses_random_salts_and_verifies() -> None:
    hasher = Pbkdf2PasswordHasher(iterations=1)
    first = hasher.hash_password("room secret")
    second = hasher.hash_password("room secret")

    assert len(first.salt) == 16
    assert first.salt != second.salt
    assert first.digest == hashlib.pbkdf2_hmac(
        "sha256", b"room secret", first.salt, first.iterations
    )
    assert hasher.verify_password("room secret", first)
    assert not hasher.verify_password("other", first)


@pytest.mark.parametrize("password", ["", "x" * 129, "😀" * 65])
def test_password_input_is_bounded(password: str) -> None:
    hasher = Pbkdf2PasswordHasher(iterations=1)

    with pytest.raises(InvalidRoomPasswordError):
        hasher.hash_password(password)


def test_unencodable_password_rejects_room_creation_without_reserving_indexes() -> None:
    repository = InMemoryRoomRepository()
    service, _ = build_service(
        repository=repository,
        password_hasher=Pbkdf2PasswordHasher(iterations=1),
    )

    with pytest.raises(InvalidRoomPasswordError) as error:
        create_room(service, password=LONE_SURROGATE_PASSWORD)

    assert LONE_SURROGATE_PASSWORD not in str(error.value)
    assert not repository.contains_code("ABCDEFGH")


def test_unencodable_password_rejects_join_without_room_mutation() -> None:
    service, repository = build_service(password_hasher=Pbkdf2PasswordHasher(iterations=1))
    created = create_room(service, password="valid password")
    before = service.get_room_snapshot(created.room_id)

    with pytest.raises(InvalidRoomPasswordError) as error:
        join(service, created.room_code, password=LONE_SURROGATE_PASSWORD)

    assert LONE_SURROGATE_PASSWORD not in str(error.value)
    assert service.get_room_snapshot(created.room_id) == before
    assert service.get_room_snapshot_by_code(created.room_code) == before
    assert repository.contains_code(created.room_code)


def test_unencodable_password_rejects_update_without_room_mutation() -> None:
    service, repository = build_service(password_hasher=Pbkdf2PasswordHasher(iterations=1))
    created = create_room(service)
    before = service.get_room_snapshot(created.room_id)

    with pytest.raises(InvalidRoomPasswordError) as error:
        service.update_room_settings(
            room_id=created.room_id,
            actor=HOST,
            update=RoomSettingsUpdate(password=LONE_SURROGATE_PASSWORD),
        )

    assert LONE_SURROGATE_PASSWORD not in str(error.value)
    assert service.get_room_snapshot(created.room_id) == before
    assert service.get_room_snapshot_by_code(created.room_code) == before
    assert repository.contains_code(created.room_code)


def test_only_host_can_approve_reject_kick_update_or_close() -> None:
    service, _ = build_service()
    created = create_room(service)
    join(service, created.room_code)
    service.request_seat(room_id=created.room_id, actor=ALICE, seat_index=2)

    operations = (
        lambda: service.approve_seat_request(room_id=created.room_id, actor=ALICE, target=ALICE),
        lambda: service.reject_seat_request(room_id=created.room_id, actor=ALICE, target=ALICE),
        lambda: service.kick_member(room_id=created.room_id, actor=ALICE, target=HOST),
        lambda: service.update_room_settings(
            room_id=created.room_id,
            actor=ALICE,
            update=RoomSettingsUpdate(room_name="Nope"),
        ),
        lambda: service.close_room(room_id=created.room_id, actor=ALICE),
    )
    for operation in operations:
        assert_unchanged_after_error(service, created.room_id, NotRoomHostError, operation)


def test_nonmember_cannot_mutate_room() -> None:
    service, _ = build_service()
    created = create_room(service)

    operations = (
        lambda: service.leave_room(room_id=created.room_id, actor=OUTSIDER),
        lambda: service.request_seat(room_id=created.room_id, actor=OUTSIDER, seat_index=0),
        lambda: service.stand_up(room_id=created.room_id, actor=OUTSIDER),
        lambda: service.close_room(room_id=created.room_id, actor=OUTSIDER),
    )
    for operation in operations:
        assert_unchanged_after_error(service, created.room_id, NotRoomMemberError, operation)


def test_host_cannot_leave_or_kick_self() -> None:
    service, _ = build_service()
    created = create_room(service)

    assert_unchanged_after_error(
        service,
        created.room_id,
        HostCannotLeaveRoomError,
        lambda: service.leave_room(room_id=created.room_id, actor=HOST),
    )
    assert_unchanged_after_error(
        service,
        created.room_id,
        CannotKickHostError,
        lambda: service.kick_member(room_id=created.room_id, actor=HOST, target=HOST),
    )


def test_seat_request_approval_and_rejection_lifecycle() -> None:
    service, _ = build_service()
    created = create_room(service)
    join(service, created.room_code)
    join(service, created.room_code, BOB, "Bob")

    requested = service.request_seat(
        room_id=created.room_id,
        actor=ALICE,
        seat_index=4,
    )
    assert [(request.guest_id, request.seat_index) for request in requested.seat_requests] == [
        (ALICE, 4)
    ]
    approved = service.approve_seat_request(
        room_id=created.room_id,
        actor=HOST,
        target=ALICE,
    )
    assert approved.seat_requests == ()
    assert approved.seats[4].guest_id == ALICE
    assert approved.seats[4].stack == 10_000
    assert next(member for member in approved.members if member.guest_id == ALICE).status is (
        RoomMemberStatus.SEATED
    )

    service.request_seat(room_id=created.room_id, actor=BOB, seat_index=1)
    rejected = service.reject_seat_request(
        room_id=created.room_id,
        actor=HOST,
        target=BOB,
    )
    assert rejected.seat_requests == ()
    assert rejected.seats[1].guest_id is None


def test_host_uses_the_same_request_and_approval_path() -> None:
    service, _ = build_service()
    created = create_room(service)

    requested = service.request_seat(
        room_id=created.room_id,
        actor=HOST,
        seat_index=3,
    )
    assert requested.seat_requests[0].guest_id == HOST
    seated = service.approve_seat_request(
        room_id=created.room_id,
        actor=HOST,
        target=HOST,
    )
    assert seated.seats[3].guest_id == HOST
    assert seated.seat_requests == ()


def test_automatic_seating_assigns_immediately_only_while_open() -> None:
    service, _ = build_service()
    created = create_room(service, approval=False)
    join(service, created.room_code)
    join(service, created.room_code, BOB, "Bob")

    seated = service.request_seat(
        room_id=created.room_id,
        actor=ALICE,
        seat_index=3,
    )
    assert seated.seat_requests == ()
    assert seated.seats[3].guest_id == ALICE

    service.stand_up(room_id=created.room_id, actor=ALICE)
    service.request_seat(room_id=created.room_id, actor=HOST, seat_index=0)
    service.request_seat(room_id=created.room_id, actor=BOB, seat_index=1)
    service.start_hand(room_id=created.room_id, actor=HOST, expected_hand_number=1)
    assert_unchanged_after_error(
        service,
        created.room_id,
        ActiveHandMutationError,
        lambda: service.request_seat(
            room_id=created.room_id,
            actor=ALICE,
            seat_index=3,
        ),
    )


def test_seat_request_conflicts_and_invalid_seats_are_atomic() -> None:
    service, _ = build_service()
    created = create_room(service)
    join(service, created.room_code)
    join(service, created.room_code, BOB, "Bob")
    service.request_seat(room_id=created.room_id, actor=ALICE, seat_index=2)

    assert_unchanged_after_error(
        service,
        created.room_id,
        DuplicateSeatRequestError,
        lambda: service.request_seat(room_id=created.room_id, actor=ALICE, seat_index=3),
    )
    assert_unchanged_after_error(
        service,
        created.room_id,
        RoomSeatAlreadyRequestedError,
        lambda: service.request_seat(room_id=created.room_id, actor=BOB, seat_index=2),
    )
    for invalid in (-1, 6, True, "2"):
        assert_unchanged_after_error(
            service,
            created.room_id,
            InvalidRoomSeatError,
            lambda invalid=invalid: service.request_seat(
                room_id=created.room_id,
                actor=BOB,
                seat_index=invalid,  # type: ignore[arg-type]
            ),
        )


def test_occupied_seat_and_double_seating_are_rejected() -> None:
    service, _ = build_service()
    created = create_room(service, approval=False)
    join(service, created.room_code)
    join(service, created.room_code, BOB, "Bob")
    service.request_seat(room_id=created.room_id, actor=ALICE, seat_index=5)

    assert_unchanged_after_error(
        service,
        created.room_id,
        RoomSeatOccupiedError,
        lambda: service.request_seat(room_id=created.room_id, actor=BOB, seat_index=5),
    )
    assert_unchanged_after_error(
        service,
        created.room_id,
        MemberAlreadySeatedError,
        lambda: service.request_seat(room_id=created.room_id, actor=ALICE, seat_index=1),
    )


def test_missing_seat_request_and_unseated_stand_are_atomic() -> None:
    service, _ = build_service()
    created = create_room(service)
    join(service, created.room_code)

    assert_unchanged_after_error(
        service,
        created.room_id,
        SeatRequestNotFoundError,
        lambda: service.approve_seat_request(room_id=created.room_id, actor=HOST, target=ALICE),
    )
    assert_unchanged_after_error(
        service,
        created.room_id,
        MemberNotSeatedError,
        lambda: service.stand_up(room_id=created.room_id, actor=ALICE),
    )


def test_standing_retains_stack_and_reseating_ignores_new_default() -> None:
    service, _ = build_service()
    created = create_room(service, approval=False, stack=8_000)
    join(service, created.room_code)
    service.request_seat(room_id=created.room_id, actor=ALICE, seat_index=0)

    updated = service.update_room_settings(
        room_id=created.room_id,
        actor=HOST,
        update=RoomSettingsUpdate(default_starting_stack=20_000),
    )
    assert updated.seats[0].stack == 8_000

    standing = service.stand_up(room_id=created.room_id, actor=ALICE)
    alice = next(member for member in standing.members if member.guest_id == ALICE)
    assert alice.status is RoomMemberStatus.IN_ROOM
    assert alice.stack == 8_000
    assert standing.seats[0].guest_id is None

    reseated = service.request_seat(
        room_id=created.room_id,
        actor=ALICE,
        seat_index=5,
    )
    assert reseated.seats[5].stack == 8_000


def test_zero_stack_member_stands_and_reseats_sitting_out_without_reset() -> None:
    service, repository = build_service()
    created = create_room(service, approval=False)
    join(service, created.room_code)
    service.request_seat(room_id=created.room_id, actor=ALICE, seat_index=0)

    candidate = repository.get_by_id(created.room_id).copy()
    member = candidate.members[ALICE]
    candidate.table.leave_seat(player_id=member.player_id)
    candidate.table.seat_player(
        seat_index=SeatIndex(0),
        player_id=member.player_id,
        stack=ChipStack(0),
        status=ParticipationStatus.SITTING_OUT,
    )
    candidate.validate()
    repository.replace(candidate)

    standing = service.stand_up(room_id=created.room_id, actor=ALICE)
    standing_alice = next(member for member in standing.members if member.guest_id == ALICE)
    assert standing_alice.status is RoomMemberStatus.IN_ROOM
    assert standing_alice.stack == 0
    assert repository.get_by_id(created.room_id).members[ALICE].retained_stack == ChipStack(0)

    reseated = service.request_seat(
        room_id=created.room_id,
        actor=ALICE,
        seat_index=4,
    )
    assert reseated.seats[4].guest_id == ALICE
    assert reseated.seats[4].stack == 0
    reseated_room = repository.get_by_id(created.room_id)
    occupant = reseated_room.table.seat_at(SeatIndex(4)).occupant
    assert occupant is not None
    assert occupant.stack == ChipStack(0)
    assert occupant.status is ParticipationStatus.SITTING_OUT
    assert reseated_room.members[ALICE].retained_stack is None
    reseated_room.validate()


def test_leave_and_kick_vacate_seats_discard_stack_and_allow_fresh_rejoin() -> None:
    service, repository = build_service(codes=("ABCDEFGH",))
    created = create_room(service, approval=False, stack=7_000)
    join(service, created.room_code)
    join(service, created.room_code, BOB, "Bob")
    service.request_seat(room_id=created.room_id, actor=ALICE, seat_index=1)
    service.request_seat(room_id=created.room_id, actor=BOB, seat_index=4)
    prior_alice_player = repository.get_by_id(created.room_id).members[ALICE].player_id

    left = service.leave_room(room_id=created.room_id, actor=ALICE)
    assert ALICE not in {member.guest_id for member in left.members}
    assert left.seats[1].guest_id is None
    kicked = service.kick_member(room_id=created.room_id, actor=HOST, target=BOB)
    assert BOB not in {member.guest_id for member in kicked.members}
    assert kicked.seats[4].guest_id is None

    service.update_room_settings(
        room_id=created.room_id,
        actor=HOST,
        update=RoomSettingsUpdate(default_starting_stack=12_000),
    )
    join(service, created.room_code)
    new_alice_player = repository.get_by_id(created.room_id).members[ALICE].player_id
    reseated = service.request_seat(
        room_id=created.room_id,
        actor=ALICE,
        seat_index=2,
    )
    assert new_alice_player != prior_alice_player
    assert reseated.seats[2].stack == 12_000


def test_kicking_unknown_member_is_atomic() -> None:
    service, _ = build_service()
    created = create_room(service)

    assert_unchanged_after_error(
        service,
        created.room_id,
        MemberNotFoundError,
        lambda: service.kick_member(room_id=created.room_id, actor=HOST, target=ALICE),
    )


def test_leaving_or_kicking_unseated_members_removes_pending_requests() -> None:
    service, _ = build_service()
    created = create_room(service)
    join(service, created.room_code)
    join(service, created.room_code, BOB, "Bob")
    service.request_seat(room_id=created.room_id, actor=ALICE, seat_index=1)
    service.request_seat(room_id=created.room_id, actor=BOB, seat_index=4)

    left = service.leave_room(room_id=created.room_id, actor=ALICE)
    assert {request.guest_id for request in left.seat_requests} == {BOB}
    kicked = service.kick_member(room_id=created.room_id, actor=HOST, target=BOB)
    assert kicked.seat_requests == ()


def test_settings_update_normalizes_and_validates_complete_candidate() -> None:
    service, _ = build_service()
    created = create_room(service)

    updated = service.update_room_settings(
        room_id=created.room_id,
        actor=HOST,
        update=RoomSettingsUpdate(
            room_name="  New\tName ",
            small_blind=100,
            big_blind=200,
            default_starting_stack=30_000,
            seating_approval_required=False,
        ),
    )
    assert updated.settings.room_name == "New Name"
    assert updated.settings.small_blind == 100
    assert updated.settings.big_blind == 200
    assert updated.settings.default_starting_stack == 30_000
    assert not updated.settings.seating_approval_required

    assert_unchanged_after_error(
        service,
        created.room_id,
        InvalidRoomSettingsError,
        lambda: service.update_room_settings(
            room_id=created.room_id,
            actor=HOST,
            update=RoomSettingsUpdate(big_blind=40),
        ),
    )


def test_password_can_be_added_replaced_and_removed_without_snapshot_material() -> None:
    service, _ = build_service()
    created = create_room(service)

    protected = service.update_room_settings(
        room_id=created.room_id,
        actor=HOST,
        update=RoomSettingsUpdate(password="first"),
    )
    assert protected.settings.password_protected
    assert "first" not in repr(protected)
    service.update_room_settings(
        room_id=created.room_id,
        actor=HOST,
        update=RoomSettingsUpdate(password="second"),
    )
    with pytest.raises(WrongRoomPasswordError):
        join(service, created.room_code, password="first")
    join(service, created.room_code, password="second")

    unprotected = service.update_room_settings(
        room_id=created.room_id,
        actor=HOST,
        update=RoomSettingsUpdate(password=None),
    )
    assert not unprotected.settings.password_protected

    assert_unchanged_after_error(
        service,
        created.room_id,
        InvalidRoomPasswordError,
        lambda: service.update_room_settings(
            room_id=created.room_id,
            actor=HOST,
            update=RoomSettingsUpdate(password=""),
        ),
    )


def test_approval_toggle_preserves_pending_requests_and_does_not_seat() -> None:
    service, _ = build_service()
    created = create_room(service)
    join(service, created.room_code)
    pending = service.request_seat(
        room_id=created.room_id,
        actor=ALICE,
        seat_index=3,
    )

    updated = service.update_room_settings(
        room_id=created.room_id,
        actor=HOST,
        update=RoomSettingsUpdate(seating_approval_required=False),
    )

    assert updated.seat_requests == pending.seat_requests
    assert updated.seats == pending.seats
    assert all(seat.guest_id is None for seat in updated.seats)


def test_active_hand_allows_safe_lobby_changes_but_blocks_unsafe_mutations() -> None:
    service, _ = build_service()
    created = create_room(service)
    join(service, created.room_code)
    join(service, created.room_code, BOB, "Bob")
    service.request_seat(room_id=created.room_id, actor=ALICE, seat_index=1)
    service.approve_seat_request(room_id=created.room_id, actor=HOST, target=ALICE)
    service.request_seat(room_id=created.room_id, actor=HOST, seat_index=0)
    service.approve_seat_request(room_id=created.room_id, actor=HOST, target=HOST)
    service.start_hand(room_id=created.room_id, actor=HOST, expected_hand_number=1)

    joined = service.join_room(
        room_code=created.room_code,
        actor=OUTSIDER,
        nickname="Observer",
    )
    assert OUTSIDER in {member.guest_id for member in joined.members}
    requested = service.request_seat(room_id=created.room_id, actor=BOB, seat_index=4)
    assert requested.seat_requests[0].guest_id == BOB
    renamed = service.update_room_settings(
        room_id=created.room_id,
        actor=HOST,
        update=RoomSettingsUpdate(
            room_name="During Hand",
            seating_approval_required=False,
            password="new password",
        ),
    )
    assert renamed.settings.room_name == "During Hand"
    assert renamed.seat_requests == requested.seat_requests
    rejected = service.reject_seat_request(
        room_id=created.room_id,
        actor=HOST,
        target=BOB,
    )
    assert rejected.seat_requests == ()
    service.leave_room(room_id=created.room_id, actor=BOB)
    service.kick_member(room_id=created.room_id, actor=HOST, target=OUTSIDER)

    blocked = (
        lambda: service.stand_up(room_id=created.room_id, actor=ALICE),
        lambda: service.leave_room(room_id=created.room_id, actor=ALICE),
        lambda: service.kick_member(room_id=created.room_id, actor=HOST, target=ALICE),
        lambda: service.update_room_settings(
            room_id=created.room_id,
            actor=HOST,
            update=RoomSettingsUpdate(big_blind=200),
        ),
        lambda: service.close_room(room_id=created.room_id, actor=HOST),
    )
    for operation in blocked:
        assert_unchanged_after_error(
            service,
            created.room_id,
            ActiveHandMutationError,
            operation,
        )


def test_close_clears_requests_preserves_members_and_seats_and_is_not_idempotent() -> None:
    service, _ = build_service()
    created = create_room(service)
    join(service, created.room_code)
    join(service, created.room_code, BOB, "Bob")
    service.request_seat(room_id=created.room_id, actor=ALICE, seat_index=0)
    service.approve_seat_request(room_id=created.room_id, actor=HOST, target=ALICE)
    before_close = service.request_seat(
        room_id=created.room_id,
        actor=BOB,
        seat_index=5,
    )

    closed = service.close_room(room_id=created.room_id, actor=HOST)

    assert closed.status is RoomStatus.CLOSED
    assert closed.seat_requests == ()
    assert closed.members == before_close.members
    assert closed.seats == before_close.seats
    assert closed.host_guest_id == HOST
    assert_unchanged_after_error(
        service,
        created.room_id,
        RoomClosedError,
        lambda: service.close_room(room_id=created.room_id, actor=HOST),
    )


def test_all_closed_room_mutations_are_rejected_without_change() -> None:
    service, _ = build_service()
    created = create_room(service)
    join(service, created.room_code)
    service.close_room(room_id=created.room_id, actor=HOST)

    operations = (
        lambda: join(service, created.room_code, BOB, "Bob"),
        lambda: service.leave_room(room_id=created.room_id, actor=ALICE),
        lambda: service.kick_member(room_id=created.room_id, actor=HOST, target=ALICE),
        lambda: service.request_seat(room_id=created.room_id, actor=ALICE, seat_index=0),
        lambda: service.approve_seat_request(room_id=created.room_id, actor=HOST, target=ALICE),
        lambda: service.reject_seat_request(room_id=created.room_id, actor=HOST, target=ALICE),
        lambda: service.stand_up(room_id=created.room_id, actor=ALICE),
        lambda: service.update_room_settings(
            room_id=created.room_id,
            actor=HOST,
            update=RoomSettingsUpdate(room_name="Other"),
        ),
    )
    for operation in operations:
        assert_unchanged_after_error(service, created.room_id, RoomClosedError, operation)


def test_snapshots_are_immutable_deterministic_and_contain_no_domain_aggregate() -> None:
    service, _ = build_service()
    created = create_room(service, password="secret")
    join(service, created.room_code, password="secret")
    snapshot = service.request_seat(
        room_id=created.room_id,
        actor=ALICE,
        seat_index=4,
    )
    same = service.get_room_snapshot_by_code(created.room_code.lower())

    assert snapshot == same
    assert isinstance(snapshot.members, tuple)
    assert isinstance(snapshot.seats, tuple)
    assert isinstance(snapshot.seat_requests, tuple)
    assert [seat.seat_index for seat in snapshot.seats] == list(range(6))
    assert all(
        not isinstance(getattr(snapshot, field.name), TableState) for field in fields(snapshot)
    )
    assert "secret" not in repr(snapshot)
    assert "PlayerId" not in repr(snapshot)
    with pytest.raises(FrozenInstanceError):
        snapshot.status = RoomStatus.CLOSED  # type: ignore[misc]


def make_internal_room(
    *,
    room_id: RoomId,
    code: str,
    host: GuestId,
    player: str,
) -> _Room:
    return _Room(
        room_id=room_id,
        room_code=code,
        host_guest_id=host,
        settings=RoomSettings(room_name=code),
        password_record=None,
        host_player_id=PlayerId(player),
        host_nickname=host.value,
    )


def test_repository_duplicate_id_and_code_insertions_are_atomic() -> None:
    repository = InMemoryRoomRepository()
    first = make_internal_room(
        room_id=RoomId("room-1"),
        code="ABCDEFGH",
        host=HOST,
        player="player-1",
    )
    repository.add(first)

    duplicate_id = make_internal_room(
        room_id=first.room_id,
        code="BCDEFGHJ",
        host=ALICE,
        player="player-2",
    )
    with pytest.raises(DuplicateRoomIdError):
        repository.add(duplicate_id)
    assert not repository.contains_code("BCDEFGHJ")
    assert repository.get_by_id(first.room_id) is first

    duplicate_code = make_internal_room(
        room_id=RoomId("room-2"),
        code=first.room_code,
        host=ALICE,
        player="player-2",
    )
    with pytest.raises(DuplicateRoomCodeError):
        repository.add(duplicate_code)
    assert repository.get_by_code("abcdefgh") is first


def test_repository_indexes_remain_reconciled_across_concurrent_creates() -> None:
    service = RoomService(password_hasher=FastPasswordHasher())

    def create(index: int) -> RoomSnapshot:
        return service.create_room(
            actor=GuestId(f"concurrent-guest-{index}"),
            nickname=f"Host {index}",
            settings=RoomSettings(room_name=f"Room {index}"),
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        snapshots = tuple(executor.map(create, range(32)))

    assert len({snapshot.room_id for snapshot in snapshots}) == len(snapshots)
    assert len({snapshot.room_code for snapshot in snapshots}) == len(snapshots)
    for snapshot in snapshots:
        assert service.get_room_snapshot(snapshot.room_id) == snapshot
        assert service.get_room_snapshot_by_code(snapshot.room_code) == snapshot


def test_repository_replacement_cannot_desynchronize_code_index() -> None:
    repository = InMemoryRoomRepository()
    room = make_internal_room(
        room_id=RoomId("room-1"),
        code="ABCDEFGH",
        host=HOST,
        player="player-1",
    )
    repository.add(room)
    candidate = room.copy()
    candidate.room_code = "BCDEFGHJ"

    with pytest.raises(InvalidRoomStateError):
        repository.replace(candidate)

    assert repository.get_by_id(room.room_id) is room
    assert repository.get_by_code("abcdefgh") is room
    assert not repository.contains_code("BCDEFGHJ")


def test_repository_missing_lookup_and_dangling_index_errors_are_typed() -> None:
    repository = InMemoryRoomRepository()

    with pytest.raises(RoomNotFoundError):
        repository.get_by_id(RoomId("missing"))
    with pytest.raises(RoomNotFoundError):
        repository.get_by_code("ABCDEFGH")

    repository._ids_by_code["ABCDEFGH"] = RoomId("missing")
    with pytest.raises(InvalidRoomStateError):
        repository.get_by_code("abcdefgh")


def test_candidate_copy_preserves_dealer_button_and_is_independent() -> None:
    room = make_internal_room(
        room_id=RoomId("room-1"),
        code="ABCDEFGH",
        host=HOST,
        player="player-1",
    )
    room.table.seat_player(
        seat_index=SeatIndex(0),
        player_id=room.members[HOST].player_id,
        stack=ChipStack(100),
        status=ParticipationStatus.SITTING_IN,
    )
    room.members[HOST] = replace(
        room.members[HOST],
        status=RoomMemberStatus.SEATED,
        retained_stack=None,
    )
    room.table.move_button()

    copied = room.copy()

    assert copied.table.button_position == room.table.button_position
    copied.table.sit_out(player_id=room.members[HOST].player_id)
    assert (
        room.table.seat_at(SeatIndex(0)).occupant.status is ParticipationStatus.SITTING_IN  # type: ignore[union-attr]
    )
