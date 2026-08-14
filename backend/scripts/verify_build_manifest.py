"""Read-only Agent build-manifest verification (E0-IMAGES).

Every Python service process starts through this check before the guarded
launcher execs its role command: the manifest must be structurally valid, its
HMAC signature must match the canonical payload, and (when `--expect-head` is
given) the pinned Alembic head must equal the declared core head.  Fails
closed with stable codes; never mutates anything.

    python scripts/verify_build_manifest.py \
        --manifest /tmp/agent-manifest.signed.json --expect-head 0006_agent_runtime
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


def verify_manifest(manifest: dict, expect_head: str | None = None) -> None:
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
    if expect_head is not None and manifest["alembic_head"] != expect_head:
        raise SystemExit(
            f"BUILD_MANIFEST_HEAD_MISMATCH: manifest {manifest['alembic_head']} != expected {expect_head}"
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Verify the signed Agent build manifest")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--expect-head", default=None)
    args = parser.parse_args(argv)
    manifest = json.loads(pathlib.Path(args.manifest).read_text())
    verify_manifest(manifest, expect_head=args.expect_head)
    print(f"manifest OK (head={manifest['alembic_head']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
