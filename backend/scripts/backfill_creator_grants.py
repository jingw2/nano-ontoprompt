"""Backfill missing Ontology creator grants (repair path for Issue 1).

Ontologies created before the 0003 access foundation, or written by direct-SQL
flows that bypass `POST /api/v1/ontologies`, have no `OntologyProjectAccessGrant`
row — the publication lifecycle panel then gets 404 ONTOLOGY_NOT_FOUND on
`GET /api/v1/ontologies/{id}/releases` and would stay in loading.  Run this to
repair: it inserts the exact creator grant for every active editor/admin creator
(discover|read|edit|publish), a discover|read grant for active viewer creators,
and an `ONTOLOGY_OWNER_RECOVERY_REQUIRED` finding for inactive/missing creators.

Idempotent (ON CONFLICT DO NOTHING).  Requires a schema that already has the
grant table (0003+) — run `scripts/run_migrations.py upgrade head` first.

Usage: DATABASE_URL=postgresql://user:pass@host:5432/db python scripts/backfill_creator_grants.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.services.ontology_access import backfill_creator_grants


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2
    engine = create_engine(url)
    try:
        with Session(engine) as db:
            report = backfill_creator_grants(db)
    except RuntimeError as exc:
        print(f"backfill aborted: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print(
        "creator-grant backfill complete: "
        f"{report['editor_or_admin_grants']} editor/admin grants, "
        f"{report['viewer_grants']} viewer grants, "
        f"{report['owner_recovery_findings_inserted']} owner-recovery findings inserted"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
