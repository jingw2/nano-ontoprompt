# Test Data Coverage Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand `test_data/` with deterministic edge-case, boundary, semantic, and missing-format fixtures, and add the minimum pytest coverage needed to run them against the real extraction/connector/pipeline code, so gaps that are currently invisible (silent data corruption, unsupported formats, untested code paths) become visible findings.

**Architecture:** Standalone, seeded Python generator scripts under `test_data/generators/` write fixture files into `test_data/edge_cases/<category>/` or directly into the existing per-domain directories. Each fixture is then exercised by a pytest test in `backend/tests/` that calls the real production function (`convert_document`, `SQLConnector`, `_collect_sources`) directly — no new abstraction layer, no mocking of the code under test.

**Tech Stack:** Python 3, `python-docx`, `python-pptx`, `openpyxl`, `xlwt` (new, dev-only), `pypdf`, LibreOffice headless (`soffice`, already installed at `/opt/homebrew/bin/soffice`), pytest, real Postgres via `TEST_DATABASE_URL`.

**Spec:** `docs/superpowers/specs/2026-08-26-test-data-coverage-design.md`

## Global Constraints

- All generator scripts are deterministic: no unseeded randomness. Every literal value in this plan's generators is fixed data, not `random.*` — determinism is guaranteed by construction, not by seeding.
- Generated fixture files are committed to git; no test or CI step regenerates them at run time.
- `.doc`/`.ppt` generation requires LibreOffice (`soffice`) locally, one time, to produce the committed fixtures — already installed on this machine at `/opt/homebrew/bin/soffice`. No downstream test or CI step depends on `soffice` being present.
- Where a fixture proves an existing, real bug (embedded-comma CSV misalignment, non-UTF8 mojibake, unsupported `.xls`/`.xml`/`.doc`/`.ppt`, `SQLConnector.pull_full()`'s pandas-3.0 incompatibility), the test documents current (broken) behavior — via `pytest.mark.xfail(reason=..., strict=True)` when asserting the *desired* behavior, or via a direct assertion of the *actual* behavior with an explanatory comment — never a silent `xfail` with no reason. Fixing these bugs is out of scope (tracked for sub-project E).
- New pytest tests append to the existing `backend/tests/test_document_service.py` (established location for `convert_document` tests) rather than creating a parallel file, except where a new subsystem (`v2/connection`, `v2/pipeline`) has its own existing test directory.
- Backend tests run from the `backend/` directory (`cd backend && python -m pytest -q`), per `.github/workflows/agent-mvp.yml`.

---

### Task 1: Malformed-file fixtures (corrupted zip containers + truncated PDF)

**Files:**
- Create: `test_data/generators/generate_malformed.py`
- Modify: `backend/tests/test_document_service.py`

**Interfaces:**
- Produces: `test_data/edge_cases/malformed/corrupted_docx.docx`, `corrupted_xlsx.xlsx`, `truncated.pdf` (committed fixture files). `corrupted_pptx.pptx` already exists as an inline byte pattern in the existing test — this task does not duplicate it as a fixture file.
- Consumes: `app.services.document_service.convert_document` (existing, `backend/app/services/document_service.py:49`).

- [ ] **Step 1: Write the generator script**

```python
"""Generates malformed-file fixtures: corrupted OOXML zips and a truncated PDF.

Corruption is done by truncating a valid file's last 300 bytes, which removes
the ZIP end-of-central-directory record (docx/xlsx) without leaving the file
empty — this is what makes MarkItDown's ZipConverter report a genuine
"[ERROR] Invalid or corrupted zip file" instead of a generic not-found error.
"""
from pathlib import Path

from docx import Document
import openpyxl
from pypdf import PdfWriter

BASE = Path(__file__).resolve().parent.parent / "edge_cases" / "malformed"
BASE.mkdir(parents=True, exist_ok=True)


def _truncate(src: Path, dst: Path, cut_bytes: int = 300) -> None:
    data = src.read_bytes()
    dst.write_bytes(data[:-cut_bytes])
    src.unlink()


def generate_corrupted_docx() -> None:
    valid = BASE / "_valid_docx_scratch.docx"
    doc = Document()
    doc.add_paragraph("测试内容 for corruption fixture")
    doc.save(valid)
    _truncate(valid, BASE / "corrupted_docx.docx")


def generate_corrupted_xlsx() -> None:
    valid = BASE / "_valid_xlsx_scratch.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["客户ID", "金额"])
    ws.append(["C001", 100])
    wb.save(valid)
    _truncate(valid, BASE / "corrupted_xlsx.xlsx")


def generate_truncated_pdf() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    valid = BASE / "_valid_pdf_scratch.pdf"
    with open(valid, "wb") as f:
        writer.write(f)
    data = valid.read_bytes()
    (BASE / "truncated.pdf").write_bytes(data[: len(data) // 2])
    valid.unlink()


def main() -> None:
    generate_corrupted_docx()
    generate_corrupted_xlsx()
    generate_truncated_pdf()
    print(f"Wrote malformed fixtures to {BASE}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the generator**

Run: `python test_data/generators/generate_malformed.py`
Expected: prints the output path; `test_data/edge_cases/malformed/corrupted_docx.docx`, `corrupted_xlsx.xlsx`, and `truncated.pdf` exist, no `_valid_*_scratch.*` files remain.

- [ ] **Step 3: Verify determinism**

Run: `python test_data/generators/generate_malformed.py` a second time, then `git status test_data/edge_cases/malformed/` (after a first `git add`) or `shasum test_data/edge_cases/malformed/*`
Expected: byte-identical output both runs (no diff).

- [ ] **Step 4: Write the failing tests**

Append to `backend/tests/test_document_service.py`:

```python
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[2] / "test_data" / "edge_cases" / "malformed"


def test_corrupted_docx_fixture_fails_gracefully():
    # _convert_docx (document_service.py:117) raises "Package not found" for a
    # truncated zip; convert_document surfaces that as a clear error instead
    # of silently returning empty/garbage content.
    result = convert_document(str(FIXTURES / "corrupted_docx.docx"))
    assert not result.ok
    assert "DOCX" in (result.error or "") or "Package" in (result.error or "")


def test_corrupted_xlsx_fixture_is_not_usable():
    # Exercises the CONVERSION_ERROR_MARKERS path (document_service.py:4-13):
    # MarkItDown returns "[ERROR] Invalid or corrupted zip file: ..." as
    # content instead of raising; is_usable_converted_text must reject it.
    result = convert_document(str(FIXTURES / "corrupted_xlsx.xlsx"))
    assert not result.ok


def test_truncated_pdf_fixture_fails_gracefully():
    result = convert_document(str(FIXTURES / "truncated.pdf"))
    assert not result.ok
```

- [ ] **Step 5: Run tests to verify they fail (fixtures don't exist yet if Steps 1-3 were skipped)**

Run: `cd backend && python -m pytest tests/test_document_service.py -k "corrupted_docx_fixture or corrupted_xlsx_fixture or truncated_pdf_fixture" -v`
Expected: if Steps 1-3 already ran, these PASS immediately (the fixtures already demonstrate correct behavior) — this task has no code to fix, only fixtures and tests to add. Confirm they pass, not fail.

- [ ] **Step 6: Commit**

```bash
git add -f test_data/generators/generate_malformed.py test_data/edge_cases/malformed/ backend/tests/test_document_service.py
git commit -m "test: add malformed-file fixtures and coverage for corrupted docx/xlsx/pdf"
```

---

### Task 2: Non-UTF8 and CSV structural fixtures

**Files:**
- Create: `test_data/generators/generate_csv_structural.py`
- Modify: `backend/tests/test_document_service.py`

**Interfaces:**
- Produces: `test_data/edge_cases/malformed/non_utf8.csv`, `non_utf8.txt`; `test_data/edge_cases/csv_structural/embedded_comma_quoted.csv`, `embedded_quote_escaped.csv`, `empty_cells.csv`, `header_only.csv`, `duplicate_columns.csv`.
- Consumes: `app.services.document_service.convert_document` (existing).

- [ ] **Step 1: Write the generator script**

```python
"""Generates non-UTF8 and CSV-structural-edge-case fixtures."""
from pathlib import Path

TEST_DATA = Path(__file__).resolve().parent.parent
MALFORMED = TEST_DATA / "edge_cases" / "malformed"
CSV_STRUCTURAL = TEST_DATA / "edge_cases" / "csv_structural"
MALFORMED.mkdir(parents=True, exist_ok=True)
CSV_STRUCTURAL.mkdir(parents=True, exist_ok=True)


def generate_non_utf8() -> None:
    # GBK is a realistic real-world encoding for Chinese business documents,
    # not an arbitrary corrupt byte sequence.
    text = "姓名,城市\n张三,北京\n李四,上海\n"
    (MALFORMED / "non_utf8.csv").write_text(text, encoding="gbk")
    (MALFORMED / "non_utf8.txt").write_text("客户备注：张三，来自北京", encoding="gbk")


def generate_csv_structural() -> None:
    # Comma inside a properly-quoted cell: valid CSV, breaks the naive
    # comma-split converter in document_service.py:_read_csv_as_markdown.
    (CSV_STRUCTURAL / "embedded_comma_quoted.csv").write_text(
        '客户ID,备注\n1,"北京, 上海"\n2,正常\n', encoding="utf-8-sig", newline=""
    )
    # Escaped internal quote, no embedded comma — does not exercise the bug,
    # kept as a contrast/regression-safety fixture.
    (CSV_STRUCTURAL / "embedded_quote_escaped.csv").write_text(
        '客户ID,备注\n1,"客户说""你好"""\n2,正常\n', encoding="utf-8-sig", newline=""
    )
    (CSV_STRUCTURAL / "empty_cells.csv").write_text(
        "客户ID,姓名,城市\n1,,北京\n2,李四,\n", encoding="utf-8-sig", newline=""
    )
    (CSV_STRUCTURAL / "header_only.csv").write_text(
        "客户ID,姓名,城市\n", encoding="utf-8-sig", newline=""
    )
    (CSV_STRUCTURAL / "duplicate_columns.csv").write_text(
        "客户ID,金额,金额\n1,100,200\n", encoding="utf-8-sig", newline=""
    )


def main() -> None:
    generate_non_utf8()
    generate_csv_structural()
    print(f"Wrote non-UTF8 fixtures to {MALFORMED} and CSV-structural fixtures to {CSV_STRUCTURAL}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the generator**

Run: `python test_data/generators/generate_csv_structural.py`
Expected: 6 files created across the two directories listed above.

- [ ] **Step 3: Write the tests, including two documented-bug xfails**

Append to `backend/tests/test_document_service.py`:

```python
CSV_STRUCTURAL_FIXTURES = Path(__file__).resolve().parents[2] / "test_data" / "edge_cases" / "csv_structural"


def test_header_only_csv_succeeds_with_zero_data_rows():
    result = convert_document(str(CSV_STRUCTURAL_FIXTURES / "header_only.csv"))
    assert result.ok
    lines = (result.content or "").splitlines()
    assert len(lines) == 2  # header row + separator row, no data rows


def test_empty_cells_csv_preserves_column_count():
    result = convert_document(str(CSV_STRUCTURAL_FIXTURES / "empty_cells.csv"))
    assert result.ok
    header_cols = result.content.splitlines()[0].count("|") - 1
    for line in result.content.splitlines()[2:]:
        assert line.count("|") - 1 == header_cols


def test_quote_escaped_without_comma_is_not_corrupted():
    # No embedded comma inside the escaped-quote field, so the naive
    # converter happens to produce the right column count.
    result = convert_document(str(CSV_STRUCTURAL_FIXTURES / "embedded_quote_escaped.csv"))
    assert result.ok
    assert result.content.splitlines()[0].count("|") == result.content.splitlines()[2].count("|")


@pytest.mark.xfail(
    reason="Known bug: document_service.py:_read_csv_as_markdown naive-splits "
    "on every comma, including ones inside quoted fields, corrupting column "
    "alignment for any CSV with a comma-containing quoted value. Fix tracked "
    "as sub-project E; this test should start passing (and the xfail marker "
    "should be removed) once _read_csv_as_markdown uses the csv module.",
    strict=True,
)
def test_embedded_comma_in_quotes_does_not_corrupt_columns():
    result = convert_document(str(CSV_STRUCTURAL_FIXTURES / "embedded_comma_quoted.csv"))
    assert result.ok
    header_cols = result.content.splitlines()[0].count("|") - 1
    data_line = result.content.splitlines()[2]  # "1,"北京, 上海"" row
    assert data_line.count("|") - 1 == header_cols


NON_UTF8_FIXTURES = Path(__file__).resolve().parents[2] / "test_data" / "edge_cases" / "malformed"


@pytest.mark.xfail(
    reason="Known bug: document_service.py:_read_plain_text and "
    "_read_csv_as_markdown always decode with encoding='utf-8', "
    "errors='replace', so a real-world GBK-encoded Chinese file silently "
    "decodes to mojibake (U+FFFD replacement characters) with ok=True and "
    "no error surfaced. Fix tracked as sub-project E (e.g. encoding "
    "detection via chardet, or explicit GBK fallback); this test should "
    "start passing once that lands.",
    strict=True,
)
def test_gbk_encoded_csv_decodes_correctly():
    result = convert_document(str(NON_UTF8_FIXTURES / "non_utf8.csv"))
    assert result.ok
    assert "张三" in result.content
    assert "�" not in result.content


@pytest.mark.xfail(
    reason="Same root cause as test_gbk_encoded_csv_decodes_correctly, via "
    "_read_plain_text instead of _read_csv_as_markdown.",
    strict=True,
)
def test_gbk_encoded_txt_decodes_correctly():
    result = convert_document(str(NON_UTF8_FIXTURES / "non_utf8.txt"))
    assert result.ok
    assert "张三" in result.content
    assert "�" not in result.content
```

Add `import pytest` at the top of `backend/tests/test_document_service.py` if not already present (it is not, per the current 22-line file).

- [ ] **Step 4: Run tests and verify the expected pass/xfail split**

Run: `cd backend && python -m pytest tests/test_document_service.py -v`
Expected: `test_header_only_csv_succeeds_with_zero_data_rows`, `test_empty_cells_csv_preserves_column_count`, `test_quote_escaped_without_comma_is_not_corrupted` PASS. `test_embedded_comma_in_quotes_does_not_corrupt_columns`, `test_gbk_encoded_csv_decodes_correctly`, `test_gbk_encoded_txt_decodes_correctly` show as `XFAIL` (not `FAILED`).

- [ ] **Step 5: Commit**

```bash
git add -f test_data/generators/generate_csv_structural.py test_data/edge_cases/malformed/non_utf8.csv test_data/edge_cases/malformed/non_utf8.txt test_data/edge_cases/csv_structural/ backend/tests/test_document_service.py
git commit -m "test: add non-UTF8 and CSV-structural fixtures, document 2 silent-corruption bugs"
```

---

### Task 3: Boundary fixtures (empty, single-row, large, extreme-length)

**Files:**
- Create: `test_data/generators/generate_boundary.py`
- Modify: `backend/tests/test_document_service.py`

**Interfaces:**
- Produces: `test_data/edge_cases/boundary/empty_file.csv`, `single_row.csv`, `large_10k_rows.csv`, `extreme_length_field.csv`.
- Consumes: `app.services.document_service.convert_document` (existing).

- [ ] **Step 1: Write the generator script**

```python
"""Generates boundary-condition CSV fixtures: a zero-byte file, a single
data row, 10k rows, and an extreme-length field value."""
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "edge_cases" / "boundary"
BASE.mkdir(parents=True, exist_ok=True)


def generate_empty_file() -> None:
    (BASE / "empty_file.csv").write_bytes(b"")


def generate_single_row() -> None:
    (BASE / "single_row.csv").write_text(
        "客户ID,姓名,城市\n1,张三,北京\n", encoding="utf-8-sig", newline=""
    )


def generate_large_10k_rows() -> None:
    lines = ["客户ID,备注"] + [f"{i},正常" for i in range(10000)]
    (BASE / "large_10k_rows.csv").write_text("\n".join(lines), encoding="utf-8-sig")


def generate_extreme_length_field() -> None:
    long_field = "x" * 20000
    (BASE / "extreme_length_field.csv").write_text(
        f"客户ID,备注\n1,{long_field}\n", encoding="utf-8-sig", newline=""
    )


def main() -> None:
    generate_empty_file()
    generate_single_row()
    generate_large_10k_rows()
    generate_extreme_length_field()
    print(f"Wrote boundary fixtures to {BASE}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the generator**

Run: `python test_data/generators/generate_boundary.py`
Expected: 4 files created in `test_data/edge_cases/boundary/`.

- [ ] **Step 3: Write the tests**

Append to `backend/tests/test_document_service.py`:

```python
BOUNDARY_FIXTURES = Path(__file__).resolve().parents[2] / "test_data" / "edge_cases" / "boundary"


def test_empty_file_csv_fails_with_clear_error():
    result = convert_document(str(BOUNDARY_FIXTURES / "empty_file.csv"))
    assert not result.ok
    assert result.error == "CSV 文件为空"


def test_single_row_csv_converts():
    result = convert_document(str(BOUNDARY_FIXTURES / "single_row.csv"))
    assert result.ok
    assert len(result.content.splitlines()) == 3  # header + separator + 1 data row


def test_large_10k_row_csv_converts_without_truncation():
    result = convert_document(str(BOUNDARY_FIXTURES / "large_10k_rows.csv"))
    assert result.ok
    assert len(result.content.splitlines()) == 10002  # header + separator + 10000 rows


def test_extreme_length_field_is_not_truncated():
    result = convert_document(str(BOUNDARY_FIXTURES / "extreme_length_field.csv"))
    assert result.ok
    assert "x" * 20000 in result.content
```

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/test_document_service.py -k "empty_file_csv or single_row_csv or large_10k or extreme_length" -v`
Expected: all 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add -f test_data/generators/generate_boundary.py test_data/edge_cases/boundary/ backend/tests/test_document_service.py
git commit -m "test: add boundary-condition CSV fixtures (single-row, 10k-row, extreme-length-field)"
```

---

### Task 4: Semantic fixtures (missing fields, conflicting duplicates, out-of-range values)

**Files:**
- Create: `test_data/generators/generate_semantic.py`
- Modify: `backend/tests/test_document_service.py`

**Interfaces:**
- Produces: `test_data/edge_cases/semantic/missing_required_fields.csv`, `conflicting_duplicate_entities.csv`, `out_of_range_values.csv`.
- Consumes: `app.services.document_service.convert_document` (existing).

These fixtures test the LLM extraction layer's judgment, not the file parser (per spec) — sub-project D defines correctness scoring for them. This task only proves the files are well-formed CSV that `convert_document` can read; it does not assert anything about extracted entities.

- [ ] **Step 1: Write the generator script**

```python
"""Generates semantic-edge-case CSV fixtures: missing required fields,
conflicting duplicate entities, and out-of-range values. These exercise the
LLM extraction layer's judgment, not the file parser — see sub-project D for
correctness scoring."""
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "edge_cases" / "semantic"
BASE.mkdir(parents=True, exist_ok=True)


def generate_missing_required_fields() -> None:
    (BASE / "missing_required_fields.csv").write_text(
        "客户ID,姓名,信用等级\n"
        "C001,张三,A\n"
        ",李四,B\n"  # missing 客户ID
        "C003,,C\n",  # missing 姓名
        encoding="utf-8-sig", newline="",
    )


def generate_conflicting_duplicate_entities() -> None:
    (BASE / "conflicting_duplicate_entities.csv").write_text(
        "客户ID,姓名,信用等级\n"
        "C001,张三,A\n"
        "C001,张三,C\n",  # same 客户ID, conflicting 信用等级
        encoding="utf-8-sig", newline="",
    )


def generate_out_of_range_values() -> None:
    (BASE / "out_of_range_values.csv").write_text(
        "客户ID,放款金额(元),放款日期\n"
        "C001,-5000,2026-03-01\n"  # negative amount
        "C002,10000,2099-12-31\n",  # far-future date
        encoding="utf-8-sig", newline="",
    )


def main() -> None:
    generate_missing_required_fields()
    generate_conflicting_duplicate_entities()
    generate_out_of_range_values()
    print(f"Wrote semantic fixtures to {BASE}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the generator**

Run: `python test_data/generators/generate_semantic.py`
Expected: 3 files created in `test_data/edge_cases/semantic/`.

- [ ] **Step 3: Write the tests**

Append to `backend/tests/test_document_service.py`:

```python
SEMANTIC_FIXTURES = Path(__file__).resolve().parents[2] / "test_data" / "edge_cases" / "semantic"


@pytest.mark.parametrize("filename", [
    "missing_required_fields.csv",
    "conflicting_duplicate_entities.csv",
    "out_of_range_values.csv",
])
def test_semantic_fixture_is_well_formed_csv(filename):
    result = convert_document(str(SEMANTIC_FIXTURES / filename))
    assert result.ok
    assert result.content.startswith("|")
```

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/test_document_service.py -k semantic_fixture -v`
Expected: 3 PASS (parametrized).

- [ ] **Step 5: Commit**

```bash
git add -f test_data/generators/generate_semantic.py test_data/edge_cases/semantic/ backend/tests/test_document_service.py
git commit -m "test: add semantic-edge-case fixtures (missing fields, conflicting duplicates, out-of-range values)"
```

---

### Task 5: Missing-format fixtures — `.xls` and `.xml`, per domain

**Files:**
- Create: `test_data/generators/_domain_schemas.py`
- Create: `test_data/generators/generate_missing_formats.py`
- Modify: `backend/tests/test_document_service.py`

**Interfaces:**
- Produces: `DOMAIN_SCHEMAS: dict[str, tuple[list[str], list[list]]]` in `test_data/generators/_domain_schemas.py`, keyed by domain directory name, mapping to `(headers, rows)`. This is consumed by Task 6.
- Produces: `test_data/<domain>/<domain>_sample.xls` and `test_data/<domain>/<domain>_sample.xml` for all 8 domains (信贷/供应链/教育/医疗/财务/法律/营销/HR).
- Consumes: `app.services.document_service.convert_document` (existing).

- [ ] **Step 1: Write the shared domain-schema table**

```python
"""Shared per-domain (headers, rows) tables used by generate_missing_formats.py
and generate_legacy_office.py, so both scripts produce content in the same
shape without duplicating the data table."""

DOMAIN_SCHEMAS: dict[str, tuple[list[str], list[list]]] = {
    "信贷": (
        ["客户ID", "产品类型", "放款金额", "放款日期"],
        [
            ["CUS9001", "消费贷(循环额度)", 15000, "2026-03-01"],
            ["CUS9002", "经营贷", 80000, "2026-03-05"],
            ["CUS9003", "抵押贷", 320000, "2026-03-10"],
        ],
    ),
    "供应链": (
        ["供应商ID", "物料名称", "采购数量", "到货日期"],
        [
            ["SUP001", "钢材", 500, "2026-03-01"],
            ["SUP002", "塑料颗粒", 1200, "2026-03-04"],
            ["SUP003", "电子元件", 3000, "2026-03-08"],
        ],
    ),
    "教育": (
        ["学生ID", "课程名称", "成绩", "考试日期"],
        [
            ["STU001", "高等数学", 88, "2026-01-10"],
            ["STU002", "大学英语", 76, "2026-01-11"],
            ["STU003", "数据结构", 92, "2026-01-12"],
        ],
    ),
    "医疗": (
        ["患者ID", "诊断结果", "就诊日期", "主治医生"],
        [
            ["PAT001", "高血压", "2026-02-01", "王医生"],
            ["PAT002", "2型糖尿病", "2026-02-03", "李医生"],
            ["PAT003", "上呼吸道感染", "2026-02-05", "张医生"],
        ],
    ),
    "财务": (
        ["凭证编号", "科目名称", "金额", "记账日期"],
        [
            ["V20260301", "管理费用", 12000, "2026-03-01"],
            ["V20260302", "主营业务收入", 85000, "2026-03-02"],
            ["V20260303", "应收账款", 43000, "2026-03-03"],
        ],
    ),
    "法律": (
        ["合同编号", "合同名称", "签订日期", "违约金"],
        [
            ["CT2026001", "采购框架协议", "2026-01-05", 50000],
            ["CT2026002", "软件许可协议", "2026-01-15", 20000],
            ["CT2026003", "办公租赁合同", "2026-02-01", 100000],
        ],
    ),
    "营销": (
        ["线索ID", "渠道来源", "转化状态", "跟进日期"],
        [
            ["LD1001", "线上广告", "已转化", "2026-03-01"],
            ["LD1002", "合作导流", "跟进中", "2026-03-02"],
            ["LD1003", "自然流量", "未转化", "2026-03-03"],
        ],
    ),
    "HR": (
        ["员工ID", "部门", "职位", "入职日期"],
        [
            ["EMP101", "技术部", "后端工程师", "2025-09-01"],
            ["EMP102", "市场部", "市场专员", "2025-10-15"],
            ["EMP103", "人事部", "招聘主管", "2025-11-01"],
        ],
    ),
}
```

- [ ] **Step 2: Write the `.xls`/`.xml` generator**

```python
"""Generates .xls and .xml missing-format fixtures for every test_data domain,
using the shared DOMAIN_SCHEMAS table. .xls requires the dev-only `xlwt`
package (`pip install xlwt`) to regenerate; the committed output has no
runtime dependency on it."""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import xlwt

from _domain_schemas import DOMAIN_SCHEMAS

TEST_DATA = Path(__file__).resolve().parent.parent


def _xml_tag(header: str) -> str:
    # XML element names may not contain parentheses or most punctuation;
    # strip anything that isn't a word character or CJK character.
    return re.sub(r"[^\w一-鿿]", "", header) or "字段"


def write_xls(domain: str, headers: list[str], rows: list[list]) -> None:
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Sheet1")
    for col, header in enumerate(headers):
        ws.write(0, col, header)
    for row_idx, row in enumerate(rows, start=1):
        for col, value in enumerate(row):
            ws.write(row_idx, col, value)
    wb.save(str(TEST_DATA / domain / f"{domain}_sample.xls"))


def write_xml(domain: str, headers: list[str], rows: list[list]) -> None:
    tags = [_xml_tag(h) for h in headers]
    root = ET.Element("记录列表")
    for row in rows:
        record = ET.SubElement(root, "记录")
        for tag, value in zip(tags, row):
            ET.SubElement(record, tag).text = str(value)
    ET.ElementTree(root).write(
        str(TEST_DATA / domain / f"{domain}_sample.xml"),
        encoding="utf-8", xml_declaration=True,
    )


def main() -> None:
    for domain, (headers, rows) in DOMAIN_SCHEMAS.items():
        write_xls(domain, headers, rows)
        write_xml(domain, headers, rows)
    print(f"Wrote .xls/.xml fixtures for {len(DOMAIN_SCHEMAS)} domains")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the generator**

Run: `cd test_data/generators && python -m pip install xlwt && python generate_missing_formats.py` (run from within `test_data/generators/` so the `from _domain_schemas import DOMAIN_SCHEMAS` local import resolves; alternatively run `python -m test_data.generators.generate_missing_formats` from the repo root if that's preferred, but there is no `__init__.py` in this directory today, so run it from inside the directory).
Expected: prints "Wrote .xls/.xml fixtures for 8 domains"; each of the 8 domain directories now contains `<domain>_sample.xls` and `<domain>_sample.xml`.

- [ ] **Step 4: Write the tests documenting that both formats are currently unsupported**

Append to `backend/tests/test_document_service.py`:

```python
DOMAINS = ["信贷", "供应链", "教育", "医疗", "财务", "法律", "营销", "HR"]
TEST_DATA_ROOT = Path(__file__).resolve().parents[2] / "test_data"


@pytest.mark.parametrize("domain", DOMAINS)
def test_xls_sample_fixture_exists(domain):
    assert (TEST_DATA_ROOT / domain / f"{domain}_sample.xls").exists()


@pytest.mark.parametrize("domain", DOMAINS)
def test_xml_sample_fixture_exists(domain):
    assert (TEST_DATA_ROOT / domain / f"{domain}_sample.xml").exists()


def test_xls_is_currently_unsupported_by_convert_document():
    # Known gap: .xls is in app.config.allowed_upload_extensions, but
    # MarkItDown has no .xls converter and convert_document has no special
    # case for it, so every .xls upload fails conversion outright. Tracked
    # as a finding for sub-project E.
    result = convert_document(str(TEST_DATA_ROOT / "信贷" / "信贷_sample.xls"))
    assert not result.ok


def test_xml_is_currently_unsupported_by_convert_document():
    # Same gap as .xls: .xml is an allowed upload extension with no working
    # conversion path today.
    result = convert_document(str(TEST_DATA_ROOT / "信贷" / "信贷_sample.xml"))
    assert not result.ok
```

- [ ] **Step 5: Run tests**

Run: `cd backend && python -m pytest tests/test_document_service.py -k "xls_sample or xml_sample or xls_is_currently or xml_is_currently" -v`
Expected: all PASS (16 existence checks + 2 unsupported-format checks = 18 tests).

- [ ] **Step 6: Commit**

```bash
git add -f test_data/generators/_domain_schemas.py test_data/generators/generate_missing_formats.py test_data/*/*_sample.xls test_data/*/*_sample.xml backend/tests/test_document_service.py
git commit -m "test: add per-domain .xls/.xml fixtures, document both as currently unsupported"
```

---

### Task 6: Missing-format fixtures — legacy `.doc` and `.ppt`, per domain

**Files:**
- Create: `test_data/generators/generate_legacy_office.py`
- Modify: `backend/tests/test_document_service.py`

**Interfaces:**
- Consumes: `DOMAIN_SCHEMAS` from `test_data/generators/_domain_schemas.py` (produced by Task 5 — this task must run after Task 5).
- Produces: `test_data/<domain>/<domain>_sample.doc` and `test_data/<domain>/<domain>_sample.ppt` for all 8 domains.

- [ ] **Step 1: Write the generator script**

```python
"""Generates legacy .doc and .ppt missing-format fixtures for every test_data
domain. python-docx/python-pptx can only write modern OOXML (.docx/.pptx);
producing a genuine legacy binary requires converting through headless
LibreOffice, so this script builds a .docx/.pptx first, then shells out to
`soffice --convert-to doc` / `--convert-to ppt`. This conversion step is a
one-time local dependency for regenerating fixtures — the committed .doc/.ppt
output has no runtime dependency on LibreOffice."""
import shutil
import subprocess
from pathlib import Path

from docx import Document
from pptx import Presentation

from _domain_schemas import DOMAIN_SCHEMAS

TEST_DATA = Path(__file__).resolve().parent.parent
SOFFICE = shutil.which("soffice")


def _table_text(headers: list[str], rows: list[list]) -> list[str]:
    lines = [" | ".join(headers)]
    for row in rows:
        lines.append(" | ".join(str(v) for v in row))
    return lines


def write_docx_then_doc(domain: str, headers: list[str], rows: list[list]) -> None:
    domain_dir = TEST_DATA / domain
    scratch = domain_dir / f"_{domain}_scratch.docx"
    doc = Document()
    doc.add_heading(f"{domain} 示例数据", level=1)
    for line in _table_text(headers, rows):
        doc.add_paragraph(line)
    doc.save(scratch)
    subprocess.run(
        [SOFFICE, "--headless", "--convert-to", "doc", "--outdir", str(domain_dir), str(scratch)],
        check=True, capture_output=True,
    )
    scratch.unlink()
    (domain_dir / f"_{domain}_scratch.doc").rename(domain_dir / f"{domain}_sample.doc")


def write_pptx_then_ppt(domain: str, headers: list[str], rows: list[list]) -> None:
    domain_dir = TEST_DATA / domain
    scratch = domain_dir / f"_{domain}_scratch.pptx"
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = f"{domain} 示例数据"
    body = slide.placeholders[1].text_frame
    body.text = _table_text(headers, rows)[0]
    for line in _table_text(headers, rows)[1:]:
        body.add_paragraph().text = line
    prs.save(scratch)
    subprocess.run(
        [SOFFICE, "--headless", "--convert-to", "ppt", "--outdir", str(domain_dir), str(scratch)],
        check=True, capture_output=True,
    )
    scratch.unlink()
    (domain_dir / f"_{domain}_scratch.ppt").rename(domain_dir / f"{domain}_sample.ppt")


def main() -> None:
    if not SOFFICE:
        raise SystemExit(
            "soffice not found on PATH. Install LibreOffice "
            "(e.g. `brew install --cask libreoffice`) to regenerate .doc/.ppt fixtures."
        )
    for domain, (headers, rows) in DOMAIN_SCHEMAS.items():
        write_docx_then_doc(domain, headers, rows)
        write_pptx_then_ppt(domain, headers, rows)
    print(f"Wrote .doc/.ppt fixtures for {len(DOMAIN_SCHEMAS)} domains")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the generator**

Run: `cd test_data/generators && python generate_legacy_office.py`
Expected: prints "Wrote .doc/.ppt fixtures for 8 domains"; each domain directory contains `<domain>_sample.doc` and `<domain>_sample.ppt`; no `_<domain>_scratch.*` files remain.

- [ ] **Step 3: Write the tests documenting both formats are currently unsupported**

Append to `backend/tests/test_document_service.py`:

```python
@pytest.mark.parametrize("domain", DOMAINS)
def test_doc_sample_fixture_exists(domain):
    assert (TEST_DATA_ROOT / domain / f"{domain}_sample.doc").exists()


@pytest.mark.parametrize("domain", DOMAINS)
def test_ppt_sample_fixture_exists(domain):
    assert (TEST_DATA_ROOT / domain / f"{domain}_sample.ppt").exists()


def test_doc_is_currently_unsupported_by_convert_document():
    # Known gap, same shape as .xls/.xml: legacy .doc is an allowed upload
    # extension with no working conversion path today (MarkItDown lists it
    # under unsupported formats). Tracked for sub-project E.
    result = convert_document(str(TEST_DATA_ROOT / "信贷" / "信贷_sample.doc"))
    assert not result.ok


def test_ppt_is_currently_unsupported_by_convert_document():
    result = convert_document(str(TEST_DATA_ROOT / "信贷" / "信贷_sample.ppt"))
    assert not result.ok
```

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/test_document_service.py -k "doc_sample or ppt_sample or doc_is_currently or ppt_is_currently" -v`
Expected: all PASS (16 existence checks + 2 unsupported-format checks = 18 tests).

- [ ] **Step 5: Commit**

```bash
git add -f test_data/generators/generate_legacy_office.py test_data/*/*_sample.doc test_data/*/*_sample.ppt backend/tests/test_document_service.py
git commit -m "test: add per-domain legacy .doc/.ppt fixtures via LibreOffice conversion, document as unsupported"
```

---

### Task 7: Multi-source fixtures and pipeline ingestion test

**Files:**
- Create: `test_data/generators/generate_multi_source.py`
- Create: `backend/tests/v2/pipeline/test_multi_source_ingestion.py`

**Interfaces:**
- Produces: `test_data/edge_cases/multi_source/customers_v1.csv`, `customers_v2_conflicting.csv`, `orders_schema_a.csv`, `orders_schema_b_mismatched.csv`.
- Consumes: `app.tasks.v2.pipeline_run._collect_sources(db, pl) -> list[dict]` (existing, `backend/app/tasks/v2/pipeline_run.py:202`), `app.models.v2.dataset.Dataset` (existing, `backend/app/models/v2/dataset.py:7`).

- [ ] **Step 1: Write the generator script**

```python
"""Generates multi-source pipeline fixtures: two customer files with an
overlapping ID and a conflicting attribute, and two order files with
different column names for the same conceptual entity type."""
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "edge_cases" / "multi_source"
BASE.mkdir(parents=True, exist_ok=True)


def generate_customers() -> None:
    (BASE / "customers_v1.csv").write_text(
        "客户ID,姓名,信用等级\nC001,张三,A\nC002,李四,B\n",
        encoding="utf-8-sig", newline="",
    )
    (BASE / "customers_v2_conflicting.csv").write_text(
        # C001 repeats with a conflicting 信用等级 (A vs B) — same ID, two
        # sources disagreeing on an attribute.
        "客户ID,姓名,信用等级\nC001,张三,B\nC003,王五,A\n",
        encoding="utf-8-sig", newline="",
    )


def generate_orders() -> None:
    (BASE / "orders_schema_a.csv").write_text(
        "订单编号,客户ID,金额\nO001,C001,500\n",
        encoding="utf-8-sig", newline="",
    )
    (BASE / "orders_schema_b_mismatched.csv").write_text(
        # Same conceptual entity (an order) with English column names instead
        # of the Chinese schema used by orders_schema_a.csv.
        "OrderNo,CustomerID,Amount\nO002,C002,800\n",
        encoding="utf-8-sig", newline="",
    )


def main() -> None:
    generate_customers()
    generate_orders()
    print(f"Wrote multi-source fixtures to {BASE}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the generator**

Run: `python test_data/generators/generate_multi_source.py`
Expected: 4 files created in `test_data/edge_cases/multi_source/`.

- [ ] **Step 3: Write the failing test**

Create `backend/tests/v2/pipeline/test_multi_source_ingestion.py`:

```python
"""Multi-source pipeline ingestion: proves _collect_sources() gathers every
connector-node file as a separate source when a Pipeline definition lists
more than one, and that multi_source is computed correctly downstream. Does
not assert anything about how conflicting attributes across sources get
resolved during extraction — that is sub-project D's scoring concern."""
from types import SimpleNamespace

import pytest

from app.tasks.v2.pipeline_run import _collect_sources


@pytest.fixture
def two_datasets(db):
    from app.models.v2.dataset import Dataset

    ds_a = Dataset(id="ds-customers-a", name="customers_v1", kind="structured")
    ds_b = Dataset(id="ds-customers-b", name="customers_v2_conflicting", kind="structured")
    db.add(ds_a)
    db.add(ds_b)
    db.commit()
    return ds_a, ds_b


def test_collect_sources_returns_both_connector_node_files(db, two_datasets):
    ds_a, ds_b = two_datasets
    pl = SimpleNamespace(
        definition={
            "nodes": [
                {
                    "type": "connector",
                    "config": {
                        "files": [
                            {"dataset_id": ds_a.id, "name": "customers_v1.csv"},
                            {"dataset_id": ds_b.id, "name": "customers_v2_conflicting.csv"},
                        ]
                    },
                }
            ]
        },
        source_dataset_id=None,
        route=None,
    )

    sources = _collect_sources(db, pl)

    assert len(sources) == 2
    collected_ids = {s["dataset_id"] for s in sources}
    assert collected_ids == {ds_a.id, ds_b.id}
    assert {s["filename"] for s in sources} == {"customers_v1.csv", "customers_v2_conflicting.csv"}


def test_multi_source_flag_is_true_for_two_sources(db, two_datasets):
    ds_a, ds_b = two_datasets
    pl = SimpleNamespace(
        definition={
            "nodes": [{
                "type": "connector",
                "config": {"files": [
                    {"dataset_id": ds_a.id, "name": "a.csv"},
                    {"dataset_id": ds_b.id, "name": "b.csv"},
                ]},
            }]
        },
        source_dataset_id=None,
        route=None,
    )

    sources = _collect_sources(db, pl)
    multi_source = len(sources) > 1

    assert multi_source is True


def test_single_source_is_not_multi_source(db, two_datasets):
    ds_a, _ = two_datasets
    pl = SimpleNamespace(
        definition={
            "nodes": [{
                "type": "connector",
                "config": {"files": [{"dataset_id": ds_a.id, "name": "a.csv"}]},
            }]
        },
        source_dataset_id=None,
        route=None,
    )

    sources = _collect_sources(db, pl)
    multi_source = len(sources) > 1

    assert multi_source is False
    assert len(sources) == 1
```

The `db` fixture is the existing SQLite-backed fixture from `backend/tests/conftest.py:139-144` — `v2_datasets` has no PostgreSQL-only column types, so it is created automatically by that conftest's `_create_sqlite_compatible_tables`.

- [ ] **Step 4: Run the test to verify it fails first (before confirming `db` fixture coverage)**

Run: `cd backend && python -m pytest tests/v2/pipeline/test_multi_source_ingestion.py -v`
Expected: if `v2_datasets` is not yet part of the SQLite-compatible table set for some reason, this fails with a clear SQLAlchemy/table-missing error — investigate and report as `NEEDS_CONTEXT` rather than working around it, since the conftest is shared infrastructure. If `v2_datasets` is created correctly (expected, since it uses only `String`/`JSON`/`DateTime` columns), all 3 tests should already PASS on first run, since `_collect_sources` is existing, working code — this task adds coverage, not a fix.

- [ ] **Step 5: Confirm all 3 tests pass**

Run: `cd backend && python -m pytest tests/v2/pipeline/test_multi_source_ingestion.py -v`
Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add -f test_data/generators/generate_multi_source.py test_data/edge_cases/multi_source/ backend/tests/v2/pipeline/test_multi_source_ingestion.py
git commit -m "test: add multi-source fixtures and _collect_sources coverage for pipeline_run.py"
```

---

### Task 8: SQL connector real-Postgres integration tests

**Files:**
- Create: `backend/tests/v2/connection/test_sql_connector_integration.py`

**Interfaces:**
- Consumes: `app.services.connection.sql_connector.SQLConnector` (existing, `backend/app/services/connection/sql_connector.py:22`) — constructor `SQLConnector(config: dict)`, methods `test_connection() -> bool`, `list_resources() -> list[str]`, `pull_sample(resource: str, limit: int = 100) -> list[dict]`, `pull_full(resource: str) -> list[dict]`, `pull_delta(resource: str, since: str | None = None) -> list[dict]`.

This test uses a real disposable Postgres schema (via `TEST_DATABASE_URL`, same database the rest of the suite already uses), not mocks — replacing today's fully-mocked-only coverage in `backend/tests/v2/connection/test_sql_connector.py`. It does not touch alembic/app tables at all: the fixture table is plain SQL, unrelated to the application's own schema.

- [ ] **Step 1: Write the fixture and failing tests**

Create `backend/tests/v2/connection/test_sql_connector_integration.py`:

```python
"""SQLConnector against a real, disposable Postgres schema — no mocks.
Complements test_sql_connector.py's mocked unit tests with real behavior for
test_connection, list_resources, pull_full, pull_sample, and a real
watermark-based pull_delta."""
import os
import uuid
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, text

from app.services.connection.sql_connector import SQLConnector

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def _scoped_url(schema: str) -> str:
    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema},public', safe='-=,')}"


@pytest.fixture
def pg_schema():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "sqlconn_it_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'''
            CREATE TABLE "{schema}".t_customers (
                id INT PRIMARY KEY,
                name TEXT NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
        '''))
        connection.execute(text(f'''
            INSERT INTO "{schema}".t_customers (id, name, updated_at) VALUES
                (1, 'Alice', '2026-01-01 00:00:00'),
                (2, 'Bob', '2026-02-01 00:00:00'),
                (3, 'Carol', '2026-03-01 00:00:00')
        '''))
    yield _scoped_url(schema)
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def test_connection_succeeds_against_real_schema(pg_schema):
    connector = SQLConnector({"connection_string": pg_schema})
    assert connector.test_connection() is True


def test_list_resources_finds_the_seeded_table(pg_schema):
    connector = SQLConnector({"connection_string": pg_schema})
    assert "t_customers" in connector.list_resources()


@pytest.mark.xfail(
    reason="Known bug: sql_connector.py:75 calls pd.read_sql(query, "
    "self._get_engine()) — a raw SQLAlchemy Engine. pandas>=2.2 is "
    "unpinned in requirements.txt and currently resolves to pandas 3.0.3, "
    "which no longer recognizes a bare Engine as a SQLAlchemy connectable "
    "and falls through to a legacy DBAPI2 code path that calls "
    "engine.cursor() directly, raising "
    "'Engine' object has no attribute 'cursor'. pull_full() cannot "
    "execute at all against a real database today. Fix tracked as "
    "sub-project E (e.g. pass engine.connect() instead of engine, or pin "
    "pandas<3). This test should start passing once that lands.",
    strict=True,
)
def test_pull_full_returns_all_seeded_rows(pg_schema):
    connector = SQLConnector({"connection_string": pg_schema})
    rows = connector.pull_full("t_customers")
    assert len(rows) == 3
    assert {r["name"] for r in rows} == {"Alice", "Bob", "Carol"}


def test_pull_sample_respects_limit(pg_schema):
    connector = SQLConnector({"connection_string": pg_schema})
    rows = connector.pull_sample("t_customers", limit=2)
    assert len(rows) == 2


def test_pull_delta_with_real_watermark_returns_only_newer_rows(pg_schema):
    connector = SQLConnector({
        "connection_string": pg_schema,
        "watermark_column": "updated_at",
    })
    rows = connector.pull_delta("t_customers", since="2026-01-15 00:00:00")
    assert {r["name"] for r in rows} == {"Bob", "Carol"}


@pytest.mark.xfail(
    reason="Same root cause as test_pull_full_returns_all_seeded_rows: "
    "pull_delta with no watermark_column falls back to pull_full "
    "(sql_connector.py:81-82), which is currently broken against a real "
    "engine under pandas 3.0.3.",
    strict=True,
)
def test_pull_delta_without_watermark_falls_back_to_pull_full(pg_schema):
    connector = SQLConnector({"connection_string": pg_schema})
    rows = connector.pull_delta("t_customers", since=None)
    assert len(rows) == 3
```

- [ ] **Step 2: Run the tests**

Run: `cd backend && TEST_DATABASE_URL=postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt python -m pytest tests/v2/connection/test_sql_connector_integration.py -v` (this is the local dev Postgres already running from this session's earlier dev-environment setup; substitute a dedicated test database if you have one)
Expected: `test_connection_succeeds_against_real_schema`, `test_list_resources_finds_the_seeded_table`, `test_pull_sample_respects_limit`, `test_pull_delta_with_real_watermark_returns_only_newer_rows` PASS. `test_pull_full_returns_all_seeded_rows` and `test_pull_delta_without_watermark_falls_back_to_pull_full` show as `XFAIL` (not `FAILED`) — this was verified empirically while writing this plan: `pull_full()` genuinely raises `AttributeError: 'Engine' object has no attribute 'cursor'` against this environment's pandas 3.0.3, confirming the xfail is real, not speculative.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/v2/connection/test_sql_connector_integration.py
git commit -m "test: add real-Postgres integration tests for SQLConnector"
```

---

### Task 9: `test_data/README.md`

**Files:**
- Create: `test_data/README.md`

**Interfaces:**
- Consumes: the file layout produced by Tasks 1-8 (this task only documents; it creates no new fixtures or tests).

- [ ] **Step 1: Write the README**

Create `test_data/README.md`:

```markdown
# test_data/

## Layout

- `信贷/`, `供应链/`, `教育/`, `医疗/`, `财务/`, `法律/`, `营销/`, `HR/` — one
  realistic happy-path sample per accepted upload format
  (csv/xlsx/json/md/pdf/docx/pptx), plus `<domain>_sample.xls`,
  `<domain>_sample.xml`, `<domain>_sample.doc`, `<domain>_sample.ppt` (added
  to exercise formats that were previously untested — see Known Issues).
  `信贷/` also has `generate_credit_data.py`, a one-off enrichment script for
  that domain's original fixtures.
- `edge_cases/malformed/` — corrupted OOXML zips, a truncated PDF, and
  non-UTF8 text/CSV. Proves `document_service.py`'s `CONVERSION_ERROR_MARKERS`
  handling and exposes a silent-mojibake bug (see Known Issues).
- `edge_cases/csv_structural/` — embedded commas/quotes, empty cells,
  header-only, duplicate columns. Exposes a column-misalignment bug (see
  Known Issues).
- `edge_cases/boundary/` — a zero-byte file, single-row, 10,000-row, and
  extreme-field-length CSVs, proving graceful failure on empty input and no
  crash or truncation at scale.
- `edge_cases/semantic/` — missing required fields, conflicting duplicate
  entities, and out-of-range values. These test the LLM extraction layer's
  judgment, not the file parser; there is no pass/fail assertion for them
  here (see the extraction-quality eval harness, sub-project D).
- `edge_cases/multi_source/` — two customer files with a conflicting shared
  ID, and two order files with mismatched column schemas, feeding
  `pipeline_run.py`'s multi-source ingestion path.
- `generators/` — deterministic Python scripts that produce every file above.
  Regenerating is idempotent (same seed data every run); `generate_missing_formats.py`
  requires `pip install xlwt` once, and `generate_legacy_office.py` requires
  LibreOffice (`soffice`) on `PATH` once — neither is a runtime dependency of
  the committed fixtures.

## Known issues found while building this fixture set

These are real, verified gaps in `backend/app/services/document_service.py`
and `backend/app/config.py`'s `allowed_upload_extensions`, found by adding
fixtures that exercise code paths with previously zero test coverage. None
are fixed by this fixture set — see `backend/tests/test_document_service.py`
for the tests that document each one, and sub-project E for the planned fix.

1. **`.xls`, `.xml`, `.doc`, `.ppt` are accepted upload extensions with no
   working conversion path.** MarkItDown has no converter for any of the
   four; every upload of these types fails outright today.
2. **Non-UTF8 CSV/text silently produces mojibake instead of an error.**
   `_read_plain_text` and `_read_csv_as_markdown` always decode with
   `encoding='utf-8', errors='replace'`, so a GBK-encoded file (a realistic
   encoding for Chinese business documents) "succeeds" with `ok=True` and
   U+FFFD replacement characters in place of the real text.
3. **A comma inside a quoted CSV field corrupts column alignment.**
   `_read_csv_as_markdown` naive-splits on every comma character, including
   ones inside quoted values, so `"北京, 上海"` becomes two misaligned table
   cells instead of one.
4. **`SQLConnector.pull_full()` cannot execute against a real database.**
   `sql_connector.py:75` passes a raw SQLAlchemy `Engine` to
   `pd.read_sql()`; `requirements.txt` pins `pandas>=2.2` with no upper
   bound, which currently resolves to pandas 3.0.3 — a version that no
   longer accepts a bare `Engine` there and raises `AttributeError:
   'Engine' object has no attribute 'cursor'`. `pull_delta()` with no
   `watermark_column` falls back to `pull_full()` and inherits the same
   break. This was invisible before this task because the only prior
   coverage (`test_sql_connector.py`) mocks `create_engine` entirely — see
   `backend/tests/v2/connection/test_sql_connector_integration.py` for the
   real-database tests that caught it.

## Orphaned content

`test_data/api/`, `db/`, `frontend/`, `documents/`, and most of the loose
root-level scripts are not referenced by any current code path. Cleaning
these up is sub-project A, not addressed here.
```

- [ ] **Step 2: Verify the file lists are accurate**

Run: `find test_data/edge_cases -type f | sort` and cross-check every path mentioned above exists; run `ls test_data/*/  | grep sample` and confirm all 8 domains have the 4 new per-domain files.
Expected: no discrepancies between the README's claims and the actual directory contents.

- [ ] **Step 3: Commit**

```bash
git add -f test_data/README.md
git commit -m "docs: document test_data/ layout and known extraction gaps found while adding fixtures"
```
