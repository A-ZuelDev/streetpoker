from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response

from streetpoker.api.realtime import RealtimeRoomCoordinator
from streetpoker.api.request_body_limit import (
    ROOM_CREATION_MAX_BODY_BYTES,
    RoomCreationBodyLimitMiddleware,
    RoomCreationBodyTooLarge,
)
from streetpoker.api.routes.health import router as health_router
from streetpoker.api.routes.realtime import router as realtime_router
from streetpoker.api.routes.rooms import room_http_error
from streetpoker.api.routes.rooms import router as rooms_router
from streetpoker.application import RoomService
from streetpoker.config import get_settings


def create_app(*, room_service: RoomService | None = None) -> FastAPI:
    settings = get_settings()
    service = RoomService() if room_service is None else room_service
    coordinator = RealtimeRoomCoordinator(service)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await coordinator.shutdown()

    app = FastAPI(
        title="StreetPoker API",
        version="0.1.0",
        description="Authoritative API foundation for StreetPoker.",
        lifespan=lifespan,
    )

    @app.exception_handler(RequestValidationError)
    async def sanitize_room_validation(
        request: Request,
        error: RequestValidationError,
    ) -> Response:
        route = request.scope.get("route")
        if request.method == "POST" and getattr(route, "path", None) == "/rooms":
            return room_http_error(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "validation_error",
                "The request payload is invalid.",
            )
        return await request_validation_exception_handler(request, error)

    @app.exception_handler(RoomCreationBodyTooLarge)
    async def sanitize_oversized_room_request(
        _request: Request,
        _error: RoomCreationBodyTooLarge,
    ) -> Response:
        return room_http_error(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "request_too_large",
            "The request payload is too large.",
        )

    app.state.realtime_coordinator = coordinator
    app.add_middleware(
        RoomCreationBodyLimitMiddleware,
        max_body_bytes=ROOM_CREATION_MAX_BODY_BYTES,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(rooms_router)
    app.include_router(realtime_router)
    return app
