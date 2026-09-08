"""Model config selection helpers for LLM/VLM call sites."""
from __future__ import annotations

from typing import Iterable


VLM_TAGS = {"VLM提取", "vlm", "vision", "视觉", "多模态", "multimodal"}
VLM_TOKENS = ("omni", "vlm", "vision", "multimodal")


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def usage_tags(model_config) -> list[str]:
    options = getattr(model_config, "options", None) or {}
    return [str(tag) for tag in _as_list(options.get("usage_tags"))]


def is_vlm_config(model_config) -> bool:
    options = getattr(model_config, "options", None) or {}
    tags = set(usage_tags(model_config))
    if tags & VLM_TAGS:
        return True
    modalities = " ".join(str(x).lower() for x in _as_list(options.get("modalities")))
    model_names = " ".join(str(x).lower() for x in _as_list(getattr(model_config, "models", None)))
    provider = str(getattr(model_config, "provider", "") or "").lower()
    return (
        "vision" in modalities
        or "image" in modalities
        or any(token in model_names for token in VLM_TOKENS)
        or any(token in provider for token in ("omni", "vlm"))
    )


def select_llm_model_config(
    db=None,
    model_id: str | None = None,
    purpose_tags: Iterable[str] = (),
    allow_vlm: bool = False,
):
    """Select a configured LLM.

    Text LLM callers should keep allow_vlm=False so a VLM such as mimo-omni
    does not override a text model such as DeepSeek just because it was updated
    later. VLM callers pass allow_vlm=True and purpose_tags=("VLM提取",).
    """
    from app.database import SessionLocal
    from app.models.model_config import ModelConfig

    owns_db = db is None
    db = db or SessionLocal()
    try:
        query = db.query(ModelConfig).filter(ModelConfig.config_type == "llm")
        if model_id:
            selected = query.filter(ModelConfig.id == model_id).first()
            if selected:
                return selected

        configs = query.order_by(ModelConfig.updated_at.desc()).all()
        if not configs:
            return None

        # Only active identities with an immutable active version are eligible;
        # blocked/archived/unversioned configs never fall back into selection.
        # Pre-0004 schemas (no versioning columns) keep the legacy selection.
        from sqlalchemy import text as _text
        from app.services.model_version import versioning_schema_present
        active_ids = None
        if versioning_schema_present(db):
            active_ids = {
                row[0]
                for row in db.execute(_text(
                    "SELECT id FROM model_configs WHERE config_type = 'llm' "
                    "AND status = 'active' AND active_version_id IS NOT NULL"
                )).all()
            }
        eligible = [item for item in configs if active_ids is None or item.id in active_ids]
        if not eligible:
            return None
        configs = eligible

        requested_tags = [str(tag) for tag in purpose_tags if tag]
        for tag in requested_tags:
            for item in configs:
                if tag in usage_tags(item):
                    return item

        if allow_vlm:
            vlm_candidates = [item for item in configs if is_vlm_config(item)]
            candidates = vlm_candidates or configs
        else:
            candidates = [item for item in configs if not is_vlm_config(item)]
        return candidates[0] if candidates else configs[0]
    finally:
        if owns_db:
            db.close()


def select_journey_model_config(version_id: str, db=None):
    """Resolve one pinned business-journey DeepSeek vision model config version.

    Rejects any provider/model/origin drift, never selects another provider
    or model in its place, and rejects a stale pin (the version must be its
    identity's current ACTIVE version) -- mirroring
    `resolve_llm_caller_by_version`'s "no stale pins" check. Raises
    `ModelConfigurationError` with a stable reason code on any mismatch,
    missing version, or non-active pin. There is no fallback.
    """
    import json as _json

    from sqlalchemy import text as _text

    from evals.business_journeys.contracts import (
        MODEL_ID,
        OFFICIAL_ORIGIN,
        ImmutableModelConfigVersion,
        ModelConfigurationError,
    )

    owns_db = db is None
    if owns_db:
        from app.database import SessionLocal

        db = SessionLocal()
    try:
        row = db.execute(
            _text(
                "SELECT id, provider, api_base, model_contract, behavior_hash, created_at "
                "FROM model_config_versions WHERE id = :id"
            ),
            {"id": version_id},
        ).mappings().one_or_none()
        if row is None:
            raise ModelConfigurationError(f"MODEL_CONFIG_VERSION_NOT_FOUND: {version_id}")

        active = db.execute(
            _text("SELECT 1 FROM model_configs WHERE active_version_id = :id LIMIT 1"),
            {"id": version_id},
        ).scalar_one_or_none()
        if not active:
            raise ModelConfigurationError(f"MODEL_CONFIG_VERSION_NOT_ACTIVE: {version_id}")

        if row["provider"] != "deepseek":
            raise ModelConfigurationError(f"PROVIDER_MISMATCH: {row['provider']!r}")

        # `model_contract` is a JSON column; a raw `text()` SELECT (needed so
        # this stays testable against a plain fake/dict-backed `db`, not only
        # a real ORM session) returns it as a JSON-encoded string on SQLite,
        # not an auto-decoded Python list -- decode explicitly rather than
        # indexing into the raw `'['` character.
        contract_raw = row["model_contract"]
        if isinstance(contract_raw, str):
            try:
                contract = _json.loads(contract_raw) if contract_raw else []
            except ValueError as exc:
                raise ModelConfigurationError(f"MODEL_CONTRACT_MALFORMED: {version_id}") from exc
        else:
            contract = contract_raw or []
        observed_model_id = (
            contract[0].get("provider_model_revision") if contract and isinstance(contract[0], dict) else None
        )
        if observed_model_id != MODEL_ID:
            raise ModelConfigurationError(f"MODEL_ID_MISMATCH: {observed_model_id!r}")
        if row["api_base"] != OFFICIAL_ORIGIN:
            raise ModelConfigurationError(f"ORIGIN_MISMATCH: {row['api_base']!r}")
        return ImmutableModelConfigVersion(
            version_id=row["id"],
            provider="deepseek",
            model_id=MODEL_ID,
            origin=OFFICIAL_ORIGIN,
            behavior_hash=row["behavior_hash"],
            frozen_at=row["created_at"],
        )
    finally:
        if owns_db:
            db.close()


def llm_call_kwargs(model_config, db=None) -> dict | None:
    if not model_config:
        return None
    cfg_type = getattr(model_config, "config_type", None) or "llm"
    if cfg_type != "llm":
        # OCR/other stay on the legacy tagged path — byte-for-byte unchanged.
        from app.services import encryption_service

        models = _as_list(getattr(model_config, "models", None))
        model_name = str(models[0]) if models else ""
        if not model_name:
            return None
        api_key = ""
        encrypted = getattr(model_config, "api_key_encrypted", None)
        if encrypted:
            api_key = encryption_service.decrypt(encrypted)
        return {
            "provider": getattr(model_config, "provider", None),
            "api_key": api_key,
            "api_base": getattr(model_config, "api_base", None),
            "model": model_name,
        }
    # LLM callers pin the immutable active version; no fallback.
    from app.database import SessionLocal
    from app.services.model_callers.extraction import (
        resolve_llm_caller,
        ModelVersionUnavailableError,
    )

    owns_db = db is None
    db = db or SessionLocal()
    try:
        return resolve_llm_caller(db, model_config.id)
    except ModelVersionUnavailableError:
        return None
    finally:
        if owns_db:
            db.close()
