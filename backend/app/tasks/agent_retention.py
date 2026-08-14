"""Fixed-policy Agent retention Celery task (P3A-RETENTION).

Claims the per-domain purge job with a lease and runs the memory-free ten
idempotent steps.  No dynamic policy, hold, epoch or memory cleanup.
"""
from app.tasks.celery_app import celery_app


@celery_app.task(bind=True, name="agent.retention_purge")
def agent_retention_purge(self, security_domain_id: str, purge_class: str = "turn"):
    import app.models  # noqa: F401 — register all tables
    from app.database import SessionLocal
    from app.services.retention.fixed_policy import claim_purge_job, run_fixed_purge

    db = SessionLocal()
    try:
        claim = claim_purge_job(db, security_domain_id=security_domain_id, purge_class=purge_class)
        if claim is None:
            return {"skipped": True}
        return run_fixed_purge(db, security_domain_id=security_domain_id,
                               job_id=claim["id"], claim_token=claim["claim_token"])
    finally:
        db.close()
