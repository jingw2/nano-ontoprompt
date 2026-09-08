"""Regenerate `backend/openapi-agent.json` deterministically (I-BACKEND).

Run from the backend directory:

    python -m scripts.generate_openapi

The manifest is the deterministic (sorted-key) serialization of the live
`app.openapi()` schema; `test_core_route_registration.py` fails when the
committed file drifts from it.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402


def main():
    schema = app.openapi()
    manifest = pathlib.Path(__file__).resolve().parents[1] / "openapi-agent.json"
    manifest.write_text(
        json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {manifest} ({manifest.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
