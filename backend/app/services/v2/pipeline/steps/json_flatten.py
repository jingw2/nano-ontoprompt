"""JSON 嵌套结构 → 平面表转换 Step"""
from __future__ import annotations
import json
from typing import Any

from app.services.v2.pipeline.base import PipelineStep, PipelineContext


class JsonFlattenStep(PipelineStep):
    """
    将嵌套 JSON 对象转换为平面 row。

    spec 选项:
      sep: str (默认 ".") — 嵌套键分隔符
      max_depth: int (默认 10) — 最大嵌套深度
      array_explode: bool (默认 True) — 将数组字段拆分为多个 row
      array_fields: list[str] — 显式指定要 explode 的数组字段 (空列表则自动检测)
    """

    def run(self, ctx: PipelineContext, data: list[dict]) -> list[dict]:
        spec = ctx.spec.get("json_flatten", {})
        sep = spec.get("sep", ".")
        max_depth = spec.get("max_depth", 10)
        array_explode = spec.get("array_explode", True)
        array_fields = spec.get("array_fields", [])

        result = []
        for row in data:
            flat = self._flatten(row, sep=sep, max_depth=max_depth, prefix="", depth=0)
            if array_explode:
                exploded = self._explode_arrays(flat, array_fields)
                result.extend(exploded)
            else:
                result.append(flat)

        ctx.meta["json_flatten"] = {
            "rows_before": len(data),
            "rows_after": len(result),
        }
        return result

    def _flatten(self, obj: Any, sep: str, max_depth: int, prefix: str, depth: int) -> dict:
        """递归拍平嵌套字典。数组暂存为 JSON 字符串。"""
        if depth > max_depth:
            return {prefix: str(obj)} if prefix else {}

        result = {}
        if isinstance(obj, dict):
            for k, v in obj.items():
                new_key = f"{prefix}{sep}{k}" if prefix else k
                if isinstance(v, dict):
                    result.update(self._flatten(v, sep, max_depth, new_key, depth + 1))
                elif isinstance(v, list):
                    # 数组用特殊标记保存, 留待后续 explode 处理
                    result[f"__array__{new_key}"] = v
                else:
                    result[new_key] = v
        else:
            result[prefix] = obj

        return result

    def _explode_arrays(self, flat_row: dict, explicit_fields: list[str]) -> list[dict]:
        """按数组字段拆分 row (cross join 方式)。"""
        # 收集数组字段
        array_keys = [k for k in flat_row if k.startswith("__array__")]

        # 非数组字段构成基础 row
        base = {k: v for k, v in flat_row.items() if not k.startswith("__array__")}

        if not array_keys:
            return [base]

        # 仅 explode 第一个数组字段 (多数组场景简化处理)
        array_key = array_keys[0]
        real_key = array_key[len("__array__"):]
        array_val = flat_row[array_key]

        remaining_arrays = {k: v for k, v in flat_row.items() if k.startswith("__array__") and k != array_key}

        rows = []
        if not isinstance(array_val, list) or len(array_val) == 0:
            row = dict(base)
            row[real_key] = json.dumps(array_val)
            row.update({k[len("__array__"):]: json.dumps(v) for k, v in remaining_arrays.items()})
            return [row]

        for item in array_val:
            row = dict(base)
            if isinstance(item, dict):
                for ik, iv in item.items():
                    row[f"{real_key}.{ik}"] = iv
            else:
                row[real_key] = item
            row.update({k[len("__array__"):]: json.dumps(v) for k, v in remaining_arrays.items()})
            rows.append(row)

        return rows
