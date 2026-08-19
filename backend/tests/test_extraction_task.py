"""回归测试: 提取任务卡在 running/85% 的问题

修复 15d56bd:
- 保存阶段抛异常后, 异常处理器的 except: pass 把错误吞掉,
  如果此时 session 也坏了, 任务就永远停在 running/85%, 没有任何日志.
- 现在: 外层 except 记录日志, session 损坏时用新 session 兜底标记 failed.
"""
import uuid

import pytest

from app.models.extraction_task import ExtractionTask
from app.models.file import UploadedFile
from app.models.model_config import ModelConfig
from app.models.ontology import OntologyProject
from app.models.prompt import Prompt
from app.models.user import User
from app.services.auth_service import hash_password
from tests.conftest import TestSession


def _seed(db, admin_user) -> str:
    ontology = OntologyProject(
        id=str(uuid.uuid4()), name="回归本体", domain="供应链", created_by=admin_user.id,
    )
    prompt = Prompt(
        id=str(uuid.uuid4()), name="p", domain="供应链",
        content="提取实体", created_by=admin_user.id,
    )
    model = ModelConfig(
        id=str(uuid.uuid4()), name="m", provider="openai",
        api_base="http://localhost:11434/v1", created_by=admin_user.id,
    )
    file = UploadedFile(
        id=str(uuid.uuid4()), ontology_id=ontology.id,
        filename="doc.md", file_path="/tmp/doc.md", file_size=10,
        mime_type="text/markdown", converted_md="供应商A 与 供应商B 建立采购关系",
    )
    task = ExtractionTask(
        id=str(uuid.uuid4()), ontology_id=ontology.id,
        prompt_id=prompt.id, model_id=model.id,
        status="running",
        parameters={"model_name": "gpt-4o-mini"},
        progress={"stage": "saving results", "pct": 85},
    )
    db.add_all([ontology, prompt, model, file, task])
    db.commit()
    return task.id


def _valid_result():
    return {
        "entities": [
            {"name_cn": "供应商A", "name_en": "SupplierA", "type": "Supplier",
             "description": "供应商A", "properties": {"code": "SUP-001"}},
            {"name_cn": "供应商B", "name_en": "SupplierB", "type": "Supplier",
             "description": "供应商B", "properties": {"code": "SUP-002"}},
        ],
        "relations": [
            {"source": "供应商A", "target": "供应商B", "type": "RELATED",
             "confidence": 0.9},
        ],
        "logic_rules": [
            {"name_cn": "规则一", "formula": "score > 0", "linked_entities": ["供应商A"]},
        ],
        "actions": [
            {"name_cn": "动作一", "function_code": "def run(x):\n    return x",
             "linked_entities": ["供应商A"], "linked_logic_names": ["规则一"]},
        ],
    }


def _unserializable_result():
    """properties 里塞一个 json 无法序列化的值, 触发保存阶段 commit 失败."""
    result = _valid_result()
    result["entities"][0]["properties"]["boom"] = object()
    return result


@pytest.fixture
def admin_user(db):
    user = User(id=str(uuid.uuid4()), username="admin", email="admin@test.com",
                password_hash=hash_password("admin123"), role="admin")
    db.add(user); db.commit(); db.refresh(user)
    return user


@pytest.fixture
def patch_sessionlocal(monkeypatch):
    """让 run_extraction 里的 SessionLocal 指向测试库. 默认全部返回正常 session."""
    def _patch(factory):
        monkeypatch.setattr("app.database.SessionLocal", factory)
        return factory
    return _patch


def _normal_session():
    return TestSession()


class TestSaveFailureMarksTaskFailed:
    """保存阶段抛异常后任务必须被标记 failed, 而不是停在 running/85%."""

    def test_save_error_records_error_and_fails(self, db, admin_user, patch_sessionlocal, monkeypatch):
        task_id = _seed(db, admin_user)
        patch_sessionlocal(_normal_session)

        from app.services import llm_service
        monkeypatch.setattr(llm_service, "extract_ontology", lambda *a, **k: _unserializable_result())
        monkeypatch.setattr(llm_service, "infer_relations", lambda *a, **k: [])

        from app.tasks.extraction import run_extraction
        run_extraction(task_id)

        s = TestSession()
        try:
            task = s.query(ExtractionTask).filter(ExtractionTask.id == task_id).first()
            assert task.status == "failed", "保存阶段失败后任务必须落为 failed, 而非卡在 running/85%"
            assert task.error, "必须记录原始错误, 不能被 except: pass 吞掉"
        finally:
            s.close()


class TestBrokenSessionFallback:
    """主 session 损坏时, 必须用新 session 兜底标记 failed."""

    def test_fresh_session_marks_failed_when_primary_session_broken(self, db, admin_user, patch_sessionlocal, monkeypatch):
        task_id = _seed(db, admin_user)

        class BrokenSession:
            def __init__(self, real):
                self._real = real

            def __getattr__(self, name):
                return getattr(self._real, name)

            def rollback(self):
                raise RuntimeError("session is broken")

        calls = {"n": 0}

        def factory():
            calls["n"] += 1
            if calls["n"] == 1:
                return BrokenSession(TestSession())
            return TestSession()

        patch_sessionlocal(factory)

        from app.services import llm_service
        monkeypatch.setattr(llm_service, "extract_ontology", lambda *a, **k: _unserializable_result())
        monkeypatch.setattr(llm_service, "infer_relations", lambda *a, **k: [])

        from app.tasks.extraction import run_extraction
        run_extraction(task_id)

        s = TestSession()
        try:
            task = s.query(ExtractionTask).filter(ExtractionTask.id == task_id).first()
            assert task.status == "failed", "主 session 损坏时也必须标记 failed"
            assert task.error
        finally:
            s.close()
