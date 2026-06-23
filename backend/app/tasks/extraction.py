from celery import Celery
from app.config import settings

celery_app = Celery("ontoprompt", broker=settings.redis_url, backend=settings.redis_url)
# broker 不可用时快速失败 (默认会长时间重试, 导致 API 请求阻塞)
celery_app.conf.task_publish_retry = False
celery_app.conf.broker_connection_timeout = 3


# ── Confidence calibration (Fix 5) ─────────────────────────────────────────
def _calibrate_confidence(result: dict) -> dict:
    """Adjust LLM-generated confidence scores using objective completeness signals."""
    import ast

    entities    = result.get("entities", [])
    relations   = result.get("relations", [])
    logic_rules = result.get("logic_rules", [])
    actions     = result.get("actions", [])

    entity_names = {e.get("name_cn") for e in entities if e.get("name_cn")}

    # Entities that appear in at least one relation get a small boost
    in_graph: set = set()
    for r in relations:
        in_graph.add(r.get("source")); in_graph.add(r.get("target"))

    for e in entities:
        base = float(e.get("confidence") or 0.85)
        adj  = 0.0
        if not (e.get("properties") and len(e.get("properties", {})) > 0): adj -= 0.10
        if not (e.get("description") or "").strip():                        adj -= 0.05
        if e.get("name_cn") in in_graph:                                    adj += 0.05
        e["confidence"] = round(max(0.30, min(0.98, base + adj)), 3)

    for r in relations:
        base = float(r.get("confidence") or 0.85)
        if r.get("source") not in entity_names or r.get("target") not in entity_names:
            r["confidence"] = 0.30   # broken reference → low confidence
        else:
            r["confidence"] = round(max(0.40, min(0.98, base)), 3)

    logic_names = {r.get("name_cn") for r in logic_rules if r.get("name_cn")}
    for rule in logic_rules:
        base = float(rule.get("confidence") or 0.85)
        adj  = 0.0
        if not rule.get("linked_entities"):                adj -= 0.10
        if not (rule.get("formula") or "").strip():        adj -= 0.05
        rule["confidence"] = round(max(0.30, min(0.98, base + adj)), 3)

    for action in actions:
        base = float(action.get("confidence") or 0.85)
        code = (action.get("function_code") or "").strip()
        adj  = 0.0
        if not code or len(code) < 20:
            adj -= 0.20
        else:
            try:
                ast.parse(code)
            except SyntaxError:
                adj -= 0.15
        if not action.get("linked_entities"): adj -= 0.05
        action["confidence"] = round(max(0.30, min(0.98, base + adj)), 3)

    return result


def _dedup_existing(db, ontology_id: str, model_cls, name_field: str):
    """Delete duplicate rows with the same (ontology_id, name_field), keeping the richest one."""
    rows = db.query(model_cls).filter(model_cls.ontology_id == ontology_id).all()
    seen: dict = {}
    for row in rows:
        key = getattr(row, name_field, None)
        if not key:
            continue
        if key not in seen:
            seen[key] = row
        else:
            # Keep the one with more data (prefer non-None properties/code/formula)
            incumbent = seen[key]
            challenger_score = _richness(row)
            incumbent_score  = _richness(incumbent)
            if challenger_score > incumbent_score:
                db.delete(incumbent)
                seen[key] = row
            else:
                db.delete(row)


def _richness(obj) -> int:
    """Heuristic score for how data-rich an ORM object is — higher = keep."""
    score = 0
    for attr in ("properties", "function_code", "formula", "description", "linked_entities"):
        val = getattr(obj, attr, None)
        if val:
            score += len(str(val))
    return score


