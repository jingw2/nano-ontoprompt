"""Signed Skill administration (P7C).

Create/approve lifecycle with the signature gate: a version may only be
approved when at least one stored signature verifies against the stored
manifest's canonical hash. Approval re-validates the manifest's tool
descriptors against the governed descriptor vocabulary."""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.skills import (
    manifest_canonical_hash, validate_skill_manifest, verify_manifest_signature,
)


class SkillError(Exception):
    """Rejected skill operation."""


def _new_id() -> str:
    return str(uuid.uuid4())


def create_package(db: Session, *, actor_id: str, name: str) -> dict:
    package_id = _new_id()
    db.execute(text(
        "INSERT INTO skill_packages (id, name, status, created_by, created_at, updated_at) "
        "VALUES (:id, :name, 'active', :actor, now(), now())"
    ), {"id": package_id, "name": name, "actor": actor_id})
    db.commit()
    return {"id": package_id, "name": name, "status": "active"}


def create_skill_version(db: Session, *, actor_id: str, package_id: str,
                         manifest: dict, signatures: list[dict]) -> dict:
    exists = db.execute(text(
        "SELECT 1 FROM skill_packages WHERE id = :id"
    ), {"id": package_id}).scalar_one_or_none()
    if exists is None:
        raise SkillError("PACKAGE_NOT_FOUND")
    problems = validate_skill_manifest(manifest)
    if problems:
        raise SkillError(f"MANIFEST_INVALID:{';'.join(problems)}")
    canonical_hash = manifest_canonical_hash(manifest)
    if not signatures:
        raise SkillError("SIGNATURE_MISSING")
    # verify every signature BEFORE persisting anything — a bad batch fails
    # the whole create atomically
    for signature in signatures:
        if not verify_manifest_signature(
                manifest=manifest, public_key_hex=signature["public_key_hex"],
                signature_hex=signature["signature_hex"]):
            raise SkillError("SIGNATURE_INVALID")
    next_version = db.execute(text(
        "SELECT COALESCE(MAX(version_no), 0) + 1 FROM skill_versions WHERE package_id = :id"
    ), {"id": package_id}).scalar_one()
    version_id = _new_id()
    import json
    db.execute(text(
        "INSERT INTO skill_versions (id, package_id, version_no, manifest, canonical_hash, "
        "approval_status, created_by, created_at) "
        "VALUES (:id, :pkg, :vno, CAST(:manifest AS json), :hash, 'pending', :actor, now())"
    ), {"id": version_id, "pkg": package_id, "vno": next_version,
        "manifest": json.dumps(manifest, ensure_ascii=False), "hash": canonical_hash, "actor": actor_id})
    for signature in signatures:
        db.execute(text(
            "INSERT INTO skill_signatures (id, version_id, algorithm, public_key_hex, "
            "signature_hex, signer_identity, signed_at) "
            "VALUES (:id, :vid, 'ed25519', :pub, :sig, :identity, now())"
        ), {"id": _new_id(), "vid": version_id, "pub": signature["public_key_hex"],
            "sig": signature["signature_hex"], "identity": signature.get("signer_identity")})
    db.commit()
    return {"id": version_id, "package_id": package_id, "version_no": next_version,
            "canonical_hash": canonical_hash, "approval_status": "pending"}


def approve_skill_version(db: Session, *, actor_id: str, version_id: str) -> dict:
    row = db.execute(text(
        "SELECT v.approval_status, v.manifest, v.canonical_hash FROM skill_versions v WHERE v.id = :id"
    ), {"id": version_id}).mappings().one_or_none()
    if row is None:
        raise SkillError("VERSION_NOT_FOUND")
    if row["approval_status"] != "pending":
        raise SkillError("SIGNATURE_ALREADY_APPROVED")
    manifest = row["manifest"]
    if isinstance(manifest, str):
        import json
        manifest = json.loads(manifest)
    # dispatch-time integrity recheck is the same code path — approval and
    # dispatch can never disagree
    if manifest_canonical_hash(manifest) != row["canonical_hash"]:
        raise SkillError("CANONICAL_HASH_MISMATCH")
    problems = validate_skill_manifest(manifest)
    if problems:
        raise SkillError(f"MANIFEST_INVALID:{';'.join(problems)}")
    signatures = db.execute(text(
        "SELECT public_key_hex, signature_hex FROM skill_signatures WHERE version_id = :id"
    ), {"id": version_id}).mappings().all()
    valid = any(verify_manifest_signature(
        manifest=manifest, public_key_hex=s["public_key_hex"],
        signature_hex=s["signature_hex"]) for s in signatures)
    if not valid:
        raise SkillError("APPROVAL_NO_VALID_SIGNATURE")
    db.execute(text(
        "UPDATE skill_versions SET approval_status = 'approved', approved_by = :actor, "
        "approved_at = now() WHERE id = :id"
    ), {"actor": actor_id, "id": version_id})
    db.commit()
    return {"id": version_id, "approval_status": "approved"}


def list_skill_packages(db: Session) -> list[dict]:
    rows = db.execute(text(
        "SELECT id, name, status FROM skill_packages ORDER BY name"
    )).mappings().all()
    return [dict(r) for r in rows]


def list_skill_versions(db: Session, package_id: str | None = None) -> list[dict]:
    if package_id is not None:
        rows = db.execute(text(
            "SELECT v.id, v.package_id, v.version_no, v.approval_status, v.canonical_hash, v.manifest "
            "FROM skill_versions v WHERE v.package_id = :p ORDER BY v.version_no DESC"
        ), {"p": package_id}).mappings().all()
    else:
        rows = db.execute(text(
            "SELECT v.id, v.package_id, v.version_no, v.approval_status, v.canonical_hash, v.manifest "
            "FROM skill_versions v ORDER BY v.version_no DESC"
        )).mappings().all()
    return [dict(r) for r in rows]
