"""Deterministic Agent build-manifest signing (E0-IMAGES).

Signs the canonical manifest payload (all fields except `signature`) with
HMAC-SHA256.  The signing key comes from the `BUILD_MANIFEST_KEY` environment
variable and falls back to the repository's dev-only key; production rotates
the key and never embeds it in the image.

    BUILD_MANIFEST_KEY=... python scripts/sign_build_manifest.py \
        --input /tmp/agent-manifest.json --output /tmp/agent-manifest.signed.json
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import pathlib

DEV_SIGNING_KEY = "ontexus-agent-dev-signing-key"


def signing_key() -> bytes:
    return os.environ.get("BUILD_MANIFEST_KEY", DEV_SIGNING_KEY).encode()


def canonical_payload(manifest: dict) -> bytes:
    body = {k: v for k, v in manifest.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def sign_manifest(manifest: dict, key: bytes) -> dict:
    signed = dict(manifest)
    signed["signature"] = hmac.new(key, canonical_payload(manifest), hashlib.sha256).hexdigest()
    return signed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Sign the Agent build manifest")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    manifest = json.loads(pathlib.Path(args.input).read_text())
    signed = sign_manifest(manifest, signing_key())
    output = pathlib.Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(signed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"signed {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
