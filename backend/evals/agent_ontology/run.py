"""E0-EVAL core-v1 evaluation runner (Section 14).

Deterministic, offline-only runner.  The fake adapter is the oracle: its
outcome exactly matches each case's expectations, so the seeded run exercises
the validators and gates on the immutable 185-case corpus.  PR CI permits
only `fake`; no production-provider baseline is ever written.  The output is
redacted (case ids + pass flags + gate results only) and byte-deterministic
(no wall-clock timestamps), with a JUnit report written alongside.

    cd backend && python -m evals.agent_ontology.run \
        --manifest core-v1 --adapter fake --seed 141 \
        --output artifacts/evals/result.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from evals.agent_ontology.validators import evaluate_gates, perfect_outcome, score_case  # noqa: E402


class EvalAdapter:
    """One adapter per Section 14: exactly fake / recorded-provider /
    production-provider.  Only `fake` may run in PR CI."""

    name = ""
    version = ""

    def run(self, case: dict, seed: int) -> dict:  # pragma: no cover - interface
        raise NotImplementedError


class FakeEvalAdapter(EvalAdapter):
    """Deterministic oracle: the recorded outcome matches the case exactly."""

    name = "fake"
    version = "1.0.0"

    def run(self, case: dict, seed: int) -> dict:
        return perfect_outcome(case)


ADAPTERS: dict[str, type[EvalAdapter]] = {"fake": FakeEvalAdapter}


def _load_manifest(evals_dir: pathlib.Path, manifest_name: str) -> dict:
    path = evals_dir / "agent_ontology" / f"{manifest_name}.json"
    return json.loads(path.read_text())


def _load_cases(evals_dir: pathlib.Path, manifest: dict) -> list[dict]:
    path = evals_dir / "agent_ontology" / manifest["case_file"]
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _verify_manifest(evals_dir: pathlib.Path, manifest: dict) -> None:
    case_file = evals_dir / "agent_ontology" / manifest["case_file"]
    digest = hashlib.sha256(case_file.read_bytes()).hexdigest()
    if digest != manifest["cases_sha256"]:
        raise SystemExit(
            f"CORPUS_HASH_DRIFT: {manifest['case_file']} hash {digest} != manifest {manifest['cases_sha256']}"
        )
    cases = _load_cases(evals_dir, manifest)
    if len(cases) != manifest["case_count"]:
        raise SystemExit(
            f"CORPUS_COUNT_DRIFT: expected {manifest['case_count']} cases, found {len(cases)}"
        )


def _junit_xml(gates: dict[str, dict]) -> str:
    rows = []
    for gate, result in gates.items():
        rows.append(
            '<testcase classname="evals.agent_ontology" name="%s">%s</testcase>' % (
                gate,
                "" if result["passed"] else (
                    f'<failure message="gate failed: {result["failures"]}"/>'
                ),
            )
        )
    total = len(gates)
    failed = sum(1 for r in gates.values() if not r["passed"])
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<testsuite name="agent-ontology-core-v1" tests="{total}" failures="{failed}" errors="0">\n'
        + "\n".join(rows) + "\n</testsuite>\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the agent-ontology core-v1 evaluation")
    parser.add_argument("--manifest", default="core-v1")
    parser.add_argument("--adapter", default="fake")
    parser.add_argument("--seed", type=int, default=141)
    parser.add_argument("--output", default="artifacts/evals/result.json")
    args = parser.parse_args(argv)

    if args.adapter not in ADAPTERS:
        raise SystemExit(f"EVAL_ADAPTER_UNKNOWN: {args.adapter!r} (allowed: {sorted(ADAPTERS)})")

    evals_dir = pathlib.Path(__file__).resolve().parents[1]
    manifest = _load_manifest(evals_dir, args.manifest)
    _verify_manifest(evals_dir, manifest)
    cases = _load_cases(evals_dir, manifest)

    adapter = ADAPTERS[args.adapter]()
    outcomes = [adapter.run(case, args.seed) for case in cases]
    scores = {case["id"]: score_case(case, outcome) for case, outcome in zip(cases, outcomes)}
    gates = evaluate_gates(scores, manifest["release_gates"])
    passed = all(result["passed"] for result in gates.values())

    run_id = hashlib.sha256(
        f"{manifest['corpus']}:{args.seed}:{manifest['cases_sha256']}".encode()
    ).hexdigest()[:16]

    result = {
        "run_id": run_id,
        "manifest": args.manifest,
        "adapter": args.adapter,
        "adapter_version": adapter.version,
        "seed": args.seed,
        "case_count": len(cases),
        "cases_sha256": manifest["cases_sha256"],
        "passed": passed,
        "scores": {
            case_id: {k: v for k, v in checks.items() if k not in ("gates",)}
            for case_id, checks in scores.items()
        },
        "gates": gates,
    }

    output = pathlib.Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    junit = output.parent / "junit.xml"
    junit.write_text(_junit_xml(gates), encoding="utf-8")
    print(f"wrote {output} ({'PASS' if passed else 'FAIL'}, {len(cases)} cases, run_id={run_id})")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
