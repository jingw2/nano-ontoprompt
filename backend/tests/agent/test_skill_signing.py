"""P7C: canonicalization + Ed25519 signature round-trip."""
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.services.skills import (
    canonicalize_manifest, manifest_canonical_hash, validate_skill_manifest,
    verify_manifest_signature,
)


def _manifest(**overrides):
    base = {
        "name": "supply-chain-summarizer",
        "description": "Summarizes supplier instances with governed reads.",
        "instructions": "Use the read tool to fetch suppliers, then summarize safety lines.",
        "tools": [{
            "alias": "read_suppliers",
            "descriptor_id": "ontology.read_instances",
            "description": "Query supplier instances",
            "parameters": {"query": "供应商"},
        }],
    }
    base.update(overrides)
    return base


def test_canonicalization_is_key_order_independent():
    a = _manifest()
    b = {"tools": a["tools"], "name": a["name"], "description": a["description"],
         "instructions": a["instructions"]}
    assert canonicalize_manifest(a) == canonicalize_manifest(b)


def test_canonical_hash_detects_tampering():
    a = _manifest()
    b = _manifest()
    b["tools"][0]["parameters"]["query"] = "攻击载荷"
    assert manifest_canonical_hash(a) != manifest_canonical_hash(b)


def test_sign_verify_round_trip_and_tamper_rejection():
    private_key = Ed25519PrivateKey.generate()
    manifest = _manifest()
    signature = private_key.sign(bytes.fromhex(manifest_canonical_hash(manifest)))
    public_hex = private_key.public_key().public_bytes_raw().hex()
    assert verify_manifest_signature(
        manifest=manifest, public_key_hex=public_hex, signature_hex=signature.hex()) is True
    tampered = _manifest(description="changed")
    assert verify_manifest_signature(
        manifest=tampered, public_key_hex=public_hex, signature_hex=signature.hex()) is False


def test_wrong_key_rejected():
    manifest = _manifest()
    wrong = Ed25519PrivateKey.generate()
    signature = wrong.sign(bytes.fromhex(manifest_canonical_hash(manifest)))
    assert verify_manifest_signature(
        manifest=manifest, public_key_hex=wrong.public_key().public_bytes_raw().hex(),
        signature_hex=signature.hex()) is True
    other = Ed25519PrivateKey.generate()
    assert verify_manifest_signature(
        manifest=manifest, public_key_hex=other.public_key().public_bytes_raw().hex(),
        signature_hex=signature.hex()) is False


def test_validate_rejects_unknown_descriptor():
    problems = validate_skill_manifest(_manifest(
        tools=[{"alias": "t", "descriptor_id": "evil.exec", "description": "d"}]))
    assert any("DESCRIPTOR_UNKNOWN" in p for p in problems)


def test_validate_rejects_bad_alias_and_duplicates():
    problems = validate_skill_manifest(_manifest(
        tools=[{"alias": "ok", "descriptor_id": "ontology.read_instances", "description": "d"},
               {"alias": "ok", "descriptor_id": "ontology.read_instances", "description": "d2"}]))
    assert any("DUPLICATE" in p for p in problems)
    problems = validate_skill_manifest(_manifest(
        tools=[{"alias": "has space", "descriptor_id": "ontology.read_instances", "description": "d"}]))
    assert any("ALIAS_INVALID" in p for p in problems)


def test_validate_instructions_only_skill_is_valid():
    assert validate_skill_manifest({"name": "n", "description": "d", "instructions": "i"}) == []
