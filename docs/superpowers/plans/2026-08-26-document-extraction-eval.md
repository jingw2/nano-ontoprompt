# Document-Extraction Quality Eval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a data-driven eval harness — mirroring `backend/evals/agent_ontology/`'s manifest+validators+runner pattern — that runs real DeepSeek extraction against 8 real domain documents and scores it two ways: ground-truth keyword recall, and structural quality gates (entity dedup, instance leakage) that need no ground truth at all.

**Architecture:** A new `backend/evals/document_extraction/` package. The extraction adapter calls the app's real, unmodified `extract_ontology()` function (`backend/app/services/llm_service.py`) directly — no database, no Celery, no mocking — against 8 curated `.md` source files from `test_data/`. Three pure-function validators score each result. A CLI runner aggregates everything into a result JSON; a pytest wrapper (skipped without an API key) exposes it to `pytest`; a new non-blocking CI step runs it for real.

**Tech Stack:** Python 3, `rapidfuzz` (new dependency), the existing `app.services.llm_service.extract_ontology` and `app.services.document_service.convert_document`.

**Spec:** `docs/superpowers/specs/2026-08-26-document-extraction-eval-design.md`

## Global Constraints

- No database, no Celery, no mocking of the code under test — the adapter calls `extract_ontology()` directly, exactly as `backend/app/tasks/extraction.py::run_extraction` does internally, just without the DB/task-orchestration layer around it (which isn't relevant to what this eval measures).
- `keyword_recall` searches across ALL extracted categories (`entities`, `relations`, `logic_rules`, `actions`, `instances`) for each required keyword — not restricted to the ground-truth entry's stated `category` field, which is informational only. An LLM's categorization of a given concept is not itself under test here; whether the concept was found at all is.
- `dedup_gate` and `instance_leakage_gate` are pure functions over a plain dict shape (see Task 2) and must have direct unit tests that need no API key and never call any network code.
- The live-extraction pytest test and the CI step must both be skippable/no-op without `DEEPSEEK_API_KEY` set — never fail loudly for a missing key, and never make a network call as a side effect of an unrelated `pytest -q` run.
- DeepSeek uses the OpenAI-compatible client path already in `_call_llm` (`backend/app/services/llm_service.py:320-354`) — pass `provider="deepseek"` (anything other than `"anthropic"` routes there), `api_base="https://api.deepseek.com"`, `model="deepseek-chat"`.
- This eval must never touch or fix any bug it finds — sub-project E's job, not this plan's.

---

### Task 1: Manifest and ground-truth data

**Files:**
- Create: `backend/evals/document_extraction/__init__.py`
- Create: `backend/evals/document_extraction/manifest/core-v1.json`
- Create: `backend/evals/document_extraction/ground_truth/信贷.json`
- Create: `backend/evals/document_extraction/ground_truth/供应链.json`
- Create: `backend/evals/document_extraction/ground_truth/教育.json`
- Create: `backend/evals/document_extraction/ground_truth/医疗.json`
- Create: `backend/evals/document_extraction/ground_truth/财务.json`
- Create: `backend/evals/document_extraction/ground_truth/法律.json`
- Create: `backend/evals/document_extraction/ground_truth/营销.json`
- Create: `backend/evals/document_extraction/ground_truth/HR.json`
- Test: `backend/tests/evals/test_document_extraction_manifest.py`

**Interfaces:**
- Produces: `manifest/core-v1.json` — a JSON array of 8 case objects, each `{"case_id": str, "domain": str, "source_file": str, "ground_truth_id": str}`. `source_file` paths are relative to the repo root.
- Produces: each `ground_truth/<domain>.json` — a JSON array of requirement objects, each `{"category": str, "required_keywords": list[str]}`.

- [ ] **Step 1: Write the manifest**

Create `backend/evals/document_extraction/manifest/core-v1.json`:

```json
[
  {"case_id": "credit-01", "domain": "信贷", "source_file": "test_data/信贷/信贷业务战略.md", "ground_truth_id": "信贷"},
  {"case_id": "supply-chain-01", "domain": "供应链", "source_file": "test_data/供应链/supply_chain_strategy.md", "ground_truth_id": "供应链"},
  {"case_id": "education-01", "domain": "教育", "source_file": "test_data/教育/academic_policy.md", "ground_truth_id": "教育"},
  {"case_id": "medical-01", "domain": "医疗", "source_file": "test_data/医疗/clinical_protocols.md", "ground_truth_id": "医疗"},
  {"case_id": "finance-01", "domain": "财务", "source_file": "test_data/财务/financial_controls.md", "ground_truth_id": "财务"},
  {"case_id": "legal-01", "domain": "法律", "source_file": "test_data/法律/legal_framework.md", "ground_truth_id": "法律"},
  {"case_id": "marketing-01", "domain": "营销", "source_file": "test_data/营销/marketing_strategy.md", "ground_truth_id": "营销"},
  {"case_id": "hr-01", "domain": "HR", "source_file": "test_data/HR/hr_policy.md", "ground_truth_id": "HR"}
]
```

- [ ] **Step 2: Write the 8 ground-truth files**

Each requirement's keywords are literal substrings confirmed present in the corresponding source file's actual content.

Create `backend/evals/document_extraction/ground_truth/信贷.json`:

```json
[
  {"category": "entity", "required_keywords": ["借款人"]},
  {"category": "entity", "required_keywords": ["信用"]},
  {"category": "entity", "required_keywords": ["授信额度"]}
]
```

Create `backend/evals/document_extraction/ground_truth/供应链.json`:

```json
[
  {"category": "entity", "required_keywords": ["供应商"]},
  {"category": "logic_rule", "required_keywords": ["交货准时率"]},
  {"category": "logic_rule", "required_keywords": ["质量合格率"]}
]
```

Create `backend/evals/document_extraction/ground_truth/教育.json`:

```json
[
  {"category": "entity", "required_keywords": ["学分"]},
  {"category": "logic_rule", "required_keywords": ["毕业"]},
  {"category": "logic_rule", "required_keywords": ["GPA"]}
]
```

Create `backend/evals/document_extraction/ground_truth/医疗.json`:

```json
[
  {"category": "entity", "required_keywords": ["高血压"]},
  {"category": "entity", "required_keywords": ["血压"]},
  {"category": "logic_rule", "required_keywords": ["治疗"]}
]
```

Create `backend/evals/document_extraction/ground_truth/财务.json`:

```json
[
  {"category": "logic_rule", "required_keywords": ["采购"]},
  {"category": "logic_rule", "required_keywords": ["审批"]},
  {"category": "logic_rule", "required_keywords": ["付款"]}
]
```

Create `backend/evals/document_extraction/ground_truth/法律.json`:

```json
[
  {"category": "entity", "required_keywords": ["合同"]},
  {"category": "entity", "required_keywords": ["争议解决"]},
  {"category": "entity", "required_keywords": ["仲裁"]}
]
```

Create `backend/evals/document_extraction/ground_truth/营销.json`:

```json
[
  {"category": "entity", "required_keywords": ["客户"]},
  {"category": "entity", "required_keywords": ["ARR"]},
  {"category": "entity", "required_keywords": ["健康度"]}
]
```

Create `backend/evals/document_extraction/ground_truth/HR.json`:

```json
[
  {"category": "entity", "required_keywords": ["员工"]},
  {"category": "logic_rule", "required_keywords": ["绩效"]},
  {"category": "entity", "required_keywords": ["职级"]}
]
```

- [ ] **Step 3: Create the package `__init__.py`**

Create `backend/evals/document_extraction/__init__.py` (empty file — makes the directory an importable package, matching `backend/evals/agent_ontology/__init__.py`'s convention).

- [ ] **Step 4: Write the failing test**

Create `backend/tests/evals/test_document_extraction_manifest.py`:

```python
"""Manifest/ground-truth data validation — no LLM calls, no API key needed."""
import json
from pathlib import Path

EVAL_ROOT = Path(__file__).resolve().parents[2] / "evals" / "document_extraction"
REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_manifest():
    with open(EVAL_ROOT / "manifest" / "core-v1.json", encoding="utf-8") as f:
        return json.load(f)


def test_manifest_has_8_cases_with_required_fields():
    cases = _load_manifest()
    assert len(cases) == 8
    for case in cases:
        assert set(case.keys()) == {"case_id", "domain", "source_file", "ground_truth_id"}


def test_manifest_source_files_exist_on_disk():
    cases = _load_manifest()
    for case in cases:
        assert (REPO_ROOT / case["source_file"]).exists(), f"missing {case['source_file']}"


def test_every_case_has_a_matching_ground_truth_file():
    cases = _load_manifest()
    for case in cases:
        gt_path = EVAL_ROOT / "ground_truth" / f"{case['ground_truth_id']}.json"
        assert gt_path.exists(), f"missing ground truth for {case['ground_truth_id']}"
        with open(gt_path, encoding="utf-8") as f:
            requirements = json.load(f)
        assert len(requirements) >= 1
        for req in requirements:
            assert set(req.keys()) == {"category", "required_keywords"}
            assert len(req["required_keywords"]) >= 1
```

- [ ] **Step 5: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/evals/test_document_extraction_manifest.py -v`
Expected: FAIL — `backend/evals/document_extraction/` doesn't exist yet (before Steps 1-3 run) or `backend/tests/evals/` has no `__init__.py`/isn't discovered. If the manifest/ground-truth files from Steps 1-3 already exist by the time you run this, the test will PASS immediately instead — that's fine, it means Steps 1-3 were already done correctly; just confirm the test genuinely exercises the files rather than being vacuously true.

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/evals/test_document_extraction_manifest.py -v`
Expected: 3 PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/evals/document_extraction/ backend/tests/evals/test_document_extraction_manifest.py
git commit -m "feat: add document-extraction eval manifest and ground truth"
```

---

### Task 2: Validators — keyword recall, dedup gate, instance-leakage gate

**Files:**
- Create: `backend/evals/document_extraction/validators.py`
- Test: `backend/tests/evals/test_document_extraction_validators.py`
- Modify: `backend/requirements.txt`

**Interfaces:**
- Consumes: nothing from other tasks — pure functions over a plain dict shape.
- Produces (used by Task 4):
  - `keyword_recall(ground_truth: list[dict], extraction_result: dict) -> dict` — returns `{"score": float, "passed": list[dict], "failed": list[dict]}`.
  - `dedup_gate(extraction_result: dict, threshold: int = 85) -> list[dict]` — returns a list of findings, each `{"category": str, "name_a": str, "name_b": str, "similarity": float}`. Empty list = clean.
  - `instance_leakage_gate(extraction_result: dict) -> list[dict]` — returns a list of findings, each `{"name": str, "reason": str}`. Empty list = clean.
- The `extraction_result` shape all three functions consume: `{"entities": [{"name_cn": str, "description": str, ...}], "relations": [...], "logic_rules": [{"name_cn": str, ...}], "actions": [{"name_cn": str, ...}], "instances": [{"entity_type": str, "name_cn": str, ...}]}` — matches `app.services.llm_service.extract_ontology`'s real return shape exactly (confirmed against `backend/tests/agent/test_simple_llm_instances.py:116-127`).

- [ ] **Step 1: Add the `rapidfuzz` dependency**

Add to `backend/requirements.txt` (alphabetically near other top-level deps, matching the file's existing style):

```
rapidfuzz==3.10.1
```

Run: `cd backend && .venv/bin/python -m pip install rapidfuzz==3.10.1`
Expected: installs cleanly.

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/evals/test_document_extraction_validators.py`:

```python
"""Validator unit tests — pure functions, no API key, no network calls."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evals.document_extraction.validators import keyword_recall, dedup_gate, instance_leakage_gate

SAMPLE_RESULT = {
    "entities": [
        {"name_cn": "借款人", "description": "申请贷款的个人"},
        {"name_cn": "信用分层", "description": "按信用评分划分的等级"},
    ],
    "relations": [],
    "logic_rules": [
        {"name_cn": "授信额度规则", "description": "根据信用评分确定授信额度"},
    ],
    "actions": [],
    "instances": [],
}


def test_keyword_recall_all_pass():
    ground_truth = [
        {"category": "entity", "required_keywords": ["借款人"]},
        {"category": "entity", "required_keywords": ["信用"]},
        {"category": "logic_rule", "required_keywords": ["授信额度"]},
    ]
    result = keyword_recall(ground_truth, SAMPLE_RESULT)
    assert result["score"] == 1.0
    assert len(result["passed"]) == 3
    assert len(result["failed"]) == 0


def test_keyword_recall_partial_failure():
    ground_truth = [
        {"category": "entity", "required_keywords": ["借款人"]},
        {"category": "entity", "required_keywords": ["不存在的关键词"]},
    ]
    result = keyword_recall(ground_truth, SAMPLE_RESULT)
    assert result["score"] == 0.5
    assert len(result["passed"]) == 1
    assert len(result["failed"]) == 1
    assert result["failed"][0]["required_keywords"] == ["不存在的关键词"]


def test_keyword_recall_searches_across_all_categories_not_just_stated_one():
    # "required_keywords" states category "logic_rule" but the keyword
    # actually only appears in an entity — must still count as found,
    # per this plan's Global Constraint that category is informational only.
    ground_truth = [{"category": "logic_rule", "required_keywords": ["借款人"]}]
    result = keyword_recall(ground_truth, SAMPLE_RESULT)
    assert result["score"] == 1.0


def test_dedup_gate_flags_near_duplicate_entity_names():
    result = {
        "entities": [
            {"name_cn": "产品研发部", "description": "负责产品开发"},
            {"name_cn": "产品研发部门", "description": "负责新产品研发工作"},
            {"name_cn": "市场部", "description": "负责市场推广"},
        ],
        "relations": [], "logic_rules": [], "actions": [], "instances": [],
    }
    findings = dedup_gate(result)
    assert len(findings) == 1
    assert {findings[0]["name_a"], findings[0]["name_b"]} == {"产品研发部", "产品研发部门"}
    assert findings[0]["category"] == "entities"


def test_dedup_gate_clean_when_no_near_duplicates():
    result = {
        "entities": [
            {"name_cn": "借款人", "description": "x"},
            {"name_cn": "资金方", "description": "y"},
        ],
        "relations": [], "logic_rules": [], "actions": [], "instances": [],
    }
    assert dedup_gate(result) == []


def test_instance_leakage_gate_flags_id_pattern_entity_names():
    result = {
        "entities": [
            {"name_cn": "客户", "description": "概念实体"},
            {"name_cn": "CUS0001", "description": "leaked instance"},
            {"name_cn": "客户001", "description": "leaked instance"},
        ],
        "relations": [], "logic_rules": [], "actions": [], "instances": [],
    }
    findings = instance_leakage_gate(result)
    flagged_names = {f["name"] for f in findings}
    assert flagged_names == {"CUS0001", "客户001"}


def test_instance_leakage_gate_clean_for_concept_names():
    result = {
        "entities": [
            {"name_cn": "客户", "description": "概念实体"},
            {"name_cn": "信用分层", "description": "概念实体"},
        ],
        "relations": [], "logic_rules": [], "actions": [], "instances": [],
    }
    assert instance_leakage_gate(result) == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/evals/test_document_extraction_validators.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'evals.document_extraction.validators'`.

- [ ] **Step 4: Write the implementation**

Create `backend/evals/document_extraction/validators.py`:

```python
"""Pure-function validators for the document-extraction eval. No database,
no network calls — every function here takes a plain extraction-result dict
and returns findings, and is unit-testable without an LLM API key."""
import re

from rapidfuzz import fuzz

_CATEGORY_KEYS = ("entities", "relations", "logic_rules", "actions", "instances")

# ID/numbered-record pattern: a bare letter-prefix+digits code (CUS0001), or
# any name ending in a run of 3+ digits (客户001) — both are the shape of a
# specific record identifier, not a concept/type name.
_ID_PATTERN = re.compile(r"^[A-Za-z]+\d+$")
_TRAILING_DIGITS_PATTERN = re.compile(r"\d{3,}$")


def _all_text_blobs(extraction_result: dict) -> list[str]:
    """Every name_cn/description/entity_type string across all categories,
    concatenated per-item so a keyword match can span name+description."""
    blobs: list[str] = []
    for category in _CATEGORY_KEYS:
        for item in extraction_result.get(category) or []:
            if not isinstance(item, dict):
                continue
            parts = [
                str(item.get("name_cn") or ""),
                str(item.get("description") or ""),
                str(item.get("entity_type") or ""),
            ]
            blobs.append(" ".join(parts))
    return blobs


def keyword_recall(ground_truth: list[dict], extraction_result: dict) -> dict:
    blobs = _all_text_blobs(extraction_result)
    passed: list[dict] = []
    failed: list[dict] = []
    for requirement in ground_truth:
        keywords = requirement["required_keywords"]
        found = any(all(kw in blob for kw in keywords) for blob in blobs)
        (passed if found else failed).append(requirement)
    total = len(ground_truth)
    score = (len(passed) / total) if total else 1.0
    return {"score": score, "passed": passed, "failed": failed}


def dedup_gate(extraction_result: dict, threshold: int = 85) -> list[dict]:
    findings: list[dict] = []
    for category in ("entities", "logic_rules", "actions"):
        items = extraction_result.get(category) or []
        names = [str(item.get("name_cn") or "") for item in items if isinstance(item, dict)]
        names = [n for n in names if n]
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                similarity = fuzz.ratio(names[i], names[j])
                if similarity >= threshold:
                    findings.append({
                        "category": category,
                        "name_a": names[i],
                        "name_b": names[j],
                        "similarity": similarity,
                    })
    return findings


def _looks_like_instance_id(name: str) -> bool:
    return bool(_ID_PATTERN.match(name)) or bool(_TRAILING_DIGITS_PATTERN.search(name))


def instance_leakage_gate(extraction_result: dict) -> list[dict]:
    findings: list[dict] = []
    for item in extraction_result.get("entities") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name_cn") or "")
        if name and _looks_like_instance_id(name):
            findings.append({"name": name, "reason": "matches ID/numbered-record pattern"})
    return findings
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/evals/test_document_extraction_validators.py -v`
Expected: 7 PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/requirements.txt backend/evals/document_extraction/validators.py backend/tests/evals/test_document_extraction_validators.py
git commit -m "feat: add document-extraction eval validators (keyword recall, dedup, instance-leakage gates)"
```

---

### Task 3: DeepSeek extraction adapter

**Files:**
- Create: `backend/evals/document_extraction/adapters.py`
- Test: `backend/tests/evals/test_document_extraction_adapters.py`

**Interfaces:**
- Consumes: `app.services.document_service.convert_document(file_path: str) -> ConversionResult` (existing), `app.services.llm_service.extract_ontology(text, prompt_content, model_config, model_name) -> dict` (existing), `app.tasks.extraction.CONCEPT_INSTANCE_DIRECTIVE` (existing, a string constant).
- Produces (used by Task 4): `DeepSeekExtractionAdapter.extract(source_file: str) -> dict` — returns the same normalized dict shape Task 2's validators consume (`entities`/`relations`/`logic_rules`/`actions`/`instances`).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/evals/test_document_extraction_adapters.py`:

```python
"""DeepSeekExtractionAdapter — skipped entirely without a real API key, since
it makes a real network call. Never runs as a side effect of a normal
`pytest -q`."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evals.document_extraction.adapters import DeepSeekExtractionAdapter

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(
    not os.environ.get("DEEPSEEK_API_KEY"),
    reason="DEEPSEEK_API_KEY not set — this test makes a real DeepSeek API call",
)
def test_adapter_extracts_a_shape_valid_result_from_a_real_domain_file():
    adapter = DeepSeekExtractionAdapter()
    source_file = str(REPO_ROOT / "test_data" / "信贷" / "信贷业务战略.md")

    result = adapter.extract(source_file)

    assert isinstance(result, dict)
    for key in ("entities", "relations", "logic_rules", "actions"):
        assert key in result
        assert isinstance(result[key], list)
    assert len(result["entities"]) > 0
    for entity in result["entities"]:
        assert "name_cn" in entity
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/evals/test_document_extraction_adapters.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'evals.document_extraction.adapters'` (this happens regardless of whether `DEEPSEEK_API_KEY` is set, since the import itself fails at collection time).

- [ ] **Step 3: Write the implementation**

Create `backend/evals/document_extraction/adapters.py`:

```python
"""Extraction adapters for the document-extraction eval. The real adapter
calls the app's actual, unmodified extraction code — no database, no
Celery — exactly the same extract_ontology() call
backend/app/tasks/extraction.py::run_extraction makes internally."""
import os

# General-purpose extraction system prompt. Production prompts are stored
# per-ontology in the `prompts` DB table; this eval has no DB dependency, so
# it uses an equivalent literal prompt instead of reading one from the DB.
BASE_PROMPT = """你是一个专业的本体（Ontology）提取助手。你的任务是从企业内部文档
（战略文件、管理制度、诊疗规范等）中提取结构化的本体信息，包括：
- entities：文档中的核心概念/类型实体
- relations：概念之间的关系
- logic_rules：文档中的条件判断规则（如 IF-THEN 规则、阈值规则）
- actions：文档描述的可执行操作或流程步骤

提取时不要遗漏文档中明确给出的规则和阈值，规则的 name_cn 应清楚概括规则内容
（例如"信用评分授信额度规则"），不要使用"规则1""处理逻辑"这类模糊命名。"""


class DeepSeekExtractionAdapter:
    """Calls the real app.services.llm_service.extract_ontology() against a
    real DeepSeek model — no mocking, no fixtures, real network calls."""

    def __init__(self, model_name: str = "deepseek-chat"):
        self._model_name = model_name

    def extract(self, source_file: str) -> dict:
        from app.services.document_service import convert_document
        from app.services.llm_service import extract_ontology
        from app.tasks.extraction import CONCEPT_INSTANCE_DIRECTIVE

        conversion = convert_document(source_file)
        if not conversion.ok:
            raise RuntimeError(f"convert_document failed for {source_file}: {conversion.error}")

        prompt_content = BASE_PROMPT + CONCEPT_INSTANCE_DIRECTIVE
        config_dict = {
            "provider": "deepseek",
            "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
            "api_base": "https://api.deepseek.com",
        }
        return extract_ontology(conversion.content, prompt_content, config_dict, self._model_name)


class FakeExtractionAdapter:
    """Deterministic, no-network adapter for testing the eval runner's own
    wiring/aggregation logic (Task 4) — never used for real quality scoring."""

    def __init__(self, canned_result: dict | None = None):
        self._canned_result = canned_result or {
            "entities": [], "relations": [], "logic_rules": [], "actions": [], "instances": [],
        }

    def extract(self, source_file: str) -> dict:
        return dict(self._canned_result)
```

- [ ] **Step 4: Run test to verify it skips cleanly without a key, or passes with one**

Run: `cd backend && python -m pytest tests/evals/test_document_extraction_adapters.py -v`
Expected (no `DEEPSEEK_API_KEY` in your shell): `1 skipped`.
If you have a real key available and want to verify the adapter end-to-end: `cd backend && DEEPSEEK_API_KEY=<your key> python -m pytest tests/evals/test_document_extraction_adapters.py -v` — expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/evals/document_extraction/adapters.py backend/tests/evals/test_document_extraction_adapters.py
git commit -m "feat: add DeepSeek extraction adapter for document-extraction eval"
```

---

### Task 4: CLI runner

**Files:**
- Create: `backend/evals/document_extraction/run.py`
- Test: `backend/tests/evals/test_document_extraction_run.py`

**Interfaces:**
- Consumes: `keyword_recall`, `dedup_gate`, `instance_leakage_gate` from `evals.document_extraction.validators` (Task 2); `DeepSeekExtractionAdapter`, `FakeExtractionAdapter` from `evals.document_extraction.adapters` (Task 3); manifest/ground-truth JSON files (Task 1).
- Produces: `run_eval(manifest_path: str, ground_truth_dir: str, adapter, repo_root: str) -> dict` — returns `{"cases": [...], "summary": {"total_cases": int, "mean_keyword_recall": float, "cases_with_dedup_findings": int, "cases_with_leakage_findings": int}}`. Each case entry: `{"case_id": str, "domain": str, "keyword_recall": {...}, "dedup_findings": [...], "leakage_findings": [...]}`.
- A `main()` CLI entry point: `python -m evals.document_extraction.run --manifest core-v1 --adapter {deepseek,fake} --output <path>`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/evals/test_document_extraction_run.py`:

```python
"""CLI runner tests using FakeExtractionAdapter — no API key, no network,
proves the runner's wiring/aggregation logic is correct independent of what
a real LLM returns."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evals.document_extraction.adapters import FakeExtractionAdapter
from evals.document_extraction.run import run_eval

EVAL_ROOT = Path(__file__).resolve().parents[2] / "evals" / "document_extraction"
REPO_ROOT = Path(__file__).resolve().parents[3]


def test_run_eval_produces_one_entry_per_manifest_case():
    adapter = FakeExtractionAdapter()
    result = run_eval(
        manifest_path=str(EVAL_ROOT / "manifest" / "core-v1.json"),
        ground_truth_dir=str(EVAL_ROOT / "ground_truth"),
        adapter=adapter,
        repo_root=str(REPO_ROOT),
    )
    assert result["summary"]["total_cases"] == 8
    assert len(result["cases"]) == 8
    case_ids = {c["case_id"] for c in result["cases"]}
    assert case_ids == {
        "credit-01", "supply-chain-01", "education-01", "medical-01",
        "finance-01", "legal-01", "marketing-01", "hr-01",
    }


def test_run_eval_empty_fake_result_fails_all_keyword_requirements():
    # FakeExtractionAdapter returns an empty result by default, so every
    # ground-truth keyword requirement should fail — proving the scoring
    # wiring actually reaches the validators, not just returns a stub score.
    adapter = FakeExtractionAdapter()
    result = run_eval(
        manifest_path=str(EVAL_ROOT / "manifest" / "core-v1.json"),
        ground_truth_dir=str(EVAL_ROOT / "ground_truth"),
        adapter=adapter,
        repo_root=str(REPO_ROOT),
    )
    assert result["summary"]["mean_keyword_recall"] == 0.0
    for case in result["cases"]:
        assert case["keyword_recall"]["score"] == 0.0


def test_run_eval_detects_dedup_finding_via_canned_result():
    canned = {
        "entities": [
            {"name_cn": "供应商", "description": "x"},
            {"name_cn": "供应商方", "description": "y"},
        ],
        "relations": [], "logic_rules": [], "actions": [], "instances": [],
    }
    adapter = FakeExtractionAdapter(canned_result=canned)
    result = run_eval(
        manifest_path=str(EVAL_ROOT / "manifest" / "core-v1.json"),
        ground_truth_dir=str(EVAL_ROOT / "ground_truth"),
        adapter=adapter,
        repo_root=str(REPO_ROOT),
    )
    assert result["summary"]["cases_with_dedup_findings"] == 8
    assert len(result["cases"][0]["dedup_findings"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/evals/test_document_extraction_run.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'evals.document_extraction.run'`.

- [ ] **Step 3: Write the implementation**

Create `backend/evals/document_extraction/run.py`:

```python
"""CLI runner for the document-extraction eval. Mirrors
backend/evals/agent_ontology/run.py's --manifest/--adapter/--output shape."""
import argparse
import json
import os

from evals.document_extraction.validators import dedup_gate, instance_leakage_gate, keyword_recall


def run_eval(manifest_path: str, ground_truth_dir: str, adapter, repo_root: str) -> dict:
    with open(manifest_path, encoding="utf-8") as f:
        cases = json.load(f)

    case_results = []
    for case in cases:
        gt_path = os.path.join(ground_truth_dir, f"{case['ground_truth_id']}.json")
        with open(gt_path, encoding="utf-8") as f:
            ground_truth = json.load(f)

        source_path = os.path.join(repo_root, case["source_file"])
        extraction_result = adapter.extract(source_path)

        case_results.append({
            "case_id": case["case_id"],
            "domain": case["domain"],
            "keyword_recall": keyword_recall(ground_truth, extraction_result),
            "dedup_findings": dedup_gate(extraction_result),
            "leakage_findings": instance_leakage_gate(extraction_result),
        })

    total = len(case_results)
    mean_recall = (
        sum(c["keyword_recall"]["score"] for c in case_results) / total
        if total else 0.0
    )
    summary = {
        "total_cases": total,
        "mean_keyword_recall": mean_recall,
        "cases_with_dedup_findings": sum(1 for c in case_results if c["dedup_findings"]),
        "cases_with_leakage_findings": sum(1 for c in case_results if c["leakage_findings"]),
    }
    return {"cases": case_results, "summary": summary}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the document-extraction quality eval")
    parser.add_argument("--manifest", default="core-v1", help="manifest name (without .json)")
    parser.add_argument("--adapter", default="deepseek", choices=["deepseek", "fake"])
    parser.add_argument("--output", default=None, help="path to write the result JSON")
    args = parser.parse_args()

    eval_root = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(eval_root, "..", "..", ".."))

    if args.adapter == "fake":
        from evals.document_extraction.adapters import FakeExtractionAdapter
        adapter = FakeExtractionAdapter()
    else:
        if not os.environ.get("DEEPSEEK_API_KEY"):
            print("DEEPSEEK_API_KEY not set — skipping document-extraction eval (no-op, not an error)")
            if args.output:
                os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
                with open(args.output, "w", encoding="utf-8") as f:
                    json.dump({"skipped": True, "reason": "DEEPSEEK_API_KEY not set"}, f)
            return
        from evals.document_extraction.adapters import DeepSeekExtractionAdapter
        adapter = DeepSeekExtractionAdapter()

    result = run_eval(
        manifest_path=os.path.join(eval_root, "manifest", f"{args.manifest}.json"),
        ground_truth_dir=os.path.join(eval_root, "ground_truth"),
        adapter=adapter,
        repo_root=repo_root,
    )

    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/evals/test_document_extraction_run.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Manually verify the CLI with the fake adapter**

Run: `cd backend && python -m evals.document_extraction.run --manifest core-v1 --adapter fake --output /tmp/eval_result.json`
Expected: prints a summary JSON with `"total_cases": 8`, `"mean_keyword_recall": 0.0`; `/tmp/eval_result.json` is written.

- [ ] **Step 6: Commit**

```bash
git add backend/evals/document_extraction/run.py backend/tests/evals/test_document_extraction_run.py
git commit -m "feat: add document-extraction eval CLI runner"
```

---

### Task 5: pytest wrapper for the real eval

**Files:**
- Create: `backend/evals/document_extraction/test_core_v1.py`

**Interfaces:**
- Consumes: `run_eval` from `evals.document_extraction.run` (Task 4), `DeepSeekExtractionAdapter` from `evals.document_extraction.adapters` (Task 3).

This is the one file in this plan that makes real DeepSeek API calls when run — it must be impossible for it to do so by accident.

- [ ] **Step 1: Write the test file**

Create `backend/evals/document_extraction/test_core_v1.py`:

```python
"""Pytest wrapper for the real document-extraction eval — every test here
makes a real DeepSeek API call and is skipped entirely without
DEEPSEEK_API_KEY. This file lives under evals/, not tests/, matching
backend/evals/agent_ontology/test_core_v1.py's placement — it is not part
of the default `pytest -q` collection from backend/tests/."""
import os

import pytest

from evals.document_extraction.adapters import DeepSeekExtractionAdapter
from evals.document_extraction.run import run_eval

EVAL_ROOT = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(EVAL_ROOT, "..", "..", ".."))

requires_deepseek_key = pytest.mark.skipif(
    not os.environ.get("DEEPSEEK_API_KEY"),
    reason="DEEPSEEK_API_KEY not set — this test makes real DeepSeek API calls",
)


@requires_deepseek_key
def test_core_v1_all_cases_run_without_error():
    result = run_eval(
        manifest_path=os.path.join(EVAL_ROOT, "manifest", "core-v1.json"),
        ground_truth_dir=os.path.join(EVAL_ROOT, "ground_truth"),
        adapter=DeepSeekExtractionAdapter(),
        repo_root=REPO_ROOT,
    )
    assert result["summary"]["total_cases"] == 8
    for case in result["cases"]:
        assert "keyword_recall" in case
```

- [ ] **Step 2: Confirm it's skipped without a key**

Run: `cd backend && python -m pytest evals/document_extraction/test_core_v1.py -v`
Expected: `1 skipped`.

- [ ] **Step 3: Confirm `pytest -q` from `backend/` does not pick this up as a surprise network call**

Run: `cd backend && python -m pytest -q --collect-only | grep document_extraction`
Expected: since `pytest -q`'s default discovery root is `backend/` and this file lives at `backend/evals/document_extraction/test_core_v1.py`, it MAY be discovered by a bare `pytest -q` (pytest's default discovery walks the whole invocation directory, not just `tests/`). If it appears in the collect-only output, this is expected — the `skipif` marker is what keeps it from running for real, not exclusion from discovery. Confirm by running the full skip check again with `pytest -q` (not just targeting the eval file): `cd backend && python -m pytest -q -k document_extraction` — expected: skipped, not run, when `DEEPSEEK_API_KEY` is unset.

- [ ] **Step 4: Commit**

```bash
git add backend/evals/document_extraction/test_core_v1.py
git commit -m "feat: add pytest wrapper for the real document-extraction eval"
```

---

### Task 6: CI wiring

**Files:**
- Modify: `.github/workflows/agent-mvp.yml`

**Interfaces:**
- Consumes: `evals.document_extraction.run`'s `main()` CLI entry point (Task 4).

- [ ] **Step 1: Add the new CI step**

In `.github/workflows/agent-mvp.yml`, find the existing step:

```yaml
      - name: Agent evaluation corpus
        run: cd backend && python -m evals.agent_ontology.run --manifest core-v1 --adapter fake --seed 141 --output artifacts/evals/result.json
```

Add immediately after it:

```yaml
      - name: Document extraction eval (non-blocking)
        continue-on-error: true
        env:
          DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}
        run: cd backend && python -m evals.document_extraction.run --manifest core-v1 --adapter deepseek --output artifacts/evals/document_extraction_result.json
