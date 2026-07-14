#!/usr/bin/env python3
"""
v1 (SQLite) → v2 (PostgreSQL + Neo4j + ChromaDB) 数据迁移脚本

用法：
  python scripts/migrate_v1_to_v2.py \
    --v1-db ./ontoprompt.db \
    --pg-url postgresql://ontoprompt:ontoprompt@localhost:5432/ontoprompt \
    [--dry-run] \
    [--report migration_report.json]

字段差异说明（v1 SQLite → v2 PostgreSQL）：
  entities:
    - properties_json (TEXT) → properties (JSON / dict)
    - 新增 version 字段，默认 'v0.1'
    - 新增 updated_at 字段，默认 created_at 值
  relations:
    - source (TEXT) → source_entity (TEXT)
    - target (TEXT) → target_entity (TEXT)
    - 新增 properties 字段，默认 {}
  users / ontology_projects / prompts / model_configs:
    - 字段完全兼容，直接迁移
"""
from __future__ import annotations

import argparse
import json
import sys
import os
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("migrate")


@dataclass
class MigrationStats:
    """迁移统计信息"""
    users: int = 0
    ontologies: int = 0
    entities: int = 0
    relations: int = 0
    files: int = 0
    prompts: int = 0
    model_configs: int = 0
    neo4j_nodes: int = 0
    neo4j_edges: int = 0
    chroma_docs: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """序列化为字典，用于 JSON 报告输出"""
        return {
            "migrated": {
                "users": self.users,
                "ontologies": self.ontologies,
                "entities": self.entities,
                "relations": self.relations,
                "files": self.files,
                "prompts": self.prompts,
                "model_configs": self.model_configs,
                "neo4j_nodes": self.neo4j_nodes,
                "neo4j_edges": self.neo4j_edges,
                "chroma_docs": self.chroma_docs,
            },
            "errors": self.errors,
            "warnings": self.warnings,
        }


