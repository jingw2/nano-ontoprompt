"""P2A-MODEL: 0004 role unification plus legacy LLM identity/version/credential migration.

Revision `0004_roles_model_versions` is additive: it installs the `users.role`
check/backfill and converts every legacy `ModelConfig.config_type='llm'` row
into one stable identity (the ModelConfig row itself), an exact immutable
behavior version, a credential binding and an active pointer, while ineligible
rows become disabled `migration_blocked` identities with append-only findings
and never abort DDL.  OCR/`other` rows are reported and left untouched and
cannot block Agent model delivery.

PostgreSQL-marked tests use TEST_DATABASE_URL; SQLite never substitutes.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from urllib.parse import quote

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker


BACKEND_DIR = Path(__file__).resolve().parents[2]
MIGRATION = BACKEND_DIR / "alembic" / "versions" / "0004_roles_model_versions.py"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"

# Deterministic Fernet key shared by the test process (seed ciphertexts) and
# the migration subprocess (decrypt classification).  Explicitly injected via
# ENCRYPTION_KEY so the migration never depends on a per-process random key.
TEST_FERNET_KEY = Fernet.generate_key().decode()
os.environ["ENCRYPTION_KEY"] = TEST_FERNET_KEY


@pytest.fixture(autouse=True)
def _pin_encryption_key():
    # Other agent test modules define their own ENCRYPTION_KEY; pin ours for
    # every in-process decrypt so module import order cannot break it.
    os.environ["ENCRYPTION_KEY"] = TEST_FERNET_KEY
    yield

NEW_0004_TABLES = {
    "model_config_versions",
    "model_credentials",
    "model_migration_findings",
}


def _enc(plaintext: str) -> str:
    return Fernet(TEST_FERNET_KEY.encode()).encrypt(plaintext.encode()).decode()


def test_p2a_model_red_contract():
    failures = []
    if not MIGRATION.exists():
        failures.append("missing alembic/versions/0004_roles_model_versions.py")
    else:
        source = MIGRATION.read_text()
        for helper in (
            "upgrade_role_unification",
            "upgrade_model_versions_foundation",
            "upgrade_legacy_llm_rows",
            "downgrade_legacy_llm_rows",
            "downgrade_model_versions_foundation",
            "downgrade_role_unification",
        ):
            if helper not in source:
                failures.append(f"0004 missing {helper}")
    model_path = BACKEND_DIR / "app" / "models" / "model_version.py"
    if not model_path.exists():
        failures.append("missing app/models/model_version.py")
    service_path = BACKEND_DIR / "app" / "services" / "model_version.py"
    if not service_path.exists():
        failures.append("missing app/services/model_version.py")
    if failures:
        pytest.fail("RED_P2A_MODEL: " + "; ".join(failures))


def _scoped_url(schema: str) -> str:
    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema}', safe='-=')}"


def _alembic(schema: str, *args, check=True):
    return subprocess.run(
        [sys.executable, "scripts/run_migrations.py", *args],
        cwd=BACKEND_DIR,
        env=dict(os.environ, DATABASE_URL=_scoped_url(schema), ENCRYPTION_KEY=TEST_FERNET_KEY),
        capture_output=True,
        text=True,
        check=check,
    )


@pytest.fixture
def full_schema():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p2a_model_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    yield schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _connection(schema: str):
    return create_engine(_scoped_url(schema))


def _insert_legacy_model(connection, *, config_id, name="Legacy LLM", config_type="llm",
                         provider="openai", models=None, options=None, api_key=None,
                         api_key_raw=None, api_base="https://api.openai.com/v1",
                         created_by="legacy-user"):
    models = ["gpt-4o"] if models is None else models
    options = {} if options is None else options
    if api_key_raw is not None:
        stored = api_key_raw
    elif api_key is None:
        stored = _enc("sk-test-" + config_id[-8:])
    elif api_key == "":
        stored = ""
    else:
        stored = _enc(api_key)
    connection.execute(text(
        "INSERT INTO model_configs (id,name,config_type,api_base,api_key_encrypted,provider,"
        "models,options,created_by,created_at,updated_at) "
        "VALUES (:id,:name,:config_type,:api_base,:api_key,:provider,:models,:options,"
        ":created_by,now(),now())"
    ), {
        "id": config_id, "name": name, "config_type": config_type, "api_base": api_base,
        "api_key": stored,
        "provider": provider, "models": json.dumps(models), "options": json.dumps(options),
        "created_by": created_by,
    })


def test_migration_0004_calls_helpers_in_normative_order(monkeypatch):
    spec = importlib.util.spec_from_file_location("migration_0004_model", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    for helper in (
        "upgrade_role_unification",
        "upgrade_model_versions_foundation",
        "upgrade_legacy_llm_rows",
    ):
        monkeypatch.setattr(module, helper, (lambda name: lambda: calls.append(name))(helper))
    module.upgrade()
    assert calls == [
        "upgrade_role_unification",
        "upgrade_model_versions_foundation",
        "upgrade_legacy_llm_rows",
    ]
    calls.clear()
    for helper in (
        "downgrade_legacy_llm_rows",
        "downgrade_model_versions_foundation",
        "downgrade_role_unification",
    ):
        monkeypatch.setattr(module, helper, (lambda name: lambda: calls.append(name))(helper))
    module.downgrade()
    assert calls == [
        "downgrade_legacy_llm_rows",
        "downgrade_model_versions_foundation",
        "downgrade_role_unification",
    ]


def test_fresh_0004_upgrade_installs_role_constraint_and_tables(full_schema):
    result = _alembic(full_schema, "upgrade", "0004_roles_model_versions")
    assert result.returncode == 0, result.stderr
    engine = _connection(full_schema)
    inspector = inspect(engine)
    migrated = set(inspector.get_table_names())
    assert NEW_0004_TABLES <= migrated
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0004_roles_model_versions"
        model_columns = {column["name"] for column in inspector.get_columns("model_configs")}
        assert {"status", "active_version_id"} <= model_columns
        # role constraint rejects values outside viewer|editor|admin
        with pytest.raises(Exception):
            connection.execute(text(
                "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
                "VALUES ('role-x','rolex','rolex@example.com','h','user',true,:domain,now(),now())"
            ), {"domain": DEFAULT_DOMAIN})
    engine.dispose()


def test_legacy_user_role_backfilled_and_audited(full_schema):
    _alembic(full_schema, "upgrade", "0003_publication_governance")
    engine = _connection(full_schema)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
            "VALUES ('legacy-user','legacy','legacy@example.com','hash','user',true,:domain,now(),now())"
        ), {"domain": DEFAULT_DOMAIN})
    engine.dispose()
    result = _alembic(full_schema, "upgrade", "0004_roles_model_versions")
    assert result.returncode == 0, result.stderr
    engine = _connection(full_schema)
    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT role FROM users WHERE id='legacy-user'"
        )).scalar_one() == "viewer"
        audit = connection.execute(text(
            "SELECT payload FROM governance_audit_outbox WHERE correlation_id='role-backfill-0004'"
        )).mappings().one_or_none()
        assert audit is not None
        payload = audit["payload"]
        assert payload.get("event_type") == "role_unification_backfill"
        assert payload.get("backfilled_user_to_viewer", 0) >= 1
    engine.dispose()


def test_populated_legacy_llm_migration_mixed_rows(full_schema):
    """Eligible rows get identity/version/credential/pointer; every ineligible
    class becomes a disabled migration_blocked identity with an append-only
    finding; OCR/other rows are reported but never participate."""
    _alembic(full_schema, "upgrade", "0003_publication_governance")
    engine = _connection(full_schema)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
            "VALUES ('legacy-user','legacy','legacy@example.com','hash','admin',true,:domain,now(),now())"
        ), {"domain": DEFAULT_DOMAIN})
        _insert_legacy_model(connection, config_id="llm-eligible-1", name="Eligible A", models=["gpt-4.1"])
        _insert_legacy_model(connection, config_id="llm-dup-1", name="Dup A")
        _insert_legacy_model(connection, config_id="llm-dup-2", name="Dup B")
        _insert_legacy_model(connection, config_id="llm-unknown-1", provider="weird_provider")
        _insert_legacy_model(connection, config_id="llm-empty-1", models=[])
        _insert_legacy_model(connection, config_id="llm-alias-1", models=["gpt-4o", "gpt-4o"])
        _insert_legacy_model(connection, config_id="llm-badkey-1", api_key="")
        _insert_legacy_model(connection, config_id="llm-badkey-2", api_key_raw="garbage-not-fernet-token")
        _insert_legacy_model(connection, config_id="ocr-row-1", config_type="ocr", provider="paddleocr")
        _insert_legacy_model(connection, config_id="other-row-1", config_type="other", provider="custom")
    engine.dispose()

    result = _alembic(full_schema, "upgrade", "0004_roles_model_versions")
    assert result.returncode == 0, result.stderr

    engine = _connection(full_schema)
    with engine.connect() as connection:
        # eligible: version 1 + credential + active pointer
        eligible = connection.execute(text(
            "SELECT active_version_id, status FROM model_configs WHERE id='llm-eligible-1'"
        )).mappings().one()
        assert eligible["status"] == "active"
        assert eligible["active_version_id"] is not None
        version = connection.execute(text(
            "SELECT version_no, provider, behavior_hash, conservative_input_limit "
            "FROM model_config_versions WHERE model_config_id='llm-eligible-1'"
        )).mappings().one()
        assert version["version_no"] == 1
        assert version["provider"] == "openai"
        assert len(version["behavior_hash"]) == 64
        assert version["conservative_input_limit"] is None  # never guessed
        cred = connection.execute(text(
            "SELECT secret_revision, status FROM model_credentials WHERE model_config_id='llm-eligible-1'"
        )).mappings().one()
        assert cred["secret_revision"] == 1
        assert cred["status"] == "active"

        # duplicate stable identity: BOTH rows blocked, no versions, no pointer
        for dup_id in ("llm-dup-1", "llm-dup-2"):
            row = connection.execute(text(
                "SELECT status, active_version_id FROM model_configs WHERE id=:id"
            ), {"id": dup_id}).mappings().one()
            assert row["status"] == "migration_blocked"
            assert row["active_version_id"] is None
            assert connection.execute(text(
                "SELECT count(*) FROM model_config_versions WHERE model_config_id=:id"
            ), {"id": dup_id}).scalar_one() == 0

        # each blocked reason gets an exact append-only finding
        def findings(config_id):
            return [r["code"] for r in connection.execute(text(
                "SELECT code FROM model_migration_findings WHERE model_config_id=:id ORDER BY code"
            ), {"id": config_id}).mappings()]
        assert "UNKNOWN_PROVIDER" in findings("llm-unknown-1")
        assert "EMPTY_MODEL_LIST" in findings("llm-empty-1")
        assert "MUTABLE_MODEL_ALIAS" in findings("llm-alias-1")
        assert "UNDECRYPTABLE_CREDENTIAL" in findings("llm-badkey-1")
        assert "UNDECRYPTABLE_CREDENTIAL" in findings("llm-badkey-2")
        assert "DUPLICATE_STABLE_IDENTITY" in findings("llm-dup-1")
        assert "DUPLICATE_STABLE_IDENTITY" in findings("llm-dup-2")

        # blocked identities are disabled and never active
        assert connection.execute(text(
            "SELECT status FROM model_configs WHERE id='llm-unknown-1'"
        )).scalar_one() == "migration_blocked"

        # OCR/other rows are reported but untouched (no status change/version)
        assert connection.execute(text(
            "SELECT status FROM model_configs WHERE id='ocr-row-1'"
        )).scalar_one() == "active"
        assert connection.execute(text(
            "SELECT count(*) FROM model_config_versions WHERE model_config_id='ocr-row-1'"
        )).scalar_one() == 0
        assert "OCR_OTHER_NOT_MIGRATED" in findings("ocr-row-1")
        assert "OCR_OTHER_NOT_MIGRATED" in findings("other-row-1")
    engine.dispose()


def test_migration_idempotent_restart(full_schema):
    """Re-running the row-migration over an already-migrated schema creates no
    duplicate versions/credentials/findings."""
    from app.services import model_version as svc

    _alembic(full_schema, "upgrade", "0004_roles_model_versions")
    engine = _connection(full_schema)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
            "VALUES ('legacy-user','legacy','legacy@example.com','hash','admin',true,:domain,now(),now())"
        ), {"domain": DEFAULT_DOMAIN})
        _insert_legacy_model(connection, config_id="llm-eligible-1")
        _insert_legacy_model(connection, config_id="llm-badkey-1", api_key="")
    engine.dispose()

    with engine.begin() as connection:
        svc.upgrade_legacy_llm_rows(connection)
        svc.upgrade_legacy_llm_rows(connection)  # idempotent restart
    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT count(*) FROM model_config_versions WHERE model_config_id='llm-eligible-1'"
        )).scalar_one() == 1
        assert connection.execute(text(
            "SELECT count(*) FROM model_credentials WHERE model_config_id='llm-eligible-1'"
        )).scalar_one() == 1
        assert connection.execute(text(
            "SELECT count(*) FROM model_migration_findings WHERE model_config_id='llm-badkey-1'"
        )).scalar_one() == 1
        # still-processed rows keep their pointers
        assert connection.execute(text(
            "SELECT count(*) FROM model_configs WHERE id='llm-eligible-1' AND status='active' "
            "AND active_version_id IS NOT NULL"
        )).scalar_one() == 1
    engine.dispose()


def test_remediation_activates_blocked_identity_and_archive_is_terminal(full_schema):
    from app.models.model_version import ModelConfigVersion
    from app.services import model_version as svc

    _alembic(full_schema, "upgrade", "0004_roles_model_versions")
    engine = _connection(full_schema)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
            "VALUES ('legacy-user','legacy','legacy@example.com','hash','admin',true,:domain,now(),now())"
        ), {"domain": DEFAULT_DOMAIN})
        _insert_legacy_model(connection, config_id="llm-remediate-1", provider="weird_provider")
        _insert_legacy_model(connection, config_id="llm-archive-1", models=[])
        # classify + migrate the freshly inserted rows in-process (the alembic
        # revision already ran; the row migration is idempotent by design)
        from app.services import model_version as svc
        svc.upgrade_legacy_llm_rows(connection)
    engine.dispose()

    Session = sessionmaker(bind=engine)
    with Session() as session:
        # blocked identities are never eligible for Agent selection
        assert svc.is_eligible_for_agent(session, "llm-remediate-1") is False
        with pytest.raises(svc.ModelVersionUnavailable):
            svc.select_active_version(session, "llm-remediate-1")

        base = session.execute(text(
            "SELECT updated_at FROM model_configs WHERE id='llm-remediate-1'"
        )).scalar_one()
        # stale base_revision -> CAS conflict
        with pytest.raises(svc.ModelRevisionConflict):
            svc.remediate_blocked_identity(session, "llm-remediate-1", base_revision="stale",
                                           provider="openai", api_base="https://example.test/v1",
                                           options={}, model_contract=[{
                                               "provider_model_revision": "gpt-4o",
                                               "tokenizer_family": "cl100k_base",
                                               "tokenizer_revision": "rev-1",
                                               "verified_context_window_tokens": 128000,
                                               "verified_maximum_output_tokens": 4096,
                                               "provider_contract_revision": "pc-1",
                                               "provider_contract_hash": "a" * 64,
                                           }], credential_binding="sk-remediated")
        # invalid contract (window smaller than output) -> MODEL_CONTRACT_INVALID
        with pytest.raises(svc.ModelContractInvalid):
            svc.remediate_blocked_identity(session, "llm-remediate-1", base_revision=base,
                                           provider="openai", api_base="https://example.test/v1",
                                           options={}, model_contract=[{
                                               "provider_model_revision": "gpt-4o",
                                               "tokenizer_family": "cl100k_base",
                                               "tokenizer_revision": "rev-1",
                                               "verified_context_window_tokens": 2048,
                                               "verified_maximum_output_tokens": 4096,
                                               "provider_contract_revision": "pc-1",
                                               "provider_contract_hash": "a" * 64,
                                           }], credential_binding="sk-remediated")
        # remediation creates version 1, activates the identity, binds a credential
        version = svc.remediate_blocked_identity(session, "llm-remediate-1", base_revision=base,
                                                 provider="openai", api_base="https://example.test/v1",
                                                 options={"temperature": 0.2}, model_contract=[{
                                                     "provider_model_revision": "gpt-4o",
                                                     "tokenizer_family": "cl100k_base",
                                                     "tokenizer_revision": "rev-1",
                                                     "verified_context_window_tokens": 128000,
                                                     "verified_maximum_output_tokens": 4096,
                                                     "provider_contract_revision": "pc-1",
                                                     "provider_contract_hash": "a" * 64,
                                                 }], credential_binding="sk-remediated")
        assert version.version_no == 1
        assert isinstance(version, ModelConfigVersion)
        assert version.conservative_input_limit == 128000 - 4096
        session.commit()
        assert svc.is_eligible_for_agent(session, "llm-remediate-1") is True
        active = svc.select_active_version(session, "llm-remediate-1")
        assert active.id == version.id
        assert active.behavior_hash == version.behavior_hash
        # credential bound + encrypted
        cred = session.execute(text(
            "SELECT secret_encrypted, status FROM model_credentials WHERE model_config_id='llm-remediate-1'"
        )).mappings().one()
        assert cred["status"] == "active"
        assert Fernet(TEST_FERNET_KEY.encode()).decrypt(cred["secret_encrypted"].encode()).decode() == "sk-remediated"
        # remediated identity can no longer be archived
        with pytest.raises(svc.ModelVersionUnavailable):
            svc.archive_blocked_identity(session, "llm-remediate-1", base_revision=base, reason="late")

        # archive a still-blocked identity -> terminal without inventing behavior
        archive_base = session.execute(text(
            "SELECT updated_at FROM model_configs WHERE id='llm-archive-1'"
        )).scalar_one()
        svc.archive_blocked_identity(session, "llm-archive-1", base_revision=archive_base, reason="obsolete")
        session.commit()
        assert session.execute(text(
            "SELECT status FROM model_configs WHERE id='llm-archive-1'"
        )).scalar_one() == "archived"
        assert svc.is_eligible_for_agent(session, "llm-archive-1") is False
        with pytest.raises(svc.ModelVersionUnavailable):
            svc.select_active_version(session, "llm-archive-1")
        # remediation of an archived identity is refused (terminal)
        with pytest.raises(svc.ModelVersionUnavailable):
            svc.remediate_blocked_identity(session, "llm-archive-1", base_revision=archive_base,
                                           provider="openai", api_base="https://x/v1", options={},
                                           model_contract=[], credential_binding="sk")
        # append-only remediation/archive evidence
        codes = [r["code"] for r in session.execute(text(
            "SELECT code FROM model_migration_findings WHERE model_config_id='llm-remediate-1' ORDER BY code"
        )).mappings()]
        assert "REMEDIATED" in codes
        archive_codes = [r["code"] for r in session.execute(text(
            "SELECT code FROM model_migration_findings WHERE model_config_id='llm-archive-1' ORDER BY code"
        )).mappings()]
        assert archive_codes == ["ARCHIVED", "EMPTY_MODEL_LIST"]
    engine.dispose()


def test_downgrade_strict_reverse(full_schema):
    _alembic(full_schema, "upgrade", "0004_roles_model_versions")
    result = _alembic(full_schema, "downgrade", "0003_publication_governance")
    assert result.returncode == 0, result.stderr
    engine = _connection(full_schema)
    inspector = inspect(engine)
    migrated = set(inspector.get_table_names())
    assert NEW_0004_TABLES.isdisjoint(migrated)
    model_columns = {column["name"] for column in inspector.get_columns("model_configs")}
    assert "active_version_id" not in model_columns
    assert "status" not in model_columns
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0003_publication_governance"
        # role CHECK removed: legacy 'user' value is accepted again on 0003
        connection.execute(text(
            "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
            "VALUES ('role-y','roley','roley@example.com','h','user',true,:domain,now(),now())"
        ), {"domain": DEFAULT_DOMAIN})
    engine.dispose()
