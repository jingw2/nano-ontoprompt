import atexit
import os
import tempfile
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# 应用级引擎（lifespan/_seed_db 使用 app.database.SessionLocal）指向独立的临时
# SQLite，与测试会话引擎分开，且不依赖仓库根目录遗留的 ontoprompt.db（其 schema
# 早于 P1A，缺少 security_domain_id）。环境变量必须在导入 app.main 之前设置。
_app_db_fd, _app_db_path = tempfile.mkstemp(prefix="ontoprompt_app_", suffix=".db")
os.close(_app_db_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_app_db_path}"

from app.main import app  # noqa: E402
from app.database import Base, engine as app_engine  # noqa: E402
from app.deps import get_db  # noqa: E402
from app.limiter import limiter  # noqa: E402
from app.services.auth_service import hash_password  # noqa: E402
from app.models.user import User  # noqa: E402
import uuid  # noqa: E402

# 测试环境关闭限流，避免连续登录/注册被 429
limiter.enabled = False

# 每次 pytest 运行使用独立的临时 SQLite, 避免并发运行互相锁库
_db_fd, _db_path = tempfile.mkstemp(prefix="ontoprompt_test_", suffix=".db")
os.close(_db_fd)

TEST_DB = f"sqlite:///{_db_path}"
engine = create_engine(TEST_DB, connect_args={"check_same_thread": False})
TestSession = sessionmaker(bind=engine)


@atexit.register
def _cleanup_test_db():
    engine.dispose()
    try:
        os.unlink(_db_path)
    except OSError:
        pass
    app_engine.dispose()
    try:
        os.unlink(_app_db_path)
    except OSError:
        pass


def _create_sqlite_compatible_tables(bind):
    """Create only the SQLite-compatible tables in the shared metadata.

    The centralized registry (`app.models.load_all_models`) also contains
    PostgreSQL-only tables (JSONB columns, PostgreSQL regex CHECKs, pgcrypto
    integrity checks) whose DDL cannot compile or execute on SQLite.  Those
    tables are verified against real PostgreSQL by the migration suites; the
    unit harness must not attempt to create them here.
    """
    from sqlalchemy.schema import CreateTable

    created = []
    for table in Base.metadata.sorted_tables:
        try:
            CreateTable(table).compile(dialect=bind.dialect)
            table.create(bind=bind, checkfirst=True)
            created.append(table)
        except Exception:
            continue
    return created


# 应用引擎的临时库：一次性建立当前里程碑的 SQLite 兼容 schema，使 lifespan 的
# _seed_db 在不依赖任何遗留本地数据库文件的前提下正常工作。
_create_sqlite_compatible_tables(app_engine)


@pytest.fixture(autouse=True)
def setup_db():
    # A disposable database and environment are established here, before any
    # app startup code runs; startup never creates tables or stamps versions
    # (E0-DB contract).  PostgreSQL-only tables are exercised through the
    # disposable verified-0003 harness in tests/agent/test_0003_full_migration.py.
    created = _create_sqlite_compatible_tables(engine)
    yield
    for table in reversed(created):
        table.drop(bind=engine, checkfirst=True)

@pytest.fixture
def db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()

@pytest.fixture
def client(db):
    def override_get_db():
        yield db
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

@pytest.fixture
def admin_user(db):
    user = User(id=str(uuid.uuid4()), username="admin", email="admin@test.com",
                password_hash=hash_password("admin123"), role="admin")
    db.add(user); db.commit(); db.refresh(user)
    return user

@pytest.fixture
def editor_user(db):
    user = User(id=str(uuid.uuid4()), username="editor", email="editor@test.com",
                password_hash=hash_password("editor123"), role="editor")
    db.add(user); db.commit(); db.refresh(user)
    return user

@pytest.fixture
def admin_token(client, admin_user):
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    return r.json()["data"]["access_token"]

@pytest.fixture
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}

@pytest.fixture
def ontology(client, auth_headers, db):
    r = client.post("/api/v1/ontologies", json={"name": "测试本体", "domain": "供应链"}, headers=auth_headers)
    return r.json()["data"]
