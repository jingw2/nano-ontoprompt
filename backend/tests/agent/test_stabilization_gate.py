"""Task 5 (M1 stabilization): README instructions must name the actual
pinned runtime versions and migration-first Compose startup, closing the
Milestone 1 gate."""
import pathlib
import stat

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "agent-mvp.yml"
GATE_SCRIPT = REPO_ROOT / "scripts" / "verify_m1_stabilization_gate.sh"


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


def test_m1_gate_script_exists_is_executable_and_env_safe():
    """The real 'fresh docker compose up against an empty database'
    acceptance criterion must be an independently reproducible, executable
    script — not just YAML/doc text assertions — and it must never clobber
    a real developer's .env (git-ignored, legitimately points at localhost
    for local non-Docker dev, which is wrong for Compose networking)."""
    assert GATE_SCRIPT.exists(), "scripts/verify_m1_stabilization_gate.sh is missing"
    mode = GATE_SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, "scripts/verify_m1_stabilization_gate.sh is not executable"
    source = GATE_SCRIPT.read_text()
    assert "set -euo pipefail" in source
    assert "trap cleanup EXIT" in source
    assert "ENV_BACKUP" in source and ".env.example" in source
    # regression: the one-shot migration service exits after completing, so
    # `docker compose ps -q` (running containers only) returns nothing for
    # it — this made a passing migration look indistinguishable from a
    # missing one and failed the gate on its own success. `-a` is required.
    assert "ps -a -q migration" in source
    # regression: backend/frontend have no Compose healthcheck, so `--wait`
    # only guarantees the container started, not that uvicorn/vite inside
    # has finished booting — a single curl right after raced the server and
    # got "curl: (52) Empty reply from server"; must poll instead.
    assert source.count("for i in $(seq 1 40); do") >= 2, (
        "backend and frontend health checks must both poll, not curl once"
    )
    assert "docker compose" in source and "up --build -d --wait" in source
    assert "curl -fsS http://127.0.0.1:8000/health" in source
    assert "curl -fsS -o /dev/null http://127.0.0.1:5173/" in source


def test_m1_gate_is_wired_into_ci():
    """The gate script must actually run in CI, not merely exist on disk —
    a script nobody invokes proves nothing."""
    import yaml

    data = yaml.safe_load(WORKFLOW.read_text())
    jobs = data["jobs"]
    assert "m1-stabilization-gate" in jobs, "agent-mvp.yml has no M1 stabilization gate job"
    run_text = "\n".join(str(s.get("run", "")) for s in jobs["m1-stabilization-gate"]["steps"])
    assert "scripts/verify_m1_stabilization_gate.sh" in run_text


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
