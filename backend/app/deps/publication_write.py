"""Old-build rejection dependency for governed publication writes.

`require_bridge_build` rejects any process whose database is not at the
bridge-compatible 0003 revision (or lacks the activation latch table) with
`MINIMUM_BUILD_NOT_READY`, so a pre-bridge writer can never reach the
latched normalized write paths.  I-BACKEND wires this into the route
dependencies that own normalized definition mutations.
"""
import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.orm import Session


def require_bridge_build(db: Session) -> None:
    """Raise MINIMUM_BUILD_NOT_READY unless the bridge (0003) schema is present."""
    try:
        head = db.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
        latched_table = db.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'publication_activation_latch')"
            )
        ).scalar_one()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="MINIMUM_BUILD_NOT_READY") from exc
    if head != "0003_publication_governance" or not latched_table:
        raise HTTPException(status_code=503, detail="MINIMUM_BUILD_NOT_READY")
