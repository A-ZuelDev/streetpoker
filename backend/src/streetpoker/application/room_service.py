"""Application service and in-memory repository for private rooms."""

import hashlib
import hmac
import secrets
from collections.abc import Callable
from typing import Protocol
from uuid import uuid4

from streetpoker.application.errors import (
    DuplicateRoomCodeError,
    DuplicateRoomIdError,
    InvalidRoomPasswordError,
    InvalidRoomStateError,
    PlayerIdGenerationError,
    RoomCreationCollisionError,
    RoomNotFoundError,
    WrongRoomPasswordError,
)
from streetpoker.application.rooms import (
    ROOM_CODE_ALPHABET,
    ROOM_CODE_LENGTH,
    SETTING_NOT_PROVIDED,
    GuestId,
    RoomId,
    RoomSettings,
    RoomSettingsUpdate,
    RoomSnapshot,
    SettingNotProvided,
    _PasswordRecord,
    _Room,
    normalize_room_code,
)
from streetpoker.domain import PlayerId

PASSWORD_HASH_ITERATIONS = 600_000
PASSWORD_SALT_BYTES = 16
MAX_PASSWORD_CHARACTERS = 128
MAX_PASSWORD_BYTES = 256
MAX_GENERATION_ATTEMPTS = 32


class PasswordHasher(Protocol):
    """Minimal injectable password hashing and verification boundary."""

    def hash_password(self, password: str) -> _PasswordRecord:
        """Validate and irreversibly hash a room password."""
        ...

    def verify_password(self, password: str, record: _PasswordRecord) -> bool:
        """Return whether a candidate password matches an existing record."""
        ...


def _validated_password_bytes(password: str) -> bytes:
    if not isinstance(password, str):
        raise InvalidRoomPasswordError("A room password must be a string.")
    if not password:
        raise InvalidRoomPasswordError("A room password cannot be empty.")
    if len(password) > MAX_PASSWORD_CHARACTERS:
        raise InvalidRoomPasswordError(
            f"A room password cannot exceed {MAX_PASSWORD_CHARACTERS} characters."
        )
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise InvalidRoomPasswordError(
            f"A room password cannot exceed {MAX_PASSWORD_BYTES} UTF-8 bytes."
        )
    return encoded


class Pbkdf2PasswordHasher:
    """Standard-library PBKDF2-HMAC-SHA256 room-password implementation."""

    __slots__ = ("_iterations",)

    def __init__(self, *, iterations: int = PASSWORD_HASH_ITERATIONS) -> None:
        if not isinstance(iterations, int) or isinstance(iterations, bool) or iterations <= 0:
            raise ValueError("PBKDF2 iterations must be a positive integer.")
        self._iterations = iterations

    def hash_password(self, password: str) -> _PasswordRecord:
        encoded = _validated_password_bytes(password)
        salt = secrets.token_bytes(PASSWORD_SALT_BYTES)
        digest = hashlib.pbkdf2_hmac("sha256", encoded, salt, self._iterations)
        return _PasswordRecord(salt=salt, digest=digest, iterations=self._iterations)

    def verify_password(self, password: str, record: _PasswordRecord) -> bool:
        encoded = _validated_password_bytes(password)
        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            encoded,
            record.salt,
            record.iterations,
        )
        return hmac.compare_digest(candidate, record.digest)


class RoomCodeSource(Protocol):
    """Injectable source for canonical candidate room codes."""

    def next_code(self) -> str:
        """Return the next candidate room code."""
        ...


class SecureRoomCodeSource:
    """Production room-code source backed by operating-system entropy."""

    def next_code(self) -> str:
        return "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(ROOM_CODE_LENGTH))


class RoomRepository(Protocol):
    """Small storage boundary for process-local room aggregates."""

    def add(self, room: _Room) -> None:
        """Atomically insert a room under both immutable indexes."""
        ...

    def get_by_id(self, room_id: RoomId) -> _Room:
        """Resolve a room by immutable ID."""
        ...

    def get_by_code(self, room_code: str) -> _Room:
        """Resolve a room by normalized, case-insensitive code."""
        ...

    def replace(self, room: _Room) -> None:
        """Atomically replace an existing same-identity room candidate."""
        ...

    def contains_code(self, room_code: str) -> bool:
        """Return whether a normalized code remains reserved."""
        ...


