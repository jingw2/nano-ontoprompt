"""Execute the deterministic runtime fixture registry without model access.

The runner is deliberately small: it invokes one exact pytest node per
deterministic manifest case and stores only its stable outcome metadata.  Test
output is never retained because it may contain fixture values unsuitable for
release artifacts.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_DATA = REPO_ROOT / "test_data/runtime"
if str(RUNTIME_DATA) not in sys.path:
    sys.path.insert(0, str(RUNTIME_DATA))

from registry import assert_case_registry, load_cases, targets_for  # noqa: E402


@dataclass(frozen=True)
class CaseExecutionResult:
    case_id: str
    target: str
    status: str
    skipped: bool
    exit_code: int


@dataclass(frozen=True)
class CaseExecutionReport:
    schema_version: int
    model_calls: int
    missing: tuple[str, ...]
    duplicates: tuple[str, ...]
    results: tuple[CaseExecutionResult, ...]


def _write_report(report_path: Path, report: CaseExecutionReport) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "schema_version": report.schema_version,
                "model_calls": report.model_calls,
                "missing": list(report.missing),
                "duplicates": list(report.duplicates),
                "results": [asdict(result) for result in report.results],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def load_case_execution_report(path: Path) -> CaseExecutionReport:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return CaseExecutionReport(
        schema_version=int(data["schema_version"]),
        model_calls=int(data["model_calls"]),
        missing=tuple(data["missing"]),
        duplicates=tuple(data["duplicates"]),
        results=tuple(CaseExecutionResult(**result) for result in data["results"]),
    )


def assert_deterministic_report(report: CaseExecutionReport) -> None:
    assert report.schema_version == 1
    assert report.model_calls == 0
    assert not report.missing
    assert not report.duplicates
    case_ids = [result.case_id for result in report.results]
    assert len(case_ids) == len(set(case_ids))
    assert all(result.status == "passed" and not result.skipped and result.exit_code == 0 for result in report.results)


def run_registered_case(case: Mapping[str, object]) -> CaseExecutionResult:
    """Run exactly one registered deterministic pytest node for ``case``."""

    assert_case_registry(case)
    case_id = str(case["case_id"])
    if case["execution_mode"] != "deterministic":
        raise ValueError(f"case {case_id!r} is not deterministic")
    target = targets_for(case_id)[0]
    if target.kind != "pytest":
        raise ValueError(f"deterministic case {case_id!r} is not a pytest target")

    pytest_node = target.path.removeprefix("backend/")
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", pytest_node, "-q"],
        cwd=REPO_ROOT / "backend",
        text=True,
        capture_output=True,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}".lower()
    skipped = " skipped" in output or "skipped " in output
    return CaseExecutionResult(
        case_id=case_id,
        target=target.path,
        status="passed" if completed.returncode == 0 and not skipped else "failed",
        skipped=skipped,
        exit_code=completed.returncode,
    )


def run_all_registered_cases(manifest_path: Path, *, report_path: Path) -> tuple[CaseExecutionResult, ...]:
    """Run every deterministic manifest case once and write a sanitized report."""

    deterministic = [case for case in load_cases(Path(manifest_path)) if case.get("execution_mode") == "deterministic"]
    case_ids = [str(case.get("case_id")) for case in deterministic]
    duplicates = tuple(sorted(case_id for case_id in set(case_ids) if case_ids.count(case_id) > 1))
    missing: list[str] = []
    results: list[CaseExecutionResult] = []
    if not duplicates:
        for case in deterministic:
            try:
                results.append(run_registered_case(case))
            except (AssertionError, KeyError, ValueError):
                missing.append(str(case.get("case_id")))

    report = CaseExecutionReport(
        schema_version=1,
        model_calls=0,
        missing=tuple(sorted(missing)),
        duplicates=duplicates,
        results=tuple(results),
    )
    _write_report(Path(report_path), report)
    assert_deterministic_report(report)
    return report.results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        run_all_registered_cases(args.manifest, report_path=args.report)
    except AssertionError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
