"""P2A-MODEL: role unification and immutable LLM model behavior versioning.

Migration `0004_roles_model_versions` helpers plus the runtime service:

* `upgrade_role_unification` backfills the legacy `user` role to `viewer`,
  installs the `viewer|editor|admin` CHECK constraint and writes an append-only
  audit-outbox summary; it never aborts on rows it reports.
* `upgrade_model_versions_foundation` adds the additive identity/version/
  credential/finding schema (never aborts on data).
* `upgrade_legacy_llm_rows` classifies every `config_type='llm'` row read-only:
  eligible rows receive one stable identity (the ModelConfig itself), an exact
  immutable behavior version, a credential binding and an active pointer;
  ineligible rows (unknown provider, empty model lists, mutable model aliases,
  duplicate stable identities, undecryptable credentials) become disabled
  `migration_blocked` identities with append-only findings and never abort DDL.
  OCR/`other` rows are reported but untouched and cannot block Agent delivery.
* The runtime service provides immutable selection, Agent-catalog eligibility,
  and administrator CAS remediation/archive primitives used by the admin API.

The migration never guesses a tokenizer/window: legacy versions keep the
verified-contract fields NULL (unverified) and `conservative_input_limit`
NULL; remediation supplies the complete contract explicitly.
"""

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone

from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import encryption_service

LLM_PROVIDERS = frozenset({"openai", "anthropic", "compatible"})
ROLE_VALUES = frozenset({"viewer", "editor", "admin"})


class ModelVersionUnavailable(Exception):
    """The identity has no active immutable behavior version (blocked/archived/
    missing), or the operation is not allowed for the current identity state."""


class ModelRevisionConflict(Exception):
    """base_revision CAS mismatch: the identity changed concurrently."""


class ModelContractInvalid(Exception):
    """The supplied immutable model contract is incomplete or inconsistent."""


def versioning_schema_present(db) -> bool:
    """True when the 0004 model-versioning columns exist.  Pre-0004 schemas
    (older binaries, unit harnesses) fall back to the legacy tagged path.
    Uses only probes that cannot fail structurally so the caller's transaction
    is never aborted or rolled back."""
    try:
        dialect = db.bind.dialect.name if db.bind is not None else "postgresql"
        if dialect == "sqlite":
            columns = db.execute(text("PRAGMA table_info(model_configs)")).mappings().all()
            return any(row["name"] == "status" for row in columns)
        row = db.execute(text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'model_configs' AND column_name = 'status'"
        )).scalar_one_or_none()
        return row is not None
    except Exception:
        return False


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_fernet_key() -> str:
    """Prefer an explicit ENCRYPTION_KEY (deterministic migration/tests);
    fall back to the configured application key in production."""
    return os.environ.get("ENCRYPTION_KEY") or encryption_service.settings.encryption_key


def _decrypt_credential(ciphertext: str) -> str:
    return Fernet(_resolve_fernet_key().encode()).decrypt(ciphertext.encode()).decode()


def decrypt_credential(ciphertext: str) -> str:
    """Public credential decryption for immutable caller resolution."""
    return _decrypt_credential(ciphertext)


def _encrypt_credential(plaintext: str) -> str:
    return Fernet(_resolve_fernet_key().encode()).encrypt(plaintext.encode()).decode()


# ---------------------------------------------------------------------------
# Role unification (0004)
# ---------------------------------------------------------------------------

