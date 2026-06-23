from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Severity(str, Enum):
    FATAL = "fatal"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ValidationIssue:
    severity: Severity
    code: str
    message: str
    location: Optional[dict] = None


@dataclass
class ValidationReport:
    issues: list = field(default_factory=list)

    def add(self, severity: Severity, code: str, message: str, location: Optional[dict] = None):
        self.issues.append(ValidationIssue(severity=severity, code=code, message=message, location=location))

    def has_fatal(self) -> bool:
        return any(i.severity == Severity.FATAL for i in self.issues)

    def has_errors(self) -> bool:
        return any(i.severity == Severity.ERROR for i in self.issues)

    def to_dict(self) -> dict:
        by_severity: dict = {}
        for issue in self.issues:
            s = issue.severity.value
            entry: dict = {"code": issue.code, "message": issue.message}
            if issue.location:
                entry["location"] = issue.location
            by_severity.setdefault(s, []).append(entry)
        return {
            "has_fatal": self.has_fatal(),
            "has_errors": self.has_errors(),
            "total_issues": len(self.issues),
            "by_severity": by_severity,
        }

    def to_summary(self) -> str:
        fatals = [i for i in self.issues if i.severity == Severity.FATAL]
        errors = [i for i in self.issues if i.severity == Severity.ERROR]
        if fatals:
            return f"[FATAL] {fatals[0].message}"
        if errors:
            msgs = "；".join(i.message for i in errors[:3])
            suffix = f"（共 {len(errors)} 个错误）" if len(errors) > 3 else ""
            return msgs + suffix
        return "校验通过"


