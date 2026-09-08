"""Agent action preview/approval schemas (P5A-PREVIEW/P5B-APPROVAL).

Canonical instance-only preview envelope; the approval request carries the
preview hash so the resolve CAS revalidates the immutable preview.
"""

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel


class PreviewActionRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    ontology_id: str
    release_id: str
    descriptor_id: str
    parameters: Dict[str, Any] = {}
    target_instance_id: Optional[str] = None


class PreviewActionResponse(BaseModel):
    model_config = {"protected_namespaces": ()}
    preview_id: str
    hash: str
    canonical: str
    schema_version: str
    release_version_no: int
    deterministic: bool
