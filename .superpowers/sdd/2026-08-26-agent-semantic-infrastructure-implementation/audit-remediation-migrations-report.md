# Audit remediation: migration portability and dynamic Alembic head

Date: 2026-08-29

## Scope and root causes

This remediation repairs the existing, unmerged `0029_runtime_identity` and
`0030_runtime_plans` revisions. No migration was added and the revision chain
remains linear:

```text
0028_snapshot_release_published -> 0029_runtime_identity -> 0030_runtime_plans (head)
```

The audit found two independent failures:

1. Build-manifest and schema-contract tests pinned the old
   `0029_runtime_identity` value even though the configured head is
   `0030_runtime_plans`.
2. The two new revisions used PostgreSQL-only JSON casts and UUID `~`
   predicates. MySQL could not compile the unbounded `String` columns and
   could not execute those PostgreSQL expressions; SQLite also cannot alter
   constraints with a direct `ALTER TABLE` operation.

## TDD evidence

### RED

Before changing the implementation, the existing contract tests reproduced the
stale-head failure:

```text
$ .venv/bin/python -m pytest tests/agent/test_build_manifest.py \
    tests/agent/test_current_schema_contract.py -q
3 failed, 8 passed
```

The failures were the two build-manifest assertions and the schema contract
assertion, all expecting `0029_runtime_identity` while Alembic resolved
`0030_runtime_plans`.

The new dialect regressions were then run against the original migrations:

```text
$ .venv/bin/python -m pytest tests/agent/test_runtime_migration_dialects.py \
    tests/agent/test_build_manifest.py tests/agent/test_current_schema_contract.py -q
3 failed, 11 passed
```

The two MySQL rendering cases failed with
`VARCHAR requires a length on dialect mysql` for `user_id`; the SQLite case
failed with `NotImplementedError: No support for ALTER of constraints in
SQLite dialect` when `0029` added its foreign key directly.

### GREEN

After the fixes, the same regressions pass:

```text
$ .venv/bin/python -m pytest tests/agent/test_runtime_migration_dialects.py -q
3 passed, 11 warnings

$ .venv/bin/python -m pytest tests/agent/test_build_manifest.py \
    tests/agent/test_current_schema_contract.py -q
11 passed, 19 warnings
```

The MySQL tests use SQLAlchemy's real MySQL dialect to render both migration
upgrades without requiring a live service. The rendered SQL has no `::json` or
PostgreSQL `~` expression and includes MySQL `REGEXP` checks, JSON constructor
defaults, bounded UUID columns, and portable timestamp defaults.

## Implementation

- `test_build_manifest.py` now resolves the expected head from the Alembic
  script directory through the same resolver as manifest verification instead
  of maintaining `OPS_ALEMBIC_HEAD`.
- `test_current_schema_contract.py` asserts one configured head and compares
  the resolver to that head. The incremental event-ingest integration fixture
  also compares its final version to the dynamic resolver.
- `0029_runtime_identity.py` adds the OAuth columns nullable, backfills legacy
  rows, then makes them required with dialect-specific defaults. SQLite uses
  Alembic batch recreation for add/alter/drop operations; PostgreSQL and MySQL
  use native operations. JSON backfills use dialect-aware literal/function
  expressions.
- `0029` and `0030` use `String(36)` for UUID identifiers, `CURRENT_TIMESTAMP`
  for timestamp defaults, MySQL `JSON_ARRAY()`/`JSON_OBJECT()` defaults, and
  dialect-specific UUID checks (PostgreSQL regex, case-sensitive MySQL
  `BINARY ... REGEXP`, SQLite `GLOB`). Existing semantic check names and
  constraints are retained. The 0029 legacy-row backfill uses those same
  dialect-aware JSON literal/function expressions, so offline SQL is
  executable rather than containing an unbound Python-list parameter.
- `test_runtime_migration_dialects.py` provides the MySQL offline regression
  and an SQLite migration/backfill/constraint test.

## Migration and runtime verification

Alembic reports exactly one linear head:

```text
$ .venv/bin/python -m alembic heads
0030_runtime_plans (head)
```

The full runtime-focused suite is green:

