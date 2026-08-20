from pydantic import BaseModel


class McpWriteRequestOut(BaseModel):
    id: str
    ontology_id: str
    release_id: str
    descriptor_id: str
    target_instance_id: str | None = None
    parameters: dict
    preview_hash: str
    preview_canonical: str
    status: str
    created_at: object
    resolved_at: object | None = None
