"""Ontology Mapping 执行服务 — PRD v1.1: Entity Mapping + Relation推断 + ChromaDB写入"""
from __future__ import annotations
import logging
import uuid as _uuid
import json
import re
from sqlalchemy.orm import Session
from app.models.v2.mapping import OntologyMapping

logger = logging.getLogger(__name__)


class MappingService:

    def __init__(self, db: Session):
        self._db = db

    # ── CRUD ─────────────────────────────────────────────────────────

    def create_mapping(self, ontology_id: str, curated_dataset_id: str, entity_class: str,
                       field_mapping: dict, primary_key_column: str | None = None,
                       confidence: float = 1.0) -> OntologyMapping:
        field_mapping = dict(field_mapping or {})
        if primary_key_column and "__primary_key__" not in field_mapping:
            field_mapping["__primary_key__"] = primary_key_column
        mapping = OntologyMapping(
            ontology_id=ontology_id, curated_dataset_id=curated_dataset_id,
            entity_class=entity_class, field_mapping=field_mapping,
            status="draft", confidence=confidence,
        )
        self._db.add(mapping); self._db.commit(); self._db.refresh(mapping)
        return mapping

    def get_mappings(self, ontology_id: str) -> list[OntologyMapping]:
        return self._db.query(OntologyMapping).filter(OntologyMapping.ontology_id == ontology_id).all()

    # ── 单个 Mapping 应用 ─────────────────────────────────────────────

    def apply_mapping(self, mapping_id: str, data: list[dict]) -> dict:
        mapping = self._db.query(OntologyMapping).filter(OntologyMapping.id == mapping_id).first()
        if not mapping:
            raise ValueError(f"Mapping {mapping_id} not found")
        if data:
            self._normalize_mapping(mapping, data)
        entities = self._rows_to_entities(mapping, data)
        neo4j_count = self._write_neo4j(mapping.entity_class, entities)
        v1_count = self._write_v1_entities(mapping, entities)
        mapping.status = "applied"
        self._db.commit()
        return {"mapping_id": mapping_id, "entity_class": mapping.entity_class,
                "nodes_created": neo4j_count, "v1_entities_written": v1_count,
                "errors": 0, "total_rows": len(data)}

    # ── 全量构建：Entity → Relation → ChromaDB ────────────────────────

    def build_all(self, ontology_id: str) -> dict:
        from app.services.v2.dataset_service import DatasetService
        mappings = self.get_mappings(ontology_id)
        if not mappings:
            return {"error": "no mappings configured", "ontology_id": ontology_id}

        ds_svc = DatasetService(self._db)

        # Phase 1: Entity Mapping
        entity_results = []
        mapping_meta: dict[str, dict] = {}

        for m in mappings:
            if not m.curated_dataset_id:
                continue
            try:
                rows = ds_svc.preview(m.curated_dataset_id, 1, limit=10000)
            except Exception as e:
                logger.warning(f"读取数据集 {m.curated_dataset_id} 失败: {e}")
                continue

            if rows:
                self._normalize_mapping(m, rows)

            entities = self._rows_to_entities(m, rows)
            neo4j_count = self._write_neo4j(m.entity_class, entities)
            v1_count = self._write_v1_entities(m, entities)

            pk_col = (m.field_mapping or {}).get("__primary_key__") or self._choose_pk_col(rows)
            entity_id_map = {
                self._row_identity_value(row, pk_col): self._stable_row_id(m, row, pk_col if pk_col in row else None)
                for row in rows
            }
            mapping_meta[m.id] = {
                "entity_class": m.entity_class, "pk_col": pk_col,
                "rows": rows, "entity_id_map": entity_id_map,
                "columns": list(rows[0].keys()) if rows else [],
                "property_mappings": (m.field_mapping or {}).get("__properties__", []),
            }
            m.status = "applied"
            entity_results.append({"mapping_id": m.id, "entity_class": m.entity_class,
                                   "v1_entities_written": v1_count, "nodes_created": neo4j_count})

        self._db.commit()

        # Phase 2: Relation 推断
        relation_results = self._infer_and_write_relations(ontology_id, mappings, mapping_meta)

        # Phase 2b: Link Mapping 处理（手动配置的跨表关系）
        link_results = self._process_link_mappings(ontology_id, mapping_meta)
        relation_results.extend(link_results)

        # Phase 3: Logic / Action Discovery
        logic_result = self._discover_logic_rules(ontology_id, mappings, mapping_meta, relation_results)
        action_result = self._discover_action_types(ontology_id, mappings, mapping_meta, relation_results, logic_result)

        # Phase 4: 写入 ChromaDB
        chroma_count = 0
        try:
            from app.services.v2.vector.chroma_service import ChromaService
            chroma = ChromaService()
            all_entities = []
            for m in mappings:
                if m.id not in mapping_meta:
                    continue
                meta = mapping_meta[m.id]
                for row in meta["rows"]:
                    eid = meta.get("entity_id_map", {}).get(self._row_identity_value(row, meta["pk_col"]))
                    if eid:
                        all_entities.append({"id": eid, "type": m.entity_class, "properties": row})
            if all_entities:
                chroma.upsert_entities(ontology_id, all_entities)
                chroma_count = len(all_entities)
        except Exception as e:
            logger.warning(f"ChromaDB 写入失败（非致命）: {e}")

        # Phase 5: 为每个实体类创建概念实体 + INSTANCE-OF 关系
        # Pipeline Mapping 按行创建的是实例实体（如"员工A"），缺少顶层概念实体（"员工"）
        # 此步骤为每个 entity_class 创建一个抽象概念实体，并将所有实例关联到它
        concept_results = {"concepts": 0, "instance_of": 0}
        try:
            from app.models.entity import Entity
            from app.models.relation import Relation
            import hashlib as _hl

            for m in mappings:
                ec = m.entity_class
                concept_id = str(_uuid.UUID(
                    _hl.md5(f"{ontology_id}:{ec}:concept".encode()).hexdigest()
                ))
                existing_concept = self._db.query(Entity).filter(Entity.id == concept_id).first()
                name_cn = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', ec).replace('_', ' ').title()
                if not existing_concept:
                    self._db.add(Entity(
                        id=concept_id, ontology_id=ontology_id,
                        name_cn=name_cn, name_en=ec,
                        type=ec, canonical_id=f"concept:{ec}",
                        description=f"{name_cn} — Pipeline Mapping 自动创建的概念实体",
                        properties={"is_concept": True, "source": "build_all_concept_inference"},
                        confidence=1.0,
                    ))
                    concept_results["concepts"] += 1
                elif existing_concept.name_cn.endswith("(概念类型)"):
                    # Upgrade: remove legacy suffix from name
                    existing_concept.name_cn = name_cn
                    concept_results["concepts"] = max(concept_results["concepts"], 1)

                # 查询该类型的所有实例实体
                instance_ids = [
                    r[0] for r in self._db.query(Entity.id).filter(
                        Entity.ontology_id == ontology_id,
                        Entity.type == ec,
                        Entity.id != concept_id,
                    ).all()
                ]

                # 跳过已有 INSTANCE-OF 的实例
                existing_linked = set(
                    r[0] for r in self._db.query(Relation.source_entity).filter(
                        Relation.ontology_id == ontology_id,
                        Relation.target_entity == concept_id,
                        Relation.type == "INSTANCE-OF",
                    ).all()
                )
                for inst_id in instance_ids:
                    if inst_id in existing_linked:
                        continue
                    self._db.add(Relation(
                        id=str(_uuid.uuid4()), ontology_id=ontology_id,
                        source_entity=inst_id, target_entity=concept_id,
                        type="INSTANCE-OF",
                        properties={"source": "build_all_concept_inference"},
                        confidence=1.0,
                    ))
                    concept_results["instance_of"] += 1

            self._db.commit()
        except Exception as e:
            logger.warning(f"概念实体创建失败（非致命）: {e}")
            self._db.rollback()

        return {
            "ontology_id": ontology_id,
            "entity_mappings": entity_results,
            "relations_written": relation_results,
            "logic_discovery": logic_result,
            "action_discovery": action_result,
            "chroma_entities_written": chroma_count,
            "concept_entities_created": concept_results["concepts"],
            "instance_of_relations_created": concept_results["instance_of"],
            "total_entities": sum(r.get("v1_entities_written", 0) for r in entity_results) + concept_results["concepts"],
            "total_relations": sum(r.get("count", 0) for r in relation_results) + concept_results["instance_of"],
            "total_logic": logic_result.get("total_v2", 0),
            "total_actions": action_result.get("total_v2", 0),
            "review_required": True,
            "publish_status": "draft",
        }

    # ── Relation 推断 ───────────────────────────────────────────────

    def _infer_and_write_relations(self, ontology_id: str, mappings: list[OntologyMapping],
                                   mapping_meta: dict) -> list[dict]:
        from app.models.entity import Entity
        from app.models.relation import Relation
        from app.models.v2.mapping import OntologyLinkMapping
        import re

        results = []
        m_list = [m for m in mappings if m.id in mapping_meta]

        # 重建前清除旧的 FK 推断关系，避免改名/数据变化后产生重复边
        stale = self._db.query(Relation).filter(Relation.ontology_id == ontology_id).all()
        for rel in stale:
            if (rel.properties or {}).get("source") == "fk_inference":
                self._db.delete(rel)
        self._db.commit()

        for i, src_m in enumerate(m_list):
            src_meta = mapping_meta[src_m.id]
            src_cols = src_meta["columns"]

            for tgt_m in m_list:
                if tgt_m.id == src_m.id:
                    continue
                tgt_meta = mapping_meta[tgt_m.id]
                tgt_class = tgt_meta["entity_class"]
                tgt_pk_col = tgt_meta["pk_col"]
                tgt_id_map = tgt_meta["entity_id_map"]

                tgt_pk_values = {
                    str(row.get(tgt_pk_col, "")).strip()
                    for row in tgt_meta.get("rows", [])
                    if row.get(tgt_pk_col) not in (None, "")
                }
                # 归一化索引: 容错 ID 格式差异 (如 SUP-001 vs SUP001)
                tgt_norm_index = {
                    self._normalize_fk_value(v): v for v in tgt_pk_values
                }
                fk_candidates = self._detect_fk_columns(
                    src_cols, tgt_class, tgt_m.entity_class,
                    src_sample_rows=src_meta.get("rows", []),
                    tgt_pk_values=tgt_pk_values,
                )

                fk_cols_linked: set[str] = set()
                for fk_col, rel_type in fk_candidates:
                    written = 0
                    src_values: list[str] = []
                    tgt_values: list[str] = []
                    seen_pairs: set[tuple[str, str]] = set()
                    for row in src_meta["rows"]:
                        fk_val = str(row.get(fk_col, ""))
                        if not fk_val:
                            continue
                        src_pk_col = src_meta["pk_col"]
                        src_pk_val = self._row_identity_value(row, src_pk_col)
                        src_eid = src_meta["entity_id_map"].get(src_pk_val)
                        tgt_eid = tgt_id_map.get(self._lookup_identity_value(tgt_pk_col, fk_val))
                        if not tgt_eid:
                            # 归一化匹配: 容错 SUP-001 vs SUP001 等格式差异
                            raw = tgt_norm_index.get(self._normalize_fk_value(fk_val))
                            if raw is not None:
                                tgt_eid = tgt_id_map.get(self._lookup_identity_value(tgt_pk_col, raw))
                        if not src_eid or not tgt_eid:
                            continue
                        # 多行映射同一实体对时去重 (如订单按 items 展开的多行)
                        if (src_eid, tgt_eid) in seen_pairs:
                            continue
                        seen_pairs.add((src_eid, tgt_eid))
                        src_exists = self._db.query(Entity).filter(Entity.id == src_eid).first()
                        tgt_exists = self._db.query(Entity).filter(Entity.id == tgt_eid).first()
                        if not src_exists or not tgt_exists:
                            continue
                        rel = Relation(
                            id=self._stable_relation_id(ontology_id, src_eid, tgt_eid, rel_type, "fk_inference"),
                            ontology_id=ontology_id,
                            source_entity=src_eid, target_entity=tgt_eid,
                            type=rel_type, properties={"fk_column": fk_col, "source": "fk_inference"},
                            confidence=0.85,
                        )
                        self._db.merge(rel)
                        src_values.append(src_eid)
                        tgt_values.append(tgt_eid)
                        written += 1
                    if written:
                        fk_cols_linked.add(fk_col)
                        cardinality = self._infer_cardinality(src_values, tgt_values)
                        inferred_link = self._upsert_inferred_link_mapping(
                            ontology_id=ontology_id,
                            src_dataset_id=src_m.curated_dataset_id,
                            tgt_dataset_id=tgt_m.curated_dataset_id,
                            relation_type=rel_type,
                            src_key=fk_col,
                            tgt_key=tgt_pk_col,
                            model=OntologyLinkMapping,
                        )
                        for rel in self._db.query(Relation).filter(
                            Relation.ontology_id == ontology_id,
                            Relation.type == rel_type,
                        ).all():
                            props = dict(rel.properties or {})
                            if props.get("source") == "fk_inference" and props.get("fk_column") == fk_col:
                                props["cardinality"] = cardinality
                                rel.properties = props
                        self._db.commit()
                        self._write_neo4j_relations(ontology_id, src_meta["entity_class"], tgt_class, rel_type)
                        results.append({"src": src_meta["entity_class"], "tgt": tgt_class,
                                        "rel_type": rel_type, "fk_col": fk_col, "count": written,
                                        "cardinality": cardinality,
                                        "link_mapping_id": inferred_link.id if inferred_link else None,
                                        "link_mapping_status": inferred_link.status if inferred_link else None})

                # ── 策略 5: 跨表同名引用列匹配 ───────────────────────────────────
                # 当两张表有完全同名的列（如中文的"患者ID"、"订单号"），且该列看起来像
                # 引用列时，直接用该列做值关联（不依赖 PK 列名一致）。
                _ref_keywords = {"id", "编号", "编码", "号", "单号", "订单"}
                _meta_extraction_cols = {"record_id", "row_type", "rule_index", "rule_count",
                    "section_index", "section_title", "section_text", "table_index",
                    "table_row_index", "thresholds", "doc_summary", "sections",
                    "condition", "action", "name", "qualifier", "definition",
                    "extraction_method", "extraction_strategy", "extraction_error"}
                _same_name_candidates = []
                for col in src_cols:
                    if col in fk_cols_linked or col == src_meta.get("pk_col"):
                        continue
                    if col not in tgt_meta["columns"]:
                        continue
                    if col.lower() in _meta_extraction_cols:
                        continue
                    col_lower = col.lower()
                    # 列名包含引用关键词 或 列值匹配 ID 模式
                    is_ref_name = any(k in col_lower for k in _ref_keywords)
                    if not is_ref_name:
                        src_vals = {str(r.get(col, "")).strip() for r in src_meta.get("rows", []) if r.get(col)}
                        src_vals.discard("")
                        if len(src_vals) >= 2 and all(
                            re.match(r'^[A-Za-z]+[\d_\-]+\d+$', v) for v in src_vals if len(v) > 1
                        ):
                            is_ref_name = True
                    if is_ref_name:
                        _same_name_candidates.append(col)

                if _same_name_candidates:
                    tgt_rows = tgt_meta.get("rows", [])
                    tgt_id_map = tgt_meta["entity_id_map"]
                    tgt_pk_col_for_lookup = tgt_meta["pk_col"]
                    for col in _same_name_candidates:
                        # 用该列自身做 linkage（值来源列而非PK列）
                        tgt_val_to_eid: dict[str, str] = {}
                        for tgt_row in tgt_rows:
                            v = str(tgt_row.get(col, "")).strip()
                            if not v:
                                continue
                            norm_v = self._normalize_fk_value(v)
                            tgt_pk_val = self._row_identity_value(tgt_row, tgt_pk_col_for_lookup)
                            eid = tgt_id_map.get(tgt_pk_val)
                            if eid and norm_v:
                                tgt_val_to_eid[norm_v] = eid

                        if len(tgt_val_to_eid) < 2:
                            continue

                        _GENERIC_REL_NAMES = {"ID", "CODE", "NO", "NUM", "NUMBER", "NAME", "KEY", "REF", "TYPE"}
                        rel_name = re.sub(r'[^A-Za-z0-9_]', '', col.replace(" ", "_")).upper() or "REF"
                        if not rel_name or rel_name in _GENERIC_REL_NAMES:
                            # Column name was Chinese-only or too generic after stripping; derive from target entity class
                            _ec = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', tgt_class).upper()
                            rel_name = re.sub(r'[^A-Z0-9_]', '', _ec) or "REF"
                        rel_type = f"HAS_{rel_name}"
                        written = 0
                        src_values: list[str] = []
                        tgt_values: list[str] = []
                        seen_pairs: set[tuple[str, str]] = set()

                        for src_row in src_meta.get("rows", []):
                            v = str(src_row.get(col, "")).strip()
                            if not v:
                                continue
                            src_pk_val = self._row_identity_value(src_row, src_meta["pk_col"])
                            src_eid = src_meta["entity_id_map"].get(src_pk_val)
                            if not src_eid:
                                continue
                            tgt_eid = tgt_val_to_eid.get(self._normalize_fk_value(v))
                            if not tgt_eid:
                                continue
                            if (src_eid, tgt_eid) in seen_pairs:
                                continue
                            seen_pairs.add((src_eid, tgt_eid))
                            if not self._db.query(Entity).filter(Entity.id == src_eid).first():
                                continue
                            if not self._db.query(Entity).filter(Entity.id == tgt_eid).first():
                                continue
                            rel = Relation(
                                id=self._stable_relation_id(ontology_id, src_eid, tgt_eid, rel_type, "same_name_fk"),
                                ontology_id=ontology_id,
                                source_entity=src_eid, target_entity=tgt_eid,
                                type=rel_type,
                                properties={"fk_column": col, "source": "same_name_fk"},
                                confidence=0.8,
                            )
                            self._db.merge(rel)
                            src_values.append(src_eid)
                            tgt_values.append(tgt_eid)
                            written += 1

                        if written:
                            fk_cols_linked.add(col)
                            cardinality = self._infer_cardinality(src_values, tgt_values)
                            self._db.commit()
                            self._write_neo4j_relations(ontology_id, src_meta["entity_class"], tgt_class, rel_type)
                            results.append({"src": src_meta["entity_class"], "tgt": tgt_class,
                                            "rel_type": rel_type, "fk_col": col,
                                            "via": "same_name_fk", "count": written,
                                            "cardinality": cardinality})

                # 跨数据集备用键推断 (PRD §2.4 ③): 只跳过 FK 已实际产出链接的列,
                # 列名疑似 FK 但值匹配不上主键的列仍可走备用键(如 mentioned_supplier 存公司名)
                results.extend(self._infer_alt_key_relations(
                    ontology_id, src_m, src_meta, tgt_m, tgt_meta,
                    skip_src_cols=fk_cols_linked, link_model=OntologyLinkMapping,
                ))
        return results

    # ── 跨数据集备用键推断 (PRD §2.4 ③ "跨数据集关系推断") ──────────────

    def _detect_alt_key_columns(self, rows: list[dict], pk_col: str | None) -> list[str]:
        """检测备用键: 近唯一(≥90%)、非纯数字、值长度足够的非主键列 (如 供应商名称)"""
        import re
        if not rows:
            return []
        alt: list[str] = []
        for col in rows[0].keys():
            if col == pk_col:
                continue
            vals = [str(r.get(col, "") or "").strip() for r in rows]
            vals = [v for v in vals if v]
            if len(vals) < 2:
                continue
            if all(re.fullmatch(r"[\d\s\.,%\-]+", v) for v in vals):
                continue  # 纯数字列(金额/比率)易误连
            if sum(len(v) >= 3 for v in vals) / len(vals) < 0.8:
                continue  # 短值枚举列(等级/状态)
            if len(set(vals)) / len(vals) < 0.9:
                continue
            alt.append(col)
        return alt

    def _infer_alt_key_relations(self, ontology_id: str, src_m: OntologyMapping, src_meta: dict,
                                 tgt_m: OntologyMapping, tgt_meta: dict,
                                 skip_src_cols: set[str], link_model) -> list[dict]:
        """源列值(支持逗号等分隔的多值)与目标备用键值重叠 → 推断关系。

        典型场景: 文档记录的 organizations 字段命中 Supplier.供应商名称。
        """
        import re
        from app.models.entity import Entity
        from app.models.relation import Relation

        tgt_rows = tgt_meta.get("rows", [])
        tgt_pk_col = tgt_meta["pk_col"]
        tgt_id_map = tgt_meta["entity_id_map"]
        tgt_class = tgt_meta["entity_class"]
        results: list[dict] = []

        # 跳过文档级元数据列与常量列: 全行同值的列不携带行级链接信息,
        # 否则文档摘要里提到的公司会让该文档所有行都连过去
        meta_cols = {"doc_summary", "sections", "source_file", "filename",
                     "markdown_text", "document_text", "extraction_method"}
        src_rows = src_meta.get("rows", [])
        usable_src_cols = []
        for col in src_meta["columns"]:
            if col in meta_cols or col == src_meta["pk_col"]:
                continue
            vals = {str(r.get(col, "") or "").strip() for r in src_rows}
            vals.discard("")
            if len(vals) <= 1:
                continue
            usable_src_cols.append(col)

        rel_name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", tgt_class).upper()
        rel_name = re.sub(r"[^A-Z0-9_]", "", rel_name) or "REF"
        rel_type = f"HAS_{rel_name}"

        for alt_col in self._detect_alt_key_columns(tgt_rows, tgt_pk_col):
            alt_to_eid: dict[str, str] = {}
            for row in tgt_rows:
                v = str(row.get(alt_col, "") or "").strip()
                if not v:
                    continue
                eid = tgt_id_map.get(self._row_identity_value(row, tgt_pk_col))
                if eid:
                    alt_to_eid[self._normalize_fk_value(v)] = eid
            if len(alt_to_eid) < 2:
                continue

            col_pairs: dict[str, set[tuple[str, str]]] = {}
            for row in src_meta["rows"]:
                src_eid = src_meta["entity_id_map"].get(
                    self._row_identity_value(row, src_meta["pk_col"]))
                if not src_eid:
                    continue
                for col in usable_src_cols:
                    if col in skip_src_cols:
                        continue
                    raw = str(row.get(col, "") or "").strip()
                    if not raw:
                        continue
                    parts = [p.strip() for p in re.split(r"[,，、;；|]", raw) if p.strip()]
                    for part in parts:
                        norm = self._normalize_fk_value(part)
                        if len(norm) < 3:
                            continue
                        tgt_eid = alt_to_eid.get(norm)
                        if tgt_eid:
                            col_pairs.setdefault(col, set()).add((src_eid, tgt_eid))

            for col, pairs in col_pairs.items():
                if not pairs:
                    continue  # 全名精确匹配(归一化≥3字符)误连概率低, 单行命中即视为有效
                written = 0
                src_values, tgt_values = [], []
                for src_eid, tgt_eid in pairs:
                    if not self._db.query(Entity).filter(Entity.id == src_eid).first():
                        continue
                    if not self._db.query(Entity).filter(Entity.id == tgt_eid).first():
                        continue
                    rel = Relation(
                        id=self._stable_relation_id(ontology_id, src_eid, tgt_eid, rel_type, "fk_inference"),
                        ontology_id=ontology_id,
                        source_entity=src_eid, target_entity=tgt_eid,
                        type=rel_type,
                        properties={"fk_column": col, "alt_column": alt_col,
                                    "via": "alternate_key", "source": "fk_inference"},
                        confidence=0.75,
                    )
                    self._db.merge(rel)
                    src_values.append(src_eid)
                    tgt_values.append(tgt_eid)
                    written += 1
                if not written:
                    continue
                cardinality = self._infer_cardinality(src_values, tgt_values)
                inferred_link = self._upsert_inferred_link_mapping(
                    ontology_id=ontology_id,
                    src_dataset_id=src_m.curated_dataset_id,
                    tgt_dataset_id=tgt_m.curated_dataset_id,
                    relation_type=rel_type,
                    src_key=col,
                    tgt_key=alt_col,
                    model=link_model,
                )
                self._db.commit()
                self._write_neo4j_relations(ontology_id, src_meta["entity_class"], tgt_class, rel_type)
                results.append({"src": src_meta["entity_class"], "tgt": tgt_class,
                                "rel_type": rel_type, "fk_col": col, "alt_col": alt_col,
                                "via": "alternate_key", "count": written,
                                "cardinality": cardinality,
                                "link_mapping_id": inferred_link.id if inferred_link else None,
                                "link_mapping_status": inferred_link.status if inferred_link else None})
        return results

    def _upsert_inferred_link_mapping(
        self,
        ontology_id: str,
        src_dataset_id: str | None,
        tgt_dataset_id: str | None,
        relation_type: str,
        src_key: str,
        tgt_key: str,
        model,
    ):
        if not src_dataset_id or not tgt_dataset_id:
            return None
        existing = self._db.query(model).filter(
            model.ontology_id == ontology_id,
            model.src_dataset_id == src_dataset_id,
            model.tgt_dataset_id == tgt_dataset_id,
            model.relation_type == relation_type,
            model.src_key == src_key,
            model.tgt_key == tgt_key,
        ).first()
        if existing:
            if existing.status not in ("active", "inferred"):
                existing.status = "inferred"
            return existing
        link = model(
            ontology_id=ontology_id,
            src_dataset_id=src_dataset_id,
            tgt_dataset_id=tgt_dataset_id,
            relation_type=relation_type,
            src_key=src_key,
            tgt_key=tgt_key,
            status="inferred",
        )
        self._db.add(link)
        return link

    # ── 工具方法 ─────────────────────────────────────────────────────

    def _normalize_mapping(self, mapping: OntologyMapping, rows: list[dict]) -> None:
        if not rows:
            return
        sample = rows[0]
        field_map = dict(mapping.field_mapping or {})
        for col in sample.keys():
            if col == "content":
                continue
            if col not in field_map:
                field_map[col] = col
        pk_col = field_map.get("__primary_key__")
        order_pk_can_merge = (
            pk_col
            and pk_col != "__row_hash__"
            and pk_col in sample
            and ("order" in (mapping.entity_class or "").lower() or "订单" in str(mapping.entity_class or ""))
            and all(self._has_display_value(row.get(pk_col)) for row in rows)
        )
        if (
            not pk_col
            or (
                pk_col != "__row_hash__"
                and not order_pk_can_merge
                and (pk_col not in sample or not self._is_unique_col(rows, pk_col))
            )
        ):
            field_map["__primary_key__"] = self._choose_pk_col(rows)
        field_map["__properties__"] = self._property_metadata(rows, field_map)
        if field_map != (mapping.field_mapping or {}):
            mapping.field_mapping = field_map
            self._db.commit()

    def _property_metadata(self, rows: list[dict], field_map: dict) -> list[dict]:
        if not rows:
            return []
        sample = rows[0]
        existing = {
            item.get("column"): item
            for item in field_map.get("__properties__", [])
            if isinstance(item, dict) and item.get("column")
        }
        technical_cols = {
            "content", "storage_uri", "markdown_text", "structured_extraction_error",
            "structured_extraction_ok", "extraction_strategy", "extraction_method",
            "source_dataset_id",
        }
        result = []
        for col in sample.keys():
            if col == "content":
                continue
            current = dict(existing.get(col) or {})
            result.append({
                "column": col,
                "property": field_map.get(col, col),
                "type": current.get("type") or self._infer_property_type(rows, col),
                "hidden": bool(current.get("hidden", col in technical_cols or col.startswith("__"))),
                "technical": bool(current.get("technical", col in technical_cols or col.startswith("__"))),
                "confidence": float(current.get("confidence", 0.85)),
                "description": current.get("description", ""),
            })
        return result

    def _property_metadata_by_column(self, field_map: dict) -> dict:
        return {
            item.get("column"): item
            for item in (field_map or {}).get("__properties__", [])
            if isinstance(item, dict) and item.get("column")
        }

    def _infer_property_type(self, rows: list[dict], col: str) -> str:
        from app.services.v2.pipeline.steps.schema_inference import SchemaInferenceStep

        for row in rows[:20]:
            value = row.get(col)
            if value not in (None, ""):
                return SchemaInferenceStep._infer_type(str(value).strip())
        return "string"

    def _choose_pk_col(self, rows: list[dict]) -> str:
        if not rows:
            return "id"
        cols = [c for c in rows[0].keys() if c != "content"]

        id_like_cols = []
        for col in cols:
            lower = col.lower()
            if lower == "id" or lower.endswith("_id") or col.endswith("ID") or "id" in lower:
                id_like_cols.append(col)
        for col in id_like_cols:
            if self._is_unique_col(rows, col):
                return col
        for preferred in ("filename", "source_file", "source_dataset_id", "order_id", "订单号", "供应商ID"):
            if preferred in cols and self._is_unique_col(rows, preferred):
                return preferred
        for col in cols:
            if self._is_unique_col(rows, col):
                return col
        return "__row_hash__"

    def _is_unique_col(self, rows: list[dict], col: str) -> bool:
        values = [str(row.get(col, "")).strip() for row in rows]
        return bool(values) and all(values) and len(set(values)) == len(values)

    def _row_hash(self, row: dict) -> str:
        return json.dumps(
            {k: v for k, v in row.items() if k != "content"},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

    @staticmethod
    def _normalize_fk_value(value: str) -> str:
        """FK 值归一化: 大写并去掉分隔符，容错 SUP-001 / sup_001 / SUP001 等格式差异"""
        import re
        return re.sub(r'[\s\-_]', '', str(value)).upper()

    def _row_identity_value(self, row: dict, pk_col: str | None) -> str:
        if pk_col and pk_col != "__row_hash__" and row.get(pk_col) not in (None, ""):
            return f"{pk_col}:{row.get(pk_col)}"
        return f"row_hash:{self._row_hash(row)}"

    def _lookup_identity_value(self, pk_col: str | None, value: str) -> str:
        if pk_col and pk_col != "__row_hash__":
            return f"{pk_col}:{value}"
        return value

    def _stable_row_id(self, mapping: OntologyMapping, row: dict, pk_col: str | None) -> str:
        identity = self._row_identity_value(row, pk_col)
        return str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"{mapping.ontology_id}:{mapping.entity_class}:{identity}"))

    def _stable_relation_id(self, ontology_id: str, src_eid: str, tgt_eid: str, rel_type: str, source: str) -> str:
        return str(_uuid.uuid5(
            _uuid.NAMESPACE_URL,
            f"{ontology_id}:{src_eid}:{rel_type}:{tgt_eid}:{source}",
        ))

    def _infer_cardinality(self, source_ids: list[str], target_ids: list[str]) -> str:
        if not source_ids or not target_ids:
            return "unknown"
        source_unique = len(set(source_ids)) == len(source_ids)
        target_unique = len(set(target_ids)) == len(target_ids)
        if source_unique and target_unique:
            return "one_to_one"
        if source_unique and not target_unique:
            return "many_to_one"
        if not source_unique and target_unique:
            return "one_to_many"
        return "many_to_many"

    def _has_display_value(self, value) -> bool:
        if value in (None, ""):
            return False
        try:
            return value == value
        except Exception:
            return True

    def _join_display_parts(self, row: dict, cols: tuple[str, ...], min_parts: int = 1) -> str | None:
        parts = []
        for col in cols:
            value = row.get(col)
            if self._has_display_value(value):
                parts.append(str(value))
        if len(parts) >= min_parts:
            return " / ".join(parts)
        return None

    def _display_name(self, mapping: OntologyMapping, row: dict, pk_col: str | None, index: int) -> str:
        row_type = str(row.get("row_type") or "")
        source_file = row.get("source_file") or row.get("filename")
        if row_type == "rule":
            condition = str(row.get("condition") or "").strip()
            suffix = f" #{row.get('rule_index')}" if row.get("rule_index") not in (None, "") else ""
            return f"{source_file or mapping.entity_class} / Rule{suffix}: {condition[:80]}".strip()
        if row_type == "table_row":
            section = row.get("section_title") or "Table"
            table_idx = row.get("table_index") or 1
            row_idx = row.get("table_row_index") or index + 1
            return f"{source_file or mapping.entity_class} / {section} / T{table_idx}R{row_idx}"
        if row_type == "section":
            section = row.get("section_title") or "Section"
            section_idx = row.get("section_index") or index + 1
            return f"{source_file or mapping.entity_class} / {section} #{section_idx}"
        if row.get("record_id") not in (None, "") and pk_col == "__row_hash__":
            return str(row.get("record_id"))

        entity_class_lower = (mapping.entity_class or "").lower()
        if "order" in entity_class_lower or "订单" in str(mapping.entity_class or ""):
            order_label = self._join_display_parts(row, ("order_id", "order_name"), min_parts=1)
            if order_label:
                return order_label
            order_label = self._join_display_parts(row, ("订单号", "订单名称"), min_parts=1)
            if order_label:
                return order_label

        for cols in (
            ("order_id", "items.sku"),
            ("order_id", "items.name"),
            ("订单号", "物料编码"),
        ):
            label = self._join_display_parts(row, cols, min_parts=2)
            if label:
                return label

        inventory_label = self._join_display_parts(row, ("日期", "物料编码", "操作类型", "所在仓库"), min_parts=3)
        if inventory_label:
            return f"{inventory_label} #{index + 1}"

        supplier_label = self._join_display_parts(row, ("供应商ID", "供应商名称"), min_parts=2)
        if supplier_label:
            return supplier_label

        if pk_col and pk_col != "__row_hash__" and pk_col in row and self._has_display_value(row.get(pk_col)):
            return str(row.get(pk_col))

        candidates = []
        for col in row.keys():
            lower = col.lower()
            if any(token in lower for token in ("name", "title")) or any(token in col for token in ("名称", "标题", "文件名")):
                candidates.append(col)
        for col in (
            pk_col,
            "order_id", "订单号", "运单号", "供应商ID", "物料编码",
            "filename", "source_file",
        ):
            if col and col != "__row_hash__" and col in row:
                candidates.append(col)
        for col in candidates:
            value = row.get(col)
            if value not in (None, ""):
                return str(value)
        return f"{mapping.entity_class} #{index + 1}"

    def _first_value(self, row: dict, cols: list[str]) -> str | None:
        for col in cols:
            if not col or col == "__row_hash__" or col not in row:
                continue
            value = row.get(col)
            if self._has_display_value(value):
                return str(value)
        return None

    def _identity_columns(self, row: dict, pk_col: str | None) -> list[str]:
        cols = list(row.keys())
        result: list[str] = []
        if pk_col:
            result.append(pk_col)
        for col in cols:
            lower = col.lower()
            if (
                lower == "id"
                or lower.endswith("_id")
                or col.endswith("ID")
                or "编号" in col
                or "编码" in col
                or col in ("订单号", "运单号", "单号")
            ):
                result.append(col)
        return list(dict.fromkeys(result))

    def _name_columns(self, row: dict) -> list[str]:
        result: list[str] = []
        for col in row.keys():
            lower = col.lower()
            if any(token in lower for token in ("name", "title")) or any(token in col for token in ("名称", "姓名", "标题")):
                result.append(col)
        return result

    def _has_cjk(self, value: str | None) -> bool:
        return bool(value and re.search(r"[\u4e00-\u9fff]", value))

    def _instance_names(self, mapping: OntologyMapping, row: dict, pk_col: str | None, index: int) -> dict[str, str]:
        """Return row-instance names. These must describe the record, not the schema/table."""
        display = self._display_name(mapping, row, pk_col, index)
        id_value = self._first_value(row, self._identity_columns(row, pk_col))
        name_value = self._first_value(row, self._name_columns(row))

        if not self._has_cjk(display):
            en = display
        elif id_value and name_value and id_value != name_value and not self._has_cjk(name_value):
            en = f"{id_value} / {name_value}"
        else:
            en = display

        return {
            "display_name": display,
            "name_cn": str(display)[:200],
            "name_en": str(en)[:200],
        }

    def _rows_to_entities(self, mapping: OntologyMapping, rows: list[dict]) -> list[dict]:
        field_map = mapping.field_mapping or {}
        pk_col = field_map.get("__primary_key__") or self._choose_pk_col(rows)
        property_meta = self._property_metadata_by_column(field_map)
        entities_by_id: dict[str, dict] = {}
        for index, row in enumerate(rows):
            props: dict = {"ontology_id": mapping.ontology_id}
            for col, prop in field_map.items():
                if col.startswith("__"):
                    continue
                if property_meta.get(col, {}).get("hidden"):
                    continue
                if col in row:
                    props[prop] = row[col]
            props["id"] = self._stable_row_id(mapping, row, pk_col if pk_col in row else None)
            props["source_id"] = props["id"]
            props.update(self._instance_names(mapping, row, pk_col, index))
            props["name"] = props["name_cn"]
            props["object_type"] = mapping.entity_class
            if props["id"] in entities_by_id:
                existing = entities_by_id[props["id"]]
                existing["source_row_count"] = int(existing.get("source_row_count", 1)) + 1
                continue
            props["source_row_count"] = 1
            entities_by_id[props["id"]] = props
        return list(entities_by_id.values())

    def _write_v1_entities(self, mapping: OntologyMapping, entities: list[dict]) -> int:
        from app.models.entity import Entity
        count = 0
        try:
            for props in entities:
                eid = props["id"]
                name_cn = props.get("name_cn") or props.get("display_name") or eid
                name_en = props.get("name_en") or props.get("display_name") or eid
                other = {k: v for k, v in props.items() if k not in ("id", "ontology_id")}
                self._db.merge(Entity(
                    id=eid, ontology_id=mapping.ontology_id,
                    name_cn=str(name_cn)[:200], name_en=str(name_en)[:200],
                    type=mapping.entity_class, properties=other,
                    confidence=mapping.confidence or 0.85,
                ))
                count += 1
            self._db.commit()
        except Exception as e:
            logger.warning(f"v1 entities 写入失败: {e}")
            self._db.rollback()
        return count

    # ── Logic / Action Discovery ───────────────────────────────────

    def _upsert_v2_logic(self, ontology_id: str, name: str, logic_type: str, description: str,
                         target_entity_type: str | None, expression: dict, source_type: str,
                         severity: str = "info") -> bool:
        from app.models.v2.logic import OntologyLogicRule

        # session 为 autoflush=False, 先 flush 让同一次运行中已 add 的同名规则可见
        self._db.flush()
        exists = self._db.query(OntologyLogicRule).filter(
            OntologyLogicRule.ontology_id == ontology_id,
            OntologyLogicRule.name == name,
        ).first()
        if exists:
            exists.logic_type = logic_type
            exists.description = description
            exists.target_entity_type = target_entity_type
            exists.expression = expression
            exists.source_type = source_type
            exists.severity = severity
            return False
        self._db.add(OntologyLogicRule(
            ontology_id=ontology_id,
            name=name,
            logic_type=logic_type,
            description=description,
            target_entity_type=target_entity_type,
            expression=expression,
            source_type=source_type,
            severity=severity,
            status="draft",
            enabled=True,
        ))
        return True

    @staticmethod
    def _readable_formula(logic_type: str, expr: dict | None, target: str | None = None) -> str:
        """把 logic 的结构化 expression 转成人类可读的公式串 (用于 v1 LogicRule.formula)"""
        e = expr or {}
        if logic_type == "validation":
            if e.get("missing_count") is not None:
                return f"{e.get('column')} 必填（缺失 {e.get('missing_count')} 行）"
            if e.get("properties"):
                return f"{target or ''} 字段类型契约（{len(e['properties'])} 个属性）".strip()
            op = e.get("operator") or e.get("op")
            if op:
                return f"{e.get('column') or e.get('field')} {op} {e.get('value')}"
            return f"{target or ''} 数据质量校验".strip()
        if logic_type == "state":
            states = e.get("states") or []
            shown = ", ".join(str(x) for x in states[:6])
            return f"{e.get('state_property')} ∈ {{{shown}}}"
        if logic_type == "mapping":
            return f"{target} ← curated:{str(e.get('curated_dataset_id') or '')[:8]}"
        if logic_type == "inference":
            return f"{e.get('src')} -[{e.get('rel_type')}]-> {e.get('tgt')}"
        if logic_type == "automation":
            return f"{e.get('trigger') or 'event'} ⇒ {e.get('effect') or 'action'}"
        return logic_type

    def _upsert_v1_logic(self, ontology_id: str, name: str, logic_type: str, description: str,
                         linked_entities: list[str] | None = None, confidence: float = 0.85,
                         formula: str | None = None) -> bool:
        from app.models.logic import LogicRule

        formula = formula or logic_type
        self._db.flush()
        exists = self._db.query(LogicRule).filter(
            LogicRule.ontology_id == ontology_id,
            LogicRule.name_cn == name,
        ).first()
        if exists:
            exists.description = description
            exists.formula = formula
            exists.linked_entities = linked_entities or []
            return False
        self._db.add(LogicRule(
            id=str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"{ontology_id}:logic:{name}")),
            ontology_id=ontology_id,
            name_cn=name,
            name_en=name.replace(" ", "_").replace(":_", "_"),
            description=description,
            formula=formula,
            confidence=confidence,
            enabled=True,
            status="draft",
            linked_entities=linked_entities or [],
        ))
        return True

    def _discover_logic_rules(self, ontology_id: str, mappings: list[OntologyMapping],
                              mapping_meta: dict, relation_results: list[dict]) -> dict:
        created_v2 = 0
        created_v1 = 0

        for m in mappings:
            meta = mapping_meta.get(m.id)
            if not meta:
                continue
            field_map = m.field_mapping or {}
            pk_col = meta.get("pk_col")
            mapping_name = f"Mapping Rule: {m.entity_class}"
            desc = f"{m.entity_class} object type is built from curated dataset {m.curated_dataset_id}."
            expr = {
                "curated_dataset_id": m.curated_dataset_id,
                "primary_key": pk_col,
                "field_mapping": field_map,
                "property_mappings": meta.get("property_mappings", []),
                "row_count": len(meta.get("rows", [])),
            }
            created_v2 += int(self._upsert_v2_logic(
                ontology_id, mapping_name, "mapping", desc, m.entity_class, expr, "mapping", "info",
            ))
            created_v1 += int(self._upsert_v1_logic(
                ontology_id, mapping_name, "mapping", desc, [m.entity_class], 0.9,
                formula=self._readable_formula("mapping", expr, m.entity_class),
            ))

            for col in meta.get("columns", []):
                if col == "content":
                    continue
                values = [row.get(col) for row in meta.get("rows", [])]
                missing = sum(1 for v in values if v in (None, ""))
                if missing:
                    name = f"Validation Rule: {m.entity_class}.{col} completeness"
                    description = f"Validate completeness for {m.entity_class}.{col}; missing rows: {missing}."
                    created_v2 += int(self._upsert_v2_logic(
                        ontology_id, name, "validation", description, m.entity_class,
                        {"column": col, "missing_count": missing, "row_count": len(values)},
                        "schema_quality", "warning",
                    ))
                    created_v1 += int(self._upsert_v1_logic(
                        ontology_id, name, "validation", description, [m.entity_class], 0.8,
                        formula=self._readable_formula(
                            "validation", {"column": col, "missing_count": missing}, m.entity_class),
                    ))

            typed_properties = [
                {"property": item.get("property"), "column": item.get("column"), "type": item.get("type")}
                for item in meta.get("property_mappings", [])
                if isinstance(item, dict) and not item.get("hidden")
            ]
            if typed_properties:
                name = f"Schema Rule: {m.entity_class} property types"
                description = f"Schema contract for {m.entity_class} properties inferred from curated dataset columns."
                created_v2 += int(self._upsert_v2_logic(
                    ontology_id, name, "validation", description, m.entity_class,
                    {"properties": typed_properties, "primary_key": pk_col},
                    "schema", "info",
                ))
                created_v1 += int(self._upsert_v1_logic(
                    ontology_id, name, "validation", description, [m.entity_class], 0.84,
                    formula=self._readable_formula(
                        "validation", {"properties": typed_properties, "primary_key": pk_col}, m.entity_class),
                ))

            state_cols = [
                col for col in meta.get("columns", [])
                if any(token in col.lower() for token in ("status", "state")) or any(token in col for token in ("状态", "阶段"))
            ]
            for col in state_cols:
                states = sorted({str(row.get(col)) for row in meta.get("rows", []) if row.get(col) not in (None, "")})
                if states:
                    name = f"State Rule: {m.entity_class}.{col}"
                    description = f"State property discovered on {m.entity_class}.{col}: {', '.join(states[:8])}."
                    created_v2 += int(self._upsert_v2_logic(
                        ontology_id, name, "state", description, m.entity_class,
                        {"state_property": col, "states": states}, "state_detection", "info",
                    ))
                    created_v1 += int(self._upsert_v1_logic(
                        ontology_id, name, "state", description, [m.entity_class], 0.82,
                        formula=self._readable_formula(
                            "state", {"state_property": col, "states": states}, m.entity_class),
                    ))

        for rel in relation_results:
            if not rel.get("count"):
                continue
            name = f"Inference Rule: {rel.get('src')} -> {rel.get('tgt')} via {rel.get('rel_type')}"
            description = f"Infer link type {rel.get('rel_type')} from {rel.get('src')} to {rel.get('tgt')}."
            created_v2 += int(self._upsert_v2_logic(
                ontology_id, name, "inference", description, rel.get("src"),
                {"src": rel.get("src"), "tgt": rel.get("tgt"), "rel_type": rel.get("rel_type"),
                 "fk_col": rel.get("fk_col"), "src_key": rel.get("src_key"), "tgt_key": rel.get("tgt_key")},
                "relation_inference", "info",
            ))
            created_v1 += int(self._upsert_v1_logic(
                ontology_id, name, "inference", description, [rel.get("src"), rel.get("tgt")], 0.85,
                formula=self._readable_formula("inference", {
                    "src": rel.get("src"), "tgt": rel.get("tgt"), "rel_type": rel.get("rel_type")}),
            ))

        automation_name = "Automation Rule: Approved curated dataset triggers mapping sync"
        automation_desc = "When a curated dataset is approved, incremental ontology mapping can upsert objects, links, vectors, logic and actions."
        created_v2 += int(self._upsert_v2_logic(
            ontology_id, automation_name, "automation", automation_desc, None,
            {"trigger": "curated_review.approved", "effect": "mapping_resync"},
            "workflow", "info",
        ))
        created_v1 += int(self._upsert_v1_logic(
            ontology_id, automation_name, "automation", automation_desc, [], 0.86,
            formula=self._readable_formula(
                "automation", {"trigger": "curated_review.approved", "effect": "mapping_resync"}),
        ))

        self._db.commit()
        from app.models.v2.logic import OntologyLogicRule
        from app.models.logic import LogicRule
        return {
            "created_v2": created_v2,
            "created_v1": created_v1,
            "total_v2": self._db.query(OntologyLogicRule).filter(OntologyLogicRule.ontology_id == ontology_id).count(),
            "total_v1": self._db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id).count(),
        }

    def _upsert_v2_action(self, ontology_id: str, name: str, category: str, description: str,
                          target_entity_type: str | None, parameters: list, effects: list,
                          criteria: list | None = None) -> bool:
        from app.models.v2.action import OntologyActionType

        self._db.flush()
        exists = self._db.query(OntologyActionType).filter(
            OntologyActionType.ontology_id == ontology_id,
            OntologyActionType.name == name,
        ).first()
        if exists:
            exists.action_category = category
            exists.description = description
            exists.target_entity_type = target_entity_type
            exists.parameters = parameters
            exists.effects = effects
            exists.submission_criteria = criteria
            return False
        self._db.add(OntologyActionType(
            ontology_id=ontology_id,
            name=name,
            action_category=category,
            description=description,
            target_entity_type=target_entity_type,
            parameters=parameters,
            submission_criteria=criteria,
            effects=effects,
            permission_rules=[{"role": "admin"}],
            status="draft",
            enabled=True,
        ))
        return True

    def _upsert_v1_action(self, ontology_id: str, name: str, category: str, description: str,
                          linked_entities: list[str] | None = None, linked_logic_ids: list[str] | None = None,
                          confidence: float = 0.82) -> bool:
        from app.models.action import Action

        self._db.flush()
        exists = self._db.query(Action).filter(
            Action.ontology_id == ontology_id,
            Action.name_cn == name,
        ).first()
        function_name = name.lower().replace(" ", "_").replace(":", "").replace("-", "_")
        function_code = (
            f"def {function_name}(context: dict) -> dict:\n"
            f"    return {{'status': 'queued', 'action': '{name}', 'context': context}}\n"
        )
        if exists:
            exists.description = description
            exists.execution_rule = category
            exists.linked_entities = linked_entities or []
            exists.linked_logic_ids = linked_logic_ids or []
            exists.function_code = function_code
            return False
        self._db.add(Action(
            id=str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"{ontology_id}:action:{name}")),
            ontology_id=ontology_id,
            name_cn=name,
            name_en=function_name,
            description=description,
            execution_rule=category,
            function_code=function_code,
            linked_entities=linked_entities or [],
            linked_logic_ids=linked_logic_ids or [],
            confidence=confidence,
            enabled=True,
            status="draft",
        ))
        return True

    def _discover_action_types(self, ontology_id: str, mappings: list[OntologyMapping],
                               mapping_meta: dict,
                               relation_results: list[dict], logic_result: dict) -> dict:
        created_v2 = 0
        created_v1 = 0

        for m in mappings:
            meta = mapping_meta.get(m.id, {})
            for verb, category, effect in (
                ("Create", "crud", "create_object"),
                ("Update", "crud", "update_object"),
            ):
                name = f"{verb} {m.entity_class}"
                description = f"{verb} object records for {m.entity_class}."
                created_v2 += int(self._upsert_v2_action(
                    ontology_id, name, category, description, m.entity_class,
                    [{"name": "data", "type": "object", "required": True}],
                    [{"action": effect, "entity_type": m.entity_class}],
                    [{"logic_type": "validation", "target_entity_type": m.entity_class}],
                ))
                created_v1 += int(self._upsert_v1_action(
                    ontology_id, name, category, description, [m.entity_class], [], 0.84,
                ))

            visible_props = [
                item for item in meta.get("property_mappings", [])
                if isinstance(item, dict) and not item.get("hidden")
            ]
            state_props = [
                item for item in visible_props
                if any(token in str(item.get("column", "")).lower() for token in ("status", "state"))
                or any(token in str(item.get("column", "")) for token in ("状态", "阶段"))
            ]
            for item in state_props:
                prop = item.get("property") or item.get("column")
                name = f"Change {m.entity_class} {prop}"
                description = f"Change state property {prop} on {m.entity_class}."
                created_v2 += int(self._upsert_v2_action(
                    ontology_id, name, "state_transition", description, m.entity_class,
                    [{"name": "target_id", "type": "object_ref", "required": True},
                     {"name": str(prop), "type": "string", "required": True}],
                    [{"action": "set_property", "property": prop}],
                    [{"logic_type": "state", "target_entity_type": m.entity_class}],
                ))
                created_v1 += int(self._upsert_v1_action(
                    ontology_id, name, "state_transition", description, [m.entity_class], [], 0.82,
                ))

            timestamp_props = [
                item for item in visible_props
                if item.get("type") == "timestamp" or any(token in str(item.get("column", "")).lower() for token in ("date", "time", "_at"))
            ]
            for item in timestamp_props[:3]:
                prop = item.get("property") or item.get("column")
                name = f"Update {m.entity_class} {prop}"
                description = f"Update timestamp property {prop} on {m.entity_class}."
                created_v2 += int(self._upsert_v2_action(
                    ontology_id, name, "crud", description, m.entity_class,
                    [{"name": "target_id", "type": "object_ref", "required": True},
                     {"name": str(prop), "type": "timestamp", "required": True}],
                    [{"action": "set_property", "property": prop}],
                ))
                created_v1 += int(self._upsert_v1_action(
                    ontology_id, name, "crud", description, [m.entity_class], [], 0.8,
                ))

        seen_rel_actions: set[str] = set()
        for rel in relation_results:
            if not rel.get("count"):
                continue
            for verb, effect in (("Link", "merge_relationship"), ("Unlink", "delete_relationship")):
                name = f"{verb} {rel.get('src')} to {rel.get('tgt')}"
                if name in seen_rel_actions:
                    continue
                seen_rel_actions.add(name)
                description = f"{verb} {rel.get('rel_type')} relation between {rel.get('src')} and {rel.get('tgt')}."
                created_v2 += int(self._upsert_v2_action(
                    ontology_id, name, "link", description, rel.get("src"),
                    [{"name": "source_id", "type": "object_ref", "required": True},
                     {"name": "target_id", "type": "object_ref", "required": True}],
                    [{"action": effect, "relation_type": rel.get("rel_type")}],
                ))
                created_v1 += int(self._upsert_v1_action(
                    ontology_id, name, "link", description, [rel.get("src"), rel.get("tgt")], [], 0.83,
                ))

        for name, category, desc in (
            ("Review Curated Mapping Candidate", "review", "Review and approve generated mapping, logic and action candidates."),
            ("Repair Data Quality Issue", "repair", "Fix missing, duplicated or invalid mapped object properties."),
            ("Sync Approved Object to External System", "writeback", "Write approved object changes back to an external system."),
        ):
            created_v2 += int(self._upsert_v2_action(
                ontology_id, name, category, desc, None,
                [{"name": "target_id", "type": "string", "required": False}],
                [{"action": category}],
            ))
            created_v1 += int(self._upsert_v1_action(
                ontology_id, name, category, desc, [], [], 0.78,
            ))

        self._db.commit()
        from app.models.v2.action import OntologyActionType
        from app.models.action import Action
        return {
            "created_v2": created_v2,
            "created_v1": created_v1,
            "total_v2": self._db.query(OntologyActionType).filter(OntologyActionType.ontology_id == ontology_id).count(),
            "total_v1": self._db.query(Action).filter(Action.ontology_id == ontology_id).count(),
            "logic_total_v2": logic_result.get("total_v2", 0),
        }

    def _write_neo4j(self, entity_class: str, entities: list[dict]) -> int:
        try:
            from app.services.v2.graph.neo4j_service import Neo4jService
            neo = Neo4jService()
            if neo.available:
                count = neo.batch_upsert_entities(entity_class, entities)
                neo.close()
                return count
        except Exception as e:
            logger.error(f"Neo4j 写入失败: {e}")
        return 0

    def _write_neo4j_relations(self, ontology_id: str, src_class: str, tgt_class: str, rel_type: str) -> None:
        from app.models.relation import Relation
        from app.models.entity import Entity
        try:
            from app.services.v2.graph.neo4j_service import Neo4jService
            neo = Neo4jService()
            if not neo.available:
                return
            rels = self._db.query(Relation).filter(
                Relation.ontology_id == ontology_id, Relation.type == rel_type,
            ).all()
            for r in rels:
                neo.upsert_relation(src_class, r.source_entity,
                                    tgt_class, r.target_entity, rel_type,
                                    props={"ontology_id": ontology_id, "confidence": r.confidence})
            neo.close()
        except Exception as e:
            logger.warning(f"Neo4j relation 写入失败（非致命）: {e}")

    # ── FK 检测（4 级策略）─────────────────────────────────────────

    def _detect_fk_columns(
        self, src_cols: list[str], tgt_entity_class: str, tgt_dataset_name: str,
        src_sample_rows: list[dict] | None = None,
        tgt_pk_values: set[str] | None = None,
    ) -> list[tuple[str, str]]:
        """多级 FK 检测: 1)标准_id/.id 2)语义词 3)值重叠 4)值模式 5)LLM"""
        candidates = []
        import re
        tgt_lower = tgt_entity_class.lower()
        tgt_name_lower = (tgt_dataset_name or "").lower()
        tgt_parts = [p.lower() for p in re.split(r'[_\-\s]|(?<=[a-z])(?=[A-Z])', tgt_entity_class) if p]
        tgt_parts.extend([p.lower() for p in re.split(r'[_\-\s]', tgt_name_lower) if p])

        for col in src_cols:
            col_lower = col.lower().rstrip("s")
            # 点号(JSON flatten 产物)与空格/连字符统一归一化为下划线
            col_clean = re.sub(r'[\s\-\.]', '_', col_lower)

            is_standard_fk = col_clean.endswith("_id") or col.endswith("Id") or col.endswith("ID")
            if is_standard_fk:
                col_prefix = re.sub(r'[_]?id$', '', col_clean)
                if (col_prefix in tgt_lower or tgt_lower in col_prefix or
                    any(part in col_prefix for part in tgt_parts if len(part) > 2)):
                    rel_name = col_prefix.upper().replace("-", "_") or tgt_lower.upper()
                    rel_type = f"HAS_{rel_name}" if not rel_name.startswith("HAS_") else rel_name
                    candidates.append((col, rel_type))
                    continue

            col_words = set(re.split(r'[_\-\s]', col_clean))
            tgt_keywords = set(tgt_parts) | {tgt_lower, tgt_name_lower}
            semantic_match = {w for w in (col_words & tgt_keywords) if len(w) > 1}
            if semantic_match:
                rel_name = max(semantic_match, key=len).upper().replace("-", "_")
                rel_type = f"HAS_{rel_name}" if not rel_name.startswith("HAS_") else rel_name
                candidates.append((col, rel_type))
                continue

            # 值重叠检测: 列值与目标主键值高度重合即判定 FK(对中文列名等无法靠列名匹配的情况有效)
            if tgt_pk_values and src_sample_rows:
                tgt_norm = {self._normalize_fk_value(v) for v in tgt_pk_values}
                sample_vals = [str(row.get(col, "")).strip() for row in src_sample_rows[:20] if row.get(col) not in (None, "")]
                if len(sample_vals) >= 2:
                    matched = sum(1 for v in sample_vals if self._normalize_fk_value(v) in tgt_norm)
                    if matched >= 2 and matched / len(sample_vals) >= 0.5:
                        rel_name = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', tgt_entity_class).upper()
                        rel_name = re.sub(r'[^A-Z0-9_]', '', rel_name) or "REF"
                        candidates.append((col, f"HAS_{rel_name}"))
                        continue

            if src_sample_rows and len(src_sample_rows) > 0:
                sample_vals = [str(row.get(col, "")) for row in src_sample_rows[:10] if row.get(col)]
                id_matches = [v for v in sample_vals if re.match(r'^[A-Za-z]+[-_]?\d+$', v)]
                if len(id_matches) >= 2:
                    prefixes = [re.match(r'^[A-Za-z]+', v) for v in id_matches]
                    prefixes = [m.group(0).upper() for m in prefixes if m]
                    if prefixes:
                        rel_type = f"HAS_{max(set(prefixes), key=prefixes.count)}"
                        candidates.append((col, rel_type))

        # 策略 4: LLM 辅助语义 FK 检测（默认关闭，避免构建时被外部服务阻塞）
        import os
        llm_fk_enabled = os.getenv("ENABLE_LLM_FK_DETECTION", "").lower() in ("1", "true", "yes")
        if llm_fk_enabled and not candidates and src_cols:
            try:
                llm_candidates = self._llm_detect_fk(src_cols, tgt_entity_class, tgt_dataset_name)
                candidates.extend(llm_candidates)
            except Exception:
                pass

        return candidates

    def _llm_detect_fk(self, src_cols: list[str], tgt_entity_class: str, tgt_dataset_name: str) -> list[tuple[str, str]]:
        """使用用户配置的 LLM 检测中文列名→英文实体名的 FK 关系"""
        try:
            from app.services import llm_service
            from app.services.model_config_selector import llm_call_kwargs, select_llm_model_config
            import json

            call_kwargs = llm_call_kwargs(select_llm_model_config(
                self._db,
                purpose_tags=("FK检测", "关系推断", "Link推断"),
                allow_vlm=False,
            ))
            if not call_kwargs:
                return []

            prompt = f"""判断以下列中哪些是外键指向目标实体。
源列名: {json.dumps(src_cols, ensure_ascii=False)}
目标实体: {tgt_entity_class}
目标数据集: {tgt_dataset_name}
规则：列名语义关联目标实体（如中文"供应商"→Supplier），或列值像ID。
返回JSON对象 {{"links":[{{"column":"列名","relation_type":"HAS_XXX"}}]}}，无匹配返回 {{"links":[]}}。只返回JSON。"""
            raw = llm_service._call_llm(
                **call_kwargs,
                messages=[{"role": "system", "content": "输出JSON。"}, {"role": "user", "content": prompt}]
            )
            result = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(result, dict):
                result = result.get("links", [])
            if isinstance(result, list):
                return [(r["column"], r["relation_type"]) for r in result if r.get("column")]
            return []
        except Exception:
            return []
    # ── Link Mapping 处理 ──

    def _process_link_mappings(self, ontology_id: str, mapping_meta: dict) -> list[dict]:
        from app.models.v2.mapping import OntologyLinkMapping, OntologyMapping as OM
        from app.models.relation import Relation

        links = self._db.query(OntologyLinkMapping).filter(
            OntologyLinkMapping.ontology_id == ontology_id,
            OntologyLinkMapping.status == "active",
        ).all()
        results = []
        for link in links:
            src_meta = tgt_meta = None
            for mid, meta in mapping_meta.items():
                m = self._db.query(OM).filter(OM.id == mid).first()
                if not m: continue
                if m.curated_dataset_id == link.src_dataset_id: src_meta = meta
                if m.curated_dataset_id == link.tgt_dataset_id: tgt_meta = meta
            if not src_meta or not tgt_meta: continue
            tgt_val_to_eid = {}
            for row, (pk_val, eid) in zip(tgt_meta["rows"], tgt_meta["entity_id_map"].items()):
                v = str(row.get(link.tgt_key, "")).strip()
                if v: tgt_val_to_eid[v] = eid
            written = 0
            src_values: list[str] = []
            tgt_values: list[str] = []
            for row, (src_pk_val, src_eid) in zip(src_meta["rows"], src_meta["entity_id_map"].items()):
                src_val = str(row.get(link.src_key, "")).strip()
                if not src_val or not src_eid: continue
                tgt_eid = tgt_val_to_eid.get(src_val)
                if not tgt_eid: continue
                src_values.append(src_eid)
                tgt_values.append(tgt_eid)
                rel = Relation(
                    id=self._stable_relation_id(ontology_id, src_eid, tgt_eid, link.relation_type, "link_mapping"),
                    ontology_id=ontology_id,
                    source_entity=src_eid, target_entity=tgt_eid,
                    type=link.relation_type,
                    properties={"mapping_type": "link_mapping", "src_key": link.src_key, "tgt_key": link.tgt_key},
                    confidence=0.9,
                )
                self._db.merge(rel); written += 1
            cardinality = self._infer_cardinality(src_values, tgt_values)
            if written:
                for rel in self._db.query(Relation).filter(
                    Relation.ontology_id == ontology_id,
                    Relation.type == link.relation_type,
                ).all():
                    props = dict(rel.properties or {})
                    if props.get("mapping_type") == "link_mapping" and props.get("src_key") == link.src_key and props.get("tgt_key") == link.tgt_key:
                        props["cardinality"] = cardinality
                        rel.properties = props
                self._db.commit()
                self._write_neo4j_relations(ontology_id, src_meta["entity_class"], tgt_meta["entity_class"], link.relation_type)
                logger.info("Link: " + src_meta["entity_class"] + "-[" + str(link.relation_type) + "]->" + tgt_meta["entity_class"] + " " + str(written) + "条")
            results.append({"src": src_meta["entity_class"], "tgt": tgt_meta["entity_class"],
                            "rel_type": link.relation_type, "src_key": link.src_key, "tgt_key": link.tgt_key,
                            "count": written, "cardinality": cardinality,
                            "warning": None if written else "No rows matched this link mapping"})
        return results
