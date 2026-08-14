"""Prompt generation records (P2B-CONFIG).

Sanitized prompt-generation provenance: agent/base version, exact model
version/name, input/output hashes, requester/time and accepted/rejected state.
Excludes credentials, memory, raw traces and sensitive properties; generation
is never auto-activated.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session


class PromptGenerationError(Exception):
    """Rejected prompt-generation operation."""


def _new_id() -> str:
    return str(uuid.uuid4())


def record_prompt_generation(
    db: Session, *, actor_id: str, agent_id: str, base_version_no: int,
    model_config_version_id: str, model_name: str, input_hash: str, output_hash: str,
) -> dict:
    generation_id = _new_id()
    db.execute(text(
        "INSERT INTO prompt_generations (id, agent_id, base_version_no, model_config_version_id, "
        "model_name, input_hash, output_hash, status, requester_id, requested_at) "
        "VALUES (:id, :agent, :base, :mvid, :mname, :ih, :oh, 'pending', :actor, now())"
    ), {"id": generation_id, "agent": agent_id, "base": base_version_no,
        "mvid": model_config_version_id, "mname": model_name,
        "ih": input_hash, "oh": output_hash, "actor": actor_id})
    db.commit()
    return {"id": generation_id, "status": "pending"}


def _set_status(db: Session, *, generation_id: str, status: str) -> dict:
    result = db.execute(text(
        "UPDATE prompt_generations SET status = :status, accepted_at = CASE WHEN :status = 'accepted' "
        "THEN now() ELSE accepted_at END WHERE id = :id AND status = 'pending'"
    ), {"status": status, "id": generation_id})
    if result.rowcount != 1:
        raise PromptGenerationError("PROMPT_GENERATION_ALREADY_RESOLVED")
    db.commit()
    return {"id": generation_id, "status": status}


def accept_prompt_generation(db: Session, *, actor_id: str, agent_id: str, generation_id: str) -> dict:
    return _set_status(db, generation_id=generation_id, status="accepted")


def reject_prompt_generation(db: Session, *, actor_id: str, agent_id: str, generation_id: str) -> dict:
    return _set_status(db, generation_id=generation_id, status="rejected")
