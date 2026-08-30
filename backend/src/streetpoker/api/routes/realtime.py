"""FastAPI WebSocket endpoint for one-room realtime sessions."""

import json
from dataclasses import dataclass
from typing import Any, Final, cast

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from streetpoker.api.realtime import ConnectionFailure, RealtimeRoomCoordinator, SocketSession
from streetpoker.api.schemas.realtime import (
    ConnectionErrorMessage,
    client_command_adapter,
    connect_request_adapter,
)

router = APIRouter(tags=["realtime"])

MAX_CLIENT_MESSAGE_BYTES: Final = 64 * 1024
COMMAND_TYPES: Final = frozenset(
    {
        "start_hand",
        "fold",
        "check",
        "call",
        "bet_to",
        "raise_to",
        "request_seat",
        "approve_seat",
        "reject_seat",
        "stand",
        "leave",
        "kick",
        "update_settings",
        "close_room",
    }
)


@dataclass(frozen=True, slots=True)
class _FatalProtocolError(Exception):
    code: str
    message: str
    close_code: int


def _coordinator(websocket: WebSocket) -> RealtimeRoomCoordinator:
    return cast(RealtimeRoomCoordinator, websocket.app.state.realtime_coordinator)


async def _receive_json_value(websocket: WebSocket) -> object:
    message = await websocket.receive()
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(
            code=message.get("code", 1000),
            reason=message.get("reason", ""),
        )
    binary = message.get("bytes")
    if binary is not None:
        raise _FatalProtocolError(
            code="unsupported_frame",
            message="Only text JSON messages are supported.",
            close_code=1003,
        )
    text = message.get("text")
    if text is None:
        raise _FatalProtocolError(
            code="invalid_frame",
            message="The WebSocket frame is invalid.",
            close_code=1002,
        )
    if len(text.encode("utf-8")) > MAX_CLIENT_MESSAGE_BYTES:
        raise _FatalProtocolError(
            code="message_too_large",
            message="The WebSocket message is too large.",
            close_code=1009,
        )
    try:
        return cast(object, json.loads(text))
    except json.JSONDecodeError as error:
        raise ValueError("malformed_json") from error


def _recover_command_id(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    command_id = value.get("command_id")
    if not isinstance(command_id, str):
        return None
    normalized = command_id.strip()
    return normalized if 1 <= len(normalized) <= 64 else None


async def _send_connection_error(
    websocket: WebSocket,
    *,
    code: str,
    message: str,
    close_code: int,
) -> None:
    outbound = ConnectionErrorMessage(code=code, message=message)
    try:
        await websocket.send_json(outbound.model_dump(mode="json"))
        await websocket.close(code=close_code, reason=code)
    except OSError, RuntimeError, WebSocketDisconnect:
        return


@router.websocket("/ws/rooms/{room_code}")
async def room_websocket(websocket: WebSocket, room_code: str) -> None:
    await websocket.accept()
    coordinator = _coordinator(websocket)
    session: SocketSession | None = None
    try:
        try:
            raw_connect = await _receive_json_value(websocket)
            connect = connect_request_adapter.validate_python(raw_connect)
        except ValueError, ValidationError:
            await _send_connection_error(
                websocket,
                code="invalid_handshake",
                message="The initial connection message is invalid.",
                close_code=1002,
            )
            return
        except _FatalProtocolError as error:
            await _send_connection_error(
                websocket,
                code=error.code,
                message=error.message,
                close_code=error.close_code,
            )
            return

        try:
            session = await coordinator.bind(websocket, room_code, connect)
        except ConnectionFailure as error:
            await _send_connection_error(
                websocket,
                code=error.error.code,
                message=error.error.message,
                close_code=error.close_code,
            )
            return

        while coordinator.registry.is_registered(session):
            try:
                raw_command = await _receive_json_value(websocket)
            except ValueError:
                coordinator.enqueue_command_error(
                    session,
                    command_id=None,
                    code="malformed_json",
                    message="The command is not valid JSON.",
                )
                continue
            except _FatalProtocolError as error:
                coordinator.enqueue_command_error(
                    session,
                    command_id=None,
                    code=error.code,
                    message=error.message,
                )
                coordinator.registry.detach_session(
                    session,
                    code=error.close_code,
                    reason=error.code,
                )
                break

            command_id = _recover_command_id(raw_command)
            command_type: Any = raw_command.get("type") if isinstance(raw_command, dict) else None
            if isinstance(command_type, str) and command_type not in COMMAND_TYPES:
                coordinator.enqueue_command_error(
                    session,
                    command_id=command_id,
                    code="unknown_command",
                    message="The command type is unknown.",
                )
                continue
            try:
                command = client_command_adapter.validate_python(raw_command)
            except ValidationError:
                coordinator.enqueue_command_error(
                    session,
                    command_id=command_id,
                    code="validation_error",
                    message="The command payload is invalid.",
                )
                continue
            await coordinator.handle_command(session, command)
    except WebSocketDisconnect:
        pass
    finally:
        if session is not None:
            await coordinator.disconnect(session)
