"""Task 5 (M1 stabilization): README instructions must name the actual
pinned runtime versions and migration-first Compose startup, closing the
Milestone 1 gate."""
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


def readme_text() -> str:
    return (REPO_ROOT / "README.md").read_text() + (REPO_ROOT / "README_zh.md").read_text()


def compose_text() -> str:
    return (
        (REPO_ROOT / "docker-compose.v2.yml").read_text()
        + (REPO_ROOT / "docker-compose.agent.yml").read_text()
    )


def test_documented_versions_match_package_metadata():
    assert "Node 22.14.0" in readme_text()
    assert "npm 11.2.0" in readme_text()
    assert "service_completed_successfully" in compose_text()


def test_compose_has_migration_first_startup():
    for compose in ("docker-compose.v2.yml", "docker-compose.agent.yml"):
        text = (REPO_ROOT / compose).read_text()
        assert "migration" in text
        assert "run_migrations.py upgrade head" in text


def test_fresh_database_gets_pgcrypto_before_migrations_run():
    """Regression: a real `docker compose up --build` against a brand-new
    postgres:16-alpine data directory has no pgcrypto extension installed —
    migration 0003_publication_governance's preflight_pgcrypto() fails
    closed with PGCRYPTO_REQUIRED, so the M1 acceptance criterion ("a fresh
    docker compose up reaches healthy services against an empty database")
    was unreachable before this init script existed."""
    init_script = REPO_ROOT / "backend" / "scripts" / "postgres-initdb" / "01-pgcrypto.sql"
    assert init_script.exists()
    assert "CREATE EXTENSION IF NOT EXISTS pgcrypto" in init_script.read_text()
    for compose in ("docker-compose.v2.yml", "docker-compose.agent.yml"):
        text = (REPO_ROOT / compose).read_text()
        assert "postgres-initdb:/docker-entrypoint-initdb.d" in text, (
            f"{compose} does not mount the pgcrypto init script into the db service"
        )
