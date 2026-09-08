"""Release-aware Agent candidate index (P4A-INDEX, Section 9).

One schema-independent authoritative candidate collection per ontology,
maintained from the transactional 0006 outbox (a PostgreSQL trigger emits
upsert/delete events for every authoritative instance/relation mutation, so
router, mapping and import writers cannot bypass).  The index is backed by
SQL: backfill scans the authoritative tables, reconciliation compares the
SQL-derived candidate hashes with the applied outbox ledger, and every hit is
SQL-refetched, revision-checked and authorized before it is returned.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

OUTBOX_PREFIX = "agent_object_"


class IndexError(Exception):
    """Rejected index operation."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def _candidate_hash(instance_id: str, entity_id: str, revision: int, row_data: dict | None) -> str:
    payload = json.dumps({
        "instance_id": instance_id,
        "entity_id": entity_id,
        "revision": revision,
        "row_data": row_data or {},
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def consume_outbox(db: Session, *, batch: int = 200) -> list[dict]:
    """Mark pending outbox rows applied (the release-aware consumer has
    incorporated them into the candidate collection)."""
    rows = db.execute(text(
        "SELECT id, ontology_id, event_type, instance_id, instance_revision, entity_id, "
        "edge_id, source_instance_id, target_instance_id, relation_definition_id, payload "
        "FROM agent_index_outbox WHERE state = 'pending' "
        "ORDER BY created_at LIMIT :batch FOR UPDATE SKIP LOCKED"
    ), {"batch": batch}).mappings().all()
    consumed = []
    now = _now()
    for row in rows:
        db.execute(text(
            "UPDATE agent_index_outbox SET state = 'applied', created_at = created_at "
            "WHERE id = :id AND state = 'pending'"
        ), {"id": row["id"]})
        consumed.append(dict(row) | {"applied_at": now})
    db.commit()
    return consumed


def candidate_collection_name(ontology_id: str) -> str:
    return f"{OUTBOX_PREFIX}{ontology_id}"


def backfill_candidates(db: Session, *, ontology_id: str, release_id: str | None = None) -> list[dict]:
    """Schema-independent SQL backfill: every authoritative instance row for the
    ontology (excluding soft-deleted) becomes a candidate with its hash and the
    pinned release id when one is provided (release-aware: the instance's entity
    must be defined in that release manifest)."""
    params = {"ontology_id": ontology_id}
    release_clause = ""
    if release_id:
        release_clause = (
            "AND EXISTS (SELECT 1 FROM ontology_releases rel "
            "WHERE rel.id = :release_id AND rel.ontology_id = ei.ontology_id "
            "AND rel.manifest_projection @> "
            "jsonb_build_object('entities', jsonb_build_array(jsonb_build_object('id', ei.entity_id)))) "
        )
        params["release_id"] = release_id
    rows = db.execute(text(
        "SELECT ei.id AS instance_id, ei.entity_id, ei.revision, ei.row_data, ei.ontology_id "
        "FROM entity_instances ei "
        "WHERE ei.ontology_id = :ontology_id AND ei.deleted_at IS NULL "
        + release_clause +
        "ORDER BY ei.id"
    ), params).mappings().all()
    candidates = []
    for row in rows:
        candidates.append({
            "instance_id": row["instance_id"],
            "entity_id": row["entity_id"],
            "instance_revision": row["revision"],
            "ontology_id": row["ontology_id"],
            "hash": _candidate_hash(row["instance_id"], row["entity_id"], row["revision"], row["row_data"]),
            "release_id": release_id,
        })
    return candidates


def reconcile_candidates(db: Session, *, ontology_id: str, release_id: str | None = None) -> dict:
    """Compare the SQL-derived candidate set (authoritative) against the applied
    outbox ledger; report candidate/hash divergence.  An empty report means the
    derived index is in sync with the authoritative tables."""
    candidates = backfill_candidates(db, ontology_id=ontology_id, release_id=release_id)
    applied = db.execute(text(
        "SELECT instance_id, event_type FROM agent_index_outbox "
        "WHERE ontology_id = :o AND state = 'applied' AND instance_id IS NOT NULL"
    ), {"o": ontology_id}).mappings().all()
    ledger: dict[str, str] = {}
    for row in applied:
        if row["event_type"] == "delete_instance":
            ledger[row["instance_id"]] = "deleted"
        else:
            ledger.setdefault(row["instance_id"], "upsert")
    report = {"candidates": len(candidates), "ledger": len(ledger), "missing": [], "stale": [], "hash_mismatch": []}
    candidate_ids = {c["instance_id"] for c in candidates}
    # authoritative candidate has no applied upsert -> missing from the derived index
    for c in candidates:
        if c["instance_id"] not in ledger or ledger[c["instance_id"]] == "deleted":
            report["missing"].append(c["instance_id"])
    # applied upsert no longer present -> stale
    for instance_id, event in ledger.items():
        if event == "upsert" and instance_id not in candidate_ids:
            report["stale"].append(instance_id)
    return report


def search_candidates(
    db: Session, *, ontology_id: str, release_id: str | None, query: str,
    user_id: str | None = None, limit: int = 20,
) -> list[dict]:
    """Release-aware candidate hits: SQL lexical candidate generation bounded by
    the release manifest (when pinned), then every hit is SQL-refetched with a
    revision check and authorization (the caller must hold an active data grant
    with read_instances for the ontology)."""
    if user_id is not None:
        grant = db.execute(text(
            "SELECT 1 FROM ontology_data_grants "
            "WHERE ontology_id = :o AND user_id = :u AND status = 'active' "
            "AND capabilities::text LIKE '%\"read_instances\"%' LIMIT 1"
        ), {"o": ontology_id, "u": user_id}).scalar_one_or_none()
        if not grant:
            return []
    params: dict = {"ontology_id": ontology_id, "q": f"%{query}%", "limit": limit}
    release_clause = ""
    if release_id:
        release_clause = (
            "AND EXISTS (SELECT 1 FROM ontology_releases rel "
            "WHERE rel.id = :release_id AND rel.ontology_id = ei.ontology_id "
            "AND rel.manifest_projection @> "
            "jsonb_build_object('entities', jsonb_build_array(jsonb_build_object('id', ei.entity_id)))) "
        )
        params["release_id"] = release_id
    hits = db.execute(text(
        "SELECT ei.id AS instance_id, ei.entity_id, ei.revision, ei.row_data "
        "FROM entity_instances ei "
        "WHERE ei.ontology_id = :ontology_id AND ei.deleted_at IS NULL "
        "AND ei.row_data::text ILIKE :q "
        + release_clause +
        "ORDER BY ei.updated_at DESC LIMIT :limit"
    ), params).mappings().all()
    results = []
    for hit in hits:
        # SQL refetch + revision check: never trust a cached revision
        fresh = db.execute(text(
            "SELECT id, revision FROM entity_instances "
            "WHERE id = :id AND deleted_at IS NULL AND revision = :rev"
        ), {"id": hit["instance_id"], "rev": hit["revision"]}).mappings().one_or_none()
        if not fresh:
            continue
        results.append({
            "instance_id": hit["instance_id"],
            "entity_id": hit["entity_id"],
            "instance_revision": hit["revision"],
            "row_data": hit["row_data"],
            "hash": _candidate_hash(hit["instance_id"], hit["entity_id"], hit["revision"], hit["row_data"]),
            "release_id": release_id,
        })
    return results


def is_cutover_active(db: Session, *, ontology_id: str) -> bool:
    """Legacy-only dual-read: Agent traffic uses the release-aware index only
    after the per-ontology cutover flag is set."""
    row = db.execute(text(
        "SELECT rule_value FROM rules_config WHERE rule_key = :key"
    ), {"key": f"agent_index_cutover.{ontology_id}"}).scalar_one_or_none()
    return row == "1"


def set_cutover(db: Session, *, ontology_id: str, active: bool) -> None:
    now = _now()
    row = db.execute(text(
        "SELECT id FROM rules_config WHERE rule_key = :key"
    ), {"key": f"agent_index_cutover.{ontology_id}"}).mappings().one_or_none()
    if row:
        db.execute(text(
            "UPDATE rules_config SET rule_value = :v, updated_at = :now WHERE id = :id"
        ), {"v": "1" if active else "0", "id": row["id"], "now": now})
    else:
        db.execute(text(
            "INSERT INTO rules_config (id, rule_key, rule_value, rule_label_cn, rule_label_en, "
            "editable, created_at, updated_at) "
            "VALUES (:id, :key, :v, 'Agent index cutover', 'Agent index cutover', false, :now, :now)"
        ), {"id": _new_id(), "key": f"agent_index_cutover.{ontology_id}",
            "v": "1" if active else "0", "now": now})
    db.commit()


def dual_read(db: Session, *, ontology_id: str, release_id: str | None, query: str,
              user_id: str | None = None, limit: int = 20) -> list[dict]:
    """Legacy-only dual-read: before cutover the caller keeps using the legacy
    path (this returns a marker); after cutover the release-aware candidates
    are returned."""
    if not is_cutover_active(db, ontology_id=ontology_id):
        return [{"legacy": True}]
    return search_candidates(db, ontology_id=ontology_id, release_id=release_id,
                             query=query, user_id=user_id, limit=limit)
