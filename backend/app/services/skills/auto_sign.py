"""System-held signature for scanned Skill uploads (P7C).

A manually authored Skill version is created with a human signer's Ed25519
signature. A self-service upload has no such signer — the trust gate is
the security scan (app/services/skills/scanner.py) — but the create/approve
machinery downstream must not know the difference: it always verifies a
real Ed25519 signature over the manifest's canonical hash. This module
signs with a deterministic keypair derived from `settings.secret_key`, so
the same manifest always verifies the same way across restarts without
persisting a private key anywhere."""
from __future__ import annotations

import hashlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import settings
from app.services.skills import manifest_canonical_hash

SYSTEM_SIGNER_IDENTITY = "system:auto-scan"


def _system_private_key() -> Ed25519PrivateKey:
    seed = hashlib.sha256(f"{settings.secret_key}:skill-auto-signer".encode("utf-8")).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def auto_sign_manifest(manifest: dict) -> dict:
    private_key = _system_private_key()
    digest = bytes.fromhex(manifest_canonical_hash(manifest))
    signature = private_key.sign(digest)
    public_bytes = private_key.public_key().public_bytes_raw()
    return {
        "public_key_hex": public_bytes.hex(),
        "signature_hex": signature.hex(),
        "signer_identity": SYSTEM_SIGNER_IDENTITY,
    }
