"""Generates semantic-edge-case CSV fixtures: missing required fields,
conflicting duplicate entities, and out-of-range values. These exercise the
LLM extraction layer's judgment, not the file parser — see sub-project D for
correctness scoring."""
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "edge_cases" / "semantic"
BASE.mkdir(parents=True, exist_ok=True)


def generate_missing_required_fields() -> None:
    (BASE / "missing_required_fields.csv").write_text(
        "客户ID,姓名,信用等级\n"
        "C001,张三,A\n"
        ",李四,B\n"  # missing 客户ID
        "C003,,C\n",  # missing 姓名
        encoding="utf-8-sig", newline="",
    )


def generate_conflicting_duplicate_entities() -> None:
    (BASE / "conflicting_duplicate_entities.csv").write_text(
        "客户ID,姓名,信用等级\n"
        "C001,张三,A\n"
        "C001,张三,C\n",  # same 客户ID, conflicting 信用等级
        encoding="utf-8-sig", newline="",
    )


def generate_out_of_range_values() -> None:
    (BASE / "out_of_range_values.csv").write_text(
        "客户ID,放款金额(元),放款日期\n"
        "C001,-5000,2026-03-01\n"  # negative amount
        "C002,10000,2099-12-31\n",  # far-future date
        encoding="utf-8-sig", newline="",
    )


def main() -> None:
    generate_missing_required_fields()
    generate_conflicting_duplicate_entities()
    generate_out_of_range_values()
    print(f"Wrote semantic fixtures to {BASE}")


if __name__ == "__main__":
    main()
