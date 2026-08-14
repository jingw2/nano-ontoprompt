"""Immutable LLM caller resolution (P2A-CALLERS).

Eligible LLM callers resolve one pinned `ModelConfigVersion` plus its active
credential binding; a blocked/archived/unversioned identity fails explicitly
with `MODEL_VERSION_UNAVAILABLE` — there is never a fallback.  OCR/`other`
configs stay on the legacy tagged path and are never resolved here.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session


class ModelVersionUnavailableError(Exception):
    """The identity has no active immutable behavior version for LLM calls."""


def resolve_llm_caller(db: Session, model_id: str) -> dict:
    """Pin the active immutable behavior version + credential for an LLM
    config.  Raises `ModelVersionUnavailableError` (never falls back)."""
    from app.models.model_config import ModelConfig
    from app.models.model_version import ModelConfigVersion
    from app.services.model_version import decrypt_credential, versioning_schema_present

    row = db.query(ModelConfig).filter(ModelConfig.id == model_id).first()
    if row is None:
        raise ModelVersionUnavailableError("MODEL_CONFIG_NOT_FOUND")
    if (row.config_type or "llm") != "llm":
        raise ModelVersionUnavailableError("MODEL_VERSION_UNAVAILABLE")
    if not versioning_schema_present(db):
        # pre-0004 schema: no immutable versions exist yet
        raise ModelVersionUnavailableError("MODEL_VERSION_UNAVAILABLE")

    state = db.execute(text(
        "SELECT status, active_version_id FROM model_configs WHERE id = :id"
    ), {"id": model_id}).mappings().one_or_none()
    if not state or state["status"] != "active" or not state["active_version_id"]:
        raise ModelVersionUnavailableError("MODEL_VERSION_UNAVAILABLE")

    version = db.get(ModelConfigVersion, state["active_version_id"])
    if version is None or version.model_config_id != model_id:
        raise ModelVersionUnavailableError("MODEL_VERSION_UNAVAILABLE")

    contract = version.model_contract or []
    model_name = contract[0].get("provider_model_revision") if contract and isinstance(contract[0], dict) else None
    if not model_name:
        raise ModelVersionUnavailableError("MODEL_VERSION_UNAVAILABLE")

    credential = db.execute(text(
        "SELECT secret_encrypted FROM model_credentials WHERE model_config_id = :id "
        "AND status = 'active' ORDER BY secret_revision DESC LIMIT 1"
    ), {"id": model_id}).mappings().one_or_none()
    api_key = decrypt_credential(credential["secret_encrypted"]) if credential else ""

    return {
        "provider": version.provider,
        "api_key": api_key,
        "api_base": version.api_base,
        "model": model_name,
        "behavior_hash": version.behavior_hash,
        "conservative_input_limit": version.conservative_input_limit,
        "version_no": version.version_no,
    }