class InMemoryRoomRepository:
    """Non-thread-safe in-memory room store with two reconciled indexes."""

    __slots__ = ("_ids_by_code", "_rooms_by_id")

    def __init__(self) -> None:
        self._rooms_by_id: dict[RoomId, _Room] = {}
        self._ids_by_code: dict[str, RoomId] = {}

    def add(self, room: _Room) -> None:
        room.validate()
        if room.room_id in self._rooms_by_id:
            raise DuplicateRoomIdError(f"Room ID {room.room_id.value!r} already exists.")
        if room.room_code in self._ids_by_code:
            raise DuplicateRoomCodeError(f"Room code {room.room_code!r} already exists.")
        self._rooms_by_id[room.room_id] = room
        self._ids_by_code[room.room_code] = room.room_id

    def get_by_id(self, room_id: RoomId) -> _Room:
        try:
            room = self._rooms_by_id[room_id]
        except KeyError:
            raise RoomNotFoundError(f"Room ID {room_id.value!r} was not found.") from None
        indexed_id = self._ids_by_code.get(room.room_code)
        if indexed_id != room.room_id:
            raise InvalidRoomStateError("The room repository indexes are inconsistent.")
        return room

    def get_by_code(self, room_code: str) -> _Room:
        normalized = normalize_room_code(room_code)
        try:
            room_id = self._ids_by_code[normalized]
        except KeyError:
            raise RoomNotFoundError(f"Room code {normalized!r} was not found.") from None
        try:
            room = self._rooms_by_id[room_id]
        except KeyError:
            raise InvalidRoomStateError("The room repository indexes are inconsistent.") from None
        if room.room_code != normalized:
            raise InvalidRoomStateError("The room repository indexes are inconsistent.")
        return room

    def replace(self, room: _Room) -> None:
        room.validate()
        try:
            existing = self._rooms_by_id[room.room_id]
        except KeyError:
            raise RoomNotFoundError(f"Room ID {room.room_id.value!r} was not found.") from None
        if room.room_code != existing.room_code:
            raise InvalidRoomStateError(
                "Room codes are immutable and cannot change on replacement."
            )
        indexed_id = self._ids_by_code.get(room.room_code)
        if indexed_id != room.room_id:
            raise InvalidRoomStateError("The room repository indexes are inconsistent.")
        self._rooms_by_id[room.room_id] = room

    def contains_code(self, room_code: str) -> bool:
        return normalize_room_code(room_code) in self._ids_by_code


def _new_room_id() -> RoomId:
    return RoomId(str(uuid4()))


def _new_player_id() -> PlayerId:
    return PlayerId(str(uuid4()))


