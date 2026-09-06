"""Task 5: static contract tests for the blocking real business-journey gate.

These tests never execute the gate script, never call DeepSeek, and never
start a live stack -- they parse `.github/workflows/agent-mvp.yml` and
`scripts/run_business_journey_gate.sh` as text/YAML and assert the structural
properties the plan requires: the job is trusted-PR-only, the model id and
trust guard are exact, the six phases run in the exact order, the scanner
runs before any upload, and the upload allowlist is exactly the two sanitized
paths.

One deliberate deviation from the plan's literal test sketch, disclosed here
rather than silently: `test_gate_order_and_sanitized_upload_are_explicit`
below anchors the final phase on the literal string
``"evals.business_journeys.artifacts"`` (the scanner's own qualified module
name) rather than the bare substring ``"artifacts"``. The bare substring
already occurs earlier in the required script text -- inside
`run_registered_cases`'s own required `--report
$REPO_ROOT/artifacts/runtime/deterministic-cases.json` path -- so an ordering
check keyed on the bare word could never pass for any script that also
contains that required, pre-existing path. The qualified module name is
unique to the final scan command and preserves the same property under test
(the scanner runs last).
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

import yaml

from evals.business_journeys.artifacts import ScannerFailureSummary

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "agent-mvp.yml"
SCRIPT_PATH = REPO_ROOT / "scripts" / "run_business_journey_gate.sh"
SPEC_PATH = REPO_ROOT / "frontend" / "src" / "test" / "e2e" / "business-journeys.spec.ts"

JOB_NAME = "business-journey-real-gate"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text())


def test_real_gate_is_blocking_and_exact_model():
    workflow = _workflow()
    job = workflow["jobs"][JOB_NAME]
    text = yaml.safe_dump(job)
    assert "continue-on-error" not in text
    assert "DEEPSEEK_API_KEY" in text
    assert "deepseek-v4-flash-vision-exp" in text
    workflow_text = WORKFLOW_PATH.read_text()
    assert "pull_request_target" not in workflow_text
    assert "github.event.pull_request.head.repo.full_name" in workflow_text
    assert "github.repository" in workflow_text
    assert "DEEPSEEK_API_BASE" not in workflow_text
    assert "base_url" not in workflow_text


def test_gate_order_and_sanitized_upload_are_explicit():
    script = SCRIPT_PATH.read_text()
    ordered = [
        "run_registered_cases",
        "--phase prepare",
        "business-journeys.spec.ts",
        "--phase verify",
        "evals.business_journeys.artifacts",
    ]
    positions = [script.index(value) for value in ordered]
    assert positions == sorted(positions)
    assert "frontend/test-results" not in script
    # Contiguous flag=value pairing (not independent substring presence):
    # the script uses quoted, REPO_ROOT-prefixed absolute paths throughout
    # for portability (unlike the workflow's own separate `id: scan` step,
    # which uses the brief's simpler bare-relative form since it always
    # runs with the repo root as its cwd) -- verify the exact pairing this
    # script actually emits, not just that both substrings appear somewhere.
    assert '--staging "$REPO_ROOT/artifacts/business_journeys/staging"' in script
    assert '--sanitized-output "$REPO_ROOT/artifacts/business_journeys/sanitized"' in script
    assert (
        '--failure-summary "$ARTIFACTS_DIR/business_journeys/scanner-failure-summary.json" \\\n'
        '    --deterministic-report "$ARTIFACTS_DIR/runtime/deterministic-cases.json"'
    ) in script


def test_workflow_uploads_only_after_scan_safe_or_safe_scan_failure():
    workflow = _workflow()
    steps = workflow["jobs"][JOB_NAME]["steps"]
    scan = next(step for step in steps if step.get("id") == "scan")
    safe_upload = next(step for step in steps if step.get("name") == "Upload sanitized journey evidence")
    failure_upload = next(step for step in steps if step.get("name") == "Upload safe scanner failure summary")
    assert (
        "scan --staging artifacts/business_journeys/staging "
        "--sanitized-output artifacts/business_journeys/sanitized "
        "--failure-summary artifacts/business_journeys/scanner-failure-summary.json "
        "--deterministic-report artifacts/runtime/deterministic-cases.json"
    ) in scan["run"]
    assert scan["if"] == "always()"
    assert safe_upload["if"] == "steps.scan.outputs.scan_safe == 'true'"
    assert failure_upload["if"] == "failure() && steps.scan.outputs.scan_safe != 'true'"
    assert safe_upload["with"]["path"] == "artifacts/business_journeys/sanitized/**"
    assert failure_upload["with"]["path"] == "artifacts/business_journeys/scanner-failure-summary.json"
    assert "always()" not in safe_upload["if"]
    assert "always()" not in failure_upload["if"]
    # No other artifact upload step exists in this job.
    upload_steps = [step for step in steps if step.get("uses", "").startswith("actions/upload-artifact")]
    assert len(upload_steps) == 2


def test_gate_rejects_a_caller_selected_application_origin_before_running_the_stack(tmp_path):
    """A supplied non-loopback origin must not receive the provider key."""
    root = tmp_path / "gate-root"
    script_dir = root / "scripts"
    script_dir.mkdir(parents=True)
    copied_script = script_dir / SCRIPT_PATH.name
    copied_script.write_text(SCRIPT_PATH.read_text())
    (root / ".env.example").write_text("")

    command_dir = tmp_path / "bin"
    command_dir.mkdir()
    (command_dir / "docker").write_text("#!/usr/bin/env bash\nexit 99\n")
    (command_dir / "docker").chmod(0o755)

    result = subprocess.run(
        ["bash", str(copied_script)],
        cwd=root,
        env={
            **os.environ,
            "PATH": f"{command_dir}:{os.environ['PATH']}",
            "DEEPSEEK_API_KEY": "test-provider-key",
            "BUSINESS_JOURNEY_API_BASE": "https://attacker.example.invalid",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "BUSINESS_JOURNEY_API_BASE_NOT_ALLOWED" in result.stderr


def test_failure_summary_is_closed_and_raw_browser_paths_are_forbidden():
    summary = ScannerFailureSummary(
        schema_version="1", run_id="run-1", status="scanner_failed",
        reason_codes=("FIXTURE_VALUE_FOUND",), category_counts={"fixture": 1},
        scanner_version="1",
    )
    assert set(summary.to_json()) == {
        "schema_version", "run_id", "status", "reason_codes",
        "category_counts", "scanner_version",
    }
    workflow_text = WORKFLOW_PATH.read_text()
    assert "frontend/test-results" not in workflow_text
    assert "trace.zip" not in workflow_text
    assert "raw logs" not in workflow_text
    assert "artifacts/" + "evals/business_journeys" not in workflow_text


def test_new_browser_spec_has_no_soft_skip():
    text = SPEC_PATH.read_text()
    assert "test.skip" not in text
    assert "test.fixme" not in text


def test_new_browser_spec_has_the_exact_three_titles():
    text = SPEC_PATH.read_text()
    for title in (
        "supply chain journey completes the governed browser loop",
        "finance journey completes the governed browser loop",
        "credit journey completes the governed browser loop",
    ):
        assert title in text


def test_one_retry_policy_for_timeout_and_429():
    playwright_config = (REPO_ROOT / "frontend" / "playwright.config.ts").read_text()
    assert "retries: 1" in playwright_config
    deepseek_client = (REPO_ROOT / "backend" / "evals" / "business_journeys" / "deepseek_client.py").read_text()
    # One retry means two total HTTP attempts (the original call plus
    # exactly one retry) for a timeout/429 -- never more.
    assert "_MAX_HTTP_ATTEMPTS = 2" in deepseek_client


def test_job_triggers_on_pull_request_only_and_rejects_non_pr_first():
    workflow = _workflow()
    job = workflow["jobs"][JOB_NAME]
    steps = job["steps"]
    script = SCRIPT_PATH.read_text()
    # The workflow's own trigger list still includes pull_request (shared
    # with other jobs); the job's own first real command must independently
    # reject a non-PR/untrusted-fork invocation before anything else runs.
    # YAML 1.1 parses the bare `on:` key as the boolean True, not the string
    # "on" -- read both to be robust to that well-known GitHub Actions quirk.
    triggers = workflow.get("on") or workflow.get(True) or {}
    assert "pull_request" in triggers
    first_run_step = next(step for step in steps if "run" in step)
    assert "TRUSTED_BRANCH_REQUIRED" in first_run_step["run"]
    assert "DEEPSEEK_API_KEY_REQUIRED" in script or "DEEPSEEK_API_KEY_REQUIRED" in yaml.safe_dump(job)


def test_script_is_strict_and_syntactically_valid():
    script = SCRIPT_PATH.read_text()
    assert "set -euo pipefail" in script
    assert "continue-on-error" not in script
    assert "down -v --remove-orphans" in script
    assert "BUSINESS_JOURNEY_ACCEPTANCE_ENABLED" in script
    assert "BUSINESS_JOURNEY_RUN_MANIFEST" in script


def test_gate_identity_is_unique_and_within_persisted_length_limits():
    """The disposable user must fit both persisted 50-character columns."""
    script = SCRIPT_PATH.read_text()
    assert 'GATE_ID="${RUN_ID#business-journey-}"' in script
    assert 'GATE_USERNAME="bjg-${GATE_ID}"' in script
    assert 'GATE_EMAIL="${GATE_USERNAME}@example.com"' in script

    # The generated suffix is epoch seconds plus the shell PID.  Even with
    # conservative decimal-width bounds, both values stay under the API's
    # persisted 50-character username/email limits.
    worst_case_username = "bjg-" + ("9" * 12) + "-" + ("9" * 7)
    worst_case_email = worst_case_username + "@example.com"
    assert len(worst_case_username) <= 50
    assert len(worst_case_email) <= 50


def test_browser_phase_uses_the_prepared_gate_identity_only_for_playwright():
    """Browser calls must authenticate as the account that owns the grants."""
    script = SCRIPT_PATH.read_text()
    browser_phase = script.split(
        "echo \"[business-journey-gate] phase 4/6: exact three browser journeys\"",
        1,
    )[1].split(
        "echo \"[business-journey-gate] phase 5/6: read-only post-browser verification\"",
        1,
    )[0]

    assert (
        '(cd frontend && AGENT_E2E_API_BASE="$API_BASE" \\\n'
        '    AGENT_E2E_ADMIN_USER="$GATE_USERNAME" \\\n'
        '    AGENT_E2E_ADMIN_PASSWORD="$GATE_PASSWORD" npx playwright test --config playwright.config.ts \\\n'
        '    src/test/e2e/business-journeys.spec.ts)'
    ) in browser_phase
    assert "export AGENT_E2E_ADMIN_USER" not in script
    assert "export AGENT_E2E_ADMIN_PASSWORD" not in script
