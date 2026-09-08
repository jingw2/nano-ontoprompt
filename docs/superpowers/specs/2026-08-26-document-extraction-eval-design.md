# Document-extraction quality eval design

This is sub-project D of the B → D → C → A decomposition (see
`docs/superpowers/specs/2026-08-26-test-data-coverage-design.md` for the
full breakdown), plus the follow-up fix sub-project E:

- **B (done)**: expanded `test_data/` with edge-case/boundary/semantic/
  missing-format fixtures.
- **D (this spec)**: a document→ontology extraction quality eval harness,
  covering both extraction correctness (ground-truth keyword recall) and
  ontology structural quality (entity dedup, instance leakage) — the gaps
  identified by the ontology-quality audit performed while scoping B.
- **C**: decide the fate of the dead domain-driven Playwright specs.
- **A**: `test_data/` hygiene (remove orphaned content, fix two broken-path
  scripts).
- **E**: fix the underlying bugs D's gates (and B's fixture tests) found —
  missing single-document entity dedup, no instance-leakage validator,
  un-humanized PipelineMapping template names, plus B's four documented
  bugs (CSV comma-in-quotes, GBK mojibake, unsupported `.xls`/`.xml`/`.doc`/
  `.ppt`, `SQLConnector.pull_full()`'s pandas incompatibility).

## Goal

Today there is zero automated testing of extraction *quality* — nothing
asserts that uploading a real document produces the right entities, or
that the resulting ontology is well-formed rather than fragmented. This
spec adds a data-driven eval, mirroring the existing, mature pattern in
`backend/evals/agent_ontology/` (manifest + cases + validators + CLI
runner), scoped to the `simple_llm` document-extraction path and the eight
happy-path domain files already in `test_data/`.

## Scope

In scope: a new `backend/evals/document_extraction/` eval package running
real extraction (via the app's actual `backend/app/tasks/extraction.py`
code, using DeepSeek) against the 8 domain sample files, scored by two
independent kinds of check — ground-truth keyword recall (did extraction
find what it obviously should) and structural quality gates (dedup,
instance leakage) that need no ground truth at all. A non-blocking CI step
wiring this into `agent-mvp.yml`.

Not in scope: fixing any bug the eval finds (sub-project E); a live test
for the PipelineMapping vague-naming finding (different build mode than
this corpus — see below); `edge_cases/` fixtures from sub-project B
(this corpus is the 8 happy-path files only, for now); embedding-based or
LLM-as-judge semantic matching (deferred — fuzzy string matching plus
keyword matching covers every case the audit actually found); making this
CI step blocking (deferred until it's proven stable).

## Directory structure

```
backend/evals/document_extraction/
  __init__.py
  manifest/
    core-v1.json          # 8 cases: domain name, source file path, ground-truth id
  ground_truth/
    信贷.json 供应链.json 教育.json 医疗.json 财务.json 法律.json 营销.json HR.json
                           # each: list of {category, required_keywords}
  adapters.py              # DeepSeekExtractionAdapter — calls real extraction.py
  validators.py            # keyword_recall(), dedup_gate(), instance_leakage_gate()
  run.py                   # CLI: --manifest --adapter --output
  test_core_v1.py          # pytest wrapper, skipped unless DEEPSEEK_API_KEY is set
```

## Manifest and ground truth format

`core-v1.json` mirrors `agent_ontology/core-v1.json`'s shape: an immutable,
versioned case list. Each case:

```json
{
  "case_id": "credit-01",
  "domain": "信贷",
  "source_file": "test_data/信贷/贷款申请记录.csv",
  "ground_truth_id": "信贷"
}
```

Each `ground_truth/<domain>.json` is a flat list of requirements, not a
full expected-output document:

```json
[
  {"category": "entity", "required_keywords": ["客户"]},
  {"category": "entity", "required_keywords": ["贷款"]},
  {"category": "logic_rule", "required_keywords": ["逾期"]},
  {"category": "action", "required_keywords": ["审批"]}
]
```

A requirement passes if *any* extracted item in the matching category
(`entities`, `logic_rules`, `actions`) has a name or description
containing every string in `required_keywords` (simple substring check,
not fuzzy — keyword presence is a low-ambiguity signal by design). The
per-case score is `passed_requirements / total_requirements`.

## Validators

Three independent checks, each returning a list of findings (empty =
clean) plus a boolean pass/fail:

- **`keyword_recall(ground_truth, extraction_result)`**: substring
  presence check described above. This is the only validator that needs
  ground truth.
- **`dedup_gate(extraction_result)`**: for each entity type, fuzzy-matches
  every pair of extracted entity names (`rapidfuzz.fuzz.ratio`, threshold
  85) and flags pairs above the threshold as probable unmerged duplicates.
  No ground truth needed — a pure internal-consistency check, directly
  targeting the audit's `产品研发部`/`产品研发部门` finding.
- **`instance_leakage_gate(extraction_result)`**: regex-flags entity names
  matching an ID/numbered-record pattern (e.g. trailing digit runs of 3+,
  or an uppercase-letter-prefix + digit pattern like `CUS0001`) that
  appear in the top-level `entities` list — targeting the audit's
  276-of-431-entities instance-leakage finding.

`dedup_gate` and `instance_leakage_gate` are pure functions over an
extraction result shape (`{entities: [...], relations: [...],
logic_rules: [...], actions: [...]}`) — they can also be unit-tested
directly against a hand-built fixture result, independent of any live
extraction call, so their own correctness doesn't depend on DeepSeek
being reachable.

## Adapter: real extraction, not a reimplementation

`DeepSeekExtractionAdapter` calls the app's real extraction path: read the
source file, run it through `convert_document()` (same function B's
fixtures test), then through the actual LLM-extraction function in
`backend/app/tasks/extraction.py`, configured to use a DeepSeek model
config. This is the same principle as B: test real production code, never
reimplement it for the test's convenience.

## CLI runner and pytest wrapper

`run.py` mirrors `agent_ontology/run.py`: `python -m
evals.document_extraction.run --manifest core-v1 --output
artifacts/evals/document_extraction_result.json`, running every case
through the adapter and all three validators, writing a result JSON with
per-case and aggregate scores.

`test_core_v1.py` is a thin pytest wrapper for local iteration, but is
`@pytest.mark.skipif(not os.environ.get("DEEPSEEK_API_KEY"), reason=...)`
— it must never fire in a normal `pytest -q` run for a developer without a
key configured, and must never make a surprise real API call as a side
effect of running the existing test suite.

## CI wiring

A new step in `.github/workflows/agent-mvp.yml`, after the existing
"Agent evaluation corpus" step:

```yaml
- name: Document extraction eval (non-blocking)
  continue-on-error: true
  env:
    DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}
  run: cd backend && python -m evals.document_extraction.run --manifest core-v1 --output artifacts/evals/document_extraction_result.json
```

**Manual setup required, outside this plan's reach**: `DEEPSEEK_API_KEY`
must be added as a GitHub Actions repository secret before this step can
run for real in CI. Without it, the step's own code should detect the
missing key and exit cleanly with a skipped/no-op result (not an error),
so `continue-on-error` isn't doing the work of masking a config mistake.

## Non-goals

- No fix to any bug a gate finds (sub-project E).
- No PipelineMapping-mode corpus or live naming-quality test (different
  build mode; deferred to E, which can test its own fix directly).
- No embedding or LLM-as-judge matching (fuzzy string + keyword matching
  only, per approved design).
- No blocking-CI promotion (stays `continue-on-error: true` until proven
  stable over real runs).
- No `edge_cases/` fixtures in this corpus (8 happy-path domain files
  only).

## Verification

- `run.py` executes end-to-end against all 8 cases with a real
  `DEEPSEEK_API_KEY` (manual local verification, since CI can't provide
  one until the secret is added) and produces a result JSON with 8
  per-case entries, each with a keyword-recall score and dedup/
  instance-leakage findings lists (possibly empty).
- `dedup_gate` and `instance_leakage_gate` each have direct unit tests
  (in `test_core_v1.py` or a sibling test file, NOT gated behind
  `DEEPSEEK_API_KEY`) against hand-built fixture extraction results that
  deliberately contain a known-duplicate pair and a known-leaked-instance
  name, proving each gate actually fires.
- `test_core_v1.py`'s live-extraction tests are confirmed skipped (not
  failed, not silently passed) when `DEEPSEEK_API_KEY` is absent.
- The new CI step is confirmed `continue-on-error: true` and does not
  affect the overall workflow's pass/fail status.