class RoomService:
    """Coordinates authorized room commands and exposes immutable snapshots only."""

    __slots__ = (
        "_code_source",
        "_password_hasher",
        "_player_id_factory",
        "_repository",
        "_room_id_factory",
    )

    def __init__(
        self,
        *,
        repository: RoomRepository | None = None,
        code_source: RoomCodeSource | None = None,
        password_hasher: PasswordHasher | None = None,
        room_id_factory: Callable[[], RoomId] = _new_room_id,
        player_id_factory: Callable[[], PlayerId] = _new_player_id,
    ) -> None:
        self._repository = InMemoryRoomRepository() if repository is None else repository
        self._code_source = SecureRoomCodeSource() if code_source is None else code_source
        self._password_hasher = (
            Pbkdf2PasswordHasher() if password_hasher is None else password_hasher
        )
        self._room_id_factory = room_id_factory
        self._player_id_factory = player_id_factory

    def create_room(
        self,
        *,
        actor: GuestId,
        nickname: str,
        settings: RoomSettings,
        password: str | None = None,
    ) -> RoomSnapshot:
        password_record = (
            None if password is None else self._password_hasher.hash_password(password)
        )
        for _ in range(MAX_GENERATION_ATTEMPTS):
            room = _Room(
                room_id=self._room_id_factory(),
                room_code=normalize_room_code(self._code_source.next_code()),
                host_guest_id=actor,
                settings=settings,
                password_record=password_record,
                host_player_id=self._player_id_factory(),
                host_nickname=nickname,
            )
            snapshot = room.snapshot()
            try:
                self._repository.add(room)
            except DuplicateRoomIdError, DuplicateRoomCodeError:
                continue
            return snapshot
        raise RoomCreationCollisionError(
            f"Could not allocate a unique room after {MAX_GENERATION_ATTEMPTS} attempts."
        )

    def join_room(
        self,
        *,
        room_code: str,
        actor: GuestId,
        nickname: str,
        password: str | None = None,
    ) -> RoomSnapshot:
        original = self._repository.get_by_code(room_code)
        original.require_not_closed()
        self._verify_join_password(original, password)
        candidate = original.copy()
        candidate.add_member(
            guest_id=actor,
            player_id=self._unique_player_id(candidate),
            nickname=nickname,
        )
        return self._commit(candidate)

    def leave_room(self, *, room_id: RoomId, actor: GuestId) -> RoomSnapshot:
        original = self._authorized_room(room_id, actor)
        candidate = original.copy()
        candidate.leave(actor=actor)
        return self._commit(candidate)

    def request_seat(
        self,
        *,
        room_id: RoomId,
        actor: GuestId,
        seat_index: int,
    ) -> RoomSnapshot:
        original = self._authorized_room(room_id, actor)
        candidate = original.copy()
        candidate.request_seat(actor=actor, seat_index=seat_index)
        return self._commit(candidate)

    def approve_seat_request(
        self,
        *,
        room_id: RoomId,
        actor: GuestId,
        target: GuestId,
    ) -> RoomSnapshot:
        original = self._host_room(room_id, actor)
        candidate = original.copy()
        candidate.approve_seat_request(target=target)
        return self._commit(candidate)

    def reject_seat_request(
        self,
        *,
        room_id: RoomId,
        actor: GuestId,
        target: GuestId,
    ) -> RoomSnapshot:
        original = self._host_room(room_id, actor)
        candidate = original.copy()
        candidate.reject_seat_request(target=target)
        return self._commit(candidate)

    def stand_up(self, *, room_id: RoomId, actor: GuestId) -> RoomSnapshot:
        original = self._authorized_room(room_id, actor)
        candidate = original.copy()
        candidate.stand_up(actor=actor)
        return self._commit(candidate)

    def kick_member(
        self,
        *,
        room_id: RoomId,
        actor: GuestId,
        target: GuestId,
    ) -> RoomSnapshot:
        original = self._host_room(room_id, actor)
        candidate = original.copy()
        candidate.kick(target=target)
        return self._commit(candidate)

    def update_room_settings(
        self,
        *,
        room_id: RoomId,
        actor: GuestId,
        update: RoomSettingsUpdate,
    ) -> RoomSnapshot:
        original = self._host_room(room_id, actor)
        original.require_not_closed()
        candidate = original.copy()
        settings = self._updated_settings(candidate.settings, update)
        password_record: _PasswordRecord | SettingNotProvided | None = SETTING_NOT_PROVIDED
        if update.password is not SETTING_NOT_PROVIDED:
            password_record = (
                None
                if update.password is None
                else self._password_hasher.hash_password(update.password)
            )
        candidate.update_settings(settings=settings, password_record=password_record)
        return self._commit(candidate)

    def close_room(self, *, room_id: RoomId, actor: GuestId) -> RoomSnapshot:
        original = self._host_room(room_id, actor)
        candidate = original.copy()
        candidate.close()
        return self._commit(candidate)

    def get_room_snapshot(self, room_id: RoomId) -> RoomSnapshot:
        return self._repository.get_by_id(room_id).snapshot()

    def get_room_snapshot_by_code(self, room_code: str) -> RoomSnapshot:
        return self._repository.get_by_code(room_code).snapshot()

    def _authorized_room(self, room_id: RoomId, actor: GuestId) -> _Room:
        room = self._repository.get_by_id(room_id)
        room.require_actor(actor)
        return room

    def _host_room(self, room_id: RoomId, actor: GuestId) -> _Room:
        room = self._repository.get_by_id(room_id)
        room.require_host(actor)
        return room

    def _commit(self, candidate: _Room) -> RoomSnapshot:
        candidate.validate()
        snapshot = candidate.snapshot()
        self._repository.replace(candidate)
        return snapshot

    def _verify_join_password(self, room: _Room, password: str | None) -> None:
        record = room.password_record
        if record is None:
            return
        if password is None or not self._password_hasher.verify_password(password, record):
            raise WrongRoomPasswordError("The room password is incorrect.")

    def _unique_player_id(self, room: _Room) -> PlayerId:
        existing = {member.player_id for member in room.members.values()}
        for _ in range(MAX_GENERATION_ATTEMPTS):
            candidate = self._player_id_factory()
            if candidate not in existing:
                return candidate
        raise PlayerIdGenerationError(
            f"Could not allocate a unique player ID after {MAX_GENERATION_ATTEMPTS} attempts."
        )

    @staticmethod
    def _updated_settings(
        current: RoomSettings,
        update: RoomSettingsUpdate,
    ) -> RoomSettings:
        if not isinstance(update, RoomSettingsUpdate):
            raise InvalidRoomStateError("Settings updates require RoomSettingsUpdate values.")
        return RoomSettings(
            room_name=(
                current.room_name if update.room_name is SETTING_NOT_PROVIDED else update.room_name
            ),
            small_blind=(
                current.small_blind
                if update.small_blind is SETTING_NOT_PROVIDED
                else update.small_blind
            ),
            big_blind=(
                current.big_blind if update.big_blind is SETTING_NOT_PROVIDED else update.big_blind
            ),
            default_starting_stack=(
                current.default_starting_stack
                if update.default_starting_stack is SETTING_NOT_PROVIDED
                else update.default_starting_stack
            ),
            seating_approval_required=(
                current.seating_approval_required
                if update.seating_approval_required is SETTING_NOT_PROVIDED
                else update.seating_approval_required
            ),
        )
