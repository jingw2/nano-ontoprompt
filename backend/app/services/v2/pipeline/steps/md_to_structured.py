"""Markdown → 구조화 JSON 추출 Step"""
from __future__ import annotations
import json
import re
import logging
from app.services.v2.pipeline.base import PipelineStep, PipelineContext

logger = logging.getLogger(__name__)


def _get_first_model(model_id: str | None = None):
    """DB 에서 사용할 LLM 모델 설정 반환"""
    try:
        from app.database import SessionLocal
        from app.models.model_config import ModelConfig
        db = SessionLocal()
        try:
            query = db.query(ModelConfig).filter(ModelConfig.config_type == "llm")
            if model_id:
                selected = query.filter(ModelConfig.id == model_id).first()
                if selected:
                    return selected
            return query.order_by(ModelConfig.updated_at.desc()).first()
        finally:
            db.close()
    except Exception:
        return None


def _call_with_model(model_config, messages: list[dict]) -> str | None:
    """사용자 설정 모델을 사용해 LLM 호출"""
    if not model_config:
        return None
    try:
        from app.services import encryption_service
        from app.services.llm_service import _call_llm
        api_key = encryption_service.decrypt(model_config.api_key_encrypted) if model_config.api_key_encrypted else ""
        model_name = (model_config.models or ["gpt-3.5-turbo"])[0]
        return _call_llm(
            model_config.provider, api_key, model_config.api_base,
            model_name, messages
        )
    except Exception as e:
        logger.info(f"LLM call failed: {e}")
        return None


