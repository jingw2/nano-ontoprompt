"""Seed the synthetic sources used by ``verify_refresh_checkpoint.sh``.

This script is intentionally one-shot and only targets the database exposed
to the running checkpoint Compose project. It uses the existing application
JWT signing mechanism for an editor credential; it does not introduce the
future Runtime delegation subsystem.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

# ``python scripts/seed_refresh_checkpoint_fixture.py`` puts only the
# script directory on ``sys.path``. Add the mounted backend root so the
# application package resolves in the same way as the API process.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.database import SessionLocal
from app.models.security_domain import DEFAULT_SECURITY_DOMAIN_ID, SecurityDomain
from app.models.user import User
from app.models.v2.connection import Connection
from app.models.v2.refresh import RefreshSourceState
from app.services.auth_service import create_access_token, hash_password


ROOT = Path(__file__).resolve().parents[2]
WEBHOOK_FIXTURE = ROOT / "test_data" / "runtime" / "fixtures" / "checkpoint_webhook_body.json"
OPERATOR_ID = "checkpoint-operator"
BATCH_SOURCE_ID = "source-checkpoint-batch"
EVENT_SOURCE_ID = "source-checkpoint-event"
BATCH_RESOURCE = "checkpoint_batch_rows"
EVENT_RESOURCE = "checkpoint_events"


def _ensure_operator(db) -> User:
    domain = db.get(SecurityDomain, DEFAULT_SECURITY_DOMAIN_ID)
    if domain is None:
        domain = SecurityDomain(
            id=DEFAULT_SECURITY_DOMAIN_ID, key="default", status="active",
        )
        db.add(domain)
        db.flush()
    operator = db.get(User, OPERATOR_ID)
    if operator is None:
        operator = User(
            id=OPERATOR_ID, username="checkpoint-operator",
            email="checkpoint-operator@synthetic.invalid",
            password_hash=hash_password(secrets.token_urlsafe(24)), role="editor",
            is_active=True, security_domain_id=DEFAULT_SECURITY_DOMAIN_ID,
        )
        db.add(operator)
        db.flush()
    return operator


def _ensure_sources(db) -> str:
    now = datetime.now(timezone.utc)
    # The batch connector reads this single synthetic row through the same
    # PostgreSQL database used by the backend container.
    db.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {BATCH_RESOURCE} (
            id VARCHAR(100) PRIMARY KEY,
            state VARCHAR(100) NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
    """))
    db.execute(text(f"""
        INSERT INTO {BATCH_RESOURCE} (id, state, updated_at)
        VALUES (:id, :state, :updated_at)
        ON CONFLICT (id) DO UPDATE SET state = EXCLUDED.state, updated_at = EXCLUDED.updated_at
    """), {"id": "checkpoint-row-001", "state": "ready", "updated_at": now})

    webhook_secret = secrets.token_urlsafe(32)
    batch_config = {
        "connection_string": settings.database_url,
        "cursor_contract": "watermark_primary_key",
        "refresh_policy": "batch",
        "watermark_column": "updated_at",
        "primary_key_column": "id",
        "resource": BATCH_RESOURCE,
    }
    event_config = {
        "cursor_contract": "opaque_source_cursor",
        "refresh_policy": "event_driven",
        # This fixture is synthetic and ephemeral. The API's existing
        # managed-webhook route reads this secret only to verify the signed
        # request; it is never printed by this script.
        "webhook_secret": webhook_secret,
    }

    batch = db.get(Connection, BATCH_SOURCE_ID)
    if batch is None:
        batch = Connection(
            id=BATCH_SOURCE_ID, name="Synthetic checkpoint batch", kind="postgres",
            status="active", config=batch_config, refresh_policy="batch",
            cursor_contract="watermark_primary_key", created_by=OPERATOR_ID,
        )
        db.add(batch)
    else:
        batch.config = batch_config
        batch.status = "active"
        batch.refresh_policy = "batch"
        batch.cursor_contract = "watermark_primary_key"

    event = db.get(Connection, EVENT_SOURCE_ID)
    if event is None:
        event = Connection(
            id=EVENT_SOURCE_ID, name="Synthetic checkpoint event", kind="rest",
            status="active", config=event_config, refresh_policy="event_driven",
            cursor_contract="opaque_source_cursor", created_by=OPERATOR_ID,
        )
        db.add(event)
    else:
        event.config = event_config
        event.status = "active"
        event.refresh_policy = "event_driven"
        event.cursor_contract = "opaque_source_cursor"
    db.flush()

    batch_state = db.query(RefreshSourceState).filter(
        RefreshSourceState.source_id == BATCH_SOURCE_ID,
        RefreshSourceState.resource == BATCH_RESOURCE,
    ).first()
    if batch_state is None:
        batch_state = RefreshSourceState(
            id="state-checkpoint-batch", source_id=BATCH_SOURCE_ID,
            resource=BATCH_RESOURCE, cursor_contract="watermark_primary_key",
            config_version=1, configuration={
                "refresh_policy": "batch", "cursor_contract": "watermark_primary_key",
                "watermark_column": "updated_at", "primary_key_column": "id",
            }, fencing_token=0,
        )
        db.add(batch_state)
    else:
        batch_state.cursor_contract = "watermark_primary_key"
        batch_state.configuration = {
            "refresh_policy": "batch", "cursor_contract": "watermark_primary_key",
            "watermark_column": "updated_at", "primary_key_column": "id",
        }

    event_state = db.query(RefreshSourceState).filter(
        RefreshSourceState.source_id == EVENT_SOURCE_ID,
        RefreshSourceState.resource == EVENT_RESOURCE,
    ).first()
    if event_state is None:
        event_state = RefreshSourceState(
            id="state-checkpoint-event", source_id=EVENT_SOURCE_ID,
            resource=EVENT_RESOURCE, cursor_contract="opaque_source_cursor",
            config_version=1, configuration={
                "refresh_policy": "event_driven", "cursor_contract": "opaque_source_cursor",
            }, fencing_token=0,
        )
        db.add(event_state)
    else:
        event_state.cursor_contract = "opaque_source_cursor"
        event_state.configuration = {
            "refresh_policy": "event_driven", "cursor_contract": "opaque_source_cursor",
        }
    db.commit()
    return webhook_secret


def main() -> int:
    # The live backend container mounts only ``backend/``. The checkpoint
    # therefore streams the repository fixture on stdin; local invocations
    # retain the convenient file fallback.
    body = sys.stdin.buffer.read()
    if not body:
        body = WEBHOOK_FIXTURE.read_bytes()
    webhook_secret = ""
    db = SessionLocal()
    try:
        operator = _ensure_operator(db)
        webhook_secret = _ensure_sources(db)
        token = create_access_token({"sub": operator.id, "role": operator.role})
    finally:
        db.close()

    # Body-only HMAC is one of ManagedWebhookAdapter's accepted canonical
    # forms. The checkpoint supplies a fresh timestamp header independently.
    signature = "sha256=" + hmac.new(
        webhook_secret.encode("utf-8"), body, hashlib.sha256,
    ).hexdigest()
    print(json.dumps({
        "operator_token": token,
        "webhook_secret": webhook_secret,
        "webhook_signature": signature,
    }, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
