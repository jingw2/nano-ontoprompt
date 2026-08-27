"""Static contract checks for the real refresh Compose checkpoint."""
from __future__ import annotations

import pathlib
import stat

import yaml


REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
GATE_SCRIPT = REPO_ROOT / "scripts" / "verify_refresh_checkpoint.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "agent-mvp.yml"


def test_refresh_checkpoint_script_exists_is_executable_and_uses_the_refresh_profile():
    assert GATE_SCRIPT.exists()
    assert GATE_SCRIPT.stat().st_mode & stat.S_IXUSR
    source = GATE_SCRIPT.read_text()
    assert "--profile refresh" in source
    assert "seed_refresh_checkpoint_fixture.py" in source
    assert "down -v --remove-orphans" in source
    assert "trap cleanup EXIT" in source


def test_refresh_checkpoint_polls_both_sources_independently():
    source = GATE_SCRIPT.read_text()
    assert "source-checkpoint-batch" in source
    assert "source-checkpoint-event" in source
    assert "poll_source \"source-checkpoint-batch\"" in source
    assert "poll_source \"source-checkpoint-event\"" in source


def test_refresh_checkpoint_is_wired_into_ci():
    data = yaml.safe_load(WORKFLOW.read_text())
    run_text = "\n".join(
        str(step.get("run", ""))
        for job in data["jobs"].values()
        for step in job.get("steps", [])
    )
    assert "scripts/verify_refresh_checkpoint.sh" in run_text


def test_refresh_checkpoint_only_claims_pass_after_cleanup_succeeds():
    source = GATE_SCRIPT.read_text()
    assert "CHECKPOINT_COMPLETE=1" in source
    assert "cleanup_status" in source
    assert '[ "$cleanup_status" -ne 0 ]' in source
    assert '[ "$CHECKPOINT_COMPLETE" -eq 1 ]' in source
