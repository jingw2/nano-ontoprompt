"""
全流程测试脚本：对每个业务域
  1. 通过 API 创建本体
  2. 通过 API 上传所有文件（同步转换）
  3. 直接调用提取逻辑（绕过 Celery）
  4. 汇总：领域 / 文件数 / 总时长 / 实体数 / 逻辑数 / 行动数

用法：cd backend && python run_full_flow_test.py
"""
import sys, os, time, uuid, json, re as _re, mimetypes, requests

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.')

from app.database import SessionLocal
from app.models.ontology import OntologyProject  # must be imported to register FK mapper
from app.models.file import UploadedFile
from app.models.model_config import ModelConfig
from app.models.prompt import Prompt
from app.models.entity import Entity
from app.models.logic import LogicRule
from app.models.action import Action
from app.models.relation import Relation
from app.services.llm_service import extract_ontology, infer_relations
from app.services.encryption_service import decrypt
from app.tasks.extraction import _calibrate_confidence, _dedup_existing, _fuzzy_resolve_entity

# ── 配置 ─────────────────────────────────────────────────────────────────────
API_BASE    = "http://localhost:8000/api/v1"
USERNAME    = "admin"
PASSWORD    = "admin123"
TEST_DATA   = os.path.abspath(os.path.join("..", "test_data"))

DOMAINS = [
    {"name": "供应链本体-测试", "domain": "供应链", "dir": "供应链",
     "prompt_id": "bfe0bb61-0f26-46f3-93d9-0c515c0e813e"},
    {"name": "HR本体-测试",    "domain": "其他",   "dir": "HR",
     "prompt_id": "779bd973-30ee-4ffd-9bfc-27e94f180fef"},
    {"name": "财务本体-测试",  "domain": "财务",   "dir": "财务",
     "prompt_id": "a1114d03-a3d3-4a76-8602-6744b562ef52"},
    {"name": "营销本体-测试",  "domain": "其他",   "dir": "营销",
     "prompt_id": "db39ed80-3361-4c5e-9e1d-672810ab89fa"},
    {"name": "医疗本体-测试",  "domain": "医疗",   "dir": "医疗",
     "prompt_id": "572a5295-e68e-4629-875a-9df2ceae6056"},
    {"name": "法律本体-测试",  "domain": "法律",   "dir": "法律",
     "prompt_id": "c1ddd6d3-b648-4a64-98ce-64d293201d25"},
    {"name": "教育本体-测试",  "domain": "教育",   "dir": "教育",
     "prompt_id": "6c6f2a3e-2b10-434d-addf-a1eed8c647d5"},
]

MIME_MAP = {
    ".md":   "text/markdown",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv":  "text/csv",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".pdf":  "application/pdf",
    ".txt":  "text/plain",
}


# ── API helpers ───────────────────────────────────────────────────────────────
def login() -> str:
    r = requests.post(f"{API_BASE}/auth/login",
                      json={"username": USERNAME, "password": PASSWORD}, timeout=10)
    r.raise_for_status()
    return r.json()["data"]["access_token"]


