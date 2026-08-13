"""Ontology project access-grant request/response schemas.

The capability vocabulary is closed (`discover|read|edit|publish`); Pydantic,
storage, and authorization reject unknown values.
"""
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.services.ontology_access import CAPABILITIES


def _validate_capabilities(values):
    if not values or len(set(values)) != len(values) or any(value not in CAPABILITIES for value in values):
        raise ValueError(f"capabilities must be a non-empty subset of {list(CAPABILITIES)}")
    return values


class CreateOntologyProjectAccessGrantRequest(BaseModel):
    user_id: str
    capabilities: list[str]
    base_revision: int = Field(default=0, ge=0)

    @field_validator("capabilities")
    @classmethod
    def _closed_vocabulary(cls, values):
        return _validate_capabilities(values)


class ReviseOntologyProjectAccessGrantRequest(BaseModel):
    base_revision: int = Field(ge=1)
    capabilities: list[str]

    @field_validator("capabilities")
    @classmethod
    def _closed_vocabulary(cls, values):
        return _validate_capabilities(values)


class RevokeOntologyProjectAccessGrantRequest(BaseModel):
    base_revision: int = Field(ge=1)


class RecoverOntologyOwnerRequest(BaseModel):
    base_finding_revision: int = Field(ge=1)
    assignee_user_id: str


class OntologyProjectAccessGrantResponse(BaseModel):
    id: str
    ontology_id: str
    user_id: str
    capabilities: list[str]
    revision: int
    status: str
    created_by: str
    revoked_by: str | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    revoked_at: datetime | None = None
