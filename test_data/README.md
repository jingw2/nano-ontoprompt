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
  ID, and two order files with mismatched column schemas. Their filenames
  prove `pipeline_run.py`'s `_collect_sources()` correctly gathers multiple
  dataset references as separate pipeline sources; the CSV content itself is
  realistic but not read or parsed by that test (see the extraction-quality
  eval harness, sub-project D, for a future consumer of the actual content).
- `generators/` — deterministic Python scripts that produce every file above.
  Regenerating is idempotent (same seed data every run); `generate_missing_formats.py`
  requires `pip install xlwt` once, and `generate_legacy_office.py` requires
  LibreOffice (`soffice`) on `PATH` once — neither is a runtime dependency of
  the committed fixtures.
- `generate_all_domains.py`, `generate_legal_hr_v2.py`, `generate_supply_chain.py`
  — the original generator scripts that produced the domain fixture content
  above (same category as `信贷/generate_credit_data.py`). Kept for
  provenance/regeneration, not run automatically.
- `test_reproducibility.py` — a manual regression check for a real,
  previously-fixed non-determinism bug (LLM auto-selection in the Pipeline
  Mapping `md_to_structured` step). Runs 5x against both the simple-LLM and
  Pipeline Mapping paths and diffs the extracted ontology across runs.
  Requires a running backend plus `mock_llm_server.py` (below).
- `mock_llm_server.py` — a local mock LLM server (port 8123) that
  `test_reproducibility.py` depends on for deterministic extraction output.
- `api/`, `db/`, `documents/` — reserved for the Agent Semantic
  Infrastructure implementation plan
  (`docs/superpowers/specs/2026-08-26-agent-semantic-infrastructure-design.md`),
  which lists these as reusable test assets. Not orphaned — do not remove
  without checking that plan first.

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

## Hygiene (sub-project A)

25 redundant, ad-hoc manual E2E/capture/debug scripts (heavily duplicated
variations of the same "run the app end-to-end and screenshot it" idea —
`e2e_full_flow.py`, `full_e2e_final.py`, `run_final_e2e.py`, `verify_e2e.py`,
etc.), plus `frontend/`, `prompts/`, and `screenshots/` (zero references
anywhere in the codebase, including from each other's scripts) were removed.
Two misplaced files unrelated to test fixtures (`供应链/ontology-agent-faq-*.md`,
a blog article) were also removed. `scripts/data/run_full_supply_chain.py`
and `run_supply_chain_pipeline.py` had a broken-path bug (`DATA_DIR` resolved
relative to `scripts/data/` instead of the repo root) — fixed, not deleted,
since both are legitimate, non-duplicated manual Pipeline-testing tools.