def upgrade_role_unification() -> None:
    """Backfill legacy `user` roles to `viewer`, install the CHECK constraint
    and audit the backfill summary.  Never aborts on reported rows."""
    from alembic import op
    import sqlalchemy as sa
    from app.models.security_domain import DEFAULT_SECURITY_DOMAIN_ID

    bind = op.get_bind()
    backfilled = bind.execute(
        sa.text("SELECT count(*) FROM users WHERE role = 'user'")
    ).scalar_one()
    bind.execute(sa.text("UPDATE users SET role = 'viewer' WHERE role = 'user'"))
    op.create_check_constraint(
        "ck_users_role_valid",
        "users",
        "role IN ('viewer', 'editor', 'admin')",
    )
    remaining = bind.execute(
        sa.text("SELECT role, count(*) FROM users GROUP BY role ORDER BY role")
    ).mappings().all()
    summary = {row["role"]: row["count"] for row in remaining}
    op.execute(
        sa.text(
            "INSERT INTO governance_audit_outbox "
            "(id, security_domain_id, correlation_id, payload, state, attempts, created_at, updated_at) "
            "VALUES (:id, :domain, 'role-backfill-0004', CAST(:payload AS jsonb), 'pending', 0, now(), now())"
        ).bindparams(
            id=_new_id(),
            domain=DEFAULT_SECURITY_DOMAIN_ID,
            payload=json.dumps({
                "event_type": "role_unification_backfill",
                "backfilled_user_to_viewer": backfilled,
                "role_summary": summary,
            }, sort_keys=True),
        )
    )


def downgrade_role_unification() -> None:
    """Drop the role CHECK constraint (the backfill itself is not reversed)."""
    from alembic import op
    op.drop_constraint("ck_users_role_valid", "users", type_="check")


# ---------------------------------------------------------------------------
# Model version schema (0004)
# ---------------------------------------------------------------------------

