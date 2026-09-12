"""Strict HTTP room-bootstrap request and response schemas."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from streetpoker.application import (
    DEFAULT_BIG_BLIND,
    DEFAULT_SMALL_BLIND,
    DEFAULT_STARTING_STACK,
)

GuestTokenText = Annotated[str, StringConstraints(max_length=128)]
NicknameText = Annotated[str, StringConstraints(max_length=256)]
RoomNameText = Annotated[str, StringConstraints(max_length=512)]
PasswordText = Annotated[str, StringConstraints(max_length=512)]


class _StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CreateRoomSettingsRequest(_StrictInput):
    room_name: RoomNameText
    small_blind: int = DEFAULT_SMALL_BLIND
    big_blind: int = DEFAULT_BIG_BLIND
    default_starting_stack: int = DEFAULT_STARTING_STACK
    seating_approval_required: bool = True


class CreateRoomRequest(_StrictInput):
    guest_token: GuestTokenText
    nickname: NicknameText
    settings: CreateRoomSettingsRequest
    password: PasswordText | None = None


class _StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CreateRoomResponse(_StrictOutput):
    room_code: str


class HttpErrorDetail(_StrictOutput):
    code: str
    message: str


class HttpErrorResponse(_StrictOutput):
    error: HttpErrorDetail
