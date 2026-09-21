"""HTTP bootstrap transport for creating the first room."""

import logging
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse

from streetpoker.api.guest_identity import derive_guest_id
from streetpoker.api.realtime import RealtimeRoomCoordinator
from streetpoker.api.schemas.rooms import (
    CreateRoomRequest,
    CreateRoomResponse,
    HttpErrorDetail,
    HttpErrorResponse,
)
from streetpoker.application import (
    InvalidNicknameError,
    InvalidRoomNameError,
    InvalidRoomPasswordError,
    InvalidRoomSettingsError,
    RoomCreationCollisionError,
    RoomSettings,
    RoomValidationError,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["rooms"])

NO_STORE_HEADERS = {"Cache-Control": "no-store"}

_VALIDATION_ERRORS: dict[type[RoomValidationError], tuple[str, str]] = {
    InvalidNicknameError: ("invalid_nickname", "The nickname is invalid."),
    InvalidRoomNameError: ("invalid_room_name", "The room name is invalid."),
    InvalidRoomSettingsError: ("invalid_settings", "The room settings are invalid."),
    InvalidRoomPasswordError: ("invalid_room_password", "The room password is invalid."),
}


def _coordinator(request: Request) -> RealtimeRoomCoordinator:
    return cast(RealtimeRoomCoordinator, request.app.state.realtime_coordinator)


def room_http_error(status_code: int, code: str, message: str) -> JSONResponse:
    content = HttpErrorResponse(error=HttpErrorDetail(code=code, message=message))
    return JSONResponse(
        status_code=status_code,
        content=content.model_dump(mode="json"),
        headers=NO_STORE_HEADERS,
    )


@router.post(
    "/rooms",
    response_model=CreateRoomResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_room(
    payload: CreateRoomRequest,
    response: Response,
    coordinator: Annotated[RealtimeRoomCoordinator, Depends(_coordinator)],
) -> CreateRoomResponse | JSONResponse:
    """Create a room and return only the code needed for WebSocket bootstrap."""
    try:
        actor = derive_guest_id(payload.guest_token)
    except ValueError:
        return room_http_error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_guest_token",
            "The guest token is invalid.",
        )

    try:
        settings = RoomSettings(
            room_name=payload.settings.room_name,
            small_blind=payload.settings.small_blind,
            big_blind=payload.settings.big_blind,
            default_starting_stack=payload.settings.default_starting_stack,
            seating_approval_required=payload.settings.seating_approval_required,
            stand_up_enabled=payload.settings.stand_up_enabled,
            stand_up_penalty_per_recipient_chips=(
                payload.settings.stand_up_penalty_per_recipient_chips
            ),
        )
        created = await coordinator.create_room(
            actor=actor,
            nickname=payload.nickname,
            settings=settings,
            password=payload.password,
        )
    except RoomCreationCollisionError:
        return room_http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "room_creation_unavailable",
            "The room could not be created right now.",
        )
    except RoomValidationError as error:
        code, message = _VALIDATION_ERRORS.get(
            type(error),
            ("validation_error", "The room request is invalid."),
        )
        return room_http_error(status.HTTP_422_UNPROCESSABLE_CONTENT, code, message)
    except Exception as error:
        logger.error(
            "Unexpected HTTP room creation failure",
            extra={"error_type": type(error).__name__},
        )
        return room_http_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
            "The server could not create the room.",
        )

    response.headers.update(NO_STORE_HEADERS)
    return CreateRoomResponse(room_code=created.room_code)
