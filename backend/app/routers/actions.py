from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user
from app.models.action import Action
from app.schemas.action import ActionCreate, ActionUpdate, ActionOut
import uuid

router = APIRouter()

@router.get("")
def list_actions(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    items = db.query(Action).filter(Action.ontology_id == ontology_id).all()
    return {"data": [ActionOut.model_validate(a).model_dump() for a in items]}

@router.post("", status_code=201)
def create_action(ontology_id: str, body: ActionCreate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    a = Action(id=str(uuid.uuid4()), ontology_id=ontology_id, **data)
    db.add(a); db.commit(); db.refresh(a)
    return {"data": ActionOut.model_validate(a).model_dump()}

@router.get("/{action_id}")
def get_action(ontology_id: str, action_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    a = db.query(Action).filter(Action.id == action_id, Action.ontology_id == ontology_id).first()
    if not a:
        raise HTTPException(404, "Not found")
    return {"data": ActionOut.model_validate(a).model_dump()}

@router.put("/{action_id}")
def update_action(ontology_id: str, action_id: str, body: ActionUpdate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    a = db.query(Action).filter(Action.id == action_id, Action.ontology_id == ontology_id).first()
    if not a:
        raise HTTPException(404, "Not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(a, k, v)
    db.commit(); db.refresh(a)
    return {"data": ActionOut.model_validate(a).model_dump()}

@router.delete("/{action_id}", status_code=204)
def delete_action(ontology_id: str, action_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    a = db.query(Action).filter(Action.id == action_id, Action.ontology_id == ontology_id).first()
    if not a:
        raise HTTPException(404, "Not found")
    db.delete(a); db.commit()


@router.post("/{action_id}/toggle")
def toggle_action(ontology_id: str, action_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    a = db.query(Action).filter(Action.id == action_id, Action.ontology_id == ontology_id).first()
    if not a:
        raise HTTPException(404, "Not found")
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
    db.commit()
    return {"enabled": a.enabled}


@router.post("/publish")
def publish_actions(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    acts = db.query(Action).filter(
        Action.ontology_id == ontology_id,
        Action.status != 'published',
        Action.enabled == True,  # noqa: E712
    ).all()
    for a in acts:
        a.status = 'published'
    try:
        from app.models.v2.action import OntologyActionType
        v2_actions = db.query(OntologyActionType).filter(
            OntologyActionType.ontology_id == ontology_id,
            OntologyActionType.status != "published",
            OntologyActionType.enabled == True,  # noqa: E712
        ).all()
        for a in v2_actions:
            a.status = "published"
    except Exception:
        pass
    db.commit()
    return {"published": len(acts)}
