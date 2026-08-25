"""Migrate backend/ontexus.db (SQLite) -> PostgreSQL, bridging schema drift.

- Only copies columns that exist in BOTH schemas (PG schema is authoritative).
- Parses JSON columns (SQLite stores them as TEXT) into dict/list for PG JSON/JSONB.
- Remaps the legacy admin user id to the current PG admin id on any *_by column.
- Adds security_domain_id to ontology_projects (0003 column absent in SQLite).
- Idempotent: INSERT ... ON CONFLICT (id) DO NOTHING.

Usage: python scripts/migrate_sqlite_to_pg.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

BACKEND = Path(__file__).resolve().parents[1]
SQLITE_PATH = BACKEND / "ontexus.db"
PG_URL = "postgresql://ontexus:ontexus@localhost:5432/ontexus"

OLD_ADMIN = "c6c052e5-40ed-450e-a300-79643d348b6d"
NEW_ADMIN = "2ee55c81-7c6b-4509-ad5a-4fd83c060a1d"
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"

# Tables with real data, in dependency-safe order (parents before children).
TABLES = [
    "ontology_projects",
    "entities",
    "relations",
    "logic_rules",
    "actions",
    "entity_instances",
    "uploaded_files",
    "extraction_tasks",
    "v2_datasets",
    "v2_dataset_versions",
    "v2_pipelines",
    "v2_pipeline_versions",
    "v2_pipeline_runs",
    "v2_curated_datasets",
    "v2_curated_reviews",
    "v2_ontology_mappings",
    "v2_ontology_link_mappings",
    "v2_ontology_logic_rules",
    "v2_ontology_action_types",
]

# Extra columns to inject per table (schema drift from later migrations).
EXTRA = {
    "ontology_projects": {"security_domain_id": DEFAULT_DOMAIN},
}

# Columns that reference the legacy admin and must be remapped (value == OLD_ADMIN).
REMAP_COLUMNS = {
    "created_by", "created_by", "reviewer_id", "edited_by", "actor_user_id", "owner_user_id",
}


def _is_json_type(t) -> bool:
    return "JSON" in str(t).upper()


def _norm_json(value):
    """Return a JSON *string* (psycopg2 adapts str; the SQL wraps it in CAST) or None."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        try:
            return json.dumps(json.loads(value), ensure_ascii=False)
        except Exception:
            return value
    return value


def main() -> None:
    sqlite = sqlite3.connect(SQLITE_PATH)
    sqlite.row_factory = sqlite3.Row
    engine = create_engine(PG_URL)
    insp = inspect(engine)

    # Sync: curated data lives in v2_datasets(kind=curated); reviews FK to
    # v2_curated_datasets. Create the missing wrapper rows first.
    sync_rows = sqlite.execute(
        "SELECT id, name, schema_json, latest_version_id, created_at, updated_at "
        "FROM v2_datasets WHERE kind='curated'"
    ).fetchall()
    synced = 0
    with engine.begin() as conn:
        for r in sync_rows:
            conn.execute(
                text(
                    "INSERT INTO v2_curated_datasets "
                    "(id, name, schema_json, latest_version_id, status, created_at, updated_at) "
                    "VALUES (:id, :name, CAST(:schema_json AS json), :latest_version_id, "
                    "'approved', :created_at, :updated_at) ON CONFLICT DO NOTHING"
                ),
                {"id": r["id"], "name": r["name"],
                 "schema_json": _norm_json(r["schema_json"]),
                 "latest_version_id": r["latest_version_id"],
                 "created_at": r["created_at"], "updated_at": r["updated_at"]},
            )
            synced += 1
    print(f"[sync] v2_curated_datasets wrappers: {synced}")

    total = 0
    valid_ontologies = {
        r[0] for r in sqlite.execute("SELECT id FROM ontology_projects").fetchall()
    }
    valid_datasets = {
        r[0] for r in sqlite.execute("SELECT id FROM v2_datasets").fetchall()
    }
    valid_pipelines = {
        r[0] for r in sqlite.execute("SELECT id FROM v2_pipelines").fetchall()
    }
    for table in TABLES:
        # SQLite columns
        try:
            s_cols = [r["name"] for r in sqlite.execute(f"PRAGMA table_info({table})").fetchall()]
        except Exception as e:
            print(f"[skip] {table}: not in SQLite ({e})")
            continue
        # PG columns + JSON type map
        try:
            pg_cols = insp.get_columns(table)
        except Exception as e:
            print(f"[skip] {table}: not in PG ({e})")
            continue
        pg_names = {c["name"] for c in pg_cols}
        json_cols = {c["name"] for c in pg_cols if _is_json_type(c["type"])}
        bool_cols = {c["name"] for c in pg_cols if "BOOL" in str(c["type"]).upper()}
        str_limits = {
            c["name"]: c["type"].length
            for c in pg_cols
            if getattr(c["type"], "length", None)
        }
        common = [c for c in s_cols if c in pg_names]
        if "id" not in common:
            print(f"[skip] {table}: no id column")
            continue

        rows = sqlite.execute(f"SELECT * FROM {table}").fetchall()
        migrated = 0
        with engine.begin() as conn:
            for row in rows:
                d = {}
                for c in common:
                    v = row[c]
                    if c in REMAP_COLUMNS and v == OLD_ADMIN:
                        v = NEW_ADMIN
                    if c in json_cols:
                        v = _norm_json(v)
                    elif c in bool_cols and v is not None:
                        v = bool(v) if isinstance(v, int) else (v if isinstance(v, bool) else str(v).lower() in ("1", "true"))
                    elif c in str_limits and isinstance(v, str) and len(v) > str_limits[c]:
                        v = v[: str_limits[c]]
                    d[c] = v
                d.update(EXTRA.get(table, {}))
                # Skip orphaned child rows (parent ontology/dataset/pipeline deleted in SQLite)
                if "ontology_id" in d and d["ontology_id"] not in valid_ontologies:
                    continue
                if table in ("v2_dataset_versions",) and d.get("dataset_id") not in valid_datasets:
                    continue
                if table in ("v2_pipeline_runs", "v2_pipeline_versions") and d.get("pipeline_id") not in valid_pipelines:
                    continue
                if table == "v2_datasets" and d.get("source_connection_id"):
                    d["source_connection_id"] = None
                if table == "extraction_tasks":
                    # prompts / model_configs are not migrated (different ids / broken keys)
                    d["prompt_id"] = None
                    d["model_id"] = None
                cols = ", ".join(d.keys())
                placeholders = ", ".join(
                    f"CAST(:{k} AS json)" if k in json_cols else f":{k}" for k in d.keys()
                )
                sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
                conn.execute(text(sql), d)
                migrated += 1
        total += migrated
        print(f"[ok] {table}: {migrated} rows (sqlite had {len(rows)})")

    print(f"\nTotal rows attempted: {total}")
    sqlite.close()


if __name__ == "__main__":
    main()
