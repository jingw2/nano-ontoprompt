"""Task 3 (M1 stabilization): schema-startup contracts must resolve the
current Alembic head dynamically (no second hand-maintained revision
constant to drift), assert the actual current memory tables, and pin
pytest-asyncio to an explicit function-scoped loop instead of the
default-unset behavior."""
import pathlib
import sys
import tomllib

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR / "scripts"))


def _resolve_current_head() -> str:
    from verify_build_manifest import resolve_alembic_head

    return resolve_alembic_head(BACKEND_DIR / "alembic")


def _registered_tables() -> set[str]:
    from app.database import Base
    from app.models import load_all_models

    load_all_models()
    return set(Base.metadata.tables)


def _pytest_ini_options() -> dict:
    data = tomllib.loads((BACKEND_DIR / "pyproject.toml").read_text())
    return data.get("tool", {}).get("pytest", {}).get("ini_options", {})


def test_schema_contract_uses_resolved_head_and_memory_tables():
    assert _resolve_current_head() == "0026_refresh_event_inbox"
    assert {"agent_turn_checkpoints", "agent_turn_checkpoint_writes"} <= _registered_tables()
    # pytest-asyncio==0.24.0 (pinned) only recognizes
    # asyncio_default_fixture_loop_scope; the later asyncio_default_test_loop_scope
    # option does not exist in this version and is intentionally not set.
    assert _pytest_ini_options().get("asyncio_default_fixture_loop_scope") == "function"
