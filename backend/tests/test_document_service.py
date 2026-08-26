"""document_service — markitdown 内部错误串不应被当作转换成功的正文"""
import os
from pathlib import Path

import pytest

from app.services.document_service import convert_document, is_usable_converted_text


def test_corrupted_pptx_is_not_usable(tmp_path):
    # OOXML/zip 魔数 + 垃圾字节：zipfile 会抛 BadZipFile，
    # markitdown 的 ZipConverter 把这个错误当正文返回（"[ERROR] Invalid or corrupted zip file: ..."）
    p = tmp_path / "corrupted.pptx"
    p.write_bytes(b"PK\x03\x04" + os.urandom(200))

    result = convert_document(
        str(p), "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )

    assert not result.ok, f"corrupted pptx should not be usable, got content={result.content!r}"


def test_is_usable_converted_text_rejects_markitdown_error_sentinel():
    assert not is_usable_converted_text("[ERROR] Invalid or corrupted zip file: /tmp/x.pptx")
    assert not is_usable_converted_text("[ERROR] Failed to process zip file /tmp/x.docx: boom")


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


def test_duplicate_columns_csv_converts():
    result = convert_document(str(CSV_STRUCTURAL_FIXTURES / "duplicate_columns.csv"))
    assert result.ok
    assert result.content.splitlines()[0].count("|") == 4  # 客户ID, 金额, 金额 (duplicate header preserved as-is)


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
    result = convert_document(str(FIXTURES / "non_utf8.csv"))
    assert result.ok
    assert "张三" in result.content
    assert "�" not in result.content


@pytest.mark.xfail(
    reason="Same root cause as test_gbk_encoded_csv_decodes_correctly, via "
    "_read_plain_text instead of _read_csv_as_markdown.",
    strict=True,
)
def test_gbk_encoded_txt_decodes_correctly():
    result = convert_document(str(FIXTURES / "non_utf8.txt"))
    assert result.ok
    assert "张三" in result.content
    assert "�" not in result.content


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
