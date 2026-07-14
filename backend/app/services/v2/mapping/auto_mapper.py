"""LLM 辅助 Ontology Mapping 自动映射建议服务"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class FieldMappingSuggestion:
    column_name: str          # Curated Dataset 的列名
    property_name: str        # 映射到 Ontology 属性名
    property_type: str        # 属性类型
    confidence: float         # 0.0 ~ 1.0
    reason: str               # 映射理由


@dataclass
class MappingSuggestion:
    entity_class: str                        # 建议的实体类名（英文）
    entity_class_cn: str                     # 中文名
    description: str                         # 实体描述
    field_mappings: list[FieldMappingSuggestion]
    primary_key_column: str | None           # 主键列名


@dataclass
class LinkSuggestion:
    source_entity_class: str
    target_entity_class: str
    relation_type: str
    source_fk_column: str      # 外键列（在 source dataset 中）
    target_pk_column: str      # 主键列（在 target dataset 中）
    confidence: float


class AutoMapper:
    """基于 LLM 的自动映射建议引擎"""

    def __init__(self, db: Session):
        self._db = db

    def suggest_field_mapping(
        self,
        dataset_name: str,
        columns: list[str],
        sample_rows: list[dict],
        ontology_domain: str = "",
    ) -> MappingSuggestion:
        """
        根据 Curated Dataset 的列信息，使用 LLM 建议实体类型和属性映射。
        LLM 不可用时使用基于规则的回退策略。
        """
        try:
            return self._llm_suggest(dataset_name, columns, sample_rows, ontology_domain)
        except Exception as e:
            logger.info(f"LLM 映射建议失败，使用规则回退: {e}")
            return self._rule_based_suggest(dataset_name, columns)

    def suggest_links(
        self,
        src_dataset_name: str,
        src_columns: list[str],
        tgt_dataset_name: str,
        tgt_columns: list[str],
    ) -> list[LinkSuggestion]:
        """
        检测两个 Dataset 间的外键关联，建议 Link 映射。
        策略：列名后缀为 _id 且与目标表主键匹配时建议关联。
        """
        links = []
        tgt_name_lower = tgt_dataset_name.lower().rstrip("s")  # 简单去复数

        for col in src_columns:
            col_lower = col.lower()
            # 外键候选：列名包含目标表名且以 _id 结尾
            if col_lower.endswith("_id") and tgt_name_lower in col_lower:
                tgt_pk = next(
                    (c for c in tgt_columns if c.lower() in ("id", f"{tgt_name_lower}_id")),
                    tgt_columns[0] if tgt_columns else "id"
                )
                links.append(LinkSuggestion(
                    source_entity_class=self._to_class_name(src_dataset_name),
                    target_entity_class=self._to_class_name(tgt_dataset_name),
                    relation_type=f"HAS_{tgt_name_lower.upper()}",
                    source_fk_column=col,
                    target_pk_column=tgt_pk,
                    confidence=0.85,
                ))

        return links

    # ── 私有方法 ─────────────────────────────────────────────────────

    def _llm_suggest(
        self,
        dataset_name: str,
        columns: list[str],
        sample_rows: list[dict],
        ontology_domain: str,
    ) -> MappingSuggestion:
        """调用 LLM 生成映射建议"""
        sample_str = json.dumps(sample_rows[:3], ensure_ascii=False, indent=2)
        prompt = f"""请为以下数据集设计 Ontology 映射方案，返回 JSON。

数据集名称：{dataset_name}
领域：{ontology_domain or "通用"}
列名：{json.dumps(columns, ensure_ascii=False)}
样本数据：
{sample_str}

要求：
1. 建议一个合适的实体类名（英文驼峰，如 SupplierOrder）
2. 为每列建议属性名（英文小写下划线）和类型（string/integer/float/boolean/datetime）
3. 识别主键列

返回格式（JSON，不要其他文字）：
{{
  "entity_class": "ClassName",
  "entity_class_cn": "中文名",
  "description": "实体描述",
  "primary_key_column": "id_column_name",
  "field_mappings": [
    {{"column": "col_name", "property": "prop_name", "type": "string", "confidence": 0.9, "reason": "理由"}}
  ]
}}"""

        from app.services import llm_service
        from app.services.model_config_selector import llm_call_kwargs, select_llm_model_config
        call_kwargs = llm_call_kwargs(select_llm_model_config(
            self._db,
            purpose_tags=("Ontology映射", "Mapping建议", "自动映射"),
            allow_vlm=False,
        ))
        if not call_kwargs:
            raise RuntimeError("No LLM model config available for mapping suggestion")
        raw = llm_service._call_llm(
            **call_kwargs,
            messages=[
                {"role": "system", "content": "你是数据建模专家，输出 JSON。"},
                {"role": "user", "content": prompt},
            ]
        )

        data = json.loads(raw) if isinstance(raw, str) else raw
        field_mappings = [
            FieldMappingSuggestion(
                column_name=fm["column"],
                property_name=fm["property"],
                property_type=fm.get("type", "string"),
                confidence=float(fm.get("confidence", 0.8)),
                reason=fm.get("reason", ""),
            )
            for fm in data.get("field_mappings", [])
        ]
        return MappingSuggestion(
            entity_class=data.get("entity_class", self._to_class_name(dataset_name)),
            entity_class_cn=data.get("entity_class_cn", dataset_name),
            description=data.get("description", ""),
            field_mappings=field_mappings,
            primary_key_column=data.get("primary_key_column"),
        )

    def _rule_based_suggest(self, dataset_name: str, columns: list[str]) -> MappingSuggestion:
        """规则回退：直接将列名映射为同名属性"""
        entity_class = self._to_class_name(dataset_name)
        pk_col = next(
            (c for c in columns if c.lower() in ("id", f"{dataset_name.lower()}_id")),
            columns[0] if columns else None
        )
        field_mappings = [
            FieldMappingSuggestion(
                column_name=col,
                property_name=col.lower().replace(" ", "_"),
                property_type=self._guess_type(col),
                confidence=0.6,
                reason="规则推断",
            )
            for col in columns
        ]
        return MappingSuggestion(
            entity_class=entity_class,
            entity_class_cn=dataset_name,
            description=f"{dataset_name} 实体",
            field_mappings=field_mappings,
            primary_key_column=pk_col,
        )

    @staticmethod
    def _to_class_name(name: str) -> str:
        """snake_case/kebab-case → CamelCase"""
        import re
        parts = re.split(r'[_\-\s]+', name)
        return "".join(p.capitalize() for p in parts if p)

    @staticmethod
    def _guess_type(col_name: str) -> str:
        col = col_name.lower()
        if any(s in col for s in ("date", "time", "at", "created", "updated")):
            return "datetime"
        if any(s in col for s in ("id", "count", "num", "age", "qty", "quantity")):
            return "integer"
        if any(s in col for s in ("price", "amount", "score", "rate", "pct", "percent")):
            return "float"
        if any(s in col for s in ("is_", "has_", "flag", "active", "enabled")):
            return "boolean"
        return "string"
