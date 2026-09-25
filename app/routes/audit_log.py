"""Read-only access to the application's audit log."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError, field_validator

from app import audit
from app.audit import AUDIT_LIMIT_DEFAULT, AUDIT_LIMIT_MAX

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


class AuditLogQuery(BaseModel):
    instance: str = Field(default="", max_length=64)
    action: str = Field(default="", max_length=64)
    actor: str = Field(default="", max_length=128)
    text: str = Field(default="", max_length=200)
    limit: int = Field(default=AUDIT_LIMIT_DEFAULT, ge=1, le=AUDIT_LIMIT_MAX)

    @field_validator("instance", "action", "actor", "text", mode="before")
    @classmethod
    def strip_whitespace(cls, value: str) -> str:
        return value.strip()


@router.get("/audit-log")
def audit_log(instance: str = "", action: str = "", actor: str = "", text: str = "",
              limit: int = AUDIT_LIMIT_DEFAULT) -> dict[str, Any]:
    try:
        query = AuditLogQuery(instance=instance, action=action, actor=actor, text=text, limit=limit)
    except ValidationError as exc:
        raise HTTPException(422, detail=exc.errors()[0]["msg"]) from exc
    try:
        result = audit.search(**query.model_dump())
    except OSError as exc:
        logger.exception("audit log read failed")
        raise HTTPException(503, detail="Audit log could not be read") from exc
    return {**result, **query.model_dump(), "returned": len(result["entries"])}
