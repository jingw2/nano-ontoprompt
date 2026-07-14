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


def llm_call_kwargs(model_config) -> dict | None:
    if not model_config:
        return None
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