```

- [ ] **Step 2: Verify the workflow YAML is well-formed**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/agent-mvp.yml'))" ` (from repo root; if `pyyaml` isn't available in your shell, use `cd backend && .venv/bin/python -c "import yaml; yaml.safe_load(open('../.github/workflows/agent-mvp.yml'))"` since it's already a transitive dependency there)
Expected: no output, no exception (valid YAML).

- [ ] **Step 3: Verify local no-key behavior matches what CI will see without the secret configured**

Run: `cd backend && python -m evals.document_extraction.run --manifest core-v1 --adapter deepseek --output /tmp/ci_check.json` (with `DEEPSEEK_API_KEY` unset in your shell)
Expected: prints "DEEPSEEK_API_KEY not set — skipping document-extraction eval (no-op, not an error)", exits 0, writes `/tmp/ci_check.json` with `{"skipped": true, ...}`. This confirms the step won't be `continue-on-error`-masking a real configuration mistake — it degrades cleanly on its own.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/agent-mvp.yml
git commit -m "ci: wire document-extraction eval as a non-blocking CI step"
```

**Note for the human operator, not part of any task's code:** `DEEPSEEK_API_KEY` must be added as a GitHub Actions repository secret (Settings → Secrets and variables → Actions) before this step will actually call DeepSeek in CI. Without it, the step runs, prints the "not set" message, and exits 0 — harmless, but the eval provides zero signal until the secret exists.
