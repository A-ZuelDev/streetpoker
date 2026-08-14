from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from streetpoker.api.routes.health import router as health_router
from streetpoker.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="StreetPoker API",
        version="0.1.0",
        description="Authoritative API foundation for StreetPoker.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    return app
