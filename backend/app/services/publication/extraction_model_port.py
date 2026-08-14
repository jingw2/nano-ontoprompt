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
    """Default port: resolves a legacy `ModelConfig` by id (redacted)."""

    def resolve_model(self, db: Session, model_id: str) -> dict | None:
        from app.models.model_config import ModelConfig

        row = db.query(ModelConfig).filter(ModelConfig.id == model_id).first()
        if row is None:
            return None
        return {
            "id": row.id,
            "name": row.name,
            "config_type": row.config_type,
        }


default_extraction_model_port: ExtractionModelPort = SqlExtractionModelPort()
