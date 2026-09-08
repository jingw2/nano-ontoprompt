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
``registry.py`` already uses throughout its own ``CASE_REGISTRY``. Eight of
those fourteen fully prove their case's claimed assertion; the other six are
marked ``target_status: "provisional"`` with a ``proves`` string describing
the narrower property their current target actually proves, because no
existing test proves the full claim yet (real journey-specific coverage is
Task 2/3's job). Only ``normal-pipeline-release`` is
``execution_mode: "real_model_browser"`` and points at a later task's
Playwright title; nothing here runs it, and this module does not add a
second runner.

All 42 deterministic cases (14 per journey) are also merged into the shared
``test_data/runtime/manifest.json`` under a globally-unique
``f"{journey-with-hyphens}-{case_id}"`` id (see
``registry.JOURNEY_DETERMINISTIC_TARGETS``/``journey_case_id``), which is
what the existing, unmodified ``run_registered_cases.py`` actually reads and
executes -- this module's own ``DETERMINISTIC_CASE_TARGETS`` is derived from
that same shared definition (``registry.JOURNEY_DETERMINISTIC_TARGET_DEFS``),
not a second, disconnected copy.
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
# The single source of truth for these mappings is
# registry.JOURNEY_DETERMINISTIC_TARGET_DEFS (registry.py is the foundational,
# import-free module here; this module imports it, not the reverse, to avoid
# a cycle). That module is also what generate_runtime_fixtures.py merges into
# the shared manifest.json as f"{journey}-{case_id}" entries, so the exact
# same target -- and the same `target_status`/`proves` honesty markers for
# the 6 cases where no existing test proves the full claimed property yet --
# is what actually gets executed by the unmodified run_registered_cases.py
# AND what a per-journey case_matrix.json records. See that dict's own
# comments for the full per-case mapping rationale/disclosure.
# ---------------------------------------------------------------------------


def _split_node(node: str) -> tuple[str, str]:
    path, _, selector = node.partition("::")
    return path, selector


DETERMINISTIC_CASE_TARGETS: dict[str, JourneyTestTarget] = {
    case_id: _pytest_target(*_split_node(target_def.node))
    for case_id, target_def in _registry.JOURNEY_DETERMINISTIC_TARGET_DEFS.items()
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
    manifest_doc: Mapping[str, object],
    inputs_doc: Mapping[str, object],
    semantic_minima_doc: Mapping[str, object],
    dialogues_doc: Mapping[str, object],
    governance_doc: Mapping[str, object],
    case_matrix_doc: Mapping[str, object],
    reproducibility_doc: Mapping[str, object],
) -> str:
    """Canonical sha256 of all seven journey documents.

    The digest is recorded inside ``reproducibility.json`` and echoed by
    every case, so those self-referential fields are replaced with fixed
    sentinels before serializing. Everything else, including manifest and
    case-matrix content, is content-addressed.
    """

    normalized_cases = json.loads(json.dumps(case_matrix_doc))
    for case in normalized_cases.get("cases", []):
        case["fixture_manifest_sha256"] = "<corpus-hash>"
        fixture_hashes = case.get("fixture_hashes")
        if isinstance(fixture_hashes, dict) and "input_manifest_sha256" in fixture_hashes:
            fixture_hashes["input_manifest_sha256"] = "<corpus-hash>"
    normalized_reproducibility = json.loads(json.dumps(reproducibility_doc))
    normalized_reproducibility["manifest_sha256"] = "<corpus-hash>"
    payload = {
        "manifest": manifest_doc,
        "inputs": inputs_doc,
        "semantic_minima": semantic_minima_doc,
        "dialogues": dialogues_doc,
        "governance": governance_doc,
        "case_matrix": normalized_cases,
        "reproducibility": normalized_reproducibility,
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

        # Every deterministic case is also merged into the shared
        # test_data/runtime/manifest.json under this qualified id, so it is
        # actually executed by the unmodified run_registered_cases.py --
        # not just registered in this journey-local namespace.
        journey_id = str(case["journey_id"])
        shared_case_id = _registry.journey_case_id(journey_id, case_id)
        assert case.get("shared_case_id") == shared_case_id, (
            f"case {case_id!r} must record shared_case_id={shared_case_id!r} "
            f"(the id it is merged into the shared manifest.json under)"
        )
        assert shared_case_id in _registry.CASE_REGISTRY, (
            f"case {case_id!r} is not merged into registry.CASE_REGISTRY as {shared_case_id!r}"
        )

        target_def = _registry.JOURNEY_DETERMINISTIC_TARGET_DEFS[case_id]
        if target_def.target_status is not None:
            assert case.get("target_status") == "provisional", (
                f"case {case_id!r} has no existing test proving its full claim and must be "
                f"marked target_status: provisional"
            )
            assert case.get("proves"), f"case {case_id!r} is provisional but has no proves text"
        else:
            assert "target_status" not in case and "proves" not in case, (
                f"case {case_id!r} has a fully-proven target and must not carry target_status/proves"
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


def _repository_root(root: Path) -> Path:
    """Resolve the repository root for a runtime corpus root.

    Kept as a narrow seam for copied-corpus contract tests; production roots
    remain ``<repo>/test_data/runtime``.
    """

    return Path(root).resolve().parent.parent


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

    repo_root = _repository_root(root)
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
        source_path = repo_root / rel_path
        if not source_path.exists():
            raise ValueError(f"{journey_id}: input path {rel_path!r} does not exist in the repository")
        if hashlib.sha256(source_path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"{journey_id}: input {entry['fixture_id']!r} hash does not match {rel_path!r}")
        if inputs_doc["input_hashes"].get(entry["fixture_id"]) != entry["sha256"]:
            raise ValueError(f"{journey_id}: input_hashes hash does not match {entry['fixture_id']!r}")
    if not has_image:
        raise ValueError(f"{journey_id}: manifest has no visual (image) input")

    for ref in inputs_doc["source_refs"]:
        if not _SHA256_RE.match(str(ref.get("sha256", ""))):
            raise ValueError(f"{journey_id}: source_ref {ref!r} has a non-sha256 hash")
        rel_path = str(ref["path"])
        source_path = repo_root / rel_path
        if rel_path.startswith("/") or ".." in Path(rel_path).parts or not source_path.exists():
            raise ValueError(f"{journey_id}: source_ref path {rel_path!r} is not a valid repository-relative path")
        if hashlib.sha256(source_path.read_bytes()).hexdigest() != ref["sha256"]:
            raise ValueError(f"{journey_id}: source_ref hash does not match {rel_path!r}")

    input_ids = {str(entry["fixture_id"]) for entry in inputs_doc["inputs"]}
    if set(inputs_doc["input_hashes"]) != input_ids:
        raise ValueError(f"{journey_id}: input_hashes must contain exactly one entry per input")
    for fixture_id, digest in inputs_doc["input_hashes"].items():
        if not _SHA256_RE.match(str(digest)):
            raise ValueError(f"{journey_id}: input_hashes {fixture_id!r} has a non-sha256 hash")

    recomputed = compute_corpus_hash(
        manifest_doc, inputs_doc, minima_doc, dialogues_doc, governance_doc, case_matrix_doc, reproducibility_doc
    )
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
