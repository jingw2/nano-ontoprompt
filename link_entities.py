"""
提取后将 LLM 实体对齐到 SNOMED 锚点，合并重复节点。

用法（推荐 Docker，无需本地 Python 依赖）：
  ./link_entities.sh --dry-run
  ./link_entities.sh --apply

本地直接运行（需先 pip install -r backend/requirements.txt）：
  python link_entities.py --dry-run
  python link_entities.py --apply

建议顺序：
  1. python import_snomed.py                     # 导入 SNOMED 锚点（推荐）
  2. LLM 分批提取文档
  3. python link_entities.py --dry-run           # 确认匹配
  4. python link_entities.py --apply             # 合并
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend" if (ROOT / "backend" / "app").is_dir() else ROOT
if not (BACKEND / "app").is_dir() and Path("/app/app").is_dir():
    BACKEND = Path("/app")
sys.path.insert(0, str(BACKEND))


def _load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip())
    url = os.environ.get("DATABASE_URL", "")
    if "@db:" in url:
        os.environ["DATABASE_URL"] = url.replace("@db:", "@localhost:")


ABBR_SUFFIX_RE = re.compile(r"^(.+?)[\s（(]+([A-Za-z][A-Za-z0-9-]*)[\s）)]\s*$")

TYPE_MAP = {
    "symptom": "Symptom",
    "disorder": "Disease",
    "indicator": "RiskIndicator",
}

KNOWLEDGE_TYPES = {
    "drug", "药物", "diagnosiscriteria", "诊断标准", "diagnosticcriteria",
    "treatment", "治疗", "scale", "量表", "test", "检查",
}

SYMPTOM_TYPES = {"symptom", "症状"}
DISEASE_TYPES = {"disease", "disorder", "疾病"}
INDICATOR_TYPES = {"riskindicator", "indicator", "风险指标"}


def normalize_name(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text.strip())
    text = re.sub(r"\s+", "", text)
    return text.lower()


def strip_abbr(name: str) -> tuple[str, str]:
    m = ABBR_SUFFIX_RE.match(name.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return name.strip(), ""


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "unknown"


def type_group(entity_type: str | None) -> str:
    t = (entity_type or "").strip().lower()
    if t in SYMPTOM_TYPES:
        return "symptom"
    if t in DISEASE_TYPES:
        return "disorder"
    if t in INDICATOR_TYPES:
        return "indicator"
    if t in {"drug", "药物"}:
        return "drug"
    if t in {"diagnosiscriteria", "诊断标准", "diagnosticcriteria"}:
        return "criteria"
    return t or "unknown"


def types_compatible(orphan_type: str | None, anchor_type: str | None) -> bool:
    og, ag = type_group(orphan_type), type_group(anchor_type)
    if og == ag:
        return True
    if og in ("disorder",) and ag in ("disorder",):
        return True
    return False


def is_knowledge_type(entity_type: str | None) -> bool:
    return type_group(entity_type) in {"drug", "criteria"} or (
        (entity_type or "").strip().lower() in KNOWLEDGE_TYPES
    )


@dataclass
class CsvRef:
    canonical_id: str
    snomed_id: str
    name_zh: str
    name_en: str
    entity_type: str


@dataclass
class MatchCandidate:
    canonical_id: str
    snomed_id: str
    name_zh: str
    name_en: str
    entity_type: str
    method: str
    score: float


@dataclass
class MatchPlan:
    orphan_id: str
    orphan_name: str
    orphan_type: str
    action: str  # merge | promote | assign | review | skip
    score: float
    method: str
    target_entity_id: str | None
    target_canonical_id: str | None
    target_name: str | None
    note: str = ""


def load_csv_refs(csv_path: Path) -> list[CsvRef]:
    refs: list[CsvRef] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        lines = [line for line in f if not line.startswith("#") and line.strip()]
    reader = csv.DictReader(lines)
    for row in reader:
        cid = (row.get("canonical_id") or "").strip()
        if not cid:
            continue
        refs.append(
            CsvRef(
                canonical_id=cid,
                snomed_id=(row.get("snomed_id") or "").strip(),
                name_zh=(row.get("name_zh") or "").strip(),
                name_en=(row.get("name_en") or "").strip(),
                entity_type=TYPE_MAP.get((row.get("type") or "").strip(), "Symptom"),
            )
        )
    return refs


class MatchIndex:
    """SNOMED CSV + DB 锚点联合索引。"""

    def __init__(self, csv_refs: list[CsvRef], db_anchors: list) -> None:
        self.by_canonical: dict[str, MatchCandidate] = {}
        self.db_anchor_by_cid: dict[str, object] = {}
        self.entries: list[tuple[str, str, MatchCandidate]] = []
        self._all_candidates: list[MatchCandidate] = []

        for a in db_anchors:
            if a.canonical_id:
                self.db_anchor_by_cid[a.canonical_id] = a

        for ref in csv_refs:
            cand = MatchCandidate(
                canonical_id=ref.canonical_id,
                snomed_id=ref.snomed_id,
                name_zh=ref.name_zh,
                name_en=ref.name_en,
                entity_type=ref.entity_type,
                method="csv",
                score=1.0,
            )
            self.by_canonical[ref.canonical_id] = cand
            self._index_candidate(cand)

        for a in db_anchors:
            if not a.canonical_id:
                continue
            cand = MatchCandidate(
                canonical_id=a.canonical_id,
                snomed_id=a.snomed_id or "",
                name_zh=a.name_cn or "",
                name_en=a.name_en or "",
                entity_type=a.type or "",
                method="db",
                score=1.0,
            )
            self._index_candidate(cand)

        self._all_candidates = list(self.by_canonical.values())

    def _index_candidate(self, cand: MatchCandidate) -> None:
        def add(key: str, kind: str) -> None:
            if key:
                self.entries.append((key, kind, cand))

        if cand.name_zh:
            add(normalize_name(cand.name_zh), "name_cn")
            base, abbr = strip_abbr(cand.name_zh)
            add(normalize_name(base), "name_cn_base")
            if abbr:
                add(normalize_name(abbr), "abbr")
        if cand.name_en:
            add(normalize_name(cand.name_en), "name_en")


def _abbr_from_entity(entity) -> str:
    abbr = (entity.name_abbr or "").strip()
    if abbr:
        return abbr
    props = entity.properties or {}
    for key in ("abbreviation", "abbr", "short_name"):
        val = props.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _aliases_from_entity(entity) -> list[str]:
    props = entity.properties or {}
    aliases = props.get("aliases") or []
    if isinstance(aliases, list):
        return [str(a).strip() for a in aliases if str(a).strip()]
    return []


def find_match(orphan, index: MatchIndex) -> MatchCandidate | None:
    if is_knowledge_type(orphan.type):
        return None

    queries: list[tuple[str, str, float]] = []

    name_cn = (orphan.name_cn or "").strip()
    name_en = (orphan.name_en or "").strip()
    base_cn, abbr_from_name = strip_abbr(name_cn)
    abbr = _abbr_from_entity(orphan) or abbr_from_name

    if name_cn:
        queries.append((normalize_name(name_cn), "exact_name_cn", 1.0))
    if base_cn and base_cn != name_cn:
        queries.append((normalize_name(base_cn), "normalized_name_cn", 0.92))
    if name_en:
        queries.append((normalize_name(name_en), "exact_name_en", 0.98))
    if abbr:
        queries.append((normalize_name(abbr), "exact_abbr", 0.95))
    for alias in _aliases_from_entity(orphan):
        queries.append((normalize_name(alias), "alias", 0.93))

    best: MatchCandidate | None = None

    for key, method, base_score in queries:
        for entry_key, kind, cand in index.entries:
            if key != entry_key:
                continue
            if not types_compatible(orphan.type, cand.entity_type):
                continue
            score = base_score
            if score > (best.score if best else 0):
                best = MatchCandidate(
                    canonical_id=cand.canonical_id,
                    snomed_id=cand.snomed_id,
                    name_zh=cand.name_zh,
                    name_en=cand.name_en,
                    entity_type=cand.entity_type,
                    method=method,
                    score=score,
                )

    # 子串匹配（较严格）
    cn = normalize_name(base_cn or name_cn)
    if cn and len(cn) >= 2:
        for cand in index._all_candidates:
            if not types_compatible(orphan.type, cand.entity_type):
                continue
            for ref in (normalize_name(cand.name_zh), normalize_name(cand.name_en)):
                if not ref or len(ref) < 2:
                    continue
                if cn in ref or ref in cn:
                    shorter, longer = min(len(cn), len(ref)), max(len(cn), len(ref))
                    if shorter / longer < 0.5:
                        continue
                    score = 0.80 + 0.08 * (shorter / longer)
                    if score > (best.score if best else 0):
                        best = MatchCandidate(
                            canonical_id=cand.canonical_id,
                            snomed_id=cand.snomed_id,
                            name_zh=cand.name_zh,
                            name_en=cand.name_en,
                            entity_type=cand.entity_type,
                            method="substring",
                            score=round(score, 3),
                        )

    # 编辑距离
    cn = normalize_name(base_cn or name_cn)
    en = normalize_name(name_en)
    if cn or en:
        for cand in index._all_candidates:
            if not types_compatible(orphan.type, cand.entity_type):
                continue
            for ref, kind in (
                (normalize_name(cand.name_zh), "fuzzy_name_cn"),
                (normalize_name(cand.name_en), "fuzzy_name_en"),
            ):
                if not ref:
                    continue
                q = cn if kind == "fuzzy_name_cn" else en
                if not q:
                    continue
                ratio = SequenceMatcher(None, q, ref).ratio()
                if ratio >= 0.85 and ratio > (best.score if best else 0):
                    best = MatchCandidate(
                        canonical_id=cand.canonical_id,
                        snomed_id=cand.snomed_id,
                        name_zh=cand.name_zh,
                        name_en=cand.name_en,
                        entity_type=cand.entity_type,
                        method=kind,
                        score=round(ratio, 3),
                    )

    return best


def make_knowledge_canonical_id(entity) -> str:
    tg = type_group(entity.type)
    raw = (entity.type or "").strip().lower()
    prefix = {
        "drug": "drug",
        "criteria": "criteria",
    }.get(tg, None)
    if prefix is None:
        if raw in {"treatment", "治疗"}:
            prefix = "treatment"
        elif raw in {"scale", "量表"}:
            prefix = "scale"
        elif raw in {"examination", "检查", "test"}:
            prefix = "exam"
        else:
            prefix = "entity"
    if entity.name_en:
        slug = slugify(entity.name_en)
    else:
        slug = hashlib.md5((entity.name_cn or "").encode()).hexdigest()[:10]
    return f"{prefix}:{slug}"


def make_custom_symptom_id(entity) -> str:
    slug = slugify(entity.name_en) if entity.name_en else hashlib.md5(
        (entity.name_cn or "").encode()
    ).hexdigest()[:10]
    return f"symptom:custom_{slug}"


def build_plans(
    orphans: list,
    anchors: list,
    index: MatchIndex,
    min_auto: float,
    min_review: float,
    assign_custom: bool,
) -> list[MatchPlan]:
    anchor_ids = {a.id for a in anchors}
    plans: list[MatchPlan] = []

    for orphan in orphans:
        if orphan.id in anchor_ids:
            continue

        match = find_match(orphan, index)
        if match:
            db_anchor = index.db_anchor_by_cid.get(match.canonical_id)
            if db_anchor and db_anchor.id != orphan.id:
                action = "merge" if match.score >= min_auto else (
                    "review" if match.score >= min_review else "skip"
                )
                plans.append(
                    MatchPlan(
                        orphan_id=orphan.id,
                        orphan_name=orphan.name_cn,
                        orphan_type=orphan.type or "",
                        action=action,
                        score=match.score,
                        method=match.method,
                        target_entity_id=db_anchor.id,
                        target_canonical_id=db_anchor.canonical_id,
                        target_name=db_anchor.name_cn,
                    )
                )
                continue

            # CSV / SNOMED 命中，但 DB 尚无锚点节点 → 直接给 orphan 赋 ID
            action = "promote" if match.score >= min_auto else (
                "review" if match.score >= min_review else "skip"
            )
            plans.append(
                MatchPlan(
                    orphan_id=orphan.id,
                    orphan_name=orphan.name_cn,
                    orphan_type=orphan.type or "",
                    action=action,
                    score=match.score,
                    method=match.method,
                    target_entity_id=orphan.id,
                    target_canonical_id=match.canonical_id,
                    target_name=match.name_zh or match.name_en,
                    note="assign canonical_id from SNOMED CSV",
                )
            )
            continue

        if is_knowledge_type(orphan.type):
            plans.append(
                MatchPlan(
                    orphan_id=orphan.id,
                    orphan_name=orphan.name_cn,
                    orphan_type=orphan.type or "",
                    action="assign",
                    score=1.0,
                    method="knowledge_type",
                    target_entity_id=orphan.id,
                    target_canonical_id=make_knowledge_canonical_id(orphan),
                    target_name=orphan.name_cn,
                    note="knowledge-layer entity",
                )
            )
            continue

        if assign_custom and type_group(orphan.type) == "symptom":
            plans.append(
                MatchPlan(
                    orphan_id=orphan.id,
                    orphan_name=orphan.name_cn,
                    orphan_type=orphan.type or "",
                    action="assign",
                    score=0.0,
                    method="custom_symptom",
                    target_entity_id=orphan.id,
                    target_canonical_id=make_custom_symptom_id(orphan),
                    target_name=orphan.name_cn,
                    note="no SNOMED match",
                )
            )
        else:
            plans.append(
                MatchPlan(
                    orphan_id=orphan.id,
                    orphan_name=orphan.name_cn,
                    orphan_type=orphan.type or "",
                    action="skip",
                    score=0.0,
                    method="none",
                    target_entity_id=None,
                    target_canonical_id=None,
                    target_name=None,
                    note="no match",
                )
            )

    return plans


def _merge_properties(anchor, orphan) -> None:
    props = dict(anchor.properties or {})
    for k, v in (orphan.properties or {}).items():
        if k not in props:
            props[k] = v
    aliases = props.get("aliases") or []
    if not isinstance(aliases, list):
        aliases = []
    for alias in [orphan.name_cn, orphan.name_en, _abbr_from_entity(orphan)]:
        if alias and alias not in aliases and alias not in (anchor.name_cn, anchor.name_en):
            aliases.append(alias)
    if aliases:
        props["aliases"] = aliases
    anchor.properties = props


def _replace_name_in_linked(items: list, orphan_name: str, anchor_name: str) -> list:
    out = []
    for item in items or []:
        out.append(anchor_name if item == orphan_name else item)
    # dedupe preserving order
    seen = set()
    deduped = []
    for x in out:
        if x not in seen:
            seen.add(x)
            deduped.append(x)
    return deduped


def apply_plans(db, ontology_id: str, plans: list[MatchPlan], csv_by_cid: dict[str, CsvRef]) -> dict:
    from app.models.entity import Entity
    from app.models.relation import Relation
    from app.models.logic import LogicRule
    from app.models.action import Action

    stats = {"merge": 0, "promote": 0, "assign": 0, "review": 0, "skip": 0}

    entities = {
        e.id: e
        for e in db.query(Entity).filter(Entity.ontology_id == ontology_id).all()
    }

    for plan in plans:
        if plan.action == "skip":
            stats["skip"] += 1
            continue
        if plan.action == "review":
            stats["review"] += 1
            continue

        orphan = entities.get(plan.orphan_id)
        if not orphan:
            continue

        if plan.action == "merge":
            anchor = entities.get(plan.target_entity_id or "")
            if not anchor or anchor.id == orphan.id:
                stats["skip"] += 1
                continue

            for rel in db.query(Relation).filter(
                Relation.ontology_id == ontology_id,
                (Relation.source_entity == orphan.id) | (Relation.target_entity == orphan.id),
            ):
                if rel.source_entity == orphan.id:
                    rel.source_entity = anchor.id
                if rel.target_entity == orphan.id:
                    rel.target_entity = anchor.id

            if not anchor.description and orphan.description:
                anchor.description = orphan.description
            if orphan.name_en and not anchor.name_en:
                anchor.name_en = orphan.name_en
            if _abbr_from_entity(orphan) and not _abbr_from_entity(anchor):
                anchor.name_abbr = _abbr_from_entity(orphan)
            _merge_properties(anchor, orphan)

            for rule in db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id):
                linked = rule.linked_entities
                if orphan.name_cn in linked:
                    rule.linked_entities = _replace_name_in_linked(
                        linked, orphan.name_cn, anchor.name_cn
                    )

            for act in db.query(Action).filter(Action.ontology_id == ontology_id):
                if orphan.name_cn in (act.linked_entities or []):
                    act.linked_entities = _replace_name_in_linked(
                        act.linked_entities, orphan.name_cn, anchor.name_cn
                    )

            db.delete(orphan)
            del entities[orphan.id]
            stats["merge"] += 1

        elif plan.action == "promote":
            ref = csv_by_cid.get(plan.target_canonical_id or "")
            orphan.canonical_id = plan.target_canonical_id
            if ref and ref.snomed_id:
                orphan.snomed_id = ref.snomed_id
            if ref and not orphan.type:
                orphan.type = ref.entity_type
            stats["promote"] += 1

        elif plan.action == "assign":
            if not orphan.canonical_id:
                orphan.canonical_id = plan.target_canonical_id
            props = dict(orphan.properties or {})
            props.setdefault("source", "llm")
            orphan.properties = props
            stats["assign"] += 1

    db.flush()

    # 关系去重
    seen: set[tuple[str, str, str]] = set()
    for rel in db.query(Relation).filter(Relation.ontology_id == ontology_id).all():
        key = (rel.source_entity, rel.target_entity, rel.type)
        if key in seen:
            db.delete(rel)
        else:
            seen.add(key)

    db.commit()
    return stats


def print_report(plans: list[MatchPlan], anchors: list, orphans: list) -> None:
    print(f"\n{'=' * 72}")
    print(f"锚点（有 canonical_id）: {len(anchors)}")
    print(f"待对齐（无 canonical_id）: {len(orphans)}")
    print(f"{'=' * 72}")

    groups = {"merge": [], "promote": [], "assign": [], "review": [], "skip": []}
    for p in plans:
        groups[p.action].append(p)

    def _section(title: str, items: list[MatchPlan], limit: int = 30) -> None:
        print(f"\n── {title} ({len(items)}) ──")
        for p in items[:limit]:
            target = p.target_canonical_id or p.target_name or "—"
            print(
                f"  [{p.score:.2f}|{p.method}] {p.orphan_name} ({p.orphan_type})"
                f"  →  {target}  {('· ' + p.note) if p.note else ''}"
            )
        if len(items) > limit:
            print(f"  ... 还有 {len(items) - limit} 条")

    _section("自动合并 → SNOMED 锚点", groups["merge"])
    _section("提升为锚点（赋 canonical_id，无 DB 重复节点）", groups["promote"])
    _section("知识层分配 ID（Drug / DiagnosisCriteria 等）", groups["assign"])
    _section("待人工确认", groups["review"])
    _section("未匹配", groups["skip"], limit=20)

    print(f"\n{'=' * 72}")
    print(
        "汇总："
        f" merge={len(groups['merge'])}"
        f" promote={len(groups['promote'])}"
        f" assign={len(groups['assign'])}"
        f" review={len(groups['review'])}"
        f" skip={len(groups['skip'])}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM 实体对齐 SNOMED 锚点并合并重复节点")
    parser.add_argument("--ontology", default="o-medical-001")
    default_csv = ROOT / "snomed_mental_health.csv"
    if not default_csv.exists() and Path("/tmp/snomed_mental_health.csv").exists():
        default_csv = Path("/tmp/snomed_mental_health.csv")
    parser.add_argument(
        "--csv",
        default=str(default_csv),
        help="SNOMED 词表 CSV（用于匹配）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只输出报告，不写入")
    parser.add_argument("--apply", action="store_true", help="执行合并并写入数据库")
    parser.add_argument("--min-auto", type=float, default=0.85, help="自动合并/提升阈值")
    parser.add_argument("--min-review", type=float, default=0.60, help="进入 review 的最低分")
    parser.add_argument(
        "--assign-custom",
        action="store_true",
        help="未匹配的 Symptom 分配 symptom:custom_* ID",
    )
    args = parser.parse_args()

    if not args.apply and not args.dry_run:
        args.dry_run = True

    _load_env()

    try:
        from app.database import SessionLocal
        from app.models.entity import Entity
        from app.models.ontology import OntologyProject
    except ModuleNotFoundError as e:
        print("✗ 缺少 Python 依赖，无法连接数据库。", file=sys.stderr)
        print(f"  详情: {e}", file=sys.stderr)
        print("", file=sys.stderr)
        print("  推荐（Docker 已在跑时）：", file=sys.stderr)
        print("    ./link_entities.sh --dry-run", file=sys.stderr)
        print("    ./link_entities.sh --apply", file=sys.stderr)
        print("", file=sys.stderr)
        print("  或本地安装依赖后重试：", file=sys.stderr)
        print("    pip install -r backend/requirements.txt", file=sys.stderr)
        print("    python link_entities.py --dry-run", file=sys.stderr)
        sys.exit(1)

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"✗ CSV 不存在: {csv_path}", file=sys.stderr)
        sys.exit(1)

    csv_refs = load_csv_refs(csv_path)
    csv_by_cid = {r.canonical_id: r for r in csv_refs}

    db = SessionLocal()
    try:
        project = db.query(OntologyProject).filter(OntologyProject.id == args.ontology).first()
        if not project:
            print(f"✗ 本体不存在: {args.ontology}", file=sys.stderr)
            sys.exit(1)

        all_entities = db.query(Entity).filter(Entity.ontology_id == args.ontology).all()
        anchors = [e for e in all_entities if e.canonical_id]
        orphans = [e for e in all_entities if not e.canonical_id]

        index = MatchIndex(csv_refs, anchors)
        plans = build_plans(
            orphans, anchors, index,
            min_auto=args.min_auto,
            min_review=args.min_review,
            assign_custom=args.assign_custom,
        )

        print_report(plans, anchors, orphans)

        if args.dry_run and not args.apply:
            print("\n[dry-run] 未写入。确认后执行: python link_entities.py --apply")
            return

        stats = apply_plans(db, args.ontology, plans, csv_by_cid)
        print(f"\n✓ 已写入：{stats}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
