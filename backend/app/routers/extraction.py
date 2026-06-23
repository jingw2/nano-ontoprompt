from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user
from app.models.extraction_task import ExtractionTask
from app.models.file import UploadedFile
from app.models.ontology import OntologyProject
from app.services.document_service import combine_converted_files
from app.schemas.extraction import ExtractionRequest, ExtractionTaskOut
import uuid

router = APIRouter()

@router.post("")
def start_extraction(ontology_id: str, body: ExtractionRequest, db: Session = Depends(get_db), _=Depends(get_current_user)):
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not project:
        raise HTTPException(404, "Ontology not found")

    files_query = db.query(UploadedFile).filter(UploadedFile.ontology_id == ontology_id)
    if body.file_ids:
        files_query = files_query.filter(UploadedFile.id.in_(body.file_ids))
    files = files_query.all()
    if not files:
        raise HTTPException(422, "请先上传文件")
    _, convert_error = combine_converted_files(files)
    if convert_error:
        raise HTTPException(422, convert_error)

    task = ExtractionTask(
        id=str(uuid.uuid4()),
        ontology_id=ontology_id,
        prompt_id=body.prompt_id,
        model_id=body.model_id,
        status="queued",
        parameters={"model_name": body.model_name, "file_ids": body.file_ids, "constraints": body.constraints or []},
        progress={"stage": "queued", "pct": 0},
    )
    db.add(task); db.commit(); db.refresh(task)

    # Update ontology status
    project.status = "creating"
    db.commit()

    # Queue Celery task
    try:
        from app.tasks.extraction import run_extraction
        run_extraction.delay(task.id)
    except Exception:
        # If celery not available, run synchronously in background thread
        import threading
        def run_sync():
            from app.tasks.extraction import run_extraction
            try:
                run_extraction(task.id)
            except Exception:
                pass
        threading.Thread(target=run_sync, daemon=True).start()

    return {"data": {"task_id": task.id}, "message": "Extraction queued"}

@router.get("/status")
def get_extraction_status(ontology_id: str, task_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    task = db.query(ExtractionTask).filter(ExtractionTask.id == task_id, ExtractionTask.ontology_id == ontology_id).first()
    if not task:
        raise HTTPException(404, "Task not found")
    return {"data": ExtractionTaskOut.model_validate(task).model_dump()}
