"""XML → 平面表转换 Step"""
from __future__ import annotations
import xml.etree.ElementTree as ET
from typing import Any

from app.services.v2.pipeline.base import PipelineStep, PipelineContext


class XmlParseStep(PipelineStep):
    """
    将 XML 字符串数据转换为平面 row 列表。

    spec 选项:
      record_path: str — 重复记录的 XPath (如: ".//record", ".//item")
      fields: list[str] — 要提取的子元素/属性列表 (空列表则全部)
      include_attributes: bool (默认 True)
    """

    def run(self, ctx: PipelineContext, data: list[dict]) -> list[dict]:
        """将 data 中每个 row 的 'xml_content' 键解析为 XML。"""
        spec = ctx.spec.get("xml_parse", {})
        record_path = spec.get("record_path", ".//record")
        fields = spec.get("fields", [])
        include_attrs = spec.get("include_attributes", True)

        result = []
        for row in data:
            xml_str = row.get("xml_content", "")
            if not xml_str:
                result.append(row)
                continue
            try:
                records = self._parse_xml(xml_str, record_path, fields, include_attrs)
                for rec in records:
                    merged = {k: v for k, v in row.items() if k != "xml_content"}
                    merged.update(rec)
                    result.append(merged)
            except ET.ParseError:
                result.append(row)  # 解析失败时保留原始数据

        ctx.meta["xml_parse"] = {"rows_before": len(data), "rows_after": len(result)}
        return result

    def _parse_xml(self, xml_str: str, record_path: str, fields: list[str], include_attrs: bool) -> list[dict]:
        root = ET.fromstring(xml_str)
        records = root.findall(record_path)

        if not records:
            # record_path 无匹配时将 root 本身作为单条记录处理
            records = [root]

        result = []
        for elem in records:
            row: dict[str, Any] = {}

            if include_attrs:
                row.update(elem.attrib)

            for child in elem:
                tag = child.tag
                if fields and tag not in fields:
                    continue
                row[tag] = child.text or ""

            result.append(row)

        return result
