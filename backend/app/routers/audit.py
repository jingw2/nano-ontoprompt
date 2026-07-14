from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user
from app.models.audit_task import AuditTask
from app.models.ontology import OntologyProject
from app.schemas.audit import AuditRequest, AuditTaskOut
import uuid

router = APIRouter()


@router.post("")
def start_audit(ontology_id: str, body: AuditRequest, db: Session = Depends(get_db), _=Depends(get_current_user)):
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not project:
        raise HTTPException(404, "Ontology not found")

    task = AuditTask(
        id=str(uuid.uuid4()),
        ontology_id=ontology_id,
        model_id=body.model_id,
        model_name=body.model_name,
        status="queued",
        progress={"stage": "queued", "pct": 0},
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    try:
        from app.tasks.audit import run_audit
        run_audit.delay(task.id)
    except Exception:
        import threading

        def run_sync():
            from app.tasks.audit import run_audit
            try:
                run_audit(task.id)
            except Exception:
                pass

        threading.Thread(target=run_sync, daemon=True).start()

    return {"data": {"task_id": task.id}, "message": "Audit queued"}


@router.get("/status")
def get_audit_status(ontology_id: str, task_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    task = db.query(AuditTask).filter(AuditTask.id == task_id, AuditTask.ontology_id == ontology_id).first()
    if not task:
        raise HTTPException(404, "Audit task not found")
    return {"data": AuditTaskOut.model_validate(task).model_dump()}
