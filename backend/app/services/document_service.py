import os
from dataclasses import dataclass

CONVERSION_ERROR_MARKERS = (
    "[File conversion failed:",
    "[Text read failed:",
    "[CSV read failed:",
    # markitdown 的 ZipConverter 在处理 pptx/docx/xlsx（本质是 zip 容器）失败时，
    # 不抛异常也不返回 None，而是把错误信息当正文返回，例如：
    # "[ERROR] Invalid or corrupted zip file: ..."。必须识别为失败，否则损坏文件会被
    # 当成"转换成功"送进 LLM，导致提取结果空白且没有任何错误提示。
    "[ERROR]",
)


@dataclass
class ConversionResult:
    content: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.content and self.content.strip())


def is_usable_converted_text(text: str | None) -> bool:
    if not text or not text.strip():
        return False
    stripped = text.strip()
    return not any(stripped.startswith(marker) for marker in CONVERSION_ERROR_MARKERS)


def combine_converted_files(files) -> tuple[str | None, str | None]:
    """Return (combined_text, error_message). error_message is set when no usable content."""
    usable = [f for f in files if is_usable_converted_text(getattr(f, "converted_md", None))]
    if not usable:
        bad_names = [getattr(f, "filename", "?") for f in files]
        return None, f"以下文件无法用于提取（转换失败或无文本内容）：{', '.join(bad_names)}"

    combined = "\n\n---\n\n".join(
        f"【来源文件】{getattr(f, 'filename', 'unknown')}\n\n{f.converted_md.strip()}"
        for f in usable
    )
    if not combined.strip():
        return None, "上传的文件中没有可用于提取的文本内容"
    return combined, None


def convert_document(file_path: str, mime_type: str | None = None) -> ConversionResult:
    ext = os.path.splitext(file_path)[1].lower()

    if ext in (".md", ".txt") or (mime_type and ("text/plain" in mime_type or "text/markdown" in mime_type)):
        return _read_plain_text(file_path)

    if ext == ".csv" or (mime_type and "csv" in mime_type):
        return _read_csv_as_markdown(file_path)

    if ext == ".xls":
        return _read_xls_as_markdown(file_path)

    if ext == ".xml":
        return _read_xml_as_markdown(file_path)

    if ext == ".doc":
        return _convert_legacy_office(file_path, "docx")

    if ext == ".ppt":
        return _convert_legacy_office(file_path, "pptx")

    docx_result: ConversionResult | None = None
    if ext == ".docx":
        docx_result = _convert_docx(file_path)
        if docx_result.ok:
            return docx_result

    try:
        from markitdown import MarkItDown

        md = MarkItDown()
        result = md.convert(file_path)
        content = (result.text_content or "").strip()
        if content and is_usable_converted_text(content):
            return ConversionResult(content=content)
        if docx_result is not None:
            return docx_result
        if content:
            return ConversionResult(error=f"文件转换失败：{content}")
        return ConversionResult(error="文件转换后没有可用文本内容")
    except BaseException as e:
        if docx_result is not None:
            return docx_result
        if mime_type and ("text" in mime_type or "csv" in mime_type):
            text_result = _read_plain_text(file_path)
            if text_result.ok:
                return text_result
        return ConversionResult(error=f"文件转换失败：{e}")


def _decode_text_bytes(raw: bytes) -> tuple[str | None, str | None]:
    """Decode raw bytes as UTF-8, falling back to GB18030 (superset of GBK —
    the realistic non-UTF-8 encoding for Chinese business documents). Returns
    (text, error); never silently replaces undecodable bytes with U+FFFD, since
    that produces mojibake with no indication anything went wrong."""
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding), None
        except UnicodeDecodeError:
            continue
    return None, "无法识别文件编码（已尝试 UTF-8、GB18030）"


def _read_plain_text(file_path: str) -> ConversionResult:
    try:
        with open(file_path, "rb") as f:
            raw = f.read()
        text, decode_error = _decode_text_bytes(raw)
        if decode_error:
            return ConversionResult(error=f"文本读取失败：{decode_error}")
        content = text.strip()
        if not content:
            return ConversionResult(error="文件为空")
        return ConversionResult(content=content)
    except Exception as e:
        return ConversionResult(error=f"文本读取失败：{e}")


