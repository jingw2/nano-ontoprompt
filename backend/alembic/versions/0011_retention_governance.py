"""Retention governance (P6A): versioned policies, legal holds, and the
per-domain epoch counter that serializes policy/hold/purge races.

`security_domains` is immutable (see `security_domains_immutable` trigger) —
the epoch counter lives in its own `retention_epochs` table instead of a
column there. Existing rows are backfilled onto a built-in policy whose
rules equal today's fixed minimums (see `TABLE_MINIMUMS` in
`app/services/retention/policy.py`), so purge behavior is unchanged until an
administrator activates a different policy.
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_retention_governance"
down_revision = "0010_agent_single_binding"
branch_labels = None
depends_on = None

# mirrors TABLE_MINIMUMS in app/services/retention/policy.py — duplicated
# here (migration-time only) because migrations must not import app code
BUILT_IN_RULES = {
    "application_state_snapshot.redact": 365,
    "clarification.delete": 1,
    "runtime_event.redact": 90,
    "message.redact": 90,
    "model_invocation.delete": 5,
    "node_execution.delete": 5,
    "checkpoint.delete": 7,
    "dispatch_outbox.delete": 30,
    "turn.delete": 7,
    "graph_index.delete": 7,
}


def upgrade() -> None:
    op.create_table(
        "retention_policies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("security_domain_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("active_version_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_retention_policies_status"),
        sa.ForeignKeyConstraint(["security_domain_id"], ["security_domains.id"], name="fk_retention_policies_domain", ondelete="RESTRICT"),
        sa.UniqueConstraint("security_domain_id", name="uq_retention_policies_domain"),
    )

    op.create_table(
        "retention_policy_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("policy_id", sa.String(36), nullable=False),
        sa.Column("version_no", sa.BigInteger(), nullable=False),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("canonical_hash", sa.String(64), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("status IN ('pending', 'active', 'superseded')", name="ck_rpv_status"),
        sa.ForeignKeyConstraint(["policy_id"], ["retention_policies.id"], name="fk_rpv_policy", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_rpv_creator", ondelete="RESTRICT"),
        sa.UniqueConstraint("policy_id", "version_no", name="uq_rpv_policy_version"),
    )

    op.create_table(
        "retention_holds",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("security_domain_id", sa.String(36), nullable=False),
        sa.Column("scope_type", sa.String(20), nullable=False),
        sa.Column("scope_id", sa.String(36), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("issued_by", sa.String(36), nullable=False),
        sa.Column("released_by", sa.String(36), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("scope_type IN ('subject', 'session', 'turn', 'object')", name="ck_rh_scope_type"),
        sa.ForeignKeyConstraint(["security_domain_id"], ["security_domains.id"], name="fk_rh_domain", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["issued_by"], ["users.id"], name="fk_rh_issuer", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["released_by"], ["users.id"], name="fk_rh_releaser", ondelete="RESTRICT"),
    )
    op.create_index("ix_rh_active_scope", "retention_holds", ["security_domain_id", "scope_type", "scope_id"],
                     postgresql_where=sa.text("released_at IS NULL"))

    op.create_table(
        "retention_epochs",
        sa.Column("security_domain_id", sa.String(36), primary_key=True),
        sa.Column("epoch", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["security_domain_id"], ["security_domains.id"], name="fk_re_domain", ondelete="RESTRICT"),
    )

    # backfill: one built-in active policy/version + epoch row per existing domain
    # Must happen BEFORE adding FKs that reference these tables
    conn = op.get_bind()
    domains = conn.execute(sa.text("SELECT id FROM security_domains")).scalars().all()
    for domain_id in domains:
        import hashlib
        import json
        import uuid as uuid_mod
        policy_id = str(uuid_mod.uuid4())
        version_id = str(uuid_mod.uuid4())
        canonical = json.dumps(BUILT_IN_RULES, sort_keys=True, separators=(",", ":"))
        # Insert policy first (without active_version_id set yet)
        conn.execute(sa.text(
            "INSERT INTO retention_policies (id, security_domain_id, status, active_version_id, created_at, updated_at) "
            "VALUES (:id, :domain, 'active', NULL, now(), now())"
        ), {"id": policy_id, "domain": domain_id})
        # Then insert version
        conn.execute(sa.text(
            "INSERT INTO retention_policy_versions "
            "(id, policy_id, version_no, rules, canonical_hash, effective_at, status, created_by, created_at) "
            "VALUES (:id, :policy, 1, CAST(:rules AS json), :hash, now(), 'active', NULL, now())"
        ), {"id": version_id, "policy": policy_id, "rules": canonical,
            "hash": hashlib.sha256(canonical.encode()).hexdigest()})
        # Update policy to reference the version
        conn.execute(sa.text(
            "UPDATE retention_policies SET active_version_id = :version_id WHERE id = :policy_id"
        ), {"version_id": version_id, "policy_id": policy_id})
        # Insert epoch
        conn.execute(sa.text(
            "INSERT INTO retention_epochs (security_domain_id, epoch, updated_at) VALUES (:domain, 0, now())"
        ), {"domain": domain_id})

    # Now add FK constraints after backfill data is in place
    op.create_foreign_key(
        "fk_retention_policies_active_version", "retention_policies", "retention_policy_versions",
        ["active_version_id"], ["id"], ondelete="RESTRICT",
    )

    op.create_foreign_key(
        "fk_agent_purge_markers_policy_version", "agent_purge_markers", "retention_policy_versions",
        ["policy_version_id"], ["id"], ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_agent_purge_markers_policy_version", "agent_purge_markers", type_="foreignkey")
    op.drop_table("retention_epochs")
    op.drop_index("ix_rh_active_scope", table_name="retention_holds")
    op.drop_table("retention_holds")
    op.drop_constraint("fk_retention_policies_active_version", "retention_policies", type_="foreignkey")
    op.drop_table("retention_policy_versions")
    op.drop_table("retention_policies")
