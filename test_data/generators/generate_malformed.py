"""Generates malformed-file fixtures: corrupted OOXML zips and a truncated PDF.

Corruption is done by truncating a valid file's last 300 bytes, which removes
the ZIP end-of-central-directory record (docx/xlsx) without leaving the file
empty — this is what makes MarkItDown's ZipConverter report a genuine
"[ERROR] Invalid or corrupted zip file" instead of a generic not-found error.
"""
import os
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
    # Use fixed timestamp for determinism
    os.utime(valid, (0, 0))
    _truncate(valid, BASE / "corrupted_docx.docx")


def generate_corrupted_xlsx() -> None:
    valid = BASE / "_valid_xlsx_scratch.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["客户ID", "金额"])
    ws.append(["C001", 100])
    wb.save(valid)
    # Use fixed timestamp for determinism
    os.utime(valid, (0, 0))
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
