"""Strict HTTP room-bootstrap request and response schemas."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from streetpoker.application import (
    DEFAULT_BIG_BLIND,
    DEFAULT_SMALL_BLIND,
    DEFAULT_STAND_UP_PENALTY_PER_RECIPIENT_CHIPS,
    DEFAULT_STARTING_STACK,
    MAX_STAND_UP_PENALTY_PER_RECIPIENT_CHIPS,
)

GuestTokenText = Annotated[str, StringConstraints(max_length=128)]
NicknameText = Annotated[str, StringConstraints(max_length=256)]
RoomNameText = Annotated[str, StringConstraints(max_length=512)]
PasswordText = Annotated[str, StringConstraints(max_length=512)]
StandUpPenalty = Annotated[
    int,
    Field(strict=True, ge=1, le=MAX_STAND_UP_PENALTY_PER_RECIPIENT_CHIPS),
]


class _StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CreateRoomSettingsRequest(_StrictInput):
    room_name: RoomNameText
    small_blind: int = DEFAULT_SMALL_BLIND
    big_blind: int = DEFAULT_BIG_BLIND
    default_starting_stack: int = DEFAULT_STARTING_STACK
    seating_approval_required: bool = True
    stand_up_enabled: bool = False
    stand_up_penalty_per_recipient_chips: StandUpPenalty = (
        DEFAULT_STAND_UP_PENALTY_PER_RECIPIENT_CHIPS
    )


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
