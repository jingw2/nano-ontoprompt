"""Deterministic E0-EVAL validators (Section 14).

`perfect_outcome` is the fake adapter's oracle: exactly the case's
expectations.  `score_case` scores one (case, outcome) pair across the
deterministic dimensions — permission decision, prohibited strings/effects,
citation recall/precision, exact parameters, interrupt/error, terminal
records (no duplicates), replay gaps, reconciliation entry, and unnecessary
clarification.  `evaluate_gates` maps case scores onto the manifest's release
gates (zero-tolerance vs point estimates) and reports the exact boundaries.
"""
from __future__ import annotations


def perfect_outcome(case: dict) -> dict:
    """Oracle outcome for a case: satisfies every expectation."""
    exp = case["expectations"]
    interrupt = exp.get("expected_interrupt")
    return {
        "case_id": case["id"],
        "decision": exp["decision"],
        "descriptors": list(exp.get("allowed_descriptors", [])),
        "citations": [{"source_id": c["source_id"]} for c in exp.get("expected_citations", [])],
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


def _contains_prohibited(case: dict, outcome: dict) -> list[str]:
    haystack = []
    for value in outcome.values():
        if isinstance(value, str):
            haystack.append(value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str):
                    haystack.append(item)
                elif isinstance(item, dict):
                    for sub in item.values():
                        if isinstance(sub, str):
                            haystack.append(sub)
    blob = "\n".join(haystack).lower()
    return [p for p in case["expectations"].get("prohibited", []) if p.lower() in blob]


def score_case(case: dict, outcome: dict) -> dict:
    """Deterministic per-dimension checks; ``passed`` is the AND of all."""
    exp = case["expectations"]
    expected_sources = {c["source_id"] for c in exp.get("expected_citations", [])}
    actual_sources = {
        c.get("source_id") for c in outcome.get("citations", [])
        if isinstance(c, dict) and c.get("source_id")
    }
    expected_params = case.get("expected_parameters", {})
    actual_params = outcome.get("parameters", {})

    checks = {
        "case_id": case["id"],
        "gates": list(case.get("gates", [])),
        "decision_ok": outcome.get("decision") == exp["decision"],
        "no_prohibited": _contains_prohibited(case, outcome) == [],
        "citations_recall": expected_sources <= actual_sources,
        # precision fails only when the outcome returns citations that were
        # never expected (vacuous when no citations are expected at all)
        "citations_precision": (not expected_sources and not actual_sources)
        or (bool(expected_sources) and actual_sources <= expected_sources),
        "parameters_ok": actual_params == expected_params,
        "interrupt_ok": outcome.get("interrupt") == exp.get("expected_interrupt"),
        "error_ok": outcome.get("error") == exp.get("expected_error"),
        "terminal_ok": set(outcome.get("terminal_records", [])) == set(exp.get("terminal_records", [])),
        "no_duplicates": outcome.get("duplicates", 0) == 0,
        "no_replay_gaps": outcome.get("replay_gaps", 0) == 0,
        "reconciliation_ok": not exp.get("must_enter_reconciliation") or bool(
            outcome.get("entered_reconciliation")),
        "clarification_not_required": not exp.get("clarification_not_required") or not bool(
            outcome.get("clarification_asked")),
    }
    checks["passed"] = all(checks[k] for k in (
        "decision_ok", "no_prohibited", "citations_recall", "citations_precision",
        "parameters_ok", "interrupt_ok", "error_ok", "terminal_ok", "no_duplicates",
        "no_replay_gaps", "reconciliation_ok", "clarification_not_required",
    ))
    return checks


def evaluate_gates(scores: dict[str, dict], release_gates: dict) -> dict[str, dict]:
    """Map per-case scores onto the manifest's release gates.

    Zero-tolerance gates pass only with zero raw failures; point gates compare
    the observed pass-rate against the declared threshold (or the precision /
    recall bounds for citation gates); upper-bound gates require the observed
    rate to stay at or below `threshold_upper`.
    """
    members: dict[str, list[str]] = {}
    for case_id, checks in scores.items():
        for gate in checks.get("gates", []):
            members.setdefault(gate, []).append(case_id)

    results: dict[str, dict] = {}
    for gate, spec in release_gates.items():
        ids = members.get(gate, [])
        denominator = spec["denominator"]
        failures = [cid for cid in ids if not scores[cid]["passed"]]
        passed_count = denominator - len(failures)
        observed = passed_count / denominator if denominator else 0.0

        if spec.get("mode") == "zero_tolerance":
            ok = not failures
            threshold = "zero raw failures"
        elif "precision" in spec or "recall" in spec:
            precision = observed  # one citation case per member
            recall = observed
            ok = precision >= spec.get("precision", 0.0) and recall >= spec.get("recall", 0.0)
            threshold = f"precision>={spec['precision']} recall>={spec['recall']}"
        elif "threshold_upper" in spec:
            # upper-bound gates measure the BAD rate (e.g. unnecessary
            # clarifications asked), which perfect outcomes keep at zero
            observed = len(failures) / denominator if denominator else 0.0
            ok = observed <= spec["threshold_upper"]
            threshold = f"bad_rate<={spec['threshold_upper']}"
        elif "threshold" in spec:
            ok = observed >= spec["threshold"]
            threshold = f"observed>={spec['threshold']}"
        else:
            ok = not failures
            threshold = "all pass"

        results[gate] = {
            "denominator": denominator,
            "failures": failures,
            "observed": observed,
            "threshold": threshold,
            "passed": bool(ok),
        }
    return results
