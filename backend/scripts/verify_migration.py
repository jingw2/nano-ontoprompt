#!/usr/bin/env python3
"""迁移结果验证脚本 — 对比 v1 SQLite 和 v2 PostgreSQL 的数据量

用法：
  python scripts/verify_migration.py \
    --v1-db ./ontoprompt.db \
    --pg-url postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt

退出码：
  0 — 所有表数量一致
  1 — 存在不一致或检查失败
"""
from __future__ import annotations

import argparse
import sqlite3
import json
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("verify")


def verify(v1_db: str, pg_url: str) -> dict:
    """对比 v1 和 v2 各表的行数，返回校验结果字典

    返回格式：
      {
        "checks": [{"table": ..., "v1": ..., "v2": ..., "match": bool}, ...],
        "mismatches": [不一致的检查项]
      }
    """
    result: dict = {"mismatches": [], "checks": []}

    # 连接 v1 SQLite
    v1_conn = sqlite3.connect(v1_db)

    # 连接 v2 PostgreSQL
    from sqlalchemy import create_engine, text

    engine = create_engine(pg_url)

    # 需要对比的表（v1表名, v2表名）
    tables = [
        ("users", "users"),
        ("ontology_projects", "ontology_projects"),
        ("entities", "entities"),
        ("relations", "relations"),
        ("prompts", "prompts"),
        ("model_configs", "model_configs"),
    ]

    with engine.connect() as v2_conn:
        for table_v1, table_v2 in tables:
            try:
                # 查询 v1 行数
                v1_count = v1_conn.execute(
                    f"SELECT COUNT(*) FROM {table_v1}"
                ).fetchone()[0]

                # 查询 v2 行数
                v2_count = v2_conn.execute(
                    text(f"SELECT COUNT(*) FROM {table_v2}")
                ).scalar()

                match = v1_count == v2_count
                check = {
                    "table": table_v1,
                    "v1": v1_count,
                    "v2": v2_count,
                    "match": match,
                }
                result["checks"].append(check)

                if not match:
                    result["mismatches"].append(check)
                    logger.warning(
                        f"不一致: {table_v1} v1={v1_count} v2={v2_count}"
                    )
                else:
                    logger.info(f"OK {table_v1}: {v1_count} 行")

            except Exception as e:
                # 记录检查失败（例如表不存在）
                err_item = {"table": table_v1, "error": str(e)}
                result["mismatches"].append(err_item)
                result["checks"].append(err_item)
                logger.warning(f"检查 {table_v1} 失败: {e}")

    v1_conn.close()
    return result


def main():
    parser = argparse.ArgumentParser(
        description="迁移结果验证工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--v1-db", default="./ontoprompt.db", help="v1 SQLite 路径")
    parser.add_argument("--pg-url", required=True, help="v2 PostgreSQL 连接字符串")
    args = parser.parse_args()

    result = verify(args.v1_db, args.pg_url)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result["mismatches"]:
        logger.error(f"验证失败：{len(result['mismatches'])} 处不一致")
        sys.exit(1)
    else:
        logger.info("验证通过：v1 和 v2 数据量一致")
        sys.exit(0)


if __name__ == "__main__":
    main()