class PostHarnessValidator:
    # Default allowed entity types — prompt-agnostic base set
    DEFAULT_ALLOWED_TYPES = {
        "Supplier", "Product", "Material", "Warehouse", "Document",
        "Category", "Process", "ProductionLine", "PurchasingOrder",
        "CustomerOrder", "Organization", "Facility", "Concept",
        "Disease", "Drug", "Symptom", "Treatment", "RiskFactor", "Pathogenesis", "Subtype", "Scale", "Examination", "NonDrugTreatment", "FAQ", "DiagnosisCriteria", "FollowupQuestion", "DiversionRule", "ClinicalFocus",
        "Asset", "Liability", "Revenue", "Expense",
        "Subject", "Right", "Obligation",
        "Course", "Knowledge", "Skill",
    }

    def validate(self, data: dict, allowed_types: Optional[set] = None) -> ValidationReport:
        report = ValidationReport()
        self._structure_check(data, report)
        if report.has_fatal():
            return report
        self._field_check(data, report)
        self._reference_check(data, report)
        self._dedup_check(data, report)
        self._type_check(data, report, allowed_types or self.DEFAULT_ALLOWED_TYPES)
        self._syntax_check(data, report)          # Fix 3: Python syntax validation
        self._linked_ref_check(data, report)      # Fix 3: semantic reference integrity
        return report

    # ── 1. Structure ────────────────────────────────────────────────────────
    def _structure_check(self, data: dict, report: ValidationReport):
        if not isinstance(data, dict):
            report.add(Severity.FATAL, "INVALID_STRUCTURE", "LLM 输出不是合法的 JSON 对象")
            return
        entities = data.get("entities")
        if not isinstance(entities, list):
            report.add(Severity.FATAL, "MISSING_ENTITIES", "缺少 entities 字段或类型错误（应为数组）")
            return
        if len(entities) == 0:
            report.add(Severity.FATAL, "EMPTY_ENTITIES",
                       "entities 数组为空，LLM 未提取到任何实体，建议检查 Prompt 或文档内容")
        for field_name in ("relations", "logic_rules", "actions"):
            val = data.get(field_name)
            if val is not None and not isinstance(val, list):
                report.add(Severity.ERROR, f"INVALID_{field_name.upper()}",
                           f"{field_name} 字段类型错误，应为数组")

    # ── 2. Required fields ──────────────────────────────────────────────────
    def _field_check(self, data: dict, report: ValidationReport):
        for i, e in enumerate(data.get("entities", [])):
            if not isinstance(e, dict):
                continue
            name = e.get("name_cn") or f"#{i}"
            if not e.get("name_cn"):
                report.add(Severity.ERROR, "ENTITY_MISSING_NAME",
                           f"实体 #{i} 缺少 name_cn", {"index": i})
            if not e.get("type"):
                report.add(Severity.ERROR, "ENTITY_MISSING_TYPE",
                           f"实体「{name}」缺少 type", {"name": name})
            props = e.get("properties") or {}
            if not isinstance(props, dict) or len(props) == 0:
                report.add(Severity.WARNING, "ENTITY_MISSING_PROPS",
                           f"实体「{name}」的 properties 为空", {"name": name})

        for i, r in enumerate(data.get("relations", [])):
            if not isinstance(r, dict):
                continue
            for fn in ("source", "target", "type"):
                if not r.get(fn):
                    report.add(Severity.ERROR, f"RELATION_MISSING_{fn.upper()}",
                               f"关系 #{i} 缺少 {fn}", {"index": i})

        for i, rule in enumerate(data.get("logic_rules", [])):
            if not isinstance(rule, dict):
                continue
            if not rule.get("name_cn"):
                report.add(Severity.ERROR, "LOGIC_MISSING_NAME",
                           f"逻辑规则 #{i} 缺少 name_cn", {"index": i})
            linked = rule.get("linked_entities") or []
            if not linked:
                name = rule.get("name_cn", f"#{i}")
                report.add(Severity.WARNING, "LOGIC_NO_LINKED_ENTITIES",
                           f"逻辑规则「{name}」的 linked_entities 为空，双向关联将缺失", {"name": name})

        for i, action in enumerate(data.get("actions", [])):
            if not isinstance(action, dict):
                continue
            name = action.get("name_cn", f"#{i}")
            if not action.get("name_cn"):
                report.add(Severity.ERROR, "ACTION_MISSING_NAME",
                           f"动作 #{i} 缺少 name_cn", {"index": i})
            if not (action.get("linked_logic_names") or action.get("linked_logic_ids")):
                report.add(Severity.WARNING, "ACTION_NO_LOGIC_LINK",
                           f"动作「{name}」未关联任何逻辑规则", {"name": name})
            if not action.get("function_code"):
                report.add(Severity.WARNING, "ACTION_NO_CODE",
                           f"动作「{name}」缺少 function_code", {"name": name})

    # ── 3. Reference integrity ──────────────────────────────────────────────
    def _reference_check(self, data: dict, report: ValidationReport):
        entity_names = {
            e["name_cn"] for e in data.get("entities", [])
            if isinstance(e, dict) and e.get("name_cn")
        }
        for i, r in enumerate(data.get("relations", [])):
            if not isinstance(r, dict):
                continue
            for direction, field_name in (("source", "source"), ("target", "target")):
                val = r.get(field_name)
                if val and val not in entity_names:
                    report.add(Severity.ERROR, f"BROKEN_REF_{direction.upper()}",
                               f"关系 #{i} 的 {field_name}「{val}」不存在于 entities",
                               {"index": i, "missing": val})

    # ── 4. Deduplication (mutates data in-place) ────────────────────────────
    def _dedup_check(self, data: dict, report: ValidationReport):
        # Entities: dedup by (name_cn, type)
        seen_ents: set = set()
        new_ents = []
        for e in data.get("entities", []):
            if not isinstance(e, dict):
                continue
            key = (e.get("name_cn"), e.get("type"))
            if key in seen_ents:
                report.add(Severity.WARNING, "ENTITY_DUPLICATE",
                           f"实体「{key[0]}」(type={key[1]}) 重复，自动保留第一条",
                           {"name_cn": key[0], "type": key[1]})
            else:
                seen_ents.add(key)
                new_ents.append(e)
        data["entities"] = new_ents

        # Relations: dedup by (source, type, target)
        seen_rels: set = set()
        new_rels = []
        for r in data.get("relations", []):
            if not isinstance(r, dict):
                continue
            key = (r.get("source"), r.get("type"), r.get("target"))
            if key in seen_rels:
                report.add(Severity.WARNING, "RELATION_DUPLICATE",
                           f"关系 ({key[0]})-[{key[1]}]→({key[2]}) 重复，自动去重")
            else:
                seen_rels.add(key)
                new_rels.append(r)
        data["relations"] = new_rels

    # ── 6. Python syntax check on function_code ────────────────────────────
    def _syntax_check(self, data: dict, report: ValidationReport):
        import ast
        for action in data.get("actions", []):
            if not isinstance(action, dict):
                continue
            code = (action.get("function_code") or "").strip()
            name = action.get("name_cn", "?")
            if not code:
                continue
            try:
                ast.parse(code)
            except SyntaxError as exc:
                report.add(
                    Severity.ERROR, "ACTION_SYNTAX_ERROR",
                    f"动作「{name}」的 function_code 语法错误: {exc.msg}（第 {exc.lineno} 行）",
                    {"name": name, "lineno": exc.lineno, "error": exc.msg},
                )

    # ── 7. Semantic reference integrity ────────────────────────────────────
    def _linked_ref_check(self, data: dict, report: ValidationReport):
        """Check that linked_entities / linked_logic_names point to real items."""
        entity_names = {
            e["name_cn"] for e in data.get("entities", [])
            if isinstance(e, dict) and e.get("name_cn")
        }
        logic_names = {
            r["name_cn"] for r in data.get("logic_rules", [])
            if isinstance(r, dict) and r.get("name_cn")
        }

        for rule in data.get("logic_rules", []):
            if not isinstance(rule, dict):
                continue
            rname = rule.get("name_cn", "?")
            for ent in rule.get("linked_entities", []):
                if ent and ent not in entity_names:
                    report.add(
                        Severity.WARNING, "LOGIC_BROKEN_ENTITY_REF",
                        f"逻辑规则「{rname}」的 linked_entities 中「{ent}」不存在于 entities",
                        {"rule": rname, "missing": ent},
                    )

        for action in data.get("actions", []):
            if not isinstance(action, dict):
                continue
            aname = action.get("name_cn", "?")
            for ent in action.get("linked_entities", []):
                if ent and ent not in entity_names:
                    report.add(
                        Severity.WARNING, "ACTION_BROKEN_ENTITY_REF",
                        f"动作「{aname}」的 linked_entities 中「{ent}」不存在于 entities",
                        {"action": aname, "missing": ent},
                    )
            for lname in action.get("linked_logic_names", []):
                if lname and lname not in logic_names:
                    report.add(
                        Severity.WARNING, "ACTION_BROKEN_LOGIC_REF",
                        f"动作「{aname}」的 linked_logic_names 中「{lname}」不存在于 logic_rules",
                        {"action": aname, "missing": lname},
                    )

    # ── 5. Type whitelist ───────────────────────────────────────────────────
    def _type_check(self, data: dict, report: ValidationReport, allowed_types: set):
        custom_count = 0
        total = len([e for e in data.get("entities", []) if isinstance(e, dict)])
        for e in data.get("entities", []):
            if not isinstance(e, dict):
                continue
            etype = e.get("type", "")
            if etype and etype not in allowed_types:
                custom_count += 1
                report.add(Severity.INFO, "UNKNOWN_ENTITY_TYPE",
                           f"实体「{e.get('name_cn', '?')}」的 type「{etype}」不在预设白名单",
                           {"name_cn": e.get("name_cn"), "type": etype})
        if total > 0 and custom_count / total > 0.5:
            report.add(Severity.WARNING, "HIGH_CUSTOM_TYPE_RATIO",
                       f"{custom_count}/{total} 个实体使用了非预设 type，建议更新 Prompt 的类型定义")
