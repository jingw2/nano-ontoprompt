from app.tasks.celery_app import celery_app


@celery_app.task(bind=True)
def run_audit(self, task_id: str):
    import app.models  # noqa: F401 — register all tables
    from app.database import SessionLocal
    from app.models.audit_task import AuditTask
    from app.models.model_config import ModelConfig
    from app.models.entity import Entity
    from app.models.relation import Relation
    from app.models.logic import LogicRule
    from app.models.action import Action
    from app.services.audit_service import run_react_audit
    from app.services.encryption_service import decrypt

    db = SessionLocal()
    try:
        task = db.query(AuditTask).filter(AuditTask.id == task_id).first()
        if not task:
            return

        task.status = "running"
        task.progress = {"stage": "loading ontology", "pct": 10}
        db.commit()

        model_cfg = db.query(ModelConfig).filter(ModelConfig.id == task.model_id).first()
        if not model_cfg:
            task.status = "failed"
            task.error = "Model config not found"
            db.commit()
            return

        model_config = {
            "provider": model_cfg.provider,
            "api_key": decrypt(model_cfg.api_key_encrypted or ""),
            "api_base": model_cfg.api_base,
        }

        # Build compact ontology snapshot (only fields needed by audit tools)
        entities_raw = db.query(Entity).filter(Entity.ontology_id == task.ontology_id).all()
        relations_raw = db.query(Relation).filter(Relation.ontology_id == task.ontology_id).all()
        logic_raw = db.query(LogicRule).filter(LogicRule.ontology_id == task.ontology_id).all()
        actions_raw = db.query(Action).filter(Action.ontology_id == task.ontology_id).all()

        id_to_name = {e.id: e.name_cn for e in entities_raw}

        entities = [{"id": e.id, "name_cn": e.name_cn, "type": e.type or "Unknown"} for e in entities_raw[:300]]
        relations = [
            {
                "id": r.id,
                "source_entity": r.source_entity,
                "target_entity": r.target_entity,
                "source_name": id_to_name.get(r.source_entity, r.source_entity),
                "target_name": id_to_name.get(r.target_entity, r.target_entity),
                "type": r.type,
            }
            for r in relations_raw
        ]
        logic_rules = [
            {"id": r.id, "name_cn": r.name_cn, "linked_entities": r.linked_entities}
            for r in logic_raw
        ]
        actions = [
            {
                "id": a.id,
                "name_cn": a.name_cn,
                "linked_entities": a.linked_entities or [],
                "linked_logic_ids": a.linked_logic_ids or [],
            }
            for a in actions_raw
        ]

        snapshot = {
            "entities": entities,
            "relations": relations,
            "logic_rules": logic_rules,
            "actions": actions,
        }

        task.progress = {"stage": "running react agent", "pct": 30}
        task.react_trace = []
        db.commit()

        max_steps = 12

        def on_step(current: int, total: int):
            pct = 30 + int(current / total * 55)
            task.progress = {"stage": "running react agent", "pct": pct}
            db.commit()

        def on_trace_step(trace: list):
            task.react_trace = trace
            db.commit()

        findings, trace = run_react_audit(
            ontology_snapshot=snapshot,
            model_config=model_config,
            model_name=task.model_name,
            on_step=on_step,
            on_trace_step=on_trace_step,
            max_steps=max_steps,
        )

        task.progress = {"stage": "saving findings", "pct": 90}
        db.commit()

        task.findings = findings
        task.react_trace = trace
        task.status = "completed"
        task.progress = {"stage": "done", "pct": 100}
        db.commit()

    except Exception as e:
        try:
            task.status = "failed"
            task.error = str(e)
            db.commit()
        except Exception:
            pass
    finally:
        db.close()
