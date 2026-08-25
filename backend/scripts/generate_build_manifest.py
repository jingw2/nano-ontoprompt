"""Deterministic Agent build-manifest generation (E0-IMAGES).

Digests are computed over file CONTENT only (no mtimes/paths of the host), so
generation is byte-deterministic.  The manifest pins the integrated source and
lock digests, the exact Alembic head, and the digest of every Agent image role
(api/dispatcher/artifact-worker/beat/watchdog/sweeper/frontend).

    cd backend && python scripts/generate_build_manifest.py \
        --root .. --output /tmp/agent-manifest.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

MANIFEST_VERSION = "agent-v1"
SCHEMA_CONTRACT_VERSION = 1

_SKIP_PARTS = {".venv", "venv", "__pycache__", ".git", "node_modules", "dist", "build",
               ".pytest_cache", ".mypy_cache", ".ruff_cache", "coverage", "test-results",
               "artifacts", ".last-run.json"}


def _file_digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_digest(root: pathlib.Path, relative_prefix: str) -> str:
    """Deterministic content digest over a file tree (sorted relpaths + hashes)."""
    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_PARTS for part in path.relative_to(root).parts):
            continue
        files.append(path)
    files.sort(key=lambda p: p.relative_to(root).as_posix())
    digest = hashlib.sha256()
    for path in files:
        rel = path.relative_to(root).as_posix()
        digest.update(rel.encode())
        digest.update(b"\x00")
        digest.update(path.read_bytes())
        digest.update(b"\x00")
    return digest.hexdigest()


def alembic_head(backend_dir: pathlib.Path) -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(backend_dir / "alembic.ini"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise SystemExit(f"AGENT_MANIFEST_INVALID: expected exactly one Alembic head, got {sorted(heads)}")
    return heads[0]


def _role_digest(role: str, *parts: str) -> str:
    digest = hashlib.sha256()
    digest.update(role.encode())
    for part in parts:
        digest.update(b"|")
        digest.update(part.encode())
    return digest.hexdigest()


def generate_manifest(root: pathlib.Path, *, backend_dir: pathlib.Path | None = None,
                      frontend_dir: pathlib.Path | None = None,
                      frontend_manifest_digest: str | None = None) -> dict:
    backend_dir = (backend_dir or root / "backend").resolve()
    frontend_dir = (frontend_dir or root / "frontend").resolve()
    head = alembic_head(backend_dir)
    backend_source = _tree_digest(backend_dir, "backend")
    backend_reqs = _file_digest(backend_dir / "requirements.txt")
    if frontend_manifest_digest is None:
        if not (frontend_dir / "package.json").exists():
            raise SystemExit(f"AGENT_MANIFEST_INVALID: frontend sources missing at {frontend_dir}")
        frontend_manifest_digest = _file_digest(frontend_dir / "package.json") + _file_digest(
            frontend_dir / "package-lock.json")

    images = {
        "api": {"ref": "ontexus-agent-api", "digest": _role_digest("api", backend_source, backend_reqs, head)},
        "dispatcher": {"ref": "ontexus-agent-dispatcher", "digest": _role_digest("dispatcher", backend_source, backend_reqs, head)},
        "artifact_worker": {"ref": "ontexus-agent-worker", "digest": _role_digest("artifact_worker", backend_source, backend_reqs, head)},
        "beat": {"ref": "ontexus-agent-beat", "digest": _role_digest("beat", backend_source, backend_reqs, head)},
        "watchdog": {"ref": "ontexus-agent-watchdog", "digest": _role_digest("watchdog", backend_source, backend_reqs, head)},
        "sweeper": {"ref": "ontexus-agent-sweeper", "digest": _role_digest("sweeper", backend_source, backend_reqs, head)},
        "frontend": {"ref": "ontexus-agent-frontend", "digest": _role_digest("frontend", frontend_manifest_digest)},
    }
    return {
        "schema_contract_version": SCHEMA_CONTRACT_VERSION,
        "manifest_version": MANIFEST_VERSION,
        "alembic_head": head,
        "backend_source_digest": backend_source,
        "backend_requirements_digest": backend_reqs,
        "frontend_manifest_digest": frontend_manifest_digest,
        "images": images,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate the signed Agent build manifest")
    parser.add_argument("--root", default=str(BACKEND.parent))
    parser.add_argument("--backend-dir", default=None)
    parser.add_argument("--frontend-dir", default=None)
    parser.add_argument("--frontend-manifest-digest", default=None)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    root = pathlib.Path(args.root).resolve()
    manifest = generate_manifest(
        root,
        backend_dir=pathlib.Path(args.backend_dir) if args.backend_dir else None,
        frontend_dir=pathlib.Path(args.frontend_dir) if args.frontend_dir else None,
        frontend_manifest_digest=args.frontend_manifest_digest,
    )
    output = pathlib.Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {output} (alembic_head={manifest['alembic_head']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
