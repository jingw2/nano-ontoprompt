"""回归测试: 审计任务卡在 running 的问题

修复前与提取任务同构的隐患: 异常后 db.commit() 失败会被 except: pass 吞掉,
session 损坏时任务永远停在 running, 没有任何日志.
现在: 记录日志, session 损坏时用新 session 兜底标记 failed.
"""
import uuid

import pytest

from app.models.audit_task import AuditTask
from app.models.model_config import ModelConfig
from app.models.ontology import OntologyProject
from app.models.user import User
from app.services.auth_service import hash_password
from tests.conftest import TestSession


def _seed(db, admin_user) -> str:
    ontology = OntologyProject(
        id=str(uuid.uuid4()), name="审计本体", domain="供应链", created_by=admin_user.id,
    )
    model = ModelConfig(
        id=str(uuid.uuid4()), name="m", provider="openai",
        api_base="http://localhost:11434/v1", created_by=admin_user.id,
    )
    task = AuditTask(
        id=str(uuid.uuid4()), ontology_id=ontology.id, model_id=model.id,
        model_name="gpt-4o-mini", status="running",
        progress={"stage": "running react agent", "pct": 30},
    )
    db.add_all([ontology, model, task])
    db.commit()
    return task.id


@pytest.fixture
def admin_user(db):
    user = User(id=str(uuid.uuid4()), username="admin", email="admin@test.com",
                password_hash=hash_password("admin123"), role="admin")
    db.add(user); db.commit(); db.refresh(user)
    return user


@pytest.fixture
def patch_sessionlocal(monkeypatch):
    def _patch(factory):
        monkeypatch.setattr("app.database.SessionLocal", factory)
        return factory
    return _patch


def _boom(*a, **k):
    raise RuntimeError("LLM audit failed")


def _run_audit(monkeypatch, task_id):
    from app.services import audit_service
    monkeypatch.setattr(audit_service, "run_react_audit", _boom)
    from app.tasks.audit import run_audit
    run_audit(task_id)


class TestAuditFailureMarksTaskFailed:
    def test_audit_error_marks_failed(self, db, admin_user, patch_sessionlocal, monkeypatch):
        task_id = _seed(db, admin_user)
        patch_sessionlocal(TestSession)
        _run_audit(monkeypatch, task_id)

        s = TestSession()
        try:
            task = s.query(AuditTask).filter(AuditTask.id == task_id).first()
            assert task.status == "failed", "审计失败后任务必须落为 failed, 而非卡在 running"
            assert task.error
        finally:
            s.close()


class TestAuditBrokenSessionFallback:
    def test_fresh_session_marks_failed_when_primary_session_broken(self, db, admin_user, patch_sessionlocal, monkeypatch):
        task_id = _seed(db, admin_user)

        class BrokenSession:
            def __init__(self, real):
                self._real = real

            def __getattr__(self, name):
                return getattr(self._real, name)

            def commit(self):
                raise RuntimeError("session is broken")

        calls = {"n": 0}

        def factory():
            calls["n"] += 1
            if calls["n"] == 1:
                return BrokenSession(TestSession())
            return TestSession()

        patch_sessionlocal(factory)
        _run_audit(monkeypatch, task_id)

        s = TestSession()
        try:
            task = s.query(AuditTask).filter(AuditTask.id == task_id).first()
            assert task.status == "failed", "主 session 损坏时也必须标记 failed"
            assert task.error
        finally:
            s.close()
