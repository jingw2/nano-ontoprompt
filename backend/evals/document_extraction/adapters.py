"""Extraction adapters for the document-extraction eval. The real adapter
calls the app's actual, unmodified extraction code — no database, no
Celery — exactly the same extract_ontology() call
backend/app/tasks/extraction.py::run_extraction makes internally."""
import os

# General-purpose extraction system prompt. Production prompts are stored
# per-ontology in the `prompts` DB table; this eval has no DB dependency, so
# it uses an equivalent literal prompt instead of reading one from the DB.
BASE_PROMPT = """你是一个专业的本体（Ontology）提取助手。你的任务是从企业内部文档
（战略文件、管理制度、诊疗规范等）中提取结构化的本体信息，包括：
- entities：文档中的核心概念/类型实体
- relations：概念之间的关系
- logic_rules：文档中的条件判断规则（如 IF-THEN 规则、阈值规则）
- actions：文档描述的可执行操作或流程步骤

提取时不要遗漏文档中明确给出的规则和阈值，规则的 name_cn 应清楚概括规则内容
（例如"信用评分授信额度规则"），不要使用"规则1""处理逻辑"这类模糊命名。"""


class DeepSeekExtractionAdapter:
    """Calls the real app.services.llm_service.extract_ontology() against a
    real DeepSeek model — no mocking, no fixtures, real network calls."""

    def __init__(self, model_name: str = "deepseek-chat"):
        self._model_name = model_name

    def extract(self, source_file: str) -> dict:
        from app.services.document_service import convert_document
        from app.services.llm_service import extract_ontology
        from app.tasks.extraction import CONCEPT_INSTANCE_DIRECTIVE

        conversion = convert_document(source_file)
        if not conversion.ok:
            raise RuntimeError(f"convert_document failed for {source_file}: {conversion.error}")

        prompt_content = BASE_PROMPT + CONCEPT_INSTANCE_DIRECTIVE
        config_dict = {
            "provider": "deepseek",
            "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
            "api_base": "https://api.deepseek.com",
        }
        return extract_ontology(conversion.content, prompt_content, config_dict, self._model_name)


class FakeExtractionAdapter:
    """Deterministic, no-network adapter for testing the eval runner's own
    wiring/aggregation logic (Task 4) — never used for real quality scoring."""

    def __init__(self, canned_result: dict | None = None):
        self._canned_result = canned_result or {
            "entities": [], "relations": [], "logic_rules": [], "actions": [], "instances": [],
        }

    def extract(self, source_file: str) -> dict:
        return dict(self._canned_result)
