"""Agent action preview (P5A-PREVIEW).

Descriptor + parameters -> canonical instance-only preview and hash, validated
against the pinned release schema, policy and target revisions.  Rejects
schema-mutation parameters.  Identical requests are deterministic.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

PREVIEW_SCHEMA_VERSION = "instance-action-preview-v1"
MUTATION_KEYS = frozenset({"delete", "drop", "truncate", "alter", "grant", "revoke"})


class PreviewError(Exception):
    """The preview request was rejected (fail closed)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def preview_action(db: Session, *, actor_id: str, agent_id: str, ontology_id: str,
                   release_id: str, descriptor_id: str, parameters: dict,
                   target_instance_id: str | None = None) -> dict:
    """Build the canonical instance-only preview and persist it.  The
    parameters must not mutate the schema and must target only the pinned
    release's defined instances."""
    # reject schema mutation attempts
    flat = json.dumps(parameters, ensure_ascii=False).lower()
    for key in MUTATION_KEYS:
        if key in flat:
            raise PreviewError("SCHEMA_MUTATION_REJECTED")
    release = db.execute(text(
        "SELECT id, version_no, schema_hash FROM ontology_releases "
        "WHERE id = :rid AND ontology_id = :o"
    ), {"rid": release_id, "o": ontology_id}).mappings().one_or_none()
    if release is None:
        raise PreviewError("RELEASE_NOT_FOUND")
    if target_instance_id:
        instance = db.execute(text(
            "SELECT id, entity_id, revision FROM entity_instances "
            "WHERE id = :iid AND ontology_id = :o AND deleted_at IS NULL "
            "AND EXISTS (SELECT 1 FROM ontology_releases rel WHERE rel.id = :rid "
            "AND rel.ontology_id = entity_instances.ontology_id "
            "AND rel.manifest_projection @> jsonb_build_object('entities', "
            "jsonb_build_array(jsonb_build_object('id', entity_instances.entity_id))))"
        ), {"iid": target_instance_id, "o": ontology_id, "rid": release_id}).mappings().one_or_none()
        if instance is None:
            raise PreviewError("TARGET_INSTANCE_NOT_IN_RELEASE")
    else:
        instance = None
    preview = {
        "schema_version": PREVIEW_SCHEMA_VERSION,
        "descriptor_id": descriptor_id,
        "ontology_id": ontology_id,
        "release_id": release_id,
        "release_version_no": release["version_no"],
        "target_instance_id": target_instance_id,
        "target_instance_revision": instance["revision"] if instance else None,
        "parameters": parameters,
    }
    canonical = _canonical(preview)
    preview_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "preview_id": _new_id(), "hash": preview_hash, "canonical": canonical,
        "schema_version": PREVIEW_SCHEMA_VERSION, "release_version_no": release["version_no"],
        "deterministic": True,
    }
