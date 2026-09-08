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
