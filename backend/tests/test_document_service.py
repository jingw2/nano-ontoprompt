"""document_service — markitdown 内部错误串不应被当作转换成功的正文"""
import os
from pathlib import Path

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
