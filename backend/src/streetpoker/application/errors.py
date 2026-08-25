"""Typed failures raised by the framework-independent application layer."""


class RoomApplicationError(Exception):
    """Base class for expected room-application failures."""


class RoomValidationError(RoomApplicationError):
    """Raised when a room-facing value or settings candidate is invalid."""


class InvalidRoomIdError(RoomValidationError):
    """Raised when a room ID is not a non-blank string."""


class InvalidGuestIdError(RoomValidationError):
    """Raised when a guest ID is not a non-blank string."""


class InvalidRoomCodeError(RoomValidationError):
    """Raised when a room code does not use the canonical format."""


class InvalidNicknameError(RoomValidationError):
    """Raised when a display nickname is invalid."""


class InvalidRoomNameError(RoomValidationError):
    """Raised when a room name is invalid."""


class InvalidRoomSettingsError(RoomValidationError):
    """Raised when complete candidate room settings are inconsistent."""


class InvalidRoomPasswordError(RoomValidationError):
    """Raised when a supplied room password violates the bounded policy."""


class InvalidRoomStateError(RoomApplicationError):
    """Raised when internal room state violates an application invariant."""


class RoomNotFoundError(RoomApplicationError):
    """Raised when no room matches an ID or code."""


class RoomClosedError(RoomApplicationError):
    """Raised when a mutation is attempted on a terminal closed room."""


class MembershipError(RoomApplicationError):
    """Base class for current-room membership failures."""


class DuplicateMembershipError(MembershipError):
    """Raised when a current member attempts to join the same room again."""


class DuplicateNicknameError(MembershipError):
    """Raised when a normalized nickname is already in use in the room."""


class MemberNotFoundError(MembershipError):
    """Raised when a target is not a current room member."""


class NotRoomMemberError(MembershipError):
    """Raised when an actor is not a current room member."""


class AuthorizationError(RoomApplicationError):
    """Base class for room authorization failures."""


class NotRoomHostError(AuthorizationError):
    """Raised when a host-only operation is attempted by another member."""


class HostCannotLeaveRoomError(AuthorizationError):
    """Raised when the host attempts to leave instead of closing the room."""


class CannotKickHostError(AuthorizationError):
    """Raised when the host attempts to kick themselves."""


class WrongRoomPasswordError(AuthorizationError):
    """Raised when the supplied room password does not verify."""


class SeatingError(RoomApplicationError):
    """Base class for room seating-coordination failures."""


class InvalidRoomSeatError(SeatingError):
    """Raised when a requested seat is not an integer from zero through five."""


class RoomSeatOccupiedError(SeatingError):
    """Raised when an occupied seat is requested or approved."""


class RoomSeatAlreadyRequestedError(SeatingError):
    """Raised when another pending request already targets a seat."""


class MemberAlreadySeatedError(SeatingError):
    """Raised when a seated member requests another seat."""


class MemberNotSeatedError(SeatingError):
    """Raised when an operation requires a seated member."""


class DuplicateSeatRequestError(SeatingError):
    """Raised when a member already has a pending seat request."""


class SeatRequestNotFoundError(SeatingError):
    """Raised when a host targets a member without a pending request."""


class ActiveHandMutationError(RoomApplicationError):
    """Raised when a room mutation would be unsafe during an active hand."""

    def __init__(self, *, operation: str) -> None:
        self.operation = operation
        super().__init__(f"Cannot {operation} while a hand is in progress.")


class RoomRepositoryError(RoomApplicationError):
    """Base class for in-memory repository consistency failures."""


class DuplicateRoomIdError(RoomRepositoryError):
    """Raised when repository insertion repeats a room ID."""


class DuplicateRoomCodeError(RoomRepositoryError):
    """Raised when repository insertion repeats a normalized room code."""


class RoomCreationCollisionError(RoomRepositoryError):
    """Raised after bounded room ID/code collision retries are exhausted."""


class PlayerIdGenerationError(RoomApplicationError):
    """Raised after bounded room-specific player ID collision retries."""
