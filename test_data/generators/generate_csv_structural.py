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
