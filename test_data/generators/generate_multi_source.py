"""Generates multi-source pipeline fixtures: two customer files with an
overlapping ID and a conflicting attribute, and two order files with
different column names for the same conceptual entity type."""
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "edge_cases" / "multi_source"
BASE.mkdir(parents=True, exist_ok=True)


def generate_customers() -> None:
    (BASE / "customers_v1.csv").write_text(
        "客户ID,姓名,信用等级\nC001,张三,A\nC002,李四,B\n",
        encoding="utf-8-sig", newline="",
    )
    (BASE / "customers_v2_conflicting.csv").write_text(
        # C001 repeats with a conflicting 信用等级 (A vs B) — same ID, two
        # sources disagreeing on an attribute.
        "客户ID,姓名,信用等级\nC001,张三,B\nC003,王五,A\n",
        encoding="utf-8-sig", newline="",
    )


def generate_orders() -> None:
    (BASE / "orders_schema_a.csv").write_text(
        "订单编号,客户ID,金额\nO001,C001,500\n",
        encoding="utf-8-sig", newline="",
    )
    (BASE / "orders_schema_b_mismatched.csv").write_text(
        # Same conceptual entity (an order) with English column names instead
        # of the Chinese schema used by orders_schema_a.csv.
        "OrderNo,CustomerID,Amount\nO002,C002,800\n",
        encoding="utf-8-sig", newline="",
    )


def main() -> None:
    generate_customers()
    generate_orders()
    print(f"Wrote multi-source fixtures to {BASE}")


if __name__ == "__main__":
    main()
