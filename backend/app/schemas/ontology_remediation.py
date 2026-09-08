"""Migration-remediation request/response schemas.

`RemediatePropertyRequest` requires a complete explicit contract and CASes on
`base_working_revision` plus the finding's `source_hash`; the service preserves
the source payload, creates or updates the normalized definition, resolves the
finding, increments the working revision, and audits atomically.  The
executable contract remediation activates with the P1B cutover.
"""
from datetime import datetime

from pydantic import BaseModel, Field


class RemediatePropertyRequest(BaseModel):
    base_working_revision: int = Field(ge=0)
    source_hash: str
    property_key: str
    explicit_schema_metadata: dict


class RemediateExecutableRequest(BaseModel):
    base_working_revision: int = Field(ge=0)
    explicit_contract_fields: dict


class MigrationRemediationFindingResponse(BaseModel):
    id: str
    ontology_id: str
    entity_id: str | None = None
    kind: str
    item_id: str
    code: str
    path: str
    message: str
    classification: str | None = None
    status: str
    revision: int | None = None
    created_at: datetime | None = None


class MigrationRemediationResponse(BaseModel):
    finding: MigrationRemediationFindingResponse
    definition: dict | None = None
    working_revision: int