def _now_iso() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串"""
    return datetime.now(timezone.utc).isoformat()


def _parse_properties_json(raw) -> dict:
    """将 v1 的 properties_json (TEXT) 安全解析为 dict"""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


class V1ToV2Migrator:
    """v1 → v2 迁移执行器

    迁移流程：
    1. 连接 v1 SQLite 和 v2 PostgreSQL
    2. 按依赖顺序迁移：users → prompts → model_configs → ontologies+entities+relations
    3. 对每个本体，额外将实体/关系同步至 Neo4j 和 ChromaDB（可选，失败仅记录警告）
    """

    def __init__(self, v1_db_path: str, pg_url: str, dry_run: bool = False):
        self.v1_db_path = v1_db_path
        self.pg_url = pg_url
        self.dry_run = dry_run
        self.stats = MigrationStats()
        self._v1_conn = None
        self._v2_session = None

    def run(self) -> MigrationStats:
        """执行完整迁移流程，返回统计信息"""
        logger.info(
            f"{'[DRY RUN] ' if self.dry_run else ''}开始迁移: {self.v1_db_path} → PostgreSQL"
        )

        try:
            self._connect_v1()
            self._connect_v2()
            self._migrate_users()
            self._migrate_prompts()
            self._migrate_model_configs()
            self._migrate_ontologies_and_entities()
            logger.info("迁移完成")
        except Exception as e:
            self.stats.errors.append(f"迁移失败: {e}")
            logger.error(f"迁移失败: {e}", exc_info=True)
        finally:
            self._cleanup()

        return self.stats

    # ── 连接管理 ─────────────────────────────────────────────────────

    def _connect_v1(self):
        """连接 v1 SQLite 数据库"""
        import sqlite3

        if not os.path.exists(self.v1_db_path):
            raise FileNotFoundError(f"v1 SQLite 数据库不存在: {self.v1_db_path}")
        self._v1_conn = sqlite3.connect(self.v1_db_path)
        self._v1_conn.row_factory = sqlite3.Row
        logger.info(f"已连接 v1 SQLite: {self.v1_db_path}")

    def _connect_v2(self):
        """连接 v2 PostgreSQL，验证连接可用性"""
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import sessionmaker

        engine = create_engine(self.pg_url, pool_pre_ping=True)
        SessionFactory = sessionmaker(bind=engine)
        self._v2_session = SessionFactory()
        # 发送 SELECT 1 验证连接是否正常
        self._v2_session.execute(text("SELECT 1"))
        logger.info("已连接 v2 PostgreSQL")

    def _cleanup(self):
        """关闭所有数据库连接"""
        if self._v1_conn:
            self._v1_conn.close()
        if self._v2_session:
            self._v2_session.close()

    # ── 数据迁移 ─────────────────────────────────────────────────────

    def _migrate_users(self):
        """迁移用户表 — 字段完全兼容，直接合并"""
        cursor = self._v1_conn.execute("SELECT * FROM users")
        rows = cursor.fetchall()
        logger.info(f"迁移用户: {len(rows)} 条")

        if not self.dry_run and rows:
            from app.models.user import User

            for row in rows:
                existing = self._v2_session.query(User).filter(User.id == row["id"]).first()
                if not existing:
                    self._v2_session.merge(User(**dict(row)))
            self._v2_session.commit()

        self.stats.users = len(rows)

    def _migrate_prompts(self):
        """迁移提示词表 — 字段完全兼容，失败仅记录警告"""
        try:
            cursor = self._v1_conn.execute("SELECT * FROM prompts")
            rows = cursor.fetchall()
            logger.info(f"迁移提示词: {len(rows)} 条")

            if not self.dry_run and rows:
                from app.models.prompt import Prompt

                for row in rows:
                    existing = self._v2_session.query(Prompt).filter(Prompt.id == row["id"]).first()
                    if not existing:
                        self._v2_session.merge(Prompt(**dict(row)))
                self._v2_session.commit()

            self.stats.prompts = len(rows)
        except Exception as e:
            self.stats.warnings.append(f"提示词迁移部分失败: {e}")
            logger.warning(f"提示词迁移部分失败: {e}")

    def _migrate_model_configs(self):
        """迁移模型配置表 — 字段完全兼容，失败仅记录警告"""
        try:
            cursor = self._v1_conn.execute("SELECT * FROM model_configs")
            rows = cursor.fetchall()
            logger.info(f"迁移模型配置: {len(rows)} 条")

            if not self.dry_run and rows:
                from app.models.model_config import ModelConfig

                for row in rows:
                    existing = (
                        self._v2_session.query(ModelConfig)
                        .filter(ModelConfig.id == row["id"])
                        .first()
                    )
                    if not existing:
                        self._v2_session.merge(ModelConfig(**dict(row)))
                self._v2_session.commit()

            self.stats.model_configs = len(rows)
        except Exception as e:
            self.stats.warnings.append(f"模型配置迁移部分失败: {e}")
            logger.warning(f"模型配置迁移部分失败: {e}")

    def _migrate_ontologies_and_entities(self):
        """迁移本体及其实体、关系，同时写入 Neo4j 和 ChromaDB（可选）

        字段映射：
          entities.properties_json (TEXT) → properties (dict)
          entities 补充 version='v0.1', updated_at=created_at
          relations.source → source_entity
          relations.target → target_entity
          relations 补充 properties={}
        """
        cursor = self._v1_conn.execute("SELECT * FROM ontology_projects")
        ontologies = cursor.fetchall()
        logger.info(f"迁移本体: {len(ontologies)} 个")

        for ont in ontologies:
            ont_id = ont["id"]
            try:
                # 1. 写入 PostgreSQL（ontology_projects 字段兼容）
                if not self.dry_run:
                    from app.models.ontology import OntologyProject

                    existing = (
                        self._v2_session.query(OntologyProject)
                        .filter(OntologyProject.id == ont_id)
                        .first()
                    )
                    if not existing:
                        self._v2_session.merge(OntologyProject(**dict(ont)))
                    self._v2_session.commit()
                self.stats.ontologies += 1

                # 2. 查询该本体的实体（v1 表结构）
                ent_cursor = self._v1_conn.execute(
                    "SELECT * FROM entities WHERE ontology_id = ?", (ont_id,)
                )
                entities = ent_cursor.fetchall()

                # 3. 查询该本体的关系（v1 表结构）
                rel_cursor = self._v1_conn.execute(
                    "SELECT * FROM relations WHERE ontology_id = ?", (ont_id,)
                )
                relations = rel_cursor.fetchall()

                if not self.dry_run:
                    # 4. 写入 PostgreSQL 实体（处理字段差异）
                    from app.models.entity import Entity

                    for ent in entities:
                        existing = (
                            self._v2_session.query(Entity)
                            .filter(Entity.id == ent["id"])
                            .first()
                        )
                        if not existing:
                            ent_data = self._map_entity(dict(ent))
                            self._v2_session.merge(Entity(**ent_data))
                    self._v2_session.commit()

                    # 5. 写入 Neo4j（失败仅警告）
                    self._sync_to_neo4j(ont_id, list(entities), list(relations))

                    # 6. 写入 ChromaDB（失败仅警告）
                    self._sync_to_chroma(ont_id, list(entities))

                self.stats.entities += len(entities)
                self.stats.relations += len(relations)
                logger.info(
                    f"  本体 {ont_id}: {len(entities)} 实体，{len(relations)} 关系"
                )

            except Exception as e:
                self.stats.errors.append(f"本体 {ont_id} 迁移失败: {e}")
                logger.warning(f"本体 {ont_id} 迁移失败: {e}")

    # ── 字段映射工具 ─────────────────────────────────────────────────

    @staticmethod
    def _map_entity(row: dict) -> dict:
        """将 v1 实体字段映射到 v2 格式

        变更：
        - properties_json (TEXT/None) → properties (dict)
        - 补充 version 字段（若缺失）
        - 补充 updated_at 字段（若缺失，取 created_at）
        """
        # 处理 properties_json → properties
        if "properties_json" in row:
            row["properties"] = _parse_properties_json(row.pop("properties_json"))
        elif "properties" not in row:
            row["properties"] = {}

        # 补充 version（v1 实体表无此字段）
        if "version" not in row or row.get("version") is None:
            row["version"] = "v0.1"

        # 补充 updated_at（v1 实体表无此字段）
        if "updated_at" not in row or row.get("updated_at") is None:
            row["updated_at"] = row.get("created_at") or _now_iso()

        # 补充 created_at（防御性处理）
        if "created_at" not in row or row.get("created_at") is None:
            row["created_at"] = _now_iso()

        return row

    @staticmethod
    def _map_relation(row: dict) -> dict:
        """将 v1 关系字段映射到 v2 格式

        变更：
        - source → source_entity
        - target → target_entity
        - 补充 properties 字段（若缺失）
        - 补充 created_at 字段（若缺失）
        """
        # source → source_entity
        if "source" in row and "source_entity" not in row:
            row["source_entity"] = row.pop("source")
        # target → target_entity
        if "target" in row and "target_entity" not in row:
            row["target_entity"] = row.pop("target")

        # 补充 properties
        if "properties" not in row or row.get("properties") is None:
            row["properties"] = {}

        # 补充 created_at
        if "created_at" not in row or row.get("created_at") is None:
            row["created_at"] = _now_iso()

        return row

    # ── Neo4j / ChromaDB 同步 ────────────────────────────────────────

    def _sync_to_neo4j(self, ontology_id: str, entities: list, relations: list):
        """将实体和关系同步至 Neo4j，失败仅记录警告"""
        try:
            from app.services.v2.legacy_extraction_bridge import LegacyExtractionBridge

            bridge = LegacyExtractionBridge()
            ent_dicts = [dict(e) for e in entities]
            rel_dicts = [dict(r) for r in relations]
            bridge.sync_to_neo4j(ontology_id, ent_dicts, rel_dicts)
            self.stats.neo4j_nodes += len(entities)
            self.stats.neo4j_edges += len(relations)
        except Exception as e:
            self.stats.warnings.append(f"Neo4j 同步失败（{ontology_id}）: {e}")
            logger.warning(f"Neo4j 同步失败（{ontology_id}）: {e}")

    def _sync_to_chroma(self, ontology_id: str, entities: list):
        """将实体同步至 ChromaDB 向量库，失败仅记录警告"""
        try:
            from app.services.v2.legacy_extraction_bridge import LegacyExtractionBridge

            bridge = LegacyExtractionBridge()
            ent_dicts = [dict(e) for e in entities]
            bridge.sync_to_chroma(ontology_id, ent_dicts)
            self.stats.chroma_docs += len(entities)
        except Exception as e:
            self.stats.warnings.append(f"ChromaDB 同步失败（{ontology_id}）: {e}")
            logger.warning(f"ChromaDB 同步失败（{ontology_id}）: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="v1→v2 数据迁移工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--v1-db", default="./ontoprompt.db", help="v1 SQLite 路径")
    parser.add_argument("--pg-url", required=True, help="v2 PostgreSQL 连接字符串")
    parser.add_argument("--dry-run", action="store_true", help="预演模式（不写入任何数据）")
    parser.add_argument("--report", default="", help="输出报告到 JSON 文件路径")
    args = parser.parse_args()

    migrator = V1ToV2Migrator(
        v1_db_path=args.v1_db,
        pg_url=args.pg_url,
        dry_run=args.dry_run,
    )
    stats = migrator.run()

    report = {
        "timestamp": datetime.now().isoformat(),
        "v1_db": args.v1_db,
        "dry_run": args.dry_run,
        "stats": stats.to_dict(),
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info(f"报告已写入: {args.report}")

    if stats.errors:
        logger.error(f"迁移完成，但有 {len(stats.errors)} 个错误")
        sys.exit(1)
    else:
        logger.info("迁移成功完成")
        sys.exit(0)


if __name__ == "__main__":
    main()
