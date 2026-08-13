from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user, require_editor
from app.models.user import User
from app.services.publication.working_copy import OntologyWorkingCopyService
from app.models.action import Action
from app.schemas.action import ActionCreate, ActionUpdate, ActionOut
import uuid

router = APIRouter()

@router.get("")
def list_actions(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    items = db.query(Action).filter(Action.ontology_id == ontology_id).all()
    return {"data": [ActionOut.model_validate(a).model_dump() for a in items]}

@router.post("", status_code=201)
def create_action(ontology_id: str, body: ActionCreate, db: Session = Depends(get_db), current_user: User = Depends(require_editor)):
    def _write():
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        a = Action(id=str(uuid.uuid4()), ontology_id=ontology_id, **data)
        db.add(a); db.flush()
        return {"data": ActionOut.model_validate(a).model_dump()}
    return OntologyWorkingCopyService.mutate(db, ontology_id=ontology_id, actor_id=current_user.id, operation="action.create", callback=_write)

@router.get("/{action_id}")
def get_action(ontology_id: str, action_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    a = db.query(Action).filter(Action.id == action_id, Action.ontology_id == ontology_id).first()
    if not a:
        raise HTTPException(404, "Not found")
    return {"data": ActionOut.model_validate(a).model_dump()}

@router.put("/{action_id}")
def update_action(ontology_id: str, action_id: str, body: ActionUpdate, db: Session = Depends(get_db), current_user: User = Depends(require_editor)):
    a = db.query(Action).filter(Action.id == action_id, Action.ontology_id == ontology_id).first()
    if not a:
        raise HTTPException(404, "Not found")
    def _write():
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(a, k, v)
        db.flush()
        return {"data": ActionOut.model_validate(a).model_dump()}
    return OntologyWorkingCopyService.mutate(db, ontology_id=ontology_id, actor_id=current_user.id, operation="action.update", callback=_write)

@router.delete("/{action_id}", status_code=204)
def delete_action(ontology_id: str, action_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_editor)):
    a = db.query(Action).filter(Action.id == action_id, Action.ontology_id == ontology_id).first()
    if not a:
        raise HTTPException(404, "Not found")
    def _write():
        db.delete(a); db.flush()
    return OntologyWorkingCopyService.mutate(db, ontology_id=ontology_id, actor_id=current_user.id, operation="action.delete", callback=_write)


@router.post("/{action_id}/toggle")
def toggle_action(ontology_id: str, action_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_editor)):
    a = db.query(Action).filter(Action.id == action_id, Action.ontology_id == ontology_id).first()
    if not a:
        raise HTTPException(404, "Not found")
    def _write():
        a.enabled = not getattr(a, 'enabled', True)
        try:
            from app.models.v2.action import OntologyActionType
            v2 = db.query(OntologyActionType).filter(
                OntologyActionType.ontology_id == ontology_id,
                OntologyActionType.name == a.name_cn,
            ).first()
            if v2:
                v2.enabled = a.enabled
                if not a.enabled and v2.status != "published":
                    v2.status = "disabled"
        except Exception:
            pass
        return {"enabled": a.enabled}
    return OntologyWorkingCopyService.mutate(db, ontology_id=ontology_id, actor_id=current_user.id, operation="action.toggle", callback=_write)


@router.post("/publish")
def publish_actions(ontology_id: str, db: Session = Depends(get_db), _=Depends(require_editor)):
    """Direct tool publication is closed; use the Ontology compiler (P1C)."""
    raise HTTPException(status_code=403, detail="PUBLICATION_NOT_ENABLED")