```text
$ .venv/bin/python -m pytest tests/runtime \
    tests/agent/test_runtime_contract.py tests/agent/test_legacy_runtime_tables.py -q
111 passed, 3 skipped, 123 warnings
```

Against the disposable local PostgreSQL service, a scratch schema was used to
run the real migration runner from the existing base through `head`; the final
`alembic_version` was `0030_runtime_plans` and all three new runtime tables
were present. A second scratch schema successfully ran `upgrade head`,
`downgrade 0028_snapshot_release_published`, and `upgrade head` again. A
third scratch schema seeded a legacy OAuth client before `0029`; after the
upgrade its new security-domain column contained the default domain and both
JSON columns contained empty arrays. Scratch schemas were dropped afterward.

Targeted source compilation and whitespace checks also pass:

```text
$ .venv/bin/python -m py_compile alembic/versions/0029_runtime_identity.py \
    alembic/versions/0030_runtime_plans.py tests/agent/test_build_manifest.py \
    tests/agent/test_current_schema_contract.py \
    tests/agent/test_runtime_migration_dialects.py \
    tests/v2/incremental/integration/test_event_ingest_integration.py
$ git diff --check
```

No `uv.lock` change was made.

## Remaining concerns and environment limits

- No `RUNTIME_MYSQL_URL`/live MySQL service or `pymysql` driver was available,
  so MySQL verification is dialect-level SQL rendering rather than a live
  upgrade. The genuine MySQL dialect coverage is committed and passes.
- An explicit run of the existing PostgreSQL event-ingest integration test
  was blocked before reaching these revisions because the shared test
  database already contained `v2_connections.refresh_policy` in a stale
  schema (`DuplicateTable`). This is pre-existing fixture/database pollution;
  the scratch-schema migration checks above were isolated and passed.
- Existing deprecation/SQLAlchemy fixture warnings remain; they are unrelated
  to this remediation.

## Fix round 1: reviewer coverage remediation

The first implementation's MySQL test used a normal offline context, so the
backfill statements were emitted with `%s` placeholders. Token checks alone
did not prove that the UUID string or JSON values could be rendered. The
reviewer's SQLite case also correctly called out that the fixture only had
columns-only tables and did not exercise preservation of a real legacy OAuth
schema.

### RED

After strengthening the MySQL renderer with `literal_binds=True` and asserting
the rendered backfill statements, the pre-fix migration failed as expected:

```text
$ .venv/bin/python -m pytest tests/agent/test_runtime_migration_dialects.py -q
FAILED ...[0029_runtime_identity]
sqlalchemy.exc.CompileError: No literal value renderer is available for
literal value "[]" with datatype JSON
```

This was the meaningful coverage failure: literal offline SQL could not be
produced for the Python JSON-list bind. The earlier SQLite RED also remains
recorded above: the original direct `ALTER TABLE` path failed with
`NotImplementedError` for constraint alteration.

### GREEN

The 0029 backfill now reuses `_json_server_default("array")`: MySQL renders
`JSON_ARRAY()` and PostgreSQL/SQLite render a quoted JSON literal. The test
context has `literal_binds=True`, rejects `%s`/`?`, and asserts the exact UUID
and JSON update statements. It also checks 0030's object/array JSON defaults.

The SQLite regression now creates a representative pre-0029 schema with:

- a real UUID-checked `oauth_clients` table;
- `created_by -> users` and `users.security_domain_id -> security_domains`
  foreign keys;
- `is_active` and `created_at` server defaults; and
- a valid, actual legacy OAuth row.

It enables SQLite foreign keys, runs 0029 upgrade and downgrade, verifies
legacy row values, UUID check SQL, defaults, and foreign-key signatures before
and after, verifies the new columns/FK/runtime credential checks on upgrade,
and verifies the new table/columns/FK are removed while the old schema is
restored on downgrade. A separate SQLite test retains 0030 constraint
coverage.

```text
$ .venv/bin/python -m pytest tests/agent/test_runtime_migration_dialects.py -q
4 passed, 12 warnings
```

The changed literal backfill was also executed against a disposable real
PostgreSQL schema containing a legacy OAuth row; 0029 completed and returned
the default security-domain UUID plus JSON empty arrays. No live MySQL service
or driver was available, so the MySQL path remains real dialect compilation
with fully literal SQL rather than a live-driver test.
