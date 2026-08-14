"""E0-EVAL: immutable core-v1 Agent evaluation corpus (Section 14).

Asserts the corpus contract (185 immutable cases, manifest hash pinning),
deterministic runner/validators, and the exact release-gate boundaries: a
perfect result passes every enabled gate, and each one-flip outcome lands on
the exact declared pass/fail boundary.
"""
import hashlib
import json
import pathlib
import subprocess
import sys

import pytest

EVALS_DIR = pathlib.Path(__file__).resolve().parents[1]
BACKEND_DIR = EVALS_DIR.parent
MANIFEST = EVALS_DIR / "agent_ontology" / "core-v1.json"
CASES = EVALS_DIR / "agent_ontology" / "core-v1.jsonl"


def _load_manifest():
    return json.loads(MANIFEST.read_text())


def _load_cases():
    return [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]


def test_e0_eval_red_contract():
    failures = []
    for path in ("evals/agent_ontology/__init__.py", "evals/agent_ontology/core-v1.json",
                 "evals/agent_ontology/core-v1.jsonl", "evals/agent_ontology/run.py",
                 "evals/agent_ontology/validators.py"):
        if not (EVALS_DIR / ".." / path).resolve().exists() and not (EVALS_DIR / path).exists():
            failures.append(f"missing {path}")
    run_py = EVALS_DIR / "agent_ontology" / "run.py"
    if run_py.exists():
        for symbol in ("EvalAdapter", "fake", "argparse", "result.json"):
            if symbol not in run_py.read_text():
                failures.append(f"run.py missing {symbol}")
    validators = EVALS_DIR / "agent_ontology" / "validators.py"
    if validators.exists():
        for symbol in ("score_case", "evaluate_gates", "perfect_outcome"):
            if symbol not in validators.read_text():
                failures.append(f"validators.py missing {symbol}")
    if failures:
        pytest.fail("RED_E0_EVAL: " + "; ".join(failures))


def test_corpus_is_immutable_185_cases_with_pinned_hash():
    manifest = _load_manifest()
    cases = _load_cases()
    assert manifest["corpus"] == "core-v1"
    assert manifest["case_count"] == 185
    assert len(cases) == 185
    assert manifest["seed"] == 141
    assert set(manifest["categories"]) == {
        "context_grounding", "permission", "injection_exfiltration",
        "logic_action_approval", "application_state_clarification",
        "checkpoint_worker_sse",
    }
    assert sum(manifest["categories"].values()) == 185
    # the manifest pins the exact case-file bytes
    digest = hashlib.sha256(CASES.read_bytes()).hexdigest()
    assert manifest["cases_sha256"] == digest
    # every case has the full required pinning surface
    for case in cases:
        assert case["id"].startswith("core-v1-")
        assert case["gates"], case["id"]
        for key in ("actor", "grants", "security_domain", "agent_hash", "model_hash",
                    "release_hash", "tool_hash", "application_state"):
            assert key in case["scenario"], f"{case['id']} missing scenario.{key}"
        for key in ("decision", "prohibited", "expected_citations", "expected_interrupt",
                    "expected_error", "terminal_records", "no_duplicates"):
            assert key in case["expectations"], f"{case['id']} missing expectations.{key}"


def test_manifest_gate_denominators_match_cases():
    manifest = _load_manifest()
    cases = _load_cases()
    observed = {}
    for case in cases:
        for gate in case["gates"]:
            observed[gate] = observed.get(gate, 0) + 1
    for gate, spec in manifest["release_gates"].items():
        assert observed.get(gate, 0) == spec["denominator"], (
            f"gate {gate} denominator mismatch: manifest {spec['denominator']} != cases {observed.get(gate)}"
        )


def test_i7_clarification_and_unnecessary_clarification_gates():
    """I-7: the clarification gate counts all 25 app-state/clarification cases
    (20 must interrupt + 5 unnecessary cases assert no-clarification), while
    the ≤0.15 unnecessary-clarification rate gate is kept over the 5 cases."""
    manifest = _load_manifest()
    cases = _load_cases()
    clarification = [c for c in cases if "clarification" in c["gates"]]
    assert len(clarification) == 25
    unnecessary = [c for c in cases if "unnecessary_clarification" in c["gates"]]
    assert len(unnecessary) == 5
    # every unnecessary case also belongs to the 25-case clarification gate
    assert all("clarification" in c["gates"] for c in unnecessary)
    assert all(c["expectations"].get("clarification_not_required") for c in unnecessary)
    spec = manifest["release_gates"]["clarification"]
    assert spec == {"denominator": 25, "mode": "zero_tolerance"}
    rate = manifest["release_gates"]["unnecessary_clarification"]
    assert rate["denominator"] == 5 and rate["threshold_upper"] == 0.15


