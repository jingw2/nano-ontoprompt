from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class FileOut(BaseModel):
    id: str
    ontology_id: str
    filename: str
    file_size: int
    mime_type: Optional[str]
    conversion_ok: bool = True
    conversion_error: Optional[str] = None
    created_at: datetime
    model_config = {"from_attributes": True}
