"""Ontology lifecycle request/response schemas (P1C-API)."""
from datetime import datetime

from pydantic import BaseModel, Field


class MarkCreatedRequest(BaseModel):
    pass


class PublishOntologyRequest(BaseModel):
    base_working_revision: int | None = Field(default=None, ge=1)
    changelog: str | None = None


class ArchiveOntologyRequest(BaseModel):
    reason: str | None = None


class RuntimeSwitchRequest(BaseModel):
    reason: str | None = None


class OntologyLifecycleResponse(BaseModel):
    ontology_id: str
    status: str | None = None
    runtime_disabled: bool | None = None


class OntologyReleaseResponse(BaseModel):
    release_id: str
    ontology_id: str
    version_no: int
    version: str
    schema_hash: str
    created_by: str | None = None
    created_at: datetime | None = None
    manifest_projection: dict | None = None


class OntologyReleaseSummaryResponse(BaseModel):
    id: str
    version_no: int
    version: str
    created_by: str | None = None
    created_at: datetime | None = None
