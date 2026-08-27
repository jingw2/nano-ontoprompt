"""Read-only Agent build-manifest verification (E0-IMAGES).

Every Python service process starts through this check before the guarded
launcher execs its role command: the manifest must be structurally valid, its
HMAC signature must match the canonical payload, and the pinned Alembic head
must equal the expected core head.  The expected head is resolved dynamically
from the Alembic script directory (`--alembic-dir`) rather than a hand-
maintained constant, so a new migration can never leave a stale pin behind;
`--expect-head` remains for an explicit static check.  Fails closed with
stable codes; never mutates anything.

    python scripts/verify_build_manifest.py \
        --manifest /tmp/agent-manifest.signed.json --alembic-dir /app/alembic
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from check_python_version import require_supported_python  # noqa: E402

require_supported_python()

from sign_build_manifest import canonical_payload, signing_key  # noqa: E402

REQUIRED_ROLES = {"api", "dispatcher", "artifact_worker", "beat", "watchdog", "sweeper", "frontend"}
REQUIRED_FIELDS = ("schema_contract_version", "manifest_version", "alembic_head",
                   "backend_source_digest", "backend_requirements_digest",
                   "frontend_manifest_digest", "images", "signature")


def resolve_alembic_head(alembic_dir: pathlib.Path) -> str:
    """Read the single current Alembic head from a script directory. Fails
    closed if there are zero or multiple heads (an unresolved branch).

    Loading every revision file (to read revision/down_revision) executes
    each one as a module — some import from the sibling `alembic_helpers`
    package, resolvable only when the backend root (alembic_dir's parent) is
    on sys.path. `run_migrations.py` gets this for free by exec'ing
    `python -m alembic` from that directory; this direct ScriptDirectory
    call does not, so it's added explicitly."""
    from alembic.script import ScriptDirectory

    backend_root = str(alembic_dir.resolve().parent)
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)
    heads = ScriptDirectory(str(alembic_dir)).get_heads()
    if len(heads) != 1:
        raise SystemExit(
            f"BUILD_MANIFEST_INVALID: expected exactly one Alembic head in {alembic_dir}, got {sorted(heads)}"
        )
    return heads[0]


def verify_manifest(
    manifest: dict,
    expect_head: str | None = None,
    alembic_dir: pathlib.Path | None = None,
) -> None:
    for field in REQUIRED_FIELDS:
        if field not in manifest:
            raise SystemExit(f"BUILD_MANIFEST_INVALID: missing field {field}")
    images = manifest["images"]
    if not isinstance(images, dict) or not REQUIRED_ROLES <= set(images):
        raise SystemExit(
            f"BUILD_MANIFEST_INVALID: images must cover {sorted(REQUIRED_ROLES)}"
        )
    for role, spec in images.items():
        if not isinstance(spec, dict) or not spec.get("ref") or not spec.get("digest"):
            raise SystemExit(f"BUILD_MANIFEST_INVALID: image {role} lacks ref/digest")
    import hmac

    expected = manifest["signature"]
    actual = hmac.new(signing_key(), canonical_payload(manifest), "sha256").hexdigest()
    if not hmac.compare_digest(expected, actual):
        raise SystemExit("BUILD_MANIFEST_SIGNATURE_MISMATCH: manifest was tampered")
    if alembic_dir is not None:
        expect_head = resolve_alembic_head(alembic_dir)
    if expect_head is not None and manifest["alembic_head"] != expect_head:
        raise SystemExit(
            f"BUILD_MANIFEST_HEAD_MISMATCH: manifest {manifest['alembic_head']} != expected {expect_head}"
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Verify the signed Agent build manifest")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--expect-head", default=None)
    parser.add_argument("--alembic-dir", default=None)
    args = parser.parse_args(argv)
    manifest = json.loads(pathlib.Path(args.manifest).read_text())
    alembic_dir = pathlib.Path(args.alembic_dir) if args.alembic_dir else None
    verify_manifest(manifest, expect_head=args.expect_head, alembic_dir=alembic_dir)
    print(f"manifest OK (head={manifest['alembic_head']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
