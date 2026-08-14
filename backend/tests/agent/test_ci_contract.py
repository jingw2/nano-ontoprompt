"""E0-CI: core Agent verification pipeline (guarded 3.11/3.12 matrix + 3.10 gate).

Asserts the GitHub Actions workflow: an exact 3.11/3.12 matrix whose
install/migration/API/worker smoke commands use the guarded launcher and
bootstrap, PostgreSQL 16 + Redis 7 services, a 3.10 negative job proving
`UNSUPPORTED_PYTHON_VERSION` with a non-zero exit BEFORE any install, and a
plan-contract check that every core packet command has one job owner.
"""
import pathlib
import subprocess
import sys

import pytest

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "agent-mvp.yml"
CHECK_SCRIPT = BACKEND_DIR / "scripts" / "ci" / "check_agent_plan_contract.py"


def test_e0_ci_red_contract():
    failures = []
    if not WORKFLOW.exists():
        failures.append("missing .github/workflows/agent-mvp.yml")
    else:
        source = WORKFLOW.read_text()
        for marker in ('"3.11"', '"3.12"', '"3.10"', "postgres:16", "redis:7",
                       "bootstrap_backend", "guarded_entrypoint", "UNSUPPORTED_PYTHON_VERSION",
                       "check_agent_plan_contract"):
            if marker not in source:
                failures.append(f"agent-mvp.yml missing {marker}")
    if not CHECK_SCRIPT.exists():
        failures.append("missing scripts/ci/check_agent_plan_contract.py")
    if failures:
        pytest.fail("RED_E0_CI: " + "; ".join(failures))


def test_workflow_matrix_exact_python_versions():
    import yaml

    data = yaml.safe_load(WORKFLOW.read_text())
    jobs = data["jobs"]
    backend = jobs["backend-matrix"]
    matrix = backend["strategy"]["matrix"]
    assert matrix["python-version"] == ["3.11", "3.12"], matrix
    negative = jobs["python-floor-negative"]
    steps_text = "\n".join(str(s.get("run", "")) for s in negative["steps"])
    assert "3.10" in steps_text or "python-3.10" in steps_text
    assert "UNSUPPORTED_PYTHON_VERSION" in steps_text
    services = backend.get("services", {})
    assert "postgres" in services and "redis" in services
    assert "16" in str(services.get("postgres", {}).get("image", ""))
    assert "redis:7" in str(services.get("redis", {}).get("image", ""))


def test_matrix_commands_use_the_guarded_launcher():
    import yaml

    data = yaml.safe_load(WORKFLOW.read_text())
    steps = data["jobs"]["backend-matrix"]["steps"]
    run_text = "\n".join(str(s.get("run", "")) for s in steps)
    assert "scripts/bootstrap_backend.py" in run_text
    assert "scripts/run_migrations.py upgrade head" in run_text
    assert "scripts/guarded_entrypoint.py" in run_text
    assert "uvicorn" in run_text and "celery" in run_text
    assert "npm run test:ci" in run_text
    assert "evals.agent_ontology.run" in run_text
    assert "check_agent_plan_contract.py" in run_text


def test_plan_contract_check_passes_and_reports_owners():
    proc = subprocess.run(
        [sys.executable, str(CHECK_SCRIPT)],
        cwd=BACKEND_DIR, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "owner" in proc.stdout.lower()
    assert "agent-mvp.yml" in proc.stdout
    for packet in ("E0-PY", "E0-DB", "I-BACKEND", "I-FRONTEND", "P1C-API", "P2B-API",
                   "P4A-STREAM", "P5B-APPROVAL", "P5C-EXECUTE", "E0-EVAL"):
        assert packet in proc.stdout, f"report missing owner for {packet}"


def test_negative_job_refuses_unsupported_python_before_install():
    """The 3.10 gate must fire through the guarded launcher BEFORE any pip."""
    import yaml

    data = yaml.safe_load(WORKFLOW.read_text())
    steps = data["jobs"]["python-floor-negative"]["steps"]
    # the install step, if any, must come AFTER the refusal assertion
    install_index = None
    refuse_index = None
    for i, step in enumerate(steps):
        run = str(step.get("run", ""))
        if "pip" in run or "bootstrap" in run:
            install_index = i
        if "UNSUPPORTED_PYTHON_VERSION" in run:
            refuse_index = i
    assert refuse_index is not None
    if install_index is not None:
        assert refuse_index < install_index, "unsupported Python must be refused before install"