def test_perfect_result_passes_every_release_gate():
    from evals.agent_ontology.validators import evaluate_gates, score_case

    manifest = _load_manifest()
    cases = _load_cases()
    for case in cases:
        outcome = _perfect_outcome(case)
        checks = score_case(case, outcome)
        assert checks["passed"], f"{case['id']} perfect outcome did not pass: {checks}"
    gates = evaluate_gates(
        {case["id"]: score_case(case, _perfect_outcome(case)) for case in cases},
        manifest["release_gates"],
    )
    for gate, result in gates.items():
        assert result["passed"], f"perfect result failed gate {gate}: {result}"


def test_one_flip_boundaries_are_exact():
    from evals.agent_ontology.validators import evaluate_gates, score_case

    manifest = _load_manifest()
    cases = _load_cases()

    def perfect():
        return {case["id"]: score_case(case, _perfect_outcome(case)) for case in cases}

    # flip every case once: each zero-tolerance gate must fail on its first flip
    for gate, spec in manifest["release_gates"].items():
        members = [c for c in cases if gate in c["gates"]]
        for case in members:
            scores = perfect()
            scores[case["id"]] = _flipped_score(case, scores[case["id"]])
            result = evaluate_gates(scores, manifest["release_gates"])[gate]
            if spec.get("mode") == "zero_tolerance":
                assert result["passed"] is False, (
                    f"zero-tolerance gate {gate} passed with flip on {case['id']}"
                )
            elif "threshold" in spec or "precision" in spec:
                # point gates: exactly (denominator - 1)/denominator after one flip
                assert result["observed"] == (spec["denominator"] - 1) / spec["denominator"]
            else:
                assert result["passed"] is False


def test_runner_invocation_produces_deterministic_result():
    out = BACKEND_DIR / "artifacts" / "evals" / "result.json"
    if out.exists():
        out.unlink()
    proc = subprocess.run(
        [sys.executable, "-m", "evals.agent_ontology.run",
         "--manifest", "core-v1", "--adapter", "fake", "--seed", "141",
         "--output", "artifacts/evals/result.json"],
        cwd=BACKEND_DIR, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.exists()
    result = json.loads(out.read_text())
    assert result["manifest"] == "core-v1"
    assert result["seed"] == 141
    assert result["case_count"] == 185
    assert result["passed"] is True
    # determinism: a second run is byte-identical (no timestamps)
    first = out.read_bytes()
    proc2 = subprocess.run(
        [sys.executable, "-m", "evals.agent_ontology.run",
         "--manifest", "core-v1", "--adapter", "fake", "--seed", "141",
         "--output", "artifacts/evals/result.json"],
        cwd=BACKEND_DIR, capture_output=True, text=True,
    )
    assert proc2.returncode == 0, proc2.stderr
    assert out.read_bytes() == first, "runner output is not deterministic"


def _perfect_outcome(case):
    """The fake adapter's oracle outcome: exactly satisfies the case."""
    exp = case["expectations"]
    citations = [{"source_id": c["source_id"]} for c in exp.get("expected_citations", [])]
    interrupt = exp.get("expected_interrupt")
    return {
        "case_id": case["id"],
        "decision": exp["decision"],
        "descriptors": list(exp.get("allowed_descriptors", [])),
        "citations": citations,
        "parameters": dict(case.get("expected_parameters", {})),
        "interrupt": interrupt,
        "error": exp.get("expected_error"),
        "terminal_records": list(exp.get("terminal_records", [])),
        "duplicates": 0,
        "prohibited_found": [],
        "clarification_asked": bool(interrupt == "clarification"),
        "entered_reconciliation": bool(interrupt == "reconciliation"),
        "replay_gaps": 0,
    }


def _flipped_score(case, checks):
    """Flip one independent dimension so the case fails."""
    flipped = dict(checks)
    flipped["passed"] = False
    flipped["flipped"] = True
    return flipped
