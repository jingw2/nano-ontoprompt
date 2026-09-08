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