def create_ontology(token: str, name: str, domain: str) -> str:
    """Create a new ontology project; handle 409 by appending timestamp."""
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"name": name, "domain": domain, "description": f"{name} 全流程测试"}
    r = requests.post(f"{API_BASE}/ontologies", json=payload, headers=headers, timeout=10)
    if r.status_code == 409:
        suffix = str(int(time.time()))[-5:]
        payload["name"] = f"{name}-{suffix}"
        r = requests.post(f"{API_BASE}/ontologies", json=payload, headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()["data"]["id"]


def upload_file(token: str, oid: str, file_path: str) -> bool:
    """Upload a single file to the ontology. Returns True on success."""
    headers = {"Authorization": f"Bearer {token}"}
    ext  = os.path.splitext(file_path)[1].lower()
    mime = MIME_MAP.get(ext, "application/octet-stream")
    fname = os.path.basename(file_path)
    try:
        with open(file_path, "rb") as fh:
            r = requests.post(
                f"{API_BASE}/ontologies/{oid}/files",
                headers=headers,
                files={"file": (fname, fh, mime)},
                timeout=60,
            )
        if r.status_code in (200, 201):
            return True
        print(f"    ⚠ 上传失败 {fname}: {r.status_code} {r.text[:120]}")
        return False
    except Exception as e:
        print(f"    ⚠ 上传异常 {fname}: {e}")
        return False


# ── Extraction (direct, no Celery) ────────────────────────────────────────────
def get_deepseek_model(db):
    models = db.query(ModelConfig).all()
    for m in models:
        if "deepseek" in (m.provider or "").lower() or "deepseek" in (m.name or "").lower():
            return m
    return models[0] if models else None


def run_extraction(db, oid: str, prompt_id: str, model_cfg, model_name: str) -> dict:
    files = db.query(UploadedFile).filter(UploadedFile.ontology_id == oid).all()
    if not files:
        return {"error": "无文件"}

    combined = "\n\n---\n\n".join(f.converted_md or "" for f in files if f.converted_md)
    if not combined.strip():
        return {"error": "文件无文本"}
    combined = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', combined)
    print(f"    文本长度: {len(combined):,} chars")

    prompt = db.query(Prompt).filter(Prompt.id == prompt_id).first()
    if not prompt:
        return {"error": f"Prompt 未找到: {prompt_id}"}

    config = {
        "provider": model_cfg.provider,
        "api_key":  decrypt(model_cfg.api_key_encrypted or ""),
        "api_base": model_cfg.api_base,
    }

    # Pass 1
    print(f"    ▶ Pass 1 LLM 提取...")
    t0 = time.time()
    try:
        result = extract_ontology(combined, prompt.content, config, model_name)
    except Exception as e:
        return {"error": f"LLM 失败: {e}"}
    t_llm = time.time() - t0
    print(f"    Pass 1 完成 ({t_llm:.1f}s): "
          f"{len(result.get('entities',[]))}实体 "
          f"{len(result.get('logic_rules',[]))}规则 "
          f"{len(result.get('actions',[]))}动作")

    result = _calibrate_confidence(result)

    # Pass 2 (infer relations if sparse)
    entities_raw  = result.get("entities", [])
    relations_raw = result.get("relations", [])
    ent_names     = {e.get("name_cn") for e in entities_raw if e.get("name_cn")}
    in_rel        = set()
    for r in relations_raw:
        in_rel.add(r.get("source", "")); in_rel.add(r.get("target", ""))
    isolated = sum(1 for n in ent_names if n and not any(n in rn or rn in n for rn in in_rel if rn))
    sparse   = len(relations_raw) < max(5, len(entities_raw) * 0.4)
    many_iso = isolated > max(2, len(entities_raw) * 0.3)

    t_infer = 0.0
    if len(entities_raw) >= 5 and (sparse or many_iso):
        print(f"    ▶ Pass 2 推理关系...")
        t1 = time.time()
        try:
            extra = infer_relations(entities_raw, relations_raw, combined, config, model_name)
            for r in (extra or []):
                src, tgt = r.get("source",""), r.get("target","")
                if (any(src in n or n in src for n in ent_names if n) and
                        any(tgt in n or n in tgt for n in ent_names if n)):
                    result["relations"].append(r)
            result = _calibrate_confidence(result)
        except Exception as e:
            print(f"    ⚠ Pass 2 失败: {e}")
        t_infer = time.time() - t1
        print(f"    Pass 2 完成 ({t_infer:.1f}s): {len(result.get('relations',[]))}条关系")

    t_total = time.time() - t0

    # Save to DB
    _dedup_existing(db, oid, Entity, "name_cn")
    _dedup_existing(db, oid, LogicRule, "name_cn")
    _dedup_existing(db, oid, Action, "name_cn")
    db.flush()

    existing_ents     = db.query(Entity).filter(Entity.ontology_id == oid).all()
    existing_ent_map  = {e.name_cn: e for e in existing_ents}
    entity_name_to_id = {e.name_cn: e.id for e in existing_ents}
    for e in existing_ents:
        if e.name_en: entity_name_to_id[e.name_en] = e.id

    for ed in result.get("entities", []):
        ncn = ed.get("name_cn") or ""
        if not ncn: continue
        props = ed.get("properties") or {}
        if not isinstance(props, dict): props = {}
        if ncn in existing_ent_map:
            ent = existing_ent_map[ncn]
            if ed.get("type"):        ent.type        = ed["type"]
            if ed.get("description"): ent.description = ed["description"]
            if props:                 ent.properties  = props
            if ed.get("name_en"):     ent.name_en     = ed["name_en"]
            ent.confidence = ed.get("confidence", ent.confidence)
            eid = ent.id
        else:
            eid = str(uuid.uuid4())
            ent = Entity(id=eid, ontology_id=oid,
                         name_cn=ncn, name_en=ed.get("name_en"),
                         type=ed.get("type"), description=ed.get("description"),
                         properties=props, confidence=ed.get("confidence", 0.85))
            db.add(ent)
            existing_ent_map[ncn] = ent
        entity_name_to_id[ncn] = eid
        if ed.get("name_en"): entity_name_to_id[ed["name_en"]] = eid

    existing_rels    = db.query(Relation).filter(Relation.ontology_id == oid).all()
    existing_rel_set = {(r.source_entity, r.target_entity, r.type) for r in existing_rels}
    for rel in result.get("relations", []):
        sid = _fuzzy_resolve_entity(rel.get("source",""), entity_name_to_id)
        tid = _fuzzy_resolve_entity(rel.get("target",""), entity_name_to_id)
        rt  = rel.get("type","关联")
        if sid and tid and (sid,tid,rt) not in existing_rel_set:
            db.add(Relation(id=str(uuid.uuid4()), ontology_id=oid,
                            source_entity=sid, target_entity=tid,
                            type=rt, confidence=rel.get("confidence",0.85)))
            existing_rel_set.add((sid,tid,rt))

    existing_rules   = db.query(LogicRule).filter(LogicRule.ontology_id == oid).all()
    existing_rule_map = {r.name_cn: r for r in existing_rules}
    for rd in result.get("logic_rules", []):
        ncn = rd.get("name_cn","")
        if not ncn: continue
        le = [entity_name_to_id.get(n) for n in (rd.get("linked_entities") or []) if entity_name_to_id.get(n)]
        if ncn in existing_rule_map:
            rule = existing_rule_map[ncn]
            if rd.get("formula"):     rule.formula     = rd["formula"]
            if rd.get("description"): rule.description = rd["description"]
            if le:                    rule.linked_entities = le
            rule.confidence = rd.get("confidence", rule.confidence)
        else:
            db.add(LogicRule(id=str(uuid.uuid4()), ontology_id=oid,
                             name_cn=ncn, name_en=rd.get("name_en"),
                             formula=rd.get("formula",""), description=rd.get("description"),
                             confidence=rd.get("confidence",0.85), linked_entities=le))

    all_rules   = db.query(LogicRule).filter(LogicRule.ontology_id == oid).all()
    rule_id_map = {r.name_cn: r.id for r in all_rules}
    existing_acts  = db.query(Action).filter(Action.ontology_id == oid).all()
    existing_act_map = {a.name_cn: a for a in existing_acts}
    for ad in result.get("actions", []):
        ncn = ad.get("name_cn","")
        if not ncn: continue
        le = [entity_name_to_id.get(n) for n in (ad.get("linked_entities") or []) if entity_name_to_id.get(n)]
        li = [rule_id_map.get(n)       for n in (ad.get("linked_logic_names") or []) if rule_id_map.get(n)]
        if ncn in existing_act_map:
            act = existing_act_map[ncn]
            if ad.get("execution_rule"): act.execution_rule = ad["execution_rule"]
            if ad.get("function_code"):  act.function_code  = ad["function_code"]
            if le: act.linked_entities  = le
            if li: act.linked_logic_ids = li
            act.confidence = ad.get("confidence", act.confidence)
        else:
            db.add(Action(id=str(uuid.uuid4()), ontology_id=oid,
                          name_cn=ncn, name_en=ad.get("name_en"),
                          execution_rule=ad.get("execution_rule",""),
                          function_code=ad.get("function_code",""),
                          description=ad.get("description"),
                          confidence=ad.get("confidence",0.85),
                          linked_entities=le, linked_logic_ids=li))
    db.commit()

    # Final counts
    final_ents  = db.query(Entity).filter(Entity.ontology_id == oid).count()
    final_rules = db.query(LogicRule).filter(LogicRule.ontology_id == oid).count()
    final_acts  = db.query(Action).filter(Action.ontology_id == oid).count()
    final_rels  = db.query(Relation).filter(Relation.ontology_id == oid).count()

    ents_list  = db.query(Entity).filter(Entity.ontology_id == oid).all()
    rules_list = db.query(LogicRule).filter(LogicRule.ontology_id == oid).all()
    acts_list  = db.query(Action).filter(Action.ontology_id == oid).all()
    with_props = sum(1 for e in ents_list if e.properties and len(e.properties) > 0)
    with_le    = sum(1 for r in rules_list if r.linked_entities and len(r.linked_entities) > 0)
    with_code  = sum(1 for a in acts_list if a.function_code and len(a.function_code.strip()) > 20)

    return {
        "t_total": round(t_total, 1),
        "t_llm":   round(t_llm,   1),
        "t_infer": round(t_infer, 1),
        "entities":   final_ents,
        "relations":  final_rels,
        "logic_rules": final_rules,
        "actions":    final_acts,
        "with_props": with_props,
        "with_le":    with_le,
        "with_code":  with_code,
    }


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 65)
    print("  OntoPrompt 全流程测试")
    print("=" * 65)

    # Login
    print("\n[Step 1] 登录...")
    token = login()
    print(f"  ✓ 获取 token: {token[:30]}...")

    db = SessionLocal()
    model_cfg = get_deepseek_model(db)
    if not model_cfg:
        print("❌ 无模型配置，退出")
        db.close()
        return
    model_name = model_cfg.models[0] if model_cfg.models else ""
    print(f"  模型: {model_cfg.name} / {model_name}")

    results = []

    for domain in DOMAINS:
        label     = domain["dir"]
        data_dir  = os.path.join(TEST_DATA, domain["dir"])
        prompt_id = domain["prompt_id"]

        print(f"\n{'─'*65}")
        print(f"  [{label}]")
        print(f"{'─'*65}")

        # Step A: list files
        if not os.path.isdir(data_dir):
            print(f"  ❌ 目录不存在: {data_dir}")
            continue
        files_on_disk = sorted([
            os.path.join(data_dir, f)
            for f in os.listdir(data_dir)
            if os.path.isfile(os.path.join(data_dir, f))
        ])
        print(f"  文件数: {len(files_on_disk)}")
        for fp in files_on_disk:
            print(f"    - {os.path.basename(fp)}")

        t_wall_start = time.time()

        # Step B: create ontology
        print(f"\n  [B] 创建本体...")
        try:
            oid = create_ontology(token, domain["name"], domain["domain"])
            print(f"      ✓ oid={oid}")
        except Exception as e:
            print(f"      ❌ 创建失败: {e}")
            continue

        # Step C: upload files
        print(f"\n  [C] 上传文件...")
        uploaded = 0
        for fp in files_on_disk:
            fname = os.path.basename(fp)
            ok = upload_file(token, oid, fp)
            if ok:
                uploaded += 1
                print(f"      ✓ {fname}")
            else:
                print(f"      ✗ {fname}")
        print(f"      上传完成: {uploaded}/{len(files_on_disk)}")

        if uploaded == 0:
            print(f"  ❌ 无文件上传成功，跳过提取")
            continue

        t_upload = time.time() - t_wall_start

        # Step D: run extraction
        print(f"\n  [D] 本体提取 (prompt={prompt_id[:8]}...)...")
        ex = run_extraction(db, oid, prompt_id, model_cfg, model_name)

        t_total_wall = time.time() - t_wall_start

        if "error" in ex:
            print(f"  ❌ 提取失败: {ex['error']}")
            continue

        print(f"\n  ✅ {label} 完成")
        print(f"     上传: {t_upload:.1f}s | 提取: {ex['t_total']}s | 壁钟: {t_total_wall:.1f}s")
        print(f"     实体={ex['entities']} 关系={ex['relations']} 逻辑={ex['logic_rules']} 行动={ex['actions']}")

        results.append({
            "label":       label,
            "files":       uploaded,
            "t_upload_s":  round(t_upload, 1),
            "t_extract_s": ex["t_total"],
            "t_total_s":   round(t_total_wall, 1),
            "entities":    ex["entities"],
            "relations":   ex["relations"],
            "logic_rules": ex["logic_rules"],
            "actions":     ex["actions"],
            "with_props":  ex["with_props"],
            "with_le":     ex["with_le"],
            "with_code":   ex["with_code"],
        })

    db.close()

    # ── Summary table ─────────────────────────────────────────────────────────
    print(f"\n\n{'='*80}")
    print("  全领域全流程测试汇总")
    print(f"{'='*80}")
    hdr = (f"{'领域':<8} {'文件数':>5} {'上传(s)':>8} {'提取(s)':>8} {'总时长(s)':>9} "
           f"{'实体':>5} {'关系':>5} {'逻辑':>5} {'行动':>5} {'属性%':>6} {'代码%':>6}")
    print(hdr)
    print("-" * 80)
    for r in results:
        prop_pct = f"{r['with_props']/r['entities']*100:.0f}%" if r['entities'] else "—"
        code_pct = f"{r['with_code']/r['actions']*100:.0f}%"  if r['actions']  else "—"
        print(
            f"{r['label']:<8} {r['files']:>5} {r['t_upload_s']:>8.1f} {r['t_extract_s']:>8.1f} "
            f"{r['t_total_s']:>9.1f} {r['entities']:>5} {r['relations']:>5} "
            f"{r['logic_rules']:>5} {r['actions']:>5} {prop_pct:>6} {code_pct:>6}"
        )
    if results:
        total_ents  = sum(r["entities"]    for r in results)
        total_rules = sum(r["logic_rules"] for r in results)
        total_acts  = sum(r["actions"]     for r in results)
        total_files = sum(r["files"]       for r in results)
        avg_time    = sum(r["t_total_s"]   for r in results) / len(results)
        print("-" * 80)
        print(f"{'合计':<8} {total_files:>5} {'—':>8} {'—':>8} {'avg'+f'{avg_time:.0f}s':>9} "
              f"{total_ents:>5} {'—':>5} {total_rules:>5} {total_acts:>5}")
    print(f"\n说明: 属性% = 有属性的实体占比，代码% = 有function_code的行动占比")
    print(f"      总时长 = 文件上传 + 本体提取（含LLM调用）")


if __name__ == "__main__":
    main()