def _read_csv_as_markdown(file_path: str) -> ConversionResult:
    import csv
    import io

    try:
        with open(file_path, "rb") as f:
            raw = f.read()
        text, decode_error = _decode_text_bytes(raw)
        if decode_error:
            return ConversionResult(error=f"CSV 读取失败：{decode_error}")
        rows = list(csv.reader(io.StringIO(text)))
        if not rows:
            return ConversionResult(error="CSV 文件为空")
        header, data_rows = rows[0], rows[1:]
        separator = "|".join(["---"] * max(len(header), 1))
        md_lines = [
            "| " + " | ".join(header) + " |",
            "| " + separator + " |",
        ]
        for row in data_rows:
            md_lines.append("| " + " | ".join(row) + " |")
        return ConversionResult(content="\n".join(md_lines))
    except Exception as e:
        return ConversionResult(error=f"CSV 读取失败：{e}")


def _read_xls_as_markdown(file_path: str) -> ConversionResult:
    try:
        import xlrd

        wb = xlrd.open_workbook(file_path)
        parts: list[str] = []
        for sheet in wb.sheets():
            if sheet.nrows == 0:
                continue
            header = [str(sheet.cell_value(0, c)) for c in range(sheet.ncols)]
            separator = "|".join(["---"] * max(len(header), 1))
            md_lines = [f"## {sheet.name}", "| " + " | ".join(header) + " |", "| " + separator + " |"]
            for r in range(1, sheet.nrows):
                row = [str(sheet.cell_value(r, c)) for c in range(sheet.ncols)]
                md_lines.append("| " + " | ".join(row) + " |")
            parts.append("\n".join(md_lines))
        content = "\n\n".join(parts).strip()
        if not content:
            return ConversionResult(error="XLS 文件中没有可提取的数据")
        return ConversionResult(content=content)
    except Exception as e:
        return ConversionResult(error=f"XLS 解析失败：{e}")


def _read_xml_as_markdown(file_path: str) -> ConversionResult:
    try:
        import xml.etree.ElementTree as ET

        tree = ET.parse(file_path)

        def flatten(elem, depth: int = 0) -> list[str]:
            lines: list[str] = []
            text = (elem.text or "").strip()
            children = list(elem)
            indent = "  " * depth
            if not children:
                if text:
                    lines.append(f"{indent}{elem.tag}: {text}")
            else:
                lines.append(f"{indent}{elem.tag}:")
                for child in children:
                    lines.extend(flatten(child, depth + 1))
            return lines

        content = "\n".join(flatten(tree.getroot())).strip()
        if not content:
            return ConversionResult(error="XML 文件中没有可提取的文本")
        return ConversionResult(content=content)
    except Exception as e:
        return ConversionResult(error=f"XML 解析失败：{e}")


def _convert_legacy_office(file_path: str, target_ext: str) -> ConversionResult:
    """Convert legacy binary .doc/.ppt to modern .docx/.pptx via headless
    LibreOffice, then reuse the existing conversion path for the modern
    format. Requires `soffice` on PATH — a real runtime dependency, not just
    a dev-machine convenience."""
    import shutil
    import subprocess
    import tempfile

    soffice = shutil.which("soffice")
    if not soffice:
        return ConversionResult(error="旧版 Office 格式转换失败：未安装 LibreOffice（soffice 不可用）")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(
                [soffice, "--headless", "--convert-to", target_ext, "--outdir", tmpdir, file_path],
                check=True, capture_output=True, timeout=60,
            )
            stem = os.path.splitext(os.path.basename(file_path))[0]
            converted_path = os.path.join(tmpdir, f"{stem}.{target_ext}")
            if not os.path.exists(converted_path):
                return ConversionResult(error="旧版 Office 格式转换失败：LibreOffice 未生成输出文件")
            if target_ext == "docx":
                return _convert_docx(converted_path)
            return convert_document(converted_path)
    except subprocess.TimeoutExpired:
        return ConversionResult(error="旧版 Office 格式转换失败：LibreOffice 转换超时")
    except Exception as e:
        return ConversionResult(error=f"旧版 Office 格式转换失败：{e}")


def _convert_docx(file_path: str) -> ConversionResult:
    try:
        from docx import Document

        doc = Document(file_path)
        parts: list[str] = []

        for para in doc.paragraphs:
            text = para.text.strip()
            if text:
                parts.append(text)

        for table in doc.tables:
            rows: list[str] = []
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                if any(cells):
                    rows.append("| " + " | ".join(cells) + " |")
            if rows:
                if len(rows) > 1:
                    col_count = rows[0].count("|") - 1
                    sep = "| " + " | ".join(["---"] * max(col_count, 1)) + " |"
                    parts.append("\n".join([rows[0], sep, *rows[1:]]))
                else:
                    parts.append("\n".join(rows))

        content = "\n\n".join(parts).strip()
        if not content:
            return ConversionResult(error="DOCX 文件中没有可提取的文本")
        return ConversionResult(content=content)
    except Exception as e:
        return ConversionResult(error=f"DOCX 解析失败：{e}")
