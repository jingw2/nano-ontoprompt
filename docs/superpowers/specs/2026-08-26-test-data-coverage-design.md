# Test data coverage expansion design

This is sub-project B of a four-part decomposition (B → D → C → A) plus a
follow-up fix sub-project E:

- **B (this spec)**: expand `test_data/` to cover edge/malformed/boundary/
  semantic cases and missing upload formats, not just happy-path fixtures.
- **D**: a document→ontology extraction quality eval harness (mirrors
  `backend/evals/agent_ontology/`'s manifest+gates pattern), including new
  ontology-quality gates (entity scattering, vague naming) informed by a
  completed audit (see Non-goals).
- **C**: decide the fate of the dead domain-driven Playwright specs.
- **A**: `test_data/` hygiene (remove orphaned content, fix two broken-path
  scripts).
- **E**: fix the underlying ontology-quality bugs the audit found
  (instance-leakage, missing single-document dedup, un-humanized
  PipelineMapping template names) — brainstormed after D so gates exist to
  verify the fix.

## Goal

`test_data/` today is 100% happy-path: one file per format per domain, no
malformed/boundary/semantic variation, and zero fixtures for four accepted
upload formats (`.xls`/`.doc`/`.ppt`/`.xml`). This leaves large parts of the
real extraction code path — the naive CSV converter, the corrupted-zip
`CONVERSION_ERROR_MARKERS` handling, entity dedup, multi-file pipeline
ingestion — completely unexercised by any fixture. This spec adds the
missing test data, generated deterministically, without redesigning
anything else.

## Scope

In scope: new fixture files and their generator scripts, plus two new
integration test files that consume fixtures generated for the SQL
connector and multi-source pipeline cases. Not in scope: building the eval
harness that scores extraction output against these fixtures (sub-project
D), removing the existing orphaned `test_data/` content (sub-project A), or
fixing any production bug the fixtures expose (sub-project E). Where a new
fixture is expected to surface an existing, known bug (the CSV converter),
this spec's own test is allowed to fail — the failure is a finding for E,
not something to be quietly special-cased away here.

## Directory structure

```
test_data/
  信贷/ 供应链/ 教育/ 医疗/ 财务/ 法律/ 营销/ HR/
    <existing happy-path files, unchanged>
    <domain>_sample.xls
    <domain>_sample.doc
    <domain>_sample.ppt
    <domain>_sample.xml
  edge_cases/
    malformed/
      corrupted_docx.docx
      corrupted_pptx.pptx
      corrupted_xlsx.xlsx
      truncated.pdf
      non_utf8.csv
      non_utf8.txt
    csv_structural/
      embedded_comma_quoted.csv
      embedded_quote_escaped.csv
      empty_cells.csv
      header_only.csv
      duplicate_columns.csv
    boundary/
      empty_file.csv
      single_row.csv
      large_10k_rows.csv
      extreme_length_field.csv
    semantic/
      missing_required_fields.csv
      conflicting_duplicate_entities.csv
      out_of_range_values.csv
    multi_source/
      customers_v1.csv
      customers_v2_conflicting.csv
      orders_schema_a.csv
      orders_schema_b_mismatched.csv
  generators/
    generate_malformed.py
    generate_csv_structural.py
    generate_boundary.py
    generate_semantic.py
    generate_multi_source.py
    generate_missing_formats.py
  README.md
```

`HR/` currently has no ontology ever generated from it (confirmed during
the audit) — this spec does not change that; running extraction against it
is a D/E concern, not a fixture-authoring concern.

## Generation mechanism

All generators are deterministic Python scripts, seeded (`random.seed(42)`
or equivalent, matching the existing `generate_credit_data.py` pattern), run
once, with output committed to git — tests never regenerate fixtures at
run time.

- **`.xls`**: `xlwt`, writing 3-5 rows of realistic-looking sample rows per
  domain (mirrors the existing `.xlsx` sample's shape).
- **`.docx`/`.pptx`** (for corruption fixtures) and legacy **`.doc`/`.ppt`**
  (for missing-format fixtures): generate a real `.docx`/`.pptx` first via
  `python-docx`/`python-pptx` (same as the existing generators), then for
  `.doc`/`.ppt` shell out once to headless LibreOffice
  (`soffice --headless --convert-to doc <file>.docx`) to produce a genuine
  legacy binary. This conversion step runs only when regenerating fixtures,
  not in CI or at test time — committed output has no LibreOffice
  dependency downstream. `generate_missing_formats.py`'s module docstring
  states this requirement explicitly.
- **`.xml`**: hand-templated (an `ElementTree`-built document mirroring the
  domain's existing record shape), no external tool needed.
- **Corrupted-zip fixtures**: generate a valid `.docx`/`.pptx`/`.xlsx`, then
  truncate or byte-flip it (e.g. drop the last 200 bytes, or overwrite the
  ZIP central directory) so it fails to open as a valid archive but is not
  simply empty — this is what makes it exercise `CONVERSION_ERROR_MARKERS`
  rather than a generic file-not-found style failure.
- **`truncated.pdf`**: generate a valid multi-page PDF (`reportlab`,
  already used elsewhere in the repo per the earlier PDF-reading work this
  session), then truncate mid-stream.
- **`non_utf8.csv`/`non_utf8.txt`**: write with `encoding='gbk'` (a
  realistic real-world encoding for Chinese business documents, not an
  arbitrary corrupt byte sequence).
- **CSV structural fixtures**: hand-templated small CSVs (3-10 rows) via
  Python's `csv` module — `embedded_comma_quoted.csv` includes a field like
  `"北京, 上海"` inside a properly-quoted cell (valid CSV, but breaks a
  naive comma-split); `embedded_quote_escaped.csv` includes a field with an
  escaped internal quote (`"客户说""你好"""`); `duplicate_columns.csv` has
  two columns both named `金额`.
- **Boundary fixtures**: `empty_file.csv` is a zero-byte file;
  `single_row.csv` has a header and exactly one data row;
  `large_10k_rows.csv` is generated by looping a domain-like row template
  10,000 times; `extreme_length_field.csv` has one field containing a
  20,000-character string.
- **Semantic fixtures**: `missing_required_fields.csv` omits a
  domain-critical column value on some rows (e.g. blank `客户ID`);
  `conflicting_duplicate_entities.csv` has two rows describing the same
  entity ID with different attribute values (e.g. same `客户ID`, different
  `信用等级`); `out_of_range_values.csv` has implausible values (negative
  amounts, dates in the far future).
- **Multi-source fixtures**: `customers_v1.csv`/`customers_v2_conflicting.csv`
  describe overlapping customer IDs with differing attributes across the
  two files; `orders_schema_a.csv`/`orders_schema_b_mismatched.csv` describe
  the same conceptual entity type with different column names/order,
  simulating two systems feeding one pipeline without a shared schema.

## What each case validates (mapping to real code)

- Corrupted-zip docx/pptx/xlsx → `backend/app/services/document_service.py`'s
  `CONVERSION_ERROR_MARKERS` handling for MarkItDown false-successes —
  currently dead code exercised by no fixture.
- CSV embedded commas/quotes → the hand-rolled comma→pipe converter in
  `convert_document()`. This is expected to **fail today** — the test
  documents the bug rather than working around it; fixing the converter is
  sub-project E's concern.
- Non-UTF8 CSV/TXT → the decode path in `convert_document()`, currently
  unexercised by any fixture.
- Boundary cases (empty/huge/long-field) → general robustness: no crash, no
  unbounded-time hang, no silent truncation.
- Semantic cases (missing fields, conflicting duplicates, out-of-range
  values) → exercise the LLM extraction layer's judgment. These do not get
  a strict pass/fail assertion in this sub-project — sub-project D defines
  what "correct" extraction looks like for them.
- SQL connector fixtures/tests → real behavior of
  `backend/app/services/connection/sql_connector.py` (`test_connection`,
  `list_resources`, `pull_full`, `pull_sample`, `pull_delta` with and
  without a watermark column) against a real disposable Postgres schema,
  replacing today's fully-mocked-only coverage. This tests the connector
  class in isolation — it does not exercise pipeline execution, because
  (per the earlier research) no code path today feeds a DB connector into
  a live `Pipeline` run.
- Multi-source fixtures/tests → `backend/app/tasks/v2/pipeline_run.py`'s
  existing `_collect_sources`/`multi_source` logic, which today only reads
  multiple uploaded-file datasets (not DB connections).

## New test files

- `backend/tests/v2/connection/test_sql_connector_integration.py` — real
  Postgres integration tests for `sql_connector.py`, using the repo's
  existing per-test schema-isolation pattern (`_scoped_url`, `CREATE
  SCHEMA`/`DROP SCHEMA CASCADE`) so it composes safely with the rest of the
  suite. Seeds a small fixture table directly via SQL in test setup (not a
  committed `.sql` file, since the fixture only needs to exist for the
  duration of one test schema).
- `backend/tests/v2/pipeline/test_multi_source_ingestion.py` — feeds the
  `multi_source/` fixtures through `pipeline_run.py`'s existing multi-file
  ingestion path and asserts both files are collected as separate sources
  (`multi_source == True`, both `_collect_sources()` entries present). This
  test does not assert anything about how conflicting attributes get
  resolved during extraction — that is sub-project D's scoring concern; it
  only proves both sources reach the pipeline.
- `test_data/README.md` — documents the full layout (happy-path domains vs.
  `edge_cases/`), what each `edge_cases/` subdirectory proves, and how to
  regenerate fixtures (including the one-time LibreOffice dependency for
  `.doc`/`.ppt`).

## Non-goals

- No changes to `convert_document()`, `sql_connector.py`, `pipeline_run.py`,
  entity dedup, or PipelineMapping naming — this spec adds test data and
  the minimum test scaffolding to run it against existing code, and
  reports failures as findings, not fixes.
- No eval harness, scoring, or gates (sub-project D).
- No removal of existing orphaned `test_data/` content (sub-project A).
- No CI wiring beyond what's needed to run the two new pytest files
  locally (CI integration is part of D, which defines the overall test
  pipeline).

## Verification

- Every generator script runs standalone and produces its declared output
  files deterministically (re-running produces byte-identical output).
- `test_sql_connector_integration.py` passes against a real local Postgres
  schema with zero mocks on the five listed connector methods.
- `test_multi_source_ingestion.py` passes, proving both multi-source
  fixtures reach `pipeline_run.py` as separate collected sources.
- The CSV embedded-comma/quote test is present and its current failure (a
  known bug) is documented in the test's own docstring/comment and in
  `test_data/README.md`'s known-issues section — not silently marked
  `xfail` without explanation.
- `test_data/README.md` accurately describes every new directory and file.
