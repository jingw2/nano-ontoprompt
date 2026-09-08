from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user, require_editor
from app.models.user import User
from app.services.publication.working_copy import OntologyWorkingCopyService
from app.models.logic import LogicRule
from app.schemas.logic import LogicRuleCreate, LogicRuleUpdate, LogicRuleOut
import uuid

router = APIRouter()

@router.get("")
def list_logic(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    items = db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id).all()
    return {"data": [LogicRuleOut.model_validate(r).model_dump() for r in items]}

@router.post("", status_code=201)
def create_logic(ontology_id: str, body: LogicRuleCreate, db: Session = Depends(get_db), current_user: User = Depends(require_editor)):
    def _write():
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        r = LogicRule(id=str(uuid.uuid4()), ontology_id=ontology_id, **data)
        db.add(r); db.flush()
        return {"data": LogicRuleOut.model_validate(r).model_dump()}
    return OntologyWorkingCopyService.mutate(db, ontology_id=ontology_id, actor_id=current_user.id, operation="logic.create", callback=_write)

@router.get("/{logic_id}")
def get_logic(ontology_id: str, logic_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    r = db.query(LogicRule).filter(LogicRule.id == logic_id, LogicRule.ontology_id == ontology_id).first()
    if not r:
        raise HTTPException(404, "Not found")
    return {"data": LogicRuleOut.model_validate(r).model_dump()}

@router.put("/{logic_id}")
def update_logic(ontology_id: str, logic_id: str, body: LogicRuleUpdate, db: Session = Depends(get_db), current_user: User = Depends(require_editor)):
    r = db.query(LogicRule).filter(LogicRule.id == logic_id, LogicRule.ontology_id == ontology_id).first()
    if not r:
        raise HTTPException(404, "Not found")
    def _write():
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(r, k, v)
        db.flush()
        return {"data": LogicRuleOut.model_validate(r).model_dump()}
    return OntologyWorkingCopyService.mutate(db, ontology_id=ontology_id, actor_id=current_user.id, operation="logic.update", callback=_write)

@router.delete("/{logic_id}", status_code=204)
def delete_logic(ontology_id: str, logic_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_editor)):
    r = db.query(LogicRule).filter(LogicRule.id == logic_id, LogicRule.ontology_id == ontology_id).first()
    if not r:
        raise HTTPException(404, "Not found")
    def _write():
        db.delete(r); db.flush()
    return OntologyWorkingCopyService.mutate(db, ontology_id=ontology_id, actor_id=current_user.id, operation="logic.delete", callback=_write)


@router.post("/{logic_id}/toggle")
def toggle_logic(ontology_id: str, logic_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_editor)):
    """Human Review: 启用/禁用规则"""
    r = db.query(LogicRule).filter(LogicRule.id == logic_id, LogicRule.ontology_id == ontology_id).first()
    if not r:
        raise HTTPException(404, "Not found")
    def _write():
        r.enabled = not getattr(r, 'enabled', True)
        try:
            from app.models.v2.logic import OntologyLogicRule
            v2 = db.query(OntologyLogicRule).filter(
                OntologyLogicRule.ontology_id == ontology_id,
                OntologyLogicRule.name == r.name_cn,
            ).first()
            if v2:
                v2.enabled = r.enabled
                if not r.enabled and v2.status != "published":
                    v2.status = "disabled"
        except Exception:
            pass
        return {"enabled": r.enabled}
    return OntologyWorkingCopyService.mutate(db, ontology_id=ontology_id, actor_id=current_user.id, operation="logic.toggle", callback=_write)


@router.post("/publish")
def publish_logic_rules(ontology_id: str, db: Session = Depends(get_db), _=Depends(require_editor)):
    """Human Review: 发布所有草稿规则 — direct tool publication is closed; use the Ontology compiler (P1C)."""
    raise HTTPException(status_code=403, detail="PUBLICATION_NOT_ENABLED")