class MarkdownToStructuredStep(PipelineStep):
    """
    Markdown 텍스트에서 구조화 필드를 추출합니다.

    spec 옵션:
      target_schema: dict  — 추출할 필드 정의 {field_name: "description"}（없으면 자동 추론）
      model_id: str        — 사용할 LLM 모델 ID
      prompt_template: str — 커스텀 프롬프트

    input:  row에 "markdown_text" 필드 포함
    output: 구조화 필드 + extraction_method 필드 추가
    """

    EXTRACT_PROMPT = """다음 문서에서 아래 필드를 추출하세요. JSON으로만 반환하세요.

필드 목록:
{schema}

문서:
{text}

출력 형식 (JSON만, 설명 없이):
{{"field1": "value1", "field2": "value2"}}"""

    AUTO_SCHEMA_PROMPT = """다음 문서를 분석하고, 이 문서 유형에 가장 유용한 구조화 레코드를 추출하세요.
각 레코드는 문서 안의 한 행, 규칙, 섹션, 항목 또는 이벤트를 나타내야 합니다.
필드 이름은 영문 snake_case로, 값은 문서에서 실제로 찾을 수 있는 것만 포함하세요.

문서:
{text}

출력 형식 (JSON만, 설명 없이):
[{{"record_id": "stable_id", "row_type": "table_row|rule|section|item", "field1": "extracted_value1"}}]"""

    def run(self, ctx: PipelineContext, data: list[dict]) -> list[dict]:
        spec = ctx.spec.get("md_to_structured", {})
        target_schema = spec.get("target_schema", {})
        model_id = spec.get("model_id", "")

        model_config = _get_first_model(model_id)

        # target_schema 없으면 자동 추론 또는 규칙 기반 추출
        if not target_schema:
            if not spec.get("auto_extract") and not spec.get("rule_based"):
                ctx.meta["md_to_structured"] = {
                    "method": "passthrough", "processed": len(data), "success": len(data)
                }
                return data
            sample_md = next((r.get("markdown_text", "") for r in data if r.get("markdown_text")), "")
            if sample_md and model_config and spec.get("auto_extract"):
                # LLM으로 자동 추출 시도
                result = self._auto_extract_with_llm(data, model_config)
                if result:
                    ctx.meta["md_to_structured"] = {
                        "method": "llm_auto", "processed": len(data), "success": len(result)
                    }
                    return result
            # 규칙 기반 폴백
            return self._rule_based_extract(data, ctx)

        # target_schema 있으면 LLM 필드 추출
        result, success = [], 0
        for row in data:
            md_text = row.get("markdown_text", "")
            if not md_text:
                result.append(row)
                continue
            try:
                extracted = self._extract(md_text, target_schema, model_config)
                row = dict(row)
                row.update(extracted)
                row["extraction_method"] = "llm_schema"
                row["structured_extraction_ok"] = True
                success += 1
            except Exception as e:
                logger.warning(f"MarkdownToStructured failed: {e}")
                row = dict(row)
                row["structured_extraction_ok"] = False
                row["structured_extraction_error"] = str(e)
            result.append(row)

        ctx.meta["md_to_structured"] = {
            "method": "llm_schema",
            "processed": len(data),
            "success": success,
            "schema_fields": list(target_schema.keys()),
        }
        return result

    # ── LLM 자동 추출（target_schema 없을 때）───────────────────────────────

    def _auto_extract_with_llm(self, data: list[dict], model_config) -> list[dict] | None:
        """LLM에게 schema 추론 + 추출을 한 번에 요청"""
        result = []
        for row in data:
            md = row.get("markdown_text", "")
            if not md:
                result.append(row)
                continue
            resp = _call_with_model(model_config, [
                {"role": "system", "content": "You are a structured data extraction expert. Return valid JSON only."},
                {"role": "user", "content": self.AUTO_SCHEMA_PROMPT.format(text=md[:4000])},
            ])
            if resp is None:
                return None  # LLM 실패 → 규칙 기반으로 폴백
            try:
                text = resp.strip()
                if "```" in text:
                    text = re.search(r'```(?:json)?\s*([\s\S]+?)```', text)
                    text = text.group(1).strip() if text else resp
                extracted = json.loads(text)
                records = extracted if isinstance(extracted, list) else [extracted]
                for idx, item in enumerate(records):
                    if not isinstance(item, dict):
                        continue
                    out = dict(row)
                    out.update({str(k): str(v) for k, v in item.items()})
                    out.setdefault("record_id", f"{row.get('filename') or row.get('source_file') or 'doc'}:llm:{idx + 1}")
                    out["extraction_method"] = "llm_auto"
                    result.append(out)
                continue
            except Exception:
                row = dict(row)
                row["extraction_method"] = "llm_auto_parse_error"
            result.append(row)
        return result

    # ── LLM 필드 추출（target_schema 있을 때）───────────────────────────────

    def _extract(self, md_text: str, schema: dict, model_config) -> dict:
        return self._extract_fields(md_text, schema, model_config)

    def _extract_fields(self, md_text: str, schema: dict, model_config) -> dict:
        """LLM으로 target_schema에 따라 필드 추출"""
        schema_str = "\n".join(f"- {k}: {v}" for k, v in schema.items())
        resp = _call_with_model(model_config, [
            {"role": "system", "content": "You are a structured data extraction assistant. Return valid JSON only."},
            {"role": "user", "content": self.EXTRACT_PROMPT.format(schema=schema_str, text=md_text[:4000])},
        ])
        if resp is None:
            return {k: "" for k in schema}
        try:
            text = resp.strip()
            if "```" in text:
                m = re.search(r'```(?:json)?\s*([\s\S]+?)```', text)
                text = m.group(1).strip() if m else text
            return json.loads(text)
        except Exception:
            return {k: "" for k in schema}

    # ── 규칙 기반 폴백 ──────────────────────────────────────────────────────

    def _rule_based_extract(self, data: list[dict], ctx: PipelineContext) -> list[dict]:
        """LLM 없을 때 정규식으로 구조화 정보 추출 (PRD: 규칙/엔터티/수치 탐지)"""
        result = []
        for row in data:
            md = row.get("markdown_text", "")
            if not md:
                result.append(row)
                continue

            base = dict(row)
            base.pop("content", None)
            source_file = base.get("source_file") or base.get("filename") or "document"
            doc_summary = md[:200].replace("\n", " ").strip()
            section_titles = re.findall(r'^#{1,6}\s+(.+)$', md, re.MULTILINE)
            common = {
                "source_file": source_file,
                "section_count": len(section_titles),
                "sections": ", ".join(section_titles[:6]),
                "doc_summary": doc_summary,
                "extraction_method": "rule_based",
            }
            start_index = len(result)

            # ① IF-THEN 규칙 추출
            rules = re.findall(
                r'IF\s+(.+?)\s+THEN\s+(.+?)(?=\n|$)',
                md, re.IGNORECASE | re.MULTILINE
            )
            for idx, (condition, action) in enumerate(rules, start=1):
                out = dict(base)
                out.update(common)
                out.update({
                    "record_id": f"{source_file}:rule:{idx}",
                    "row_type": "rule",
                    "rule_index": idx,
                    "rule_count": len(rules),
                    "condition": condition.strip(),
                    "action": action.strip(),
                })
                result.append(out)

            # ② Markdown 표格拆成结构化行
            table_rows = self._extract_table_records(md, str(source_file))
            for item in table_rows:
                out = dict(base)
                out.update(common)
                out.update(item)
                result.append(out)

            # ③ Markdown/PPTX/DOCX 章节拆行。表格/规则之外仍保留章节语义。
            section_rows = self._extract_section_records(md, str(source_file), limit=max(10, 30 - len(table_rows) - len(rules)))
            for item in section_rows:
                out = dict(base)
                out.update(common)
                out.update(item)
                result.append(out)

            # ④ 중국어 기업/조직명 추출
            org_names = re.findall(
                r'[一-龥]{2,10}(?:公司|集团|科技|物流|铝业|五金|包装|原材料)',
                md
            )
            thresholds = re.findall(r'(\d[\d,\.]+)\s*(?:万元?|吨|件|小时|天|%|个月|季度)', md)
            numeric_kvs = re.findall(r'\|\s*([^\|]+)\s*\|\s*(\d[\d,\.]*)\s*\|', md)
            enrich = {
                "organizations": ", ".join(list(dict.fromkeys(org_names))[:8]) if org_names else "",
                "thresholds": ", ".join(thresholds[:6]) if thresholds else "",
                "numeric_fields": json.dumps({k.strip(): v for k, v in numeric_kvs[:8]}, ensure_ascii=False) if numeric_kvs else "",
            }
            for out in result[start_index:]:
                out.update({k: v for k, v in enrich.items() if v})

            if not rules and not table_rows and not section_rows:
                out = dict(base)
                out.update(common)
                out.update({
                    "record_id": f"{source_file}:document:1",
                    "row_type": "document",
                    "rule_count": 0,
                })
                out.update({k: v for k, v in enrich.items() if v})
                result.append(out)

        ctx.meta["md_to_structured"] = {
            "method": "rule_based",
            "processed": len(data),
            "success": len(result),
            "emitted_records": len(result),
        }
        return result

    def _split_table_cells(self, line: str) -> list[str]:
        return [cell.strip() for cell in line.strip().strip("|").split("|")]

    def _is_table_separator(self, cells: list[str]) -> bool:
        return bool(cells) and all(re.match(r"^:?-{3,}:?$", cell.strip()) for cell in cells)

    def _extract_table_records(self, md: str, source_file: str) -> list[dict]:
        records: list[dict] = []
        lines = md.splitlines()
        current_section = ""
        table_idx = 0
        i = 0
        while i < len(lines):
            heading = re.match(r"^(#{1,6})\s+(.+)$", lines[i])
            if heading:
                current_section = heading.group(2).strip()
                i += 1
                continue
            if lines[i].lstrip().startswith("|") and i + 1 < len(lines):
                header = self._split_table_cells(lines[i])
                separator = self._split_table_cells(lines[i + 1])
                if self._is_table_separator(separator):
                    table_idx += 1
                    i += 2
                    row_idx = 0
                    while i < len(lines) and lines[i].lstrip().startswith("|"):
                        cells = self._split_table_cells(lines[i])
                        if len(cells) == len(header):
                            row_idx += 1
                            out = {
                                "record_id": f"{source_file}:table:{table_idx}:row:{row_idx}",
                                "row_type": "table_row",
                                "table_index": table_idx,
                                "table_row_index": row_idx,
                                "section_title": current_section,
                            }
                            for col_idx, col in enumerate(header):
                                key = col or f"col_{col_idx + 1}"
                                out[key] = cells[col_idx]
                            records.append(out)
                        i += 1
                    continue
            i += 1
        return records

    def _extract_section_records(self, md: str, source_file: str, limit: int = 30) -> list[dict]:
        records: list[dict] = []
        matches = list(re.finditer(r"^(#{1,6})\s+(.+)$", md, flags=re.MULTILINE))
        if not matches:
            text = md.strip()
            return [{
                "record_id": f"{source_file}:section:1",
                "row_type": "section",
                "section_index": 1,
                "section_title": PathLikeTitle(source_file),
                "section_text": text[:2000],
            }] if text else []
        for idx, match in enumerate(matches[:limit], start=1):
            start = match.end()
            end = matches[idx].start() if idx < len(matches) else len(md)
            text = md[start:end].strip()
            if not text:
                continue
            records.append({
                "record_id": f"{source_file}:section:{idx}",
                "row_type": "section",
                "section_index": idx,
                "section_title": match.group(2).strip(),
                "section_text": text[:2000],
            })
        return records


def PathLikeTitle(value: str) -> str:
    return str(value).rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
