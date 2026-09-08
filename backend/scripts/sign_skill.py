"""Sign a Skill manifest with an Ed25519 private key (P7C).

Usage: python scripts/sign_skill.py <manifest.json> <private_key_hex>

Prints a JSON object with the manifest (as parsed), its canonical hash,
the signature hex, and the matching public key hex — the payload the skill
admin API's create-version endpoint accepts. The private key is NOT
included in any output and never enters the database."""
import json
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, ".")
from app.services.skills import canonicalize_manifest, manifest_canonical_hash  # noqa: E402


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: sign_skill.py <manifest.json> <private_key_hex>", file=sys.stderr)
        sys.exit(2)
    with open(sys.argv[1], encoding="utf-8") as f:
        manifest = json.load(f)
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(sys.argv[2]))
    digest = bytes.fromhex(manifest_canonical_hash(manifest))
    signature = private_key.sign(digest)
    print(json.dumps({
        "manifest": manifest,
        "canonical_hash": manifest_canonical_hash(manifest),
        "canonical_manifest": canonicalize_manifest(manifest),
        "signature_hex": signature.hex(),
        "public_key_hex": private_key.public_key().public_bytes_raw().hex(),
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
