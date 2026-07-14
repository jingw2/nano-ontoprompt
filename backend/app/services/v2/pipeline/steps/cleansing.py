"""数据清洗 Step — NULL 处理、去重、日期规范化、jagged row 过滤"""
from __future__ import annotations
import re
from app.services.v2.pipeline.base import PipelineStep, PipelineContext

# 待规范化为 ISO 8601 的日期模式
_DATE_PATTERNS = [
    (re.compile(r'^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$'), '{}-{:02d}-{:02d}'),
    (re.compile(r'^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$'), '{2}-{0:02d}-{1:02d}'),
    (re.compile(r'^(\d{4})(\d{2})(\d{2})$'), '{}-{}-{}'),
]


def _normalize_date(val: str) -> str:
    """将日期字符串标准化为 YYYY-MM-DD 格式"""
    val = val.strip()
    for pat, fmt in _DATE_PATTERNS:
        m = pat.match(val)
        if m:
            parts = [int(g) for g in m.groups()]
            try:
                if '{2}' in fmt:
                    return fmt.format(*parts)
                return fmt.format(*parts)
            except (ValueError, IndexError):
                pass
    return val  # 不匹配则原样返回


class CleansingStep(PipelineStep):
    """
    spec 选项:
      null_strategy:   "drop" | "fill_empty" | "mark" (默认: "fill_empty")
                       mark = 原值填充为空字符串, 并增加 __null_<col>__ = "1" 标记列
      deduplicate:     bool (默认: True)
      trim_strings:    bool (默认: True)
      normalize_dates: bool (默认: True) — timestamp 列日期格式标准化
      filter_jagged:   bool (默认: True) — 删除列数不一致的行
    """

    def run(self, ctx: PipelineContext, data: list[dict]) -> list[dict]:
        if not data:
            return data

        spec = ctx.spec.get("cleansing", {})
        null_strategy   = spec.get("null_strategy",   "fill_empty")
        deduplicate     = spec.get("deduplicate",     True)
        trim_strings    = spec.get("trim_strings",    True)
        normalize_dates = spec.get("normalize_dates", True)
        filter_jagged   = spec.get("filter_jagged",   True)

        # 基准列集合 (以首行为准)
        expected_cols = set(data[0].keys())

        # 检测 timestamp 列 (参考 schema_inference 结果)
        inferred_schema: dict[str, str] = ctx.meta.get("inferred_schema", {})
        timestamp_cols = {col for col, t in inferred_schema.items() if t == "timestamp"}

        result = []
        seen: set[str] = set()
        jagged_count = 0
        null_count = 0
        date_normalized = 0

        for row in data:
            # ① jagged row 过滤 (列数不一致)
            if filter_jagged and set(row.keys()) != expected_cols:
                jagged_count += 1
                continue

            # ② NULL 处理
            cleaned: dict = {}
            null_markers: dict = {}   # mark 策略使用
            skip = False
            for k, v in row.items():
                is_null = v is None or (isinstance(v, str) and v.strip() == "")
                if is_null:
                    null_count += 1
                    if null_strategy == "drop":
                        skip = True
                        break
                    elif null_strategy == "mark":
                        cleaned[k] = ""
                        null_markers[f"__null_{k}__"] = "1"
                    else:  # fill_empty
                        cleaned[k] = ""
                elif trim_strings and isinstance(v, str):
                    cleaned[k] = v.strip()
                else:
                    cleaned[k] = v

            if skip:
                continue

            # mark 策略: 增加标记列
            cleaned.update(null_markers)

            # ③ 日期格式标准化
            if normalize_dates and timestamp_cols:
                for col in timestamp_cols:
                    if col in cleaned and cleaned[col]:
                        normalized = _normalize_date(str(cleaned[col]))
                        if normalized != cleaned[col]:
                            cleaned[col] = normalized
                            date_normalized += 1

            # ④ 去重
            if deduplicate:
                key = str(sorted(cleaned.items()))
                if key in seen:
                    continue
                seen.add(key)

            result.append(cleaned)

        ctx.meta.update({
            "rows_before": len(data),
            "rows_after": len(result),
            "dropped": len(data) - len(result),
            "jagged_removed": jagged_count,
            "null_cells_handled": null_count,
            "dates_normalized": date_normalized,
        })
        return result
