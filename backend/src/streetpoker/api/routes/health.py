from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from streetpoker.db.session import get_session

router = APIRouter(tags=["health"])


class StatusResponse(BaseModel):
    status: Literal["ok"] = "ok"


@router.get("/health", response_model=StatusResponse)
def health() -> StatusResponse:
    """Report process liveness without accessing PostgreSQL."""
    return StatusResponse()


@router.get(
    "/ready",
    response_model=StatusResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Database unavailable"}},
)
def ready(session: Annotated[Session, Depends(get_session)]) -> StatusResponse:
    """Report readiness after verifying PostgreSQL connectivity."""
    try:
        session.execute(text("SELECT 1")).scalar_one()
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from error
    return StatusResponse()
