"""Case-to-test registry and manifest loader for the business journey corpora.

Three enterprise journeys -- ``supply_chain``, ``finance``, ``credit`` -- each
have their own versioned fixture corpus under
``test_data/runtime/<journey_id>/`` (``manifest.json``, ``inputs.json``,
``semantic_minima.json``, ``dialogues.json``, ``governance.json``,
``case_matrix.json``, ``reproducibility.json``). This module is the
authoritative reader/validator for that corpus, mirroring the conventions of
the sibling ``registry.py`` (which does the same job for the generic,
non-journey corpus in ``manifest.json``):

- ``load_journey_manifest`` / ``validate_journey_manifest`` load and check the
  structural contract of one journey's corpus.
- ``journey_cases`` / ``assert_journey_case`` are the case-to-test-target
  registry for that journey's 15-case matrix.

Fourteen of each journey's fifteen cases are deterministic and reuse the
closest existing, already-passing pytest node that proves the same generic
governed-runtime property this codebase's Phase 2/3 milestones already built
(exact-plan HITL, stale-snapshot denial, MCP typed-failure surfacing, and so
on) -- the same "reuse the closest real test, disclose the mapping" approach
``registry.py`` already uses throughout its own ``CASE_REGISTRY``. Only
``normal-pipeline-release`` is ``execution_mode: "real_model_browser"`` and
points at a later task's Playwright title; nothing here runs it, and this
module does not add a second runner.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import registry as _registry

JOURNEY_IDS = ("supply_chain", "finance", "credit")

JOURNEY_MINIMA: dict[str, dict[str, object]] = {
    "supply_chain": {
        "entities": ["Supplier", "PurchaseOrder", "InventoryItem", "Warehouse"],
        "relations": ["SUPPLIES", "PLACED_WITH", "CONTAINS", "BELOW_SAFETY_STOCK"],
        "rules": ["inventory_below_safety_stock"],
        "actions": ["risk_label", "purchase_order_price_update"],
        "keywords": ["supplier", "inventory", "safety stock", "purchase order"],
        "low_risk_action": "risk_label",
        "high_risk_action": "purchase_order_price_update",
    },
    "finance": {
        "entities": ["Account", "Invoice", "Expense", "CostCenter", "AccountingPeriod"],
        "relations": ["POSTED_TO", "BELONGS_TO", "DUPLICATES", "EXCEEDS_BUDGET"],
        "rules": ["duplicate_invoice", "expense_over_budget"],
        "actions": ["risk_label", "journal_entry"],
        "keywords": ["invoice", "expense", "accounting period", "cash flow"],
        "low_risk_action": "risk_label",
        "high_risk_action": "journal_entry",
    },
    "credit": {
        "entities": ["Borrower", "LoanApplication", "Repayment", "CreditLine", "RiskAssessment"],
        "relations": ["APPLIES_FOR", "HAS_REPAYMENT", "ASSESSED_AS", "USES_CREDIT_LINE"],
        "rules": ["credit_score_limit"],
        "actions": ["risk_label", "credit_limit_update"],
        "keywords": ["borrower", "application", "repayment", "credit limit"],
        "low_risk_action": "risk_label",
        "high_risk_action": "credit_limit_update",
    },
}

# The 15 exact case IDs every journey's case_matrix.json must contain, in
# table order. Only the first is real_model_browser; the other 14 are
# deterministic.
NORMAL_CASE_ID = "normal-pipeline-release"
CASE_IDS: tuple[str, ...] = (
    "normal-pipeline-release",
    "edge-empty-result",
    "edge-duplicate-or-missing",
    "security-no-grant",
    "security-stale-release",
    "security-prompt-injection",
    "resilience-timeout-retry",
    "resilience-429-retry",
    "resilience-provider-error",
    "resilience-mcp-timeout",
    "resilience-sse-reconnect",
    "writeback-automatic",
    "writeback-hitl-approved",
    "writeback-hitl-rejected",
    "writeback-hitl-expired",
)

JOURNEY_RISK_CLASSES = {"automatic", "human_approved", "rejected", "expired"}
_JOURNEY_SPEC_PATH = "frontend/src/test/e2e/business-journeys.spec.ts"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class JourneyTestTarget:
    """One exact deterministic pytest descriptor for a journey case.

    Unlike the generic corpus's ``registry.TestTarget`` (``kind``/``path``/
    ``title``), a journey case target names ``path`` (the test file) and
    ``selector`` (the pytest node id after ``::``, or -- for the sole
    browser case -- the exact Playwright test title) separately.
    """

    kind: str
    path: str
    selector: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "path": self.path, "selector": self.selector}


def _pytest_target(path: str, selector: str) -> JourneyTestTarget:
    return JourneyTestTarget(kind="pytest", path=path, selector=selector)


# ---------------------------------------------------------------------------
# Deterministic case -> closest existing, already-passing pytest node.
#
# None of these tests were written for this plan; every one already exists
# and already passes as part of the Phase 2/3 corpus this plan's Prerequisites
# section requires. Business-journey-specific coverage of the same
# properties (e.g. a real duplicate-invoice rule, a real DeepSeek-client
# timeout/429 retry) is the job of Task 2/3 of this plan, not Task 1 -- this
# corpus registers the case and asserts the underlying governed-runtime
# invariant is proven today, not that a journey-specific rule engine already
# exists.
# ---------------------------------------------------------------------------
_RUNTIME = "backend/tests/runtime"
_AGENT = "backend/tests/agent"

DETERMINISTIC_CASE_TARGETS: dict[str, JourneyTestTarget] = {
    # No-match investigation stays ALLOW with an explicit empty result.
    "edge-empty-result": _pytest_target(
        f"{_RUNTIME}/test_runtime_api.py", "test_investigate_api_returns_empty_allow_for_no_match"
    ),
    # Real dedup proof (lineage IDs, not journey entities): the same
    # sorted/deduplicated-id contract a duplicate-invoice/application rule
    # depends on.
    "edge-duplicate-or-missing": _pytest_target(
        f"{_RUNTIME}/test_snapshot_materialization.py",
        "test_collect_lineage_returns_sorted_deduplicated_ids_and_safe_summaries",
    ),
    "security-no-grant": _pytest_target(
        f"{_RUNTIME}/test_runtime_credentials.py", "test_invalid_delegation_is_structured_denial[missing]"
    ),
    "security-stale-release": _pytest_target(
        f"{_RUNTIME}/test_snapshot_freshness.py",
        "test_stale_policy_is_allow_hitl_or_deny_without_rewriting_snapshot[hard-stale]",
    ),
    # Untrusted document content is sanitized before it can act -- the same
    # "untrusted input cannot change behavior" property prompt injection
    # requires.
    "security-prompt-injection": _pytest_target(f"{_AGENT}/test_untrusted_artifact.py", "test_strips_script_tags"),
    # Closest real unit-level timeout test: an operation timeout is mapped
    # to a typed, traceable failure with no silent retry loop. The real
    # DeepSeek-client timeout/backoff path is Task 2's client, not this
    # fixture corpus.
    "resilience-timeout-retry": _pytest_target(
        f"{_AGENT}/test_playwright_adapter.py", "test_browse_page_evaluate_timeout_maps_to_playwright_timeout"
    ),
    # Retry-safety invariant a 429 retry also depends on: retrying never
    # calls the writer twice.
    "resilience-429-retry": _pytest_target(
        f"{_RUNTIME}/test_execution_service.py", "test_idempotent_retry_never_calls_the_writer_twice"
    ),
    "resilience-provider-error": _pytest_target(
        f"{_AGENT}/test_mcp_client.py", "test_call_tool_surfaces_protocol_error"
    ),
    # A raised transport exception from the MCP client's underlying
    # transport is wrapped into a typed, traceable MCPClientError -- the
    # same code path a real socket timeout takes.
    "resilience-mcp-timeout": _pytest_target(f"{_AGENT}/test_mcp_client.py", "test_call_tool_wraps_ssrf_block"),
    "resilience-sse-reconnect": _pytest_target(f"{_AGENT}/test_event_stream.py", "test_stream_gap_detection"),
    "writeback-automatic": _pytest_target(
        f"{_RUNTIME}/test_execution_service.py", "test_automatic_execution_uses_shared_writer_and_audit[postgresql]"
    ),
    "writeback-hitl-approved": _pytest_target(
        f"{_RUNTIME}/test_risk_policy.py", "test_high_risk_plan_requires_exact_hash_hitl"
    ),
    "writeback-hitl-rejected": _pytest_target(
        f"{_RUNTIME}/test_risk_policy.py", "test_approve_exact_plan_rejects_wrong_hash"
    ),
    "writeback-hitl-expired": _pytest_target(
        f"{_RUNTIME}/test_risk_policy.py", "test_approve_exact_plan_rejects_expired_plan"
    ),
}


def browser_target(journey_id: str) -> JourneyTestTarget:
    """Return the exact, already-registered Playwright target for a journey.

    Reuses the same three ``frontend/.../business-journeys.spec.ts`` titles
    already registered in ``registry.JOURNEY_BROWSER_TARGETS`` (and stamped
    into the three legacy placeholder cases in the main ``manifest.json``),
    so there is exactly one place these three titles are spelled out.
    """

    key = f"journey-{journey_id.replace('_', '-')}"
    target = _registry.JOURNEY_BROWSER_TARGETS[key]
    return JourneyTestTarget(kind="playwright", path=target.path, selector=target.title)


def compute_corpus_hash(
    inputs_doc: Mapping[str, object],
    semantic_minima_doc: Mapping[str, object],
    dialogues_doc: Mapping[str, object],
    governance_doc: Mapping[str, object],
) -> str:
    """Canonical sha256 of one journey's content-bearing fixture files.

    Both the generator and ``load_journey_manifest`` call this so a
    checked-in corpus and a fresh generation are compared with exactly one
    hashing formula.
    """

    payload = {
        "inputs": inputs_doc,
        "semantic_minima": semantic_minima_doc,
        "dialogues": dialogues_doc,
        "governance": governance_doc,
    }
    canonical = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


# ---------------------------------------------------------------------------
# Case registry
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parent


def journey_cases(journey_id: str) -> list[Mapping[str, object]]:
    """Load the 15-case matrix for ``journey_id`` from its checked-in corpus."""

    if journey_id not in JOURNEY_IDS:
        raise ValueError(f"unknown journey_id {journey_id!r}")
    path = _ROOT / journey_id / "case_matrix.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["cases"]


def assert_journey_case(case: Mapping[str, object]) -> None:
    """Validate one journey case against its own metadata and the registry."""

    required = (
        "case_id",
        "journey_id",
        "coverage",
        "expected",
        "layers",
        "fixture_manifest_sha256",
        "risk_class",
        "fixture_hashes",
        "execution_mode",
        "skip_allowed",
        "test_targets",
    )
    for field in required:
        assert field in case, f"journey case missing required field {field!r}: {case!r}"

    case_id = str(case["case_id"])
    assert case["journey_id"] in JOURNEY_IDS, f"case {case_id!r} has unknown journey_id {case['journey_id']!r}"
    assert case["skip_allowed"] is False, f"case {case_id!r} must not be skippable"
    assert case["risk_class"] in JOURNEY_RISK_CLASSES, f"case {case_id!r} has invalid risk_class {case['risk_class']!r}"
    assert _SHA256_RE.match(str(case["fixture_manifest_sha256"])), (
        f"case {case_id!r} fixture_manifest_sha256 is not a sha256 hex digest"
    )

    targets = case["test_targets"]
    assert isinstance(targets, list) and len(targets) == 1, (
        f"case {case_id!r} must have exactly one test_targets element, got {targets!r}"
    )
    target = targets[0]
    assert set(target) >= {"kind", "path", "selector"}, f"case {case_id!r} target is missing kind/path/selector"
    assert target["kind"] in {"pytest", "playwright"}
    assert case["execution_mode"] in {"deterministic", "real_model_browser"}

    if case_id == NORMAL_CASE_ID:
        assert case["execution_mode"] == "real_model_browser", f"{case_id} must be real_model_browser"
        assert target["kind"] == "playwright"
        assert target["path"] == _JOURNEY_SPEC_PATH
        assert _registry.JOURNEY_TITLE_SUBSTRING in target["selector"]
        expected = browser_target(str(case["journey_id"]))
        assert target == expected.to_dict(), f"{case_id} target does not match the registered browser target"
    else:
        assert case["execution_mode"] == "deterministic", f"{case_id} must be deterministic"
        assert target["kind"] == "pytest"
        assert target["path"].startswith("backend/") and target["path"].endswith(".py"), (
            f"case {case_id!r} pytest target path must be a repository-relative backend test file"
        )
        assert target["selector"], f"case {case_id!r} pytest target is missing a selector"
        expected_target = DETERMINISTIC_CASE_TARGETS.get(case_id)
        if expected_target is not None:
            assert target == expected_target.to_dict(), (
                f"case {case_id!r} target {target!r} does not match the registered target "
                f"{expected_target.to_dict()!r}"
            )


# ---------------------------------------------------------------------------
# Journey manifest (typed record over the 7 per-journey files)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JourneyManifest:
    schema_version: int
    journey_id: str
    fixture_version: str
    seed: int
    model_id: str
    inputs: tuple[Mapping[str, object], ...]
    source_refs: tuple[Mapping[str, object], ...]
    input_hashes: Mapping[str, str]
    semantic_minima: Mapping[str, object]
    dialogue_scenarios: tuple[Mapping[str, object], ...]
    governance_outcomes: tuple[Mapping[str, object], ...]
    plan_instances: tuple[Mapping[str, object], ...]
    case_matrix: tuple[Mapping[str, object], ...]
    reproducibility: Mapping[str, object]
    logical_model_calls: int
    max_tool_rounds_per_turn: int
    max_http_attempts: int


_MANIFEST_REQUIRED = (
    "schema_version",
    "journey_id",
    "fixture_version",
    "seed",
    "model_id",
    "logical_model_calls",
    "max_tool_rounds_per_turn",
    "max_http_attempts",
)
_INPUTS_REQUIRED = ("inputs", "source_refs", "input_hashes")
_MINIMA_REQUIRED = (
    "entities",
    "relations",
    "rules",
    "actions",
    "keywords",
    "low_risk_action",
    "high_risk_action",
    "source_citation_ids",
    "numeric_predicates",
)
_DIALOGUES_REQUIRED = ("dialogue_scenarios",)
_GOVERNANCE_REQUIRED = ("governance_outcomes", "plan_instances")
_CASE_MATRIX_REQUIRED = ("cases",)
_REPRODUCIBILITY_REQUIRED = ("seed", "fixture_version", "manifest_sha256")


def _load_json(path: Path, journey_id: str) -> dict:
    if not path.exists():
        raise ValueError(f"{journey_id}: missing required file {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _require_fields(journey_id: str, filename: str, doc: Mapping[str, object], fields: tuple[str, ...]) -> None:
    for field in fields:
        if field not in doc:
            raise ValueError(f"{journey_id}/{filename}: missing required field {field!r}")


def load_journey_manifest(journey_id: str, root: Path) -> JourneyManifest:
    """Load and structurally validate one journey's 7-file fixture corpus.

    Rejects (raises ``ValueError``) an unknown journey, a missing required
    field, a non-repository-relative or nonexistent source path, a hash that
    is not a sha256 hex digest, a model ID other than the one official
    model, a manifest with no visual (image) input, or a corpus whose
    ``reproducibility.json`` hash does not match a fresh recomputation.
    """

    if journey_id not in JOURNEY_IDS:
        raise ValueError(f"unknown journey_id {journey_id!r}")

    journey_dir = Path(root) / journey_id
    manifest_doc = _load_json(journey_dir / "manifest.json", journey_id)
    inputs_doc = _load_json(journey_dir / "inputs.json", journey_id)
    minima_doc = _load_json(journey_dir / "semantic_minima.json", journey_id)
    dialogues_doc = _load_json(journey_dir / "dialogues.json", journey_id)
    governance_doc = _load_json(journey_dir / "governance.json", journey_id)
    case_matrix_doc = _load_json(journey_dir / "case_matrix.json", journey_id)
    reproducibility_doc = _load_json(journey_dir / "reproducibility.json", journey_id)

    _require_fields(journey_id, "manifest.json", manifest_doc, _MANIFEST_REQUIRED)
    _require_fields(journey_id, "inputs.json", inputs_doc, _INPUTS_REQUIRED)
    _require_fields(journey_id, "semantic_minima.json", minima_doc, _MINIMA_REQUIRED)
    _require_fields(journey_id, "dialogues.json", dialogues_doc, _DIALOGUES_REQUIRED)
    _require_fields(journey_id, "governance.json", governance_doc, _GOVERNANCE_REQUIRED)
    _require_fields(journey_id, "case_matrix.json", case_matrix_doc, _CASE_MATRIX_REQUIRED)
    _require_fields(journey_id, "reproducibility.json", reproducibility_doc, _REPRODUCIBILITY_REQUIRED)

    if manifest_doc["journey_id"] != journey_id:
        raise ValueError(f"{journey_id}: manifest.json declares journey_id {manifest_doc['journey_id']!r}")
    if manifest_doc["model_id"] != _registry.JOURNEY_MODEL_ID:
        raise ValueError(f"{journey_id}: unexpected model_id {manifest_doc['model_id']!r}")

    repo_root = Path(root).resolve().parent.parent
    has_image = False
    for entry in inputs_doc["inputs"]:
        for field in ("kind", "media_type", "path", "sha256", "fixture_id"):
            if field not in entry:
                raise ValueError(f"{journey_id}: input entry missing required field {field!r}: {entry!r}")
        if entry["kind"] not in ("tabular", "document", "image"):
            raise ValueError(f"{journey_id}: input {entry['fixture_id']!r} has invalid kind {entry['kind']!r}")
        if entry["kind"] == "image":
            has_image = True
        if not _SHA256_RE.match(str(entry["sha256"])):
            raise ValueError(f"{journey_id}: input {entry['fixture_id']!r} has a non-sha256 hash")
        rel_path = str(entry["path"])
        if rel_path.startswith("/") or ".." in Path(rel_path).parts:
            raise ValueError(f"{journey_id}: input path {rel_path!r} is not repository-relative")
        if not (repo_root / rel_path).exists():
            raise ValueError(f"{journey_id}: input path {rel_path!r} does not exist in the repository")
    if not has_image:
        raise ValueError(f"{journey_id}: manifest has no visual (image) input")

    for ref in inputs_doc["source_refs"]:
        if not _SHA256_RE.match(str(ref.get("sha256", ""))):
            raise ValueError(f"{journey_id}: source_ref {ref!r} has a non-sha256 hash")
        rel_path = str(ref["path"])
        if rel_path.startswith("/") or ".." in Path(rel_path).parts or not (repo_root / rel_path).exists():
            raise ValueError(f"{journey_id}: source_ref path {rel_path!r} is not a valid repository-relative path")

    recomputed = compute_corpus_hash(inputs_doc, minima_doc, dialogues_doc, governance_doc)
    if recomputed != reproducibility_doc["manifest_sha256"]:
        raise ValueError(
            f"{journey_id}: manifest is not reproducible "
            f"(recomputed {recomputed!r} != recorded {reproducibility_doc['manifest_sha256']!r})"
        )

    for case in case_matrix_doc["cases"]:
        assert_journey_case(case)

    return JourneyManifest(
        schema_version=int(manifest_doc["schema_version"]),
        journey_id=journey_id,
        fixture_version=str(manifest_doc["fixture_version"]),
        seed=int(manifest_doc["seed"]),
        model_id=str(manifest_doc["model_id"]),
        inputs=tuple(inputs_doc["inputs"]),
        source_refs=tuple(inputs_doc["source_refs"]),
        input_hashes=dict(inputs_doc["input_hashes"]),
        semantic_minima=dict(minima_doc),
        dialogue_scenarios=tuple(dialogues_doc["dialogue_scenarios"]),
        governance_outcomes=tuple(governance_doc["governance_outcomes"]),
        plan_instances=tuple(governance_doc["plan_instances"]),
        case_matrix=tuple(case_matrix_doc["cases"]),
        reproducibility=dict(reproducibility_doc),
        logical_model_calls=int(manifest_doc["logical_model_calls"]),
        max_tool_rounds_per_turn=int(manifest_doc["max_tool_rounds_per_turn"]),
        max_http_attempts=int(manifest_doc["max_http_attempts"]),
    )


def validate_journey_manifest(manifest: JourneyManifest) -> None:
    """Enforce the cardinality/coverage contract on an already-loaded manifest.

    One tabular input (at least one), one policy/report document, one fixed
    rendered visual page, one dialogue with both a normal and a high-risk
    outcome, and governance IDs exactly {automatic, approved, rejected,
    expired} with exactly three plan_instances (approved/rejected/expired).
    """

    tabular = [i for i in manifest.inputs if i["kind"] == "tabular"]
    documents = [i for i in manifest.inputs if i["kind"] == "document"]
    images = [i for i in manifest.inputs if i["kind"] == "image"]
    assert len(tabular) >= 1, f"{manifest.journey_id}: needs at least one tabular input"
    assert len(documents) == 1, f"{manifest.journey_id}: needs exactly one policy/report document"
    assert len(images) == 1, f"{manifest.journey_id}: needs exactly one fixed rendered visual page"

    assert len(manifest.dialogue_scenarios) == 1, f"{manifest.journey_id}: needs exactly one dialogue"
    scenario = manifest.dialogue_scenarios[0]
    assert "normal_outcome" in scenario and "high_risk_outcome" in scenario, (
        f"{manifest.journey_id}: dialogue must declare a normal and a high-risk outcome"
    )

    ids = {outcome["id"] for outcome in manifest.governance_outcomes}
    assert ids == {"automatic", "approved", "rejected", "expired"}, (
        f"{manifest.journey_id}: governance_outcomes ids {ids!r} != required set"
    )
    assert len(manifest.plan_instances) == 3, f"{manifest.journey_id}: needs exactly three plan_instances"
    assert {p["branch"] for p in manifest.plan_instances} == {"approved", "rejected", "expired"}
    target_fixture_ids = [p["target_fixture_id"] for p in manifest.plan_instances]
    assert len(target_fixture_ids) == len(set(target_fixture_ids)), (
        f"{manifest.journey_id}: plan_instances must use distinct target_fixture_id values"
    )

    case_ids = [case["case_id"] for case in manifest.case_matrix]
    assert set(case_ids) == set(CASE_IDS), f"{manifest.journey_id}: case_matrix case_ids != required 15 IDs"
    assert len(case_ids) == len(set(case_ids)), f"{manifest.journey_id}: case_matrix has duplicate case_ids"
