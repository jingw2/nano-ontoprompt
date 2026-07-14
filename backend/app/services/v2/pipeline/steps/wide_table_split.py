"""宽表拆分 Step — LLM 辅助分析 + 用户确认"""
from __future__ import annotations
import json
import logging
from app.services.v2.pipeline.base import PipelineStep, PipelineContext

logger = logging.getLogger(__name__)


class WideTableSplitStep(PipelineStep):
    """
    将宽表(列很多)拆分为多个规范化表。

    spec 选项:
      split_config: dict — 用户确认的拆分配置 {table_name: [col1, col2, ...]}
      suggest_only: bool (默认: False) — True 时只返回建议, 不实际拆分
      wide_threshold: int (默认: 80) — 列数达到该值视为宽表 (PRD 标准)

    spec 中无 split_config 时请求 LLM 给出建议,
    并保存到 ctx.meta["split_suggestion"]。
    """

    def run(self, ctx: PipelineContext, data: list[dict]) -> list[dict]:
        if not data:
            return data

        spec = ctx.spec.get("wide_table_split", {})
        split_config = spec.get("split_config", {})
        suggest_only = spec.get("suggest_only", False)
        wide_threshold = spec.get("wide_threshold", 80)

        columns = list(data[0].keys()) if data else []
        col_count = len(columns)

        # 非宽表则跳过
        if col_count < wide_threshold and not split_config:
            ctx.meta["wide_table_split"] = {"skipped": True, "col_count": col_count}
            return data

        # 无 split_config 时请求 LLM 建议
        if not split_config:
            suggestion = self._suggest_split(columns, data[:3])
            ctx.meta["split_suggestion"] = suggestion
            ctx.meta["wide_table_split"] = {
                "suggested": True,
                "col_count": col_count,
                "suggestion": suggestion,
            }
            if suggest_only:
                return data  # 只给建议, 不实际拆分

            # 自动执行 (无需用户确认) — 测试/自动化用
            split_config = suggestion.get("split_config", {})
            if not split_config:
                return data

        # 执行实际拆分
        from app.services.v2.duckdb_service import DuckDBService
        svc = DuckDBService()
        split_result = svc.split_wide_table(data, split_config)

        # 拆分结果存入 ctx.meta, 第一个表作为主数据返回
        ctx.meta["wide_table_split"] = {
            "executed": True,
            "tables": {name: len(rows) for name, rows in split_result.items()},
        }
        ctx.meta["split_tables"] = split_result

        # 第一个拆分表作为主 output 返回
        first_table = next(iter(split_result.values()), data)
        return first_table

    def _suggest_split(self, columns: list[str], sample_rows: list[dict]) -> dict:
        """请求 LLM 给出拆分建议 (失败时默认对半拆分)"""
        try:
            prompt = f"""请分析以下表的列清单, 给出规范化拆分方案。

列清单: {json.dumps(columns, ensure_ascii=False)}
样本数据: {json.dumps(sample_rows[:2], ensure_ascii=False)[:500]}

请将各列按逻辑关联分组, 以 JSON 返回:
{{"split_config": {{"table1": ["col1", "col2"], "table2": ["col3", "col4"]}}}}"""

            # 优先使用用户配置的模型
            from app.services.v2.pipeline.steps.md_to_structured import _get_first_model, _call_with_model
            model_cfg = _get_first_model()
            if model_cfg:
                messages = [
                    {"role": "system", "content": "You are a data modeling expert. Return valid JSON only."},
                    {"role": "user", "content": prompt},
                ]
                raw = _call_with_model(model_cfg, messages)
                if raw:
                    import re
                    match = re.search(r'\{.*\}', raw, re.DOTALL)
                    if match:
                        return json.loads(match.group())
        except Exception as e:
            logger.info(f"LLM split suggestion failed (using fallback): {e}")

        # 回退: 对半拆分
        mid = len(columns) // 2
        return {
            "split_config": {
                "table_a": columns[:mid],
                "table_b": columns[mid:],
            }
        }