def _fuzzy_resolve_entity(name: str, name_to_id: dict) -> str | None:
    """Resolve entity name to ID, falling back to substring-containment match.

    Handles cases where the LLM writes a slightly different name in relations
    than what was extracted in entities (e.g. '供应商' vs '供应商A').
    """
    if not name:
        return None
    if name in name_to_id:
        return name_to_id[name]
    # Substring containment: search name is contained in a known name, or vice versa
    candidates = [
        (kn, eid) for kn, eid in name_to_id.items()
        if kn and (name in kn or kn in name)
    ]
    if not candidates:
        return None
    # When multiple candidates, prefer the one sharing the most unique characters
    candidates.sort(key=lambda x: len(set(x[0]) & set(name)), reverse=True)
    return candidates[0][1]


@celery_app.task(bind=True)
def run_extraction(self, task_id: str):
    from app.database import SessionLocal
    from app.models.extraction_task import ExtractionTask
    from app.models.file import UploadedFile
    from app.models.model_config import ModelConfig
    from app.models.prompt import Prompt
    from app.models.entity import Entity
    from app.models.logic import LogicRule
    from app.models.action import Action
    from app.models.relation import Relation
    from app.models.ontology import OntologyProject
    from app.services.llm_service import extract_ontology, infer_relations
    from app.services.encryption_service import decrypt
    import uuid

    db = SessionLocal()
    try:
        task = db.query(ExtractionTask).filter(ExtractionTask.id == task_id).first()
        if not task:
            return

        task.status = "running"
        task.progress = {"stage": "loading files", "pct": 10}
        db.commit()

        files = db.query(UploadedFile).filter(UploadedFile.ontology_id == task.ontology_id).all()
        if not files:
            task.status = "failed"; task.error = "No files uploaded"; db.commit(); return

        combined_text = "\n\n---\n\n".join(f.converted_md or "" for f in files if f.converted_md)
        if not combined_text.strip():
            task.status = "failed"; task.error = "No text content found in files"; db.commit(); return

        # Strip control characters and normalise whitespace so the LLM doesn't
        # embed raw bytes that would later break its own JSON output.
        import re as _re
        combined_text = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', combined_text)

        model_cfg = db.query(ModelConfig).filter(ModelConfig.id == task.model_id).first()
        prompt    = db.query(Prompt).filter(Prompt.id == task.prompt_id).first()
        if not model_cfg or not prompt:
            task.status = "failed"; task.error = "Model or prompt not found"; db.commit(); return

        task.progress = {"stage": "calling LLM", "pct": 40}
        db.commit()

        model_name = task.parameters.get("model_name", "")
        config_dict = {
            "provider": model_cfg.provider,
            "api_key":  decrypt(model_cfg.api_key_encrypted or ""),
            "api_base": model_cfg.api_base,
        }

        prompt_content = prompt.content
        constraints = task.parameters.get("constraints", [])
        if constraints:
            prompt_content += "\n\n" + "\n".join(constraints)

        # ── Pass 1: main extraction ──────────────────────────────────────────
        result = extract_ontology(combined_text, prompt_content, config_dict, model_name)

        # ── Fix 5: calibrate confidence before validation ────────────────────
        result = _calibrate_confidence(result)

        # ── P0 validation ────────────────────────────────────────────────────
        task.progress = {"stage": "validating output", "pct": 65}
        db.commit()

        from app.engine.post_harness.validator import PostHarnessValidator
        validator = PostHarnessValidator()
        v_report  = validator.validate(result)
        task.validation_report = v_report.to_dict()
        db.commit()

        if v_report.has_fatal():
            task.status = "failed"; task.error = v_report.to_summary(); db.commit(); return

        # ── Fix 1: second-pass relation inference ─────────────────────────────
        entities_extracted  = result.get("entities", [])
        relations_extracted = result.get("relations", [])
        entity_count    = len(entities_extracted)
        relation_count  = len(relations_extracted)

        # Count how many entities appear in at least one relation (exact or fuzzy)
        entity_names_set = {e.get("name_cn") for e in entities_extracted if e.get("name_cn")}
        in_relation: set = set()
        for r in relations_extracted:
            in_relation.add(r.get("source") or r.get("source_entity", ""))
            in_relation.add(r.get("target") or r.get("target_entity", ""))
        isolated_count = sum(
            1 for n in entity_names_set
            if n and not any(n in rn or rn in n for rn in in_relation if rn)
        )

        # Trigger when globally sparse OR >30% of entities are isolated
        sparse = relation_count < max(5, entity_count * 0.4)
        many_isolated = isolated_count > max(2, entity_count * 0.3)
        if entity_count >= 5 and (sparse or many_isolated):
            task.progress = {"stage": "inferring relations", "pct": 75}
            db.commit()
            extra_rels = infer_relations(
                entities_extracted, relations_extracted,
                combined_text, config_dict, model_name
            )
            if extra_rels:
                # Accept relations where both endpoints fuzzy-match a known entity name
                for r in extra_rels:
                    src, tgt = r.get("source", ""), r.get("target", "")
                    src_ok = src in entity_names_set or any(
                        src in n or n in src for n in entity_names_set if n)
                    tgt_ok = tgt in entity_names_set or any(
                        tgt in n or n in tgt for n in entity_names_set if n)
                    if src_ok and tgt_ok:
                        result["relations"].append(r)
                result = _calibrate_confidence(result)

        task.progress = {"stage": "saving results", "pct": 85}
        db.commit()

        # ── Cleanup pre-existing duplicates (keep best, delete extras) ────────
        _dedup_existing(db, task.ontology_id, Entity, "name_cn")
        _dedup_existing(db, task.ontology_id, LogicRule, "name_cn")
        _dedup_existing(db, task.ontology_id, Action, "name_cn")
        db.flush()

        # ── Fix 2+4: upsert entities (by name_cn) ────────────────────────────
        existing_entities = db.query(Entity).filter(Entity.ontology_id == task.ontology_id).all()
        existing_ent_map  = {e.name_cn: e for e in existing_entities}

        entity_name_to_id: dict = {e.name_cn: e.id for e in existing_entities}
        for e in existing_entities:
            if e.name_en:
                entity_name_to_id[e.name_en] = e.id

        for e_data in result.get("entities", []):
            name_cn = e_data.get("name_cn") or e_data.get("name", "")
            if not name_cn:
                continue
            props = e_data.get("properties") or e_data.get("attributes") or e_data.get("attrs") or {}
            if not isinstance(props, dict):
                props = {}

            if name_cn in existing_ent_map:
                # Upsert: update fields that improved
                ent = existing_ent_map[name_cn]
                if e_data.get("type"):        ent.type        = e_data["type"]
                if e_data.get("description"): ent.description = e_data["description"]
                if props:                     ent.properties  = props
                if e_data.get("name_en"):     ent.name_en     = e_data["name_en"]
                ent.confidence = e_data.get("confidence", ent.confidence)
                eid = ent.id
            else:
                eid = str(uuid.uuid4())
                ent = Entity(
                    id=eid, ontology_id=task.ontology_id,
                    name_cn=name_cn, name_en=e_data.get("name_en"),
                    type=e_data.get("type"), description=e_data.get("description"),
                    properties=props, confidence=e_data.get("confidence", 0.85),
                )
                db.add(ent)
                existing_ent_map[name_cn] = ent

            entity_name_to_id[name_cn] = eid
            if e_data.get("name_en"):
                entity_name_to_id[e_data["name_en"]] = eid

        # ── Fix 2+4: upsert relations (by source_id, target_id, type) ────────
        existing_rels    = db.query(Relation).filter(Relation.ontology_id == task.ontology_id).all()
        existing_rel_set = {(r.source_entity, r.target_entity, r.type) for r in existing_rels}

        for rel in result.get("relations", []):
            src_name = rel.get("source") or rel.get("source_entity", "")
            tgt_name = rel.get("target") or rel.get("target_entity", "")
            src_id   = _fuzzy_resolve_entity(src_name, entity_name_to_id)
            tgt_id   = _fuzzy_resolve_entity(tgt_name, entity_name_to_id)
            rel_type = rel.get("type", "关联")
            if src_id and tgt_id and (src_id, tgt_id, rel_type) not in existing_rel_set:
                db.add(Relation(
                    id=str(uuid.uuid4()), ontology_id=task.ontology_id,
                    source_entity=src_id, target_entity=tgt_id,
                    type=rel_type, confidence=rel.get("confidence", 0.85),
                ))
                existing_rel_set.add((src_id, tgt_id, rel_type))

        # ── Keyword matching helpers (unchanged) ─────────────────────────────
        all_entity_names = [
            e.get("name_cn") or e.get("name", "")
            for e in result.get("entities", [])
            if e.get("name_cn") or e.get("name")
        ]
        type_to_entities: dict = {}
        for e in result.get("entities", []):
            etype = (e.get("type") or "").lower()
            ename = e.get("name_cn") or e.get("name", "")
            if ename:
                type_to_entities.setdefault(etype, []).append(ename)

        TYPE_KEYWORDS: dict = {
            "supplier": ["供应商","供货商","厂商","卖方"],
            "material": ["物料","原材料","辅料","零部件","库存"],
            "warehouse": ["仓库","库存","存储","盘点","入库","出库"],
            "product":  ["产品","成品","半成品","货物","质量","合格"],
            "document": ["订单","采购单","合同","审批","单据"],
            "process":  ["流程","工艺","步骤","采购","质检","物流"],
        }
        STOP_CHARS = set("的和在是了或且，。、（）[]【】")

        def _match_entities(text: str, entity_names: list) -> list:
            if not text: return []
            exact = [n for n in entity_names if n and n in text]
            if exact: return exact
            matched: list = []
            for etype, keywords in TYPE_KEYWORDS.items():
                if any(kw in text for kw in keywords):
                    matched.extend(type_to_entities.get(etype, []))
            return list(dict.fromkeys(matched))[:6]

        def _match_logic_rules(text: str, logic_name_to_id: dict) -> list:
            if not text: return []
            text_chars = set(text) - STOP_CHARS
            return [lid for lname, lid in logic_name_to_id.items()
                    if len(text_chars & (set(lname) - STOP_CHARS)) >= 2]

        # ── Fix 2+4: upsert logic rules (by name_cn) ─────────────────────────
        existing_rules    = db.query(LogicRule).filter(LogicRule.ontology_id == task.ontology_id).all()
        existing_rule_map = {r.name_cn: r for r in existing_rules}
        logic_name_to_id: dict = {r.name_cn: r.id for r in existing_rules}

        for r_data in result.get("logic_rules", []):
            name_cn = r_data.get("name_cn") or r_data.get("name", "")
            if not name_cn:
                continue

            llm_linked = r_data.get("linked_entities", [])
            if not llm_linked:
                combined = " ".join(filter(None, [name_cn, r_data.get("formula",""), r_data.get("description","")]))
                llm_linked = _match_entities(combined, all_entity_names)

            if name_cn in existing_rule_map:
                rule = existing_rule_map[name_cn]
                if r_data.get("formula"):     rule.formula     = r_data["formula"]
                if r_data.get("description"): rule.description = r_data["description"]
                if llm_linked:                rule.linked_entities = llm_linked
                rule.confidence = r_data.get("confidence", rule.confidence)
                rid = rule.id
            else:
                rid  = str(uuid.uuid4())
                rule = LogicRule(
                    id=rid, ontology_id=task.ontology_id,
                    name_cn=name_cn, name_en=r_data.get("name_en"),
                    description=r_data.get("description"), formula=r_data.get("formula"),
                    confidence=r_data.get("confidence", 0.85),
                )
                rule.linked_entities = llm_linked
                db.add(rule)
                existing_rule_map[name_cn] = rule

            logic_name_to_id[name_cn] = rid

        # ── Fix 2+4: upsert actions (by name_cn) ─────────────────────────────
        existing_actions    = db.query(Action).filter(Action.ontology_id == task.ontology_id).all()
        existing_action_map = {a.name_cn: a for a in existing_actions}

        for a_data in result.get("actions", []):
            name_cn = a_data.get("name_cn") or a_data.get("name", "")
            if not name_cn:
                continue

            linked_ents = a_data.get("linked_entities", [])
            if not linked_ents:
                combined = " ".join(filter(None, [name_cn, a_data.get("execution_rule",""), a_data.get("description","")]))
                linked_ents = _match_entities(combined, all_entity_names)

            linked_logic_names = a_data.get("linked_logic_names", [])
            linked_ids = [logic_name_to_id[n] for n in linked_logic_names if n in logic_name_to_id]
            linked_ids += [i for i in a_data.get("linked_logic_ids", []) if i not in linked_ids]
            if not linked_ids:
                action_text = " ".join(filter(None, [name_cn, a_data.get("execution_rule",""), a_data.get("description","")]))
                linked_ids = _match_logic_rules(action_text, logic_name_to_id)

            if name_cn in existing_action_map:
                act = existing_action_map[name_cn]
                if a_data.get("description"):    act.description    = a_data["description"]
                if a_data.get("execution_rule"): act.execution_rule = a_data["execution_rule"]
                if a_data.get("function_code"):  act.function_code  = a_data["function_code"]
                if a_data.get("name_en"):        act.name_en        = a_data["name_en"]
                if linked_ents:  act.linked_entities  = linked_ents
                if linked_ids:   act.linked_logic_ids = linked_ids
                act.confidence = a_data.get("confidence", act.confidence)
            else:
                act = Action(
                    id=str(uuid.uuid4()), ontology_id=task.ontology_id,
                    name_cn=name_cn, name_en=a_data.get("name_en"),
                    description=a_data.get("description"), execution_rule=a_data.get("execution_rule"),
                    function_code=a_data.get("function_code"),
                    linked_entities=linked_ents, linked_logic_ids=linked_ids,
                    confidence=a_data.get("confidence", 0.85),
                )
                db.add(act)
                existing_action_map[name_cn] = act

        project = db.query(OntologyProject).filter(OntologyProject.id == task.ontology_id).first()
        if project:
            project.status = "created"

        import logging
        try:
            from app.services.v2.graph.neo4j_service import Neo4jService
            neo = Neo4jService()
            if neo.available:
                all_entities = db.query(Entity).filter(Entity.ontology_id == task.ontology_id).all()
                all_relations = db.query(Relation).filter(Relation.ontology_id == task.ontology_id).all()
                batch = [{
                    "id": e.id, "ontology_id": task.ontology_id,
                    "name_cn": e.name_cn or "", "name": e.name_cn or "",
                    "name_en": e.name_en or "", "type": e.type or "",
                    "description": e.description or "", "confidence": e.confidence or 1.0,
                    "version": e.version or "v0.1",
                } for e in all_entities]
                neo.batch_upsert_entities("OntologyEntity", batch, key_field="id")
                for r in all_relations:
                    rel_type = (r.type or "RELATED").upper().replace(" ", "_").replace("-", "_")
                    neo.upsert_relation("OntologyEntity", r.source_entity, "OntologyEntity", r.target_entity,
                                        rel_type, props={"ontology_id": task.ontology_id, "confidence": r.confidence or 1.0})
                neo.close()
                logging.getLogger(__name__).info(f"Neo4j sync: {len(batch)} entities, {len(all_relations)} relations")
        except Exception as neo_err:
            logging.getLogger(__name__).warning(f"Neo4j sync after extraction failed (non-fatal): {neo_err}")

        task.status   = "completed"
        task.progress = {"stage": "done", "pct": 100}
        db.commit()

    except Exception as e:
        task = db.query(ExtractionTask).filter(ExtractionTask.id == task_id).first()
        if task:
            task.status = "failed"
            task.error  = str(e)
            db.commit()
    finally:
        db.close()
