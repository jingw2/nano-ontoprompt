"""Latched normalized-writer cutover (P1B-CUTOVER).

The cutover compares the normalized identity projection with the legacy
`Entity.properties` payloads (dual writes), requires zero divergence and zero
open blocking findings, serializes on a PostgreSQL advisory lock, and
atomically sets the irreversible `PublicationActivationLatch`.  The 0003
delete-guard triggers are no-ops before the latch and reject deletion of an
entity that still has normalized-definition or finding references after it.

`upgrade_cutover_guards()`/`downgrade_cutover_guards()` add the latch table,
Entity/Relation deprecation columns, and the guard triggers to revision 0003.
"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.services.governance_audit import enqueue_audit
from app.services.publication.preflight import preflight_entity_properties

UUID_CHECK = (
    "VALUE ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    "[0-9a-f]{4}-[0-9a-f]{12}$'"
)
LATCH_ID = "00000000-0000-0000-0000-00000000000c"
CUTOVER_LOCK_KEY = "publication-cutover-v1"


class CutoverBlocked(Exception):
    pass


def is_latched(db: Session) -> bool:
    return db.execute(sa.text("SELECT EXISTS (SELECT 1 FROM publication_activation_latch)")).scalar_one()


def compare_dual_writes(db: Session, ontology_id: str | None = None) -> dict:
    """Compare the normalized projection with the legacy properties payloads."""
    statement = (
        "SELECT e.id, e.ontology_id, e.name_cn, e.properties FROM entities e "
        "JOIN ontology_projects o ON o.id = e.ontology_id"
    )
    params: dict = {}
    if ontology_id:
        statement += " WHERE e.ontology_id = :o"
        params["o"] = ontology_id
    divergences = []
    scanned = 0
    for row in db.execute(sa.text(statement), params).mappings().all():
        scanned += 1
        definitions, _ = preflight_entity_properties(
            row["ontology_id"], row["id"], row["name_cn"], row["properties"] or {}
        )
        for definition in definitions:
            existing = db.execute(
                sa.text(
                    "SELECT value_type FROM entity_property_definitions "
                    "WHERE entity_id = :e AND normalized_key = :n"
                ),
                {"e": row["id"], "n": definition["normalized_key"]},
            ).mappings().one_or_none()
            if existing is None:
                divergences.append({
                    "entity_id": row["id"],
                    "key": definition["key"],
                    "normalized_key": definition["normalized_key"],
                    "reason": "normalized definition missing",
                })
            elif existing["value_type"] != definition["value_type"]:
                divergences.append({
                    "entity_id": row["id"],
                    "key": definition["key"],
                    "normalized_key": definition["normalized_key"],
                    "reason": "value_type divergence",
                })
    return {"ok": not divergences, "divergences": divergences, "entities_scanned": scanned}


def _open_findings(db: Session) -> int:
    return db.execute(
        sa.text("SELECT count(*) FROM ontology_migration_findings WHERE status = 'open'")
    ).scalar_one()


def activate_cutover(db: Session, *, actor_id: str, build_manifest_hash: str) -> dict:
    """Atomically set the irreversible latch after the zero-report gate."""
    existing = db.execute(
        sa.text(
            "SELECT activated_by, build_manifest_hash, activated_at FROM publication_activation_latch"
        )
    ).mappings().one_or_none()
    if existing is not None:
        return {
            "id": LATCH_ID,
            "activated_by": existing["activated_by"],
            "build_manifest_hash": existing["build_manifest_hash"],
            "activated_at": existing["activated_at"],
        }
    report = compare_dual_writes(db)
    if not report["ok"]:
        raise CutoverBlocked("CUTOVER_DIVERGENCE: " + str(report["divergences"]))
    if _open_findings(db) > 0:
        raise CutoverBlocked("CUTOVER_BLOCKED: open blocking findings remain")
    db.execute(sa.text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": CUTOVER_LOCK_KEY})
    db.execute(
        sa.text(
            "INSERT INTO publication_activation_latch (id, activated_at, activated_by, build_manifest_hash) "
            "VALUES (:id, CURRENT_TIMESTAMP, :actor, :hash)"
        ),
        {"id": LATCH_ID, "actor": actor_id, "hash": build_manifest_hash},
    )
    domain = db.execute(sa.text("SELECT id FROM security_domains WHERE status='active' LIMIT 1")).scalar_one()
    enqueue_audit(
        db.connection(),
        security_domain_id=domain,
        correlation_id=f"cutover:{LATCH_ID}",
        operation="publication.cutover.activate",
        decision="allow",
        outcome="succeeded",
        actor_user_id=actor_id,
        retention_class="standard",
    )
    db.commit()
    return {
        "id": LATCH_ID,
        "activated_by": actor_id,
        "build_manifest_hash": build_manifest_hash,
        "activated_at": None,
    }


def upgrade_cutover_guards() -> None:
    for table in ("entities", "relations"):
        op.add_column(table, sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column("deprecated_by", sa.String(36), nullable=True))
    op.create_table(
        "publication_activation_latch",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("activated_by", sa.String(36), nullable=False),
        sa.Column("build_manifest_hash", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_publication_activation_latch"),
        sa.CheckConstraint(UUID_CHECK.replace("VALUE", "id"), name="ck_publication_activation_latch_id_uuid"),
        sa.CheckConstraint(f"id = '{LATCH_ID}'", name="ck_publication_activation_latch_singleton"),
    )
    op.execute(
        """
        CREATE FUNCTION reject_publication_latch_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'LATCH_IMMUTABLE';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER publication_activation_latch_immutable BEFORE UPDATE OR DELETE "
        "ON publication_activation_latch FOR EACH ROW EXECUTE FUNCTION reject_publication_latch_mutation()"
    )
    op.execute(
        """
        CREATE FUNCTION guard_definition_delete() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE latched boolean; referenced boolean;
        BEGIN
          EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I.publication_activation_latch)', TG_TABLE_SCHEMA)
            INTO latched;
          IF NOT latched THEN RETURN OLD; END IF;
          IF TG_TABLE_NAME = 'entities' THEN
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I.entity_property_definitions WHERE entity_id = %L)', TG_TABLE_SCHEMA, OLD.id)
              INTO referenced;
            IF referenced THEN RAISE EXCEPTION 'DEFINITION_IN_USE'; END IF;
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I.ontology_migration_findings WHERE entity_id = %L)', TG_TABLE_SCHEMA, OLD.id)
              INTO referenced;
            IF referenced THEN RAISE EXCEPTION 'DEFINITION_IN_USE'; END IF;
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I.entity_instances WHERE entity_id = %L)', TG_TABLE_SCHEMA, OLD.id)
              INTO referenced;
            IF referenced THEN RAISE EXCEPTION 'DEFINITION_IN_USE'; END IF;
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I.relations WHERE source_entity = %L OR target_entity = %L)', TG_TABLE_SCHEMA, OLD.id, OLD.id)
              INTO referenced;
            IF referenced THEN RAISE EXCEPTION 'DEFINITION_IN_USE'; END IF;
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I.ontology_releases r WHERE r.manifest_projection @> CAST(%L AS jsonb))', TG_TABLE_SCHEMA, '{"entities":[{"id":"' || OLD.id || '"}]}')
              INTO referenced;
            IF referenced THEN RAISE EXCEPTION 'DEFINITION_IN_USE'; END IF;
          ELSIF TG_TABLE_NAME = 'relations' THEN
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I.ontology_releases r WHERE r.manifest_projection @> CAST(%L AS jsonb))', TG_TABLE_SCHEMA, '{"relations":[{"id":"' || OLD.id || '"}]}')
              INTO referenced;
            IF referenced THEN RAISE EXCEPTION 'DEFINITION_IN_USE'; END IF;
          END IF;
          RETURN OLD;
        END;
        $$
        """
    )
    for table in ("entities", "relations"):
        op.execute(
            f"CREATE TRIGGER {table}_delete_guard BEFORE DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION guard_definition_delete()"
        )


def downgrade_cutover_guards() -> None:
    for table in ("entities", "relations"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_delete_guard ON {table}")
    op.execute("DROP FUNCTION IF EXISTS guard_definition_delete()")
    op.execute("DROP TRIGGER IF EXISTS publication_activation_latch_immutable ON publication_activation_latch")
    op.execute("DROP FUNCTION IF EXISTS reject_publication_latch_mutation()")
    op.drop_table("publication_activation_latch")
    for table in ("relations", "entities"):
        op.drop_column(table, "deprecated_at")
        op.drop_column(table, "deprecated_by")
