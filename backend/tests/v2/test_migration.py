"""迁移脚本单元测试 — 使用内存 SQLite 模拟 v1 数据库

测试覆盖范围：
  - MigrationStats.to_dict() 字段完整性
  - dry_run=True 时不写入任何数据
  - 实体/关系数量统计正确
  - v1 数据库不存在时抛出 FileNotFoundError
  - PostgreSQL 不可用时记录 error 而非崩溃
  - to_dict 包含 neo4j_nodes 和 chroma_docs 字段
"""
import json
import sqlite3
import tempfile
import os
import pytest
from unittest.mock import MagicMock, patch


# ── 测试数据库初始化 ─────────────────────────────────────────────────

def create_v1_db(db_path: str):
    """创建最小化 v1 SQLite 数据库用于测试

    使用 v1 原始字段命名：
      - entities.properties_json (TEXT)   （v1 旧字段名）
      - relations.source / target          （v1 旧字段名）
    """
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            username TEXT,
            email TEXT,
            password_hash TEXT,
            role TEXT DEFAULT 'viewer',
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE ontology_projects (
            id TEXT PRIMARY KEY,
            name TEXT,
            domain TEXT,
            description TEXT,
            version TEXT DEFAULT 'v0.1',
            status TEXT DEFAULT 'draft',
            created_by TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            ontology_id TEXT,
            name_cn TEXT,
            name_en TEXT,
            type TEXT,
            description TEXT,
            confidence REAL DEFAULT 0.9,
            properties_json TEXT,
            version TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE relations (
            id TEXT PRIMARY KEY,
            ontology_id TEXT,
            source TEXT,
            target TEXT,
            type TEXT,
            confidence REAL DEFAULT 0.8,
            properties TEXT,
            created_at TEXT
        );
        CREATE TABLE prompts (
            id TEXT PRIMARY KEY,
            name TEXT,
            domain TEXT,
            content TEXT,
            version TEXT DEFAULT 'v1.0',
            created_by TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE model_configs (
            id TEXT PRIMARY KEY,
            name TEXT,
            provider TEXT,
            api_base TEXT,
            api_key_encrypted TEXT,
            models TEXT,
            created_by TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        INSERT INTO users VALUES (
            'u-1', 'admin', 'admin@test.com', 'hash', 'admin', 1,
            datetime('now'), datetime('now')
        );
        INSERT INTO ontology_projects VALUES (
            'o-1', '供应链图谱', '供应链', '测试本体', 'v0.1', 'draft', 'u-1',
            datetime('now'), datetime('now')
        );
        INSERT INTO entities VALUES (
            'e-1', 'o-1', '华为', 'Huawei', 'Organization', '科技公司', 0.95,
            '{}', 'v0.1', datetime('now'), datetime('now')
        );
        INSERT INTO entities VALUES (
            'e-2', 'o-1', '苹果', 'Apple', 'Organization', '科技公司', 0.92,
            '{}', 'v0.1', datetime('now'), datetime('now')
        );
        INSERT INTO relations VALUES (
            'r-1', 'o-1', 'e-1', 'e-2', 'COMPETES', 0.8, '{}', datetime('now')
        );
        INSERT INTO prompts VALUES (
            'p-1', '供应链提示词', '供应链', '提取供应链实体', 'v1.0', 'u-1',
            datetime('now'), datetime('now')
        );
    """)
    conn.commit()
    conn.close()


@pytest.fixture
def v1_db():
    """提供临时 v1 SQLite 数据库路径，测试结束后自动清理

    Windows 上 SQLite 文件在连接关闭前无法删除，使用 finalizer 兼容处理。
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    create_v1_db(db_path)
    yield db_path
    # 忽略 Windows 上因文件锁导致的删除失败
    try:
        os.unlink(db_path)
    except PermissionError:
        pass


# ── 测试用例 ─────────────────────────────────────────────────────────

def test_migration_stats_structure():
    """MigrationStats.to_dict() 包含所有必要的顶层字段"""
    from scripts.migrate_v1_to_v2 import MigrationStats

    stats = MigrationStats(users=5, ontologies=2, entities=20)
    d = stats.to_dict()

    # 顶层结构
    assert "migrated" in d
    assert "errors" in d
    assert "warnings" in d

    # migrated 子字段
    assert d["migrated"]["users"] == 5
    assert d["migrated"]["ontologies"] == 2
    assert d["migrated"]["entities"] == 20
    assert d["migrated"]["relations"] == 0
    assert d["migrated"]["files"] == 0
    assert d["migrated"]["prompts"] == 0
    assert d["migrated"]["model_configs"] == 0


def test_migration_dry_run(v1_db):
    """dry_run=True 时不调用 session.merge 或 session.commit"""
    from scripts.migrate_v1_to_v2 import V1ToV2Migrator

    # 构造 Mock PostgreSQL Session
    mock_session = MagicMock()
    mock_session.execute.return_value = MagicMock()

    migrator = V1ToV2Migrator(v1_db_path=v1_db, pg_url="postgresql://x", dry_run=True)
    # 直接注入 mock session，跳过真实 PostgreSQL 连接
    migrator._v2_session = mock_session

    try:
        with patch.object(migrator, "_connect_v2", return_value=None):
            migrator._connect_v1()
            migrator._migrate_users()
            migrator._migrate_prompts()
            migrator._migrate_ontologies_and_entities()
    finally:
        # 确保 SQLite 连接被关闭（Windows 文件锁兼容）
        migrator._cleanup()

    # dry_run 模式下绝对不写入
    mock_session.merge.assert_not_called()
    mock_session.commit.assert_not_called()

    # 统计数量仍然正确
    assert migrator.stats.users == 1
    assert migrator.stats.ontologies == 1
    assert migrator.stats.entities == 2
    assert migrator.stats.relations == 1
    assert migrator.stats.prompts == 1


def test_migration_counts_entities(v1_db):
    """正确统计 v1 数据库中的实体和关系数量（dry_run 模式）"""
    from scripts.migrate_v1_to_v2 import V1ToV2Migrator

    migrator = V1ToV2Migrator(v1_db_path=v1_db, pg_url="postgresql://x", dry_run=True)
    try:
        migrator._connect_v1()
        migrator._migrate_ontologies_and_entities()
    finally:
        # 确保 SQLite 连接被关闭（Windows 文件锁兼容）
        migrator._cleanup()

    assert migrator.stats.entities == 2
    assert migrator.stats.relations == 1
    assert migrator.stats.ontologies == 1


def test_migration_v1_db_not_found():
    """v1 数据库文件不存在时抛出 FileNotFoundError"""
    from scripts.migrate_v1_to_v2 import V1ToV2Migrator

    migrator = V1ToV2Migrator(
        v1_db_path="/nonexistent/path/that/does/not/exist.db",
        pg_url="x",
        dry_run=True,
    )
    with pytest.raises(FileNotFoundError):
        migrator._connect_v1()


def test_migration_reports_errors_on_bad_pg(v1_db):
    """PostgreSQL 连接串无效时，run() 记录 error 而不崩溃（返回统计）"""
    from scripts.migrate_v1_to_v2 import V1ToV2Migrator

    # 使用一个永远无法连接的假 URL
    migrator = V1ToV2Migrator(
        v1_db_path=v1_db,
        pg_url="postgresql://bad_user:bad_pass@127.0.0.1:19999/nonexistent_db",
    )
    stats = migrator.run()

    # 应有至少一条 error 记录
    assert len(stats.errors) > 0
    # run() 不应抛出异常，正常返回 MigrationStats
    assert stats is not None


def test_migration_stats_to_dict_complete():
    """to_dict 的 migrated 子字典包含 neo4j_nodes 和 chroma_docs 字段"""
    from scripts.migrate_v1_to_v2 import MigrationStats

    stats = MigrationStats(neo4j_nodes=10, chroma_docs=10)
    d = stats.to_dict()

    assert "neo4j_nodes" in d["migrated"]
    assert "chroma_docs" in d["migrated"]
    assert d["migrated"]["neo4j_nodes"] == 10
    assert d["migrated"]["chroma_docs"] == 10
