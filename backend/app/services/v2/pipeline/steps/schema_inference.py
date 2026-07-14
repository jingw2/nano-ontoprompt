"""Schema 自动推断 Step — timestamp 检测 + 多样本投票"""
from __future__ import annotations
import re
from app.services.v2.pipeline.base import PipelineStep, PipelineContext

# 判别日期/时间戳的正则模式 (PRD: string/int/float/timestamp/bool)
_DATE_RE = re.compile(
    r'^\d{4}[-/]\d{1,2}[-/]\d{1,2}'        # 2024-01-15 | 2024/1/15
    r'|^\d{1,2}[-/]\d{1,2}[-/]\d{4}'        # 15/01/2024
    r'|^\d{4}\d{2}\d{2}$'                   # 20240115
    r'|^\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}'  # ISO datetime
)


class SchemaInferenceStep(PipelineStep):
    """
    推断列类型 (PRD: string / integer / float / timestamp / boolean)。
    对多个样本(最多 10 行)投票, 返回更准确的类型。
    """

    def run(self, ctx: PipelineContext, data: list[dict]) -> list[dict]:
        if not data:
            return data

        # 用最多 10 行样本做类型投票
        sample_rows = data[:10]
        columns = list(data[0].keys())
        schema: dict[str, str] = {}

        for col in columns:
            votes: dict[str, int] = {}
            for row in sample_rows:
                val = row.get(col)
                if val is None or str(val).strip() == "":
                    continue
                t = self._infer_type(str(val).strip())
                votes[t] = votes.get(t, 0) + 1
            # 选择得票最多的类型; 平票时更具体的类型优先
            if votes:
                priority = ["timestamp", "integer", "float", "boolean", "string", "null"]
                schema[col] = max(votes, key=lambda t: (votes[t], -priority.index(t) if t in priority else -99))
            else:
                schema[col] = "string"

        ctx.meta["inferred_schema"] = schema
        return data

    @staticmethod
    def _infer_type(value: str) -> str:
        if not value or value.lower() in ("none", "null", "nan", ""):
            return "null"
        # 优先检查 timestamp
        if _DATE_RE.match(value):
            return "timestamp"
        if value.lower() in ("true", "false", "yes", "no", "1", "0"):
            return "boolean"
        try:
            int(value.replace(",", ""))
            return "integer"
        except (ValueError, TypeError):
            pass
        try:
            float(value.replace(",", ""))
            return "float"
        except (ValueError, TypeError):
            pass
        return "string"
