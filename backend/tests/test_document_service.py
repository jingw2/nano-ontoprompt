"""document_service — markitdown 内部错误串不应被当作转换成功的正文"""
import os

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
