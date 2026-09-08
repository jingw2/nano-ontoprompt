from typing import Dict, List, Optional
from pydantic import BaseModel


class CreateSkillPackageRequest(BaseModel):
    name: str


class SkillSignatureIn(BaseModel):
    public_key_hex: str
    signature_hex: str
    signer_identity: Optional[str] = None


class CreateSkillVersionRequest(BaseModel):
    package_id: str
    manifest: Dict
    signatures: List[SkillSignatureIn]
