"""E0-CI: every core packet command has one job owner in agent-mvp.yml.

Parses `.github/workflows/agent-mvp.yml` and verifies (a) each core packet's
owned backend test file exists and is covered by the full-suite `pytest`
invocation in the matrix job, and (b) every guarded command contract
(bootstrap, migration, launcher, evaluation, plan check, E2E red contract)
appears in exactly one job step.  Prints a dry-run ownership report and fails
closed on any unowned packet/command.

    cd backend && python scripts/ci/check_agent_plan_contract.py
"""
from __future__ import annotations

import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
WORKFLOW = REPO / ".github" / "workflows" / "agent-mvp.yml"

# packet -> owned backend test file(s) (covered by the full-suite pytest run)
PACKET_FILES: dict[str, tuple[str, ...]] = {
    "E0-PY": ("tests/agent/test_python_floor.py",),
    "E0-DB": ("tests/agent/test_schema_startup.py",),
    "P1A-AUDIT": ("tests/agent/test_governance_audit.py",),
    "P1A-INTEGRATE": ("tests/agent/test_0003_full_migration.py",),
    "P1A-ACCESS": ("tests/agent/test_ontology_access_admin.py",),
    "F0-SECURITY": ("tests/agent/test_account_revocation_security_headers.py",),
    "P1B-IMPORTS": ("tests/agent/test_import_mutation_closure.py",),
    "P1B-CLOSURE": ("tests/agent/test_legacy_route_closure.py",),
    "P1C-COMPILER": ("tests/agent/test_publication_compiler.py", "tests/agent/test_publication_cutover.py"),
    "P1C-API": ("tests/agent/test_publication_api.py",),
    "P2A-MODEL": ("tests/agent/test_model_version_migration.py",),
    "P2A-RBAC": ("tests/agent/test_role_unification.py",),
    "P2A-CALLERS": ("tests/agent/test_model_caller_cutover.py",),
    "P2B-POLICY": ("tests/agent/test_agent_policy.py",),
    "P2B-DATAGRANT": ("tests/agent/test_ontology_data_grant_api.py",),
    "P2B-API": ("tests/agent/test_agent_api.py",),
    "P2B-CONFIG": ("tests/agent/test_agent_configuration.py",),
    "P2C-TOOLS": ("tests/agent/test_tool_gateway.py",),
    "P3A-INSTANCE": ("tests/agent/test_instance_revision_migration.py",),
    "P3A-DISPATCH": ("tests/agent/test_turn_dispatch.py",),
    "P3A-TURNAPI": ("tests/agent/test_turn_api.py",),
    "P3B-RUNTIME": ("tests/agent/test_runtime_contract.py", "tests/agent/test_langgraph_worker.py"),
    "P3B-SAVER": ("tests/agent/test_checkpoint_saver.py",),
    "P3B-INTERRUPT": ("tests/agent/test_clarification.py",),
    "P3B-STATEADMIN": ("tests/agent/test_application_state_schema_admin.py",),
    "P3B-STATEAUDIT": ("tests/agent/test_application_state_audit_api.py",),
    "P4A-INDEX": ("tests/agent/test_release_aware_index.py",),
    "P4A-CONTEXT": ("tests/agent/test_turn_context.py",),
    "P4A-GATEWAY": ("tests/agent/test_tool_gateway.py",),
    "P4A-WORKER": ("tests/agent/test_langgraph_worker.py",),
    "P4A-STREAM": ("tests/agent/test_event_stream.py",),
    "P3A-RETENTION": ("tests/agent/test_fixed_retention.py",),
    "P5A-PREVIEW": ("tests/agent/test_action_preview.py",),
    "P5B-APPROVAL": ("tests/agent/test_approval_state.py",),
    "P5C-EXECUTE": ("tests/agent/test_action_execution.py",),
    "I-BACKEND": ("tests/agent/test_core_route_registration.py",),
    "E0-IMAGES": ("tests/agent/test_build_manifest.py",),
    "E0-CI": ("tests/agent/test_ci_contract.py",),
}

# dedicated command contracts that must each appear in a job step
REQUIRED_COMMANDS: tuple[str, ...] = (
    "scripts/bootstrap_backend.py",
    "scripts/run_migrations.py upgrade head",
    "scripts/guarded_entrypoint.py",
    "python -m pytest -q",
    "evals.agent_ontology.run",
    "npm run test:ci",
    "scripts/run_agent_e2e.sh",
    "scripts/ci/check_agent_plan_contract.py",
)

PACKET_COMMANDS = {
    "I-FRONTEND": "scripts/run_agent_e2e.sh",
    "E0-EVAL": "evals.agent_ontology.run",
}


def load_run_texts() -> list[tuple[str, str]]:
    import yaml

    data = yaml.safe_load(WORKFLOW.read_text())
    entries = []
    for job_name, job in data.get("jobs", {}).items():
        for step in job.get("steps", []):
            run = str(step.get("run", ""))
            if run:
                entries.append((f"{job_name} :: {step.get('name', '')}", run))
    return entries


def main(argv=None) -> int:
    if not WORKFLOW.exists():
        print(f"ERROR: missing {WORKFLOW.relative_to(REPO)}", file=sys.stderr)
        return 1
    entries = load_run_texts()
    all_run = "\n".join(run for _, run in entries)
    violations: list[str] = []
    owners: list[str] = []

    for packet, files in PACKET_FILES.items():
        for rel in files:
            path = BACKEND / rel
            if not path.exists():
                violations.append(f"{packet}: owned file missing {rel}")
                continue
            owner = next((name for name, run in entries if "python -m pytest -q" in run), None)
            owners.append(f"{packet}: owner={owner or 'UNOWNED'} ({rel})")
            if owner is None:
                violations.append(f"{packet}: no full-suite pytest owner in agent-mvp.yml")

    for packet, command in PACKET_COMMANDS.items():
        owner = next((name for name, run in entries if command in run), None)
        owners.append(f"{packet}: owner={owner or 'UNOWNED'} ({command})")
        if owner is None:
            violations.append(f"{packet}: command {command!r} has no job owner in agent-mvp.yml")

    for command in REQUIRED_COMMANDS:
        if command not in all_run:
            violations.append(f"agent-mvp.yml missing required command {command!r}")

    print("=== agent-mvp.yml core packet ownership (dry run) ===")
    for line in sorted(owners):
        print(line)
    print(f"=== {len(entries)} job steps; {len(PACKET_FILES) + len(PACKET_COMMANDS)} packet owners ===")
    if violations:
        print("=== VIOLATIONS ===", file=sys.stderr)
        for v in sorted(set(violations)):
            print(" - " + v, file=sys.stderr)
        return 1
    print("plan contract OK: every core packet command has one job owner")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
