"""Extraction model port (P1B-IMPORTS).

The extraction router resolves its model through this port so the immutable
model-version cutover (P2A-CALLERS) can reimplement it without reopening the
router.  The default SQL port validates the legacy `ModelConfig` row and
returns a redacted descriptor; an unknown id resolves to `None` (the router
rejects it with a stable error instead of a 500 FK IntegrityError).
"""
from __future__ import annotations

from typing import Protocol

from sqlalchemy.orm import Session


class ExtractionModelPort(Protocol):
    def resolve_model(self, db: Session, model_id: str) -> dict | None: ...


class SqlExtractionModelPort:
    """Default port: resolves a `ModelConfig` by id (redacted).

    LLM configs resolve only when they have an active immutable behavior
    version (blocked/archived/unversioned -> None, existence-hiding).
    OCR/`other` configs stay on the legacy tagged path unchanged.
    """

    def resolve_model(self, db: Session, model_id: str) -> dict | None:
        from sqlalchemy import text

        from app.models.model_config import ModelConfig
        from app.services.model_version import versioning_schema_present

        row = db.query(ModelConfig).filter(ModelConfig.id == model_id).first()
        if row is None:
            return None
        if (row.config_type or "llm") != "llm":
            return {
                "id": row.id,
                "name": row.name,
                "config_type": row.config_type,
            }
        if not versioning_schema_present(db):
            # pre-0004 schema: legacy resolution
            return {
                "id": row.id,
                "name": row.name,
                "config_type": row.config_type,
            }
        state = db.execute(text(
            "SELECT status, active_version_id FROM model_configs WHERE id = :id"
        ), {"id": row.id}).mappings().one_or_none()
        if not state or state["status"] != "active" or not state["active_version_id"]:
            return None
        return {
            "id": row.id,
            "name": row.name,
            "config_type": row.config_type,
            "active_version_id": state["active_version_id"],
        }


default_extraction_model_port: ExtractionModelPort = SqlExtractionModelPort()