def upgrade_model_versions_foundation() -> None:
    """Additive schema: identity status/pointer columns plus the immutable
    version, credential and append-only finding tables."""
    from alembic import op
    import sqlalchemy as sa

    op.add_column(
        "model_configs",
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
    )
    op.create_check_constraint(
        "ck_model_configs_status",
        "model_configs",
        "status IN ('active', 'migration_blocked', 'archived')",
    )
    op.add_column(
        "model_configs",
        sa.Column("active_version_id", sa.String(36), nullable=True),
    )

    op.create_table(
        "model_config_versions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("model_config_id", sa.String(36), nullable=False),
        sa.Column("version_no", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("api_base", sa.String(500), nullable=True),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("behavior_hash", sa.String(64), nullable=False),
        sa.Column("model_contract", sa.JSON(), nullable=False),
        sa.Column("conservative_input_limit", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_config_versions"),
        sa.ForeignKeyConstraint(
            ["model_config_id"], ["model_configs.id"],
            name="fk_model_config_versions_config", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_model_config_versions_creator",
        ),
        sa.UniqueConstraint(
            "model_config_id", "version_no", name="uq_model_config_versions_config_version",
        ),
    )
    op.create_index(
        "ix_model_config_versions_config",
        "model_config_versions",
        ["model_config_id"],
    )
    op.create_foreign_key(
        "fk_model_configs_active_version",
        "model_configs",
        "model_config_versions",
        ["active_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "model_credentials",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("model_config_id", sa.String(36), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("secret_revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_credentials"),
        sa.ForeignKeyConstraint(
            ["model_config_id"], ["model_configs.id"],
            name="fk_model_credentials_config", ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')", name="ck_model_credentials_status",
        ),
    )
    op.create_index("ix_model_credentials_config", "model_credentials", ["model_config_id"])

    op.create_table(
        "model_migration_findings",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("model_config_id", sa.String(36), nullable=False),
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("field", sa.String(120), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_migration_findings"),
        sa.ForeignKeyConstraint(
            ["model_config_id"], ["model_configs.id"],
            name="fk_model_migration_findings_config", ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_model_migration_findings_config", "model_migration_findings", ["model_config_id"],
    )


def downgrade_model_versions_foundation() -> None:
    """Strict reverse: drop the pointer FK, tables, then the identity columns."""
    from alembic import op

    op.drop_constraint("fk_model_configs_active_version", "model_configs", type_="foreignkey")
    op.drop_index("ix_model_migration_findings_config", table_name="model_migration_findings")
    op.drop_table("model_migration_findings")
    op.drop_index("ix_model_credentials_config", table_name="model_credentials")
    op.drop_table("model_credentials")
    op.drop_index("ix_model_config_versions_config", table_name="model_config_versions")
    op.drop_table("model_config_versions")
    op.drop_column("model_configs", "active_version_id")
    op.drop_constraint("ck_model_configs_status", "model_configs", type_="check")
    op.drop_column("model_configs", "status")


# ---------------------------------------------------------------------------
# Legacy LLM row migration (0004)
# ---------------------------------------------------------------------------

def preflight_legacy_llm_rows(connection):
    """Read-only classification of every `config_type='llm'` row.

    Returns a list of decisions: ``{row_id, name, provider, models, api_base,
    options, ok, reasons}`` where ``reasons`` is a list of
    ``{field, code, reason}`` dicts.  Never raises on data problems.
    """
    rows = connection.execute(text(
        "SELECT id, name, provider, models, options, api_base, api_key_encrypted "
        "FROM model_configs WHERE config_type = 'llm' ORDER BY created_at, id"
    )).mappings().all()

    decisions = []
    for row in rows:
        decision = {
            "row_id": row["id"],
            "name": row["name"],
            "provider": row["provider"],
            "models": row["models"] if isinstance(row["models"], list) else [],
            "api_base": row["api_base"],
            "options": row["options"] if isinstance(row["options"], dict) else {},
            "ok": True,
            "reasons": [],
        }
        if row["provider"] not in LLM_PROVIDERS:
            decision["ok"] = False
            decision["reasons"].append({
                "field": "provider",
                "code": "UNKNOWN_PROVIDER",
                "reason": f"provider {row['provider']!r} not in {sorted(LLM_PROVIDERS)}",
            })
        models = decision["models"]
        if not models or not all(isinstance(m, str) and m.strip() for m in models):
            decision["ok"] = False
            decision["reasons"].append({
                "field": "models",
                "code": "EMPTY_MODEL_LIST",
                "reason": "models must be a non-empty list of model names",
            })
        elif len(set(models)) != len(models):
            decision["ok"] = False
            decision["reasons"].append({
                "field": "models",
                "code": "MUTABLE_MODEL_ALIAS",
                "reason": f"duplicate model names {sorted({m for m in models if models.count(m) > 1})}",
            })
        ciphertext = row["api_key_encrypted"] or ""
        if not ciphertext:
            decision["ok"] = False
            decision["reasons"].append({
                "field": "api_key_encrypted",
                "code": "UNDECRYPTABLE_CREDENTIAL",
                "reason": "no credential stored",
            })
        else:
            try:
                _decrypt_credential(ciphertext)
            except Exception:
                decision["ok"] = False
                decision["reasons"].append({
                    "field": "api_key_encrypted",
                    "code": "UNDECRYPTABLE_CREDENTIAL",
                    "reason": "credential cannot be decrypted",
                })
        decisions.append(decision)

    # Duplicate stable identities: same (provider, canonical model set) among
    # otherwise-eligible rows is ambiguous; every row in the group is blocked.
    by_identity: dict = {}
    for decision in decisions:
        if not decision["ok"]:
            continue
        key = (decision["provider"], tuple(sorted(set(decision["models"]))))
        by_identity.setdefault(key, []).append(decision)
    for key, group in by_identity.items():
        if len(group) > 1:
            for decision in group:
                decision["ok"] = False
                decision["reasons"].append({
                    "field": "identity",
                    "code": "DUPLICATE_STABLE_IDENTITY",
                    "reason": (
                        f"{len(group)} LLM configs share provider/models "
                        f"{key[0]}:{list(key[1])}"
                    ),
                })
    return decisions


def _legacy_model_contract(model_names):
    """Exact contract for migrated versions: pins the legacy model name as the
    provider model revision and leaves every verified field unverified (NULL)
    — never a guessed tokenizer/window."""
    return [
        {
            "provider_model_revision": name,
            "tokenizer_family": None,
            "tokenizer_revision": None,
            "verified_context_window_tokens": None,
            "verified_maximum_output_tokens": None,
            "provider_contract_revision": None,
            "provider_contract_hash": None,
        }
        for name in model_names
    ]


def behavior_hash(provider, api_base, options, model_contract) -> str:
    payload = json.dumps(
        {
            "provider": provider,
            "api_base": api_base,
            "options": options,
            "model_contract": model_contract,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _row_has_version(connection, row_id) -> bool:
    return connection.execute(text(
        "SELECT 1 FROM model_config_versions WHERE model_config_id = :id LIMIT 1"
    ), {"id": row_id}).scalar_one_or_none() is not None


def _row_has_blocking_finding(connection, row_id) -> bool:
    return connection.execute(text(
        "SELECT 1 FROM model_migration_findings WHERE model_config_id = :id "
        "AND code NOT IN ('OCR_OTHER_NOT_MIGRATED', 'REMEDIATED', 'ARCHIVED') LIMIT 1"
    ), {"id": row_id}).scalar_one_or_none() is not None


def upgrade_legacy_llm_rows(connection=None) -> None:
    """Idempotent legacy LLM row migration.  Eligible rows receive identity,
    version 1, credential binding and active pointer; ineligible rows become
    disabled `migration_blocked` identities with exact findings.  Never aborts
    on data problems; already-processed rows are skipped.

    Called by the migration without arguments (uses the alembic connection);
    tests may pass an explicit connection to prove idempotent restart.
    """
    import sqlalchemy as sa

    if connection is None:
        from alembic import op
        bind = op.get_bind()
    else:
        bind = connection
    decisions = preflight_legacy_llm_rows(bind)

    for decision in decisions:
        row_id = decision["row_id"]
        if _row_has_version(bind, row_id) or _row_has_blocking_finding(bind, row_id):
            continue
        if decision["ok"]:
            contract = _legacy_model_contract(decision["models"])
            version_id = _new_id()
            digest = behavior_hash(
                decision["provider"], decision["api_base"], decision["options"], contract
            )
            bind.execute(
                sa.text(
                    "INSERT INTO model_config_versions "
                    "(id, model_config_id, version_no, provider, api_base, options, "
                    " behavior_hash, model_contract, conservative_input_limit, created_at) "
                    "VALUES (:id, :config, 1, :provider, :api_base, :options, :digest, "
                    " :contract, NULL, now())"
                ),
                {
                    "id": version_id,
                    "config": row_id,
                    "provider": decision["provider"],
                    "api_base": decision["api_base"],
                    "options": json.dumps(decision["options"]),
                    "digest": digest,
                    "contract": json.dumps(contract),
                },
            )
            bind.execute(
                sa.text(
                    "INSERT INTO model_credentials "
                    "(id, model_config_id, secret_encrypted, status, secret_revision, created_at) "
                    "VALUES (:id, :config, :secret, 'active', 1, now())"
                ),
                {
                    "id": _new_id(),
                    "config": row_id,
                    "secret": bind.execute(
                        sa.text("SELECT api_key_encrypted FROM model_configs WHERE id = :id"),
                        {"id": row_id},
                    ).scalar_one(),
                },
            )
            bind.execute(
                sa.text(
                    "UPDATE model_configs SET status = 'active', active_version_id = :vid "
                    "WHERE id = :id"
                ),
                {"vid": version_id, "id": row_id},
            )
        else:
            bind.execute(
                sa.text(
                    "UPDATE model_configs SET status = 'migration_blocked' "
                    "WHERE id = :id AND status = 'active'"
                ),
                {"id": row_id},
            )
            for reason in decision["reasons"]:
                bind.execute(
                    sa.text(
                        "INSERT INTO model_migration_findings "
                        "(id, model_config_id, code, field, reason, created_at) "
                        "VALUES (:id, :config, :code, :field, :reason, now())"
                    ),
                    {
                        "id": _new_id(),
                        "config": row_id,
                        "code": reason["code"],
                        "field": reason["field"],
                        "reason": reason["reason"],
                    },
                )

    # Report (but never migrate) OCR/other rows.
    for kind in ("ocr", "other"):
        for row in bind.execute(
            sa.text("SELECT id FROM model_configs WHERE config_type = :kind"),
            {"kind": kind},
        ).mappings():
            exists = bind.execute(
                sa.text(
                    "SELECT 1 FROM model_migration_findings WHERE model_config_id = :id "
                    "AND code = 'OCR_OTHER_NOT_MIGRATED' LIMIT 1"
                ),
                {"id": row["id"]},
            ).scalar_one_or_none()
            if exists is None:
                bind.execute(
                    sa.text(
                        "INSERT INTO model_migration_findings "
                        "(id, model_config_id, code, reason, created_at) "
                        "VALUES (:id, :config, 'OCR_OTHER_NOT_MIGRATED', "
                        " 'config_type not eligible for Agent model delivery; unchanged', now())"
                    ),
                    {"id": _new_id(), "config": row["id"]},
                )


def downgrade_legacy_llm_rows() -> None:
    """The row migration is not reversed (legacy evidence is never edited);
    schema removal is handled by `downgrade_model_versions_foundation`."""


# ---------------------------------------------------------------------------
# Runtime service: immutable selection, catalog eligibility, remediation
# ---------------------------------------------------------------------------

def is_eligible_for_agent(session: Session, model_config_id: str) -> bool:
    """Blocked/archived identities and identities without an active version are
    excluded from Agent catalogs and AgentVersion references."""
    row = session.execute(text(
        "SELECT status, active_version_id FROM model_configs WHERE id = :id"
    ), {"id": model_config_id}).mappings().one_or_none()
    if not row or row["status"] != "active" or not row["active_version_id"]:
        return False
    return session.execute(text(
        "SELECT 1 FROM model_config_versions WHERE id = :vid AND model_config_id = :id LIMIT 1"
    ), {"vid": row["active_version_id"], "id": model_config_id}).scalar_one_or_none() is not None


def select_active_version(session: Session, model_config_id: str) -> "ModelConfigVersion":
    """Immutable selection: returns the exact pinned active behavior version or
    raises MODEL_VERSION_UNAVAILABLE.  No fallback ever occurs."""
    from app.models.model_version import ModelConfigVersion

    row = session.execute(text(
        "SELECT active_version_id FROM model_configs WHERE id = :id AND status = 'active'"
    ), {"id": model_config_id}).mappings().one_or_none()
    if not row or not row["active_version_id"]:
        raise ModelVersionUnavailable(
            f"MODEL_VERSION_UNAVAILABLE identity {model_config_id} has no active version"
        )
    version = session.get(ModelConfigVersion, row["active_version_id"])
    if version is None or version.model_config_id != model_config_id:
        raise ModelVersionUnavailable(
            f"MODEL_VERSION_UNAVAILABLE identity {model_config_id} has no active version"
        )
    return version


def _validate_contract(model_contract) -> int:
    """Complete immutable contract validation; returns conservative input
    limit.  Never accepts a guessed tokenizer/window."""
    if not isinstance(model_contract, list) or not model_contract:
        raise ModelContractInvalid("MODEL_CONTRACT_INVALID: empty model contract")
    required = (
        "provider_model_revision",
        "tokenizer_family",
        "tokenizer_revision",
        "verified_context_window_tokens",
        "verified_maximum_output_tokens",
        "provider_contract_revision",
        "provider_contract_hash",
    )
    limit = None
    for entry in model_contract:
        if not isinstance(entry, dict) or any(entry.get(key) in (None, "") for key in required):
            raise ModelContractInvalid("MODEL_CONTRACT_INVALID: incomplete contract entry")
        try:
            window = int(entry["verified_context_window_tokens"])
            output = int(entry["verified_maximum_output_tokens"])
        except (TypeError, ValueError) as exc:
            raise ModelContractInvalid("MODEL_CONTRACT_INVALID: non-numeric windows") from exc
        if output <= 0 or window <= output:
            raise ModelContractInvalid("MODEL_CONTRACT_INVALID: inconsistent windows")
        entry_limit = window - output
        limit = entry_limit if limit is None else min(limit, entry_limit)
    return limit


def _identity_state(session: Session, model_config_id: str):
    row = session.execute(text(
        "SELECT status, updated_at FROM model_configs WHERE id = :id"
    ), {"id": model_config_id}).mappings().one_or_none()
    return row


def _check_base_revision(row, base_revision) -> None:
    if base_revision is None:
        return
    base_dt = base_revision
    if isinstance(base_revision, str):
        from datetime import datetime as _dt
        try:
            base_dt = _dt.fromisoformat(base_revision.replace("Z", "+00:00"))
        except ValueError:
            base_dt = None
    if base_dt is None or str(row["updated_at"]) != str(base_dt):
        raise ModelRevisionConflict("MODEL_REVISION_CONFLICT stale base_revision")


def legacy_contract_for(model_names) -> list:
    """Legacy-style immutable contract (verified fields unverified, never
    guessed) for freshly created/migrated LLM configs."""
    return _legacy_model_contract(model_names)


def create_next_version(
    session: Session,
    model_config_id: str,
    *,
    base_version: int | None,
    provider: str | None,
    api_base: str | None,
    options: dict,
    model_contract: list,
    credential_binding: str | None,
    changelog: str | None = None,
) -> "ModelConfigVersion":
    """Behavioral N+1: creates the next immutable version, binds an optional
    new credential and advances the active pointer.  Never updates old rows.
    `base_version` (when given) must equal the current active version number."""
    from app.models.model_version import ModelConfigVersion

    row = _identity_state(session, model_config_id)
    if not row or row["status"] != "active":
        raise ModelVersionUnavailable(
            f"MODEL_VERSION_UNAVAILABLE identity {model_config_id} is not active"
        )
    active = select_active_version(session, model_config_id)
    if base_version is not None and base_version != active.version_no:
        raise ModelRevisionConflict(
            f"MODEL_REVISION_CONFLICT base_version {base_version} != active {active.version_no}"
        )
    if model_contract:
        limit = _validate_contract(model_contract)
    else:
        model_contract = active.model_contract
        limit = active.conservative_input_limit
    next_provider = provider if provider is not None else active.provider
    digest = behavior_hash(next_provider, api_base, options, model_contract)
    version = ModelConfigVersion(
        model_config_id=model_config_id,
        version_no=active.version_no + 1,
        provider=next_provider,
        api_base=api_base,
        options=options,
        behavior_hash=digest,
        model_contract=model_contract,
        conservative_input_limit=limit,
    )
    session.add(version)
    session.flush()
    if credential_binding is not None:
        session.execute(text(
            "INSERT INTO model_credentials "
            "(id, model_config_id, secret_encrypted, status, secret_revision, created_at) "
            "VALUES (:id, :config, :secret, 'active', 1, now())"
        ), {"id": _new_id(), "config": model_config_id, "secret": _encrypt_credential(credential_binding)})
    session.execute(text(
        "UPDATE model_configs SET active_version_id = :vid, updated_at = now() WHERE id = :id"
    ), {"vid": version.id, "id": model_config_id})
    session.flush()
    return version


def rotate_credential(session: Session, model_config_id: str, credential_binding: str) -> None:
    """Revoke the active credential and bind a new one (secret_revision + 1).
    Audited secret rotation never changes the behavior hash."""
    current = session.execute(text(
        "SELECT secret_revision FROM model_credentials WHERE model_config_id = :id "
        "AND status = 'active' ORDER BY secret_revision DESC LIMIT 1"
    ), {"id": model_config_id}).mappings().one_or_none()
    next_revision = (current["secret_revision"] if current else 0) + 1
    session.execute(text(
        "UPDATE model_credentials SET status = 'revoked', revoked_at = now() "
        "WHERE model_config_id = :id AND status = 'active'"
    ), {"id": model_config_id})
    session.execute(text(
        "INSERT INTO model_credentials "
        "(id, model_config_id, secret_encrypted, status, secret_revision, created_at) "
        "VALUES (:id, :config, :secret, 'active', :rev, now())"
    ), {"id": _new_id(), "config": model_config_id,
        "secret": _encrypt_credential(credential_binding), "rev": next_revision})
    session.execute(text(
        "UPDATE model_configs SET updated_at = now() WHERE id = :id"
    ), {"id": model_config_id})
    session.flush()


def remediate_blocked_identity(
    session: Session,
    model_config_id: str,
    *,
    base_revision,
    provider: str,
    api_base: str | None,
    options: dict,
    model_contract: list,
    credential_binding: str,
) -> "ModelConfigVersion":
    """Administrator remediation under CAS: validates the complete immutable
    contract, creates version 1, binds the new credential and activates the
    identity.  Never edits the legacy evidence; appends remediation finding."""
    from app.models.model_version import ModelConfigVersion

    row = _identity_state(session, model_config_id)
    if not row or row["status"] != "migration_blocked":
        raise ModelVersionUnavailable(
            f"MODEL_VERSION_UNAVAILABLE identity {model_config_id} is not blocked"
        )
    _check_base_revision(row, base_revision)
    if _row_has_version(session, model_config_id):
        raise ModelRevisionConflict("MODEL_REVISION_CONFLICT identity already versioned")

    limit = _validate_contract(model_contract)
    digest = behavior_hash(provider, api_base, options, model_contract)
    version = ModelConfigVersion(
        model_config_id=model_config_id,
        version_no=1,
        provider=provider,
        api_base=api_base,
        options=options,
        behavior_hash=digest,
        model_contract=model_contract,
        conservative_input_limit=limit,
    )
    session.add(version)
    session.flush()
    session.execute(text(
        "INSERT INTO model_credentials "
        "(id, model_config_id, secret_encrypted, status, secret_revision, created_at) "
        "VALUES (:id, :config, :secret, 'active', 1, now())"
    ), {"id": _new_id(), "config": model_config_id, "secret": _encrypt_credential(credential_binding)})
    session.execute(text(
        "UPDATE model_configs SET status = 'active', active_version_id = :vid, updated_at = now() "
        "WHERE id = :id"
    ), {"vid": version.id, "id": model_config_id})
    session.execute(text(
        "INSERT INTO model_migration_findings "
        "(id, model_config_id, code, reason, created_at) "
        "VALUES (:id, :config, 'REMEDIATED', "
        "'blocked identity remediated with complete immutable contract', now())"
    ), {"id": _new_id(), "config": model_config_id})
    session.flush()
    return version


def archive_blocked_identity(
    session: Session,
    model_config_id: str,
    *,
    base_revision,
    reason: str,
) -> None:
    """Administrator archive under CAS: marks an unreferenced blocked identity
    terminal without inventing behavior.  Never edits the legacy evidence."""
    row = _identity_state(session, model_config_id)
    if not row or row["status"] != "migration_blocked":
        raise ModelVersionUnavailable(
            f"MODEL_VERSION_UNAVAILABLE identity {model_config_id} is not blocked"
        )
    _check_base_revision(row, base_revision)
    if _row_has_version(session, model_config_id):
        raise ModelVersionUnavailable(
            f"MODEL_VERSION_UNAVAILABLE referenced identity {model_config_id} cannot be archived"
        )
    session.execute(text(
        "UPDATE model_configs SET status = 'archived', updated_at = now() WHERE id = :id"
    ), {"id": model_config_id})
    session.execute(text(
        "INSERT INTO model_migration_findings "
        "(id, model_config_id, code, reason, created_at) "
        "VALUES (:id, :config, 'ARCHIVED', :reason, now())"
    ), {"id": _new_id(), "config": model_config_id, "reason": reason})
    session.flush()
