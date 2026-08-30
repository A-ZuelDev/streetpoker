from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from streetpoker.api.realtime import RealtimeRoomCoordinator
from streetpoker.api.routes.health import router as health_router
from streetpoker.api.routes.realtime import router as realtime_router
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
    app.state.realtime_coordinator = coordinator
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(realtime_router)
    return app
