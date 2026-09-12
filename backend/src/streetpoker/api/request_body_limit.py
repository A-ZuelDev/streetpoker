"""Narrow raw-body limits for security-sensitive HTTP transports."""

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from streetpoker.api.routes.rooms import room_http_error

ROOM_CREATION_MAX_BODY_BYTES = 8 * 1024


class RoomCreationBodyTooLarge(HTTPException):
    """Stop room-request parsing after the bounded receive limit is exceeded."""

    def __init__(self) -> None:
        super().__init__(status_code=413)


class RoomCreationBodyLimitMiddleware:
    """Bound raw POST /rooms bodies before FastAPI parses or buffers them."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self._app = app
        self._max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not _is_room_creation(scope):
            await self._app(scope, receive, send)
            return

        if _declared_body_exceeds_limit(scope, self._max_body_bytes):
            await _too_large_response()(scope, receive, send)
            return

        received_body_bytes = 0
        response_started = False

        async def receive_with_limit() -> Message:
            nonlocal received_body_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_body_bytes += len(message.get("body", b""))
                if received_body_bytes > self._max_body_bytes:
                    raise RoomCreationBodyTooLarge
            return message

        async def track_response_start(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self._app(scope, receive_with_limit, track_response_start)
        except RoomCreationBodyTooLarge:
            if response_started:  # pragma: no cover - route bodies are parsed before responses
                raise
            await _too_large_response()(scope, receive, send)


def _is_room_creation(scope: Scope) -> bool:
    if scope["type"] != "http" or scope.get("method") != "POST":
        return False

    path = scope.get("path", "")
    if path == "/rooms":
        return True

    root_path = scope.get("root_path", "").rstrip("/")
    return bool(root_path and path == f"{root_path}/rooms")


def _declared_body_exceeds_limit(scope: Scope, max_body_bytes: int) -> bool:
    for name, value in scope.get("headers", ()):
        if name.lower() != b"content-length":
            continue
        try:
            if int(value) > max_body_bytes:
                return True
        except ValueError:
            continue
    return False


def _too_large_response() -> Response:
    return room_http_error(
        413,
        "request_too_large",
        "The request payload is too large.",
    )
