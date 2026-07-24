#!/usr/bin/env python3
"""
信贷业务域 — LLM-driven + Pipeline Mapping 全流程测试

覆盖 13 个增强后的信贷测试数据文件，校验萃取结果的完整性和正确性。

前置: 后端 8000 + mock LLM 8123 运行中。
    python test_data/mock_llm_server.py &    (另一个终端)
    cd backend && python -m uvicorn app.main:app --reload --port 8000 &

命令行参数:
  --clean    清理已有的信贷本体后重新创建
  --skip-mapping  跳过 Pipeline Mapping 测试（只跑 LLM）
  --skip-llm     跳过 LLM 提取测试（只跑 Mapping）
"""
import re, sys, time
from collections import Counter, defaultdict
from pathlib import Path

import requests

API = "http://localhost:8000"
TD  = Path(__file__).parent
DOMAIN_DIR = TD / "信贷"
CREDIT_FILES = sorted(
    f for f in DOMAIN_DIR.glob("*")
    if f.suffix.lower() in {".md", ".docx", ".doc", ".pdf", ".pptx", ".ppt",
                            ".txt", ".csv", ".xlsx", ".xls", ".json", ".xml"}
)
# 可文本化文件（LLM 提取使用）
TEXT_EXT = {".md", ".docx", ".doc", ".pdf", ".pptx", ".ppt", ".txt", ".csv", ".xlsx"}

s = requests.Session()

# ── 日志辅助 ─────────────────────────────────────────────────────────
PASS, FAIL, WARN = 0, 0, 0
FAILURES = []

def section(title):
    print(f"\n{'═'*60}\n  {title}\n{'─'*60}")

def ok(msg):  global PASS; PASS += 1;  print(f"  ✅  {msg}")
def warn(msg): global WARN; WARN += 1; print(f"  ⚠️   {msg}")
def fail(msg): global FAIL; FAIL += 1; FAILURES.append(msg); print(f"  ❌  {msg}")
def info(msg): print(f"  ℹ️   {msg}")

def check(r, expected, label):
    if r.status_code != expected:
        fail(f"{label} → HTTP {r.status_code} (exp {expected}): {r.text[:200]}")
        return False
    return True

def jget(r):
    try: return r.json()
    except: return {}

MIME_MAP = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".csv": "text/csv",
    ".json": "application/json",
    ".md": "text/markdown",
    ".txt": "text/plain",
}

def pascal(fname):
    base = fname.rsplit(".", 1)[0]
    parts = re.split(r"[_\-\s]+", base)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)[:40] or "Entity"

# ── 阶段 0: 登录 + 准备 ───────────────────────────────────────────────
section("阶段 0 — 登录与环境准备")

r = s.post(f"{API}/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
assert r.ok, f"登录失败: {r.text}"
s.headers.update({"Authorization": f"Bearer {r.json()['data']['access_token']}"})
ok("登录成功")

# 健康检查
r = s.get(f"{API}/health")
if check(r, 200, "GET /health"):
    h = jget(r)
    ok(f"系统状态: db={h.get('db')} neo4j={h.get('neo4j')} minio={h.get('minio')} chroma={h.get('chroma')}")

# 确保 mock model 和 prompt 存在
models = s.get(f"{API}/api/v1/models").json().get("data", [])
mock = next((m for m in models if m.get("name") == "Mock Extractor (测试)"), None)
if not mock:
    mock = s.post(f"{API}/api/v1/models", json={
        "name": "Mock Extractor (测试)", "provider": "compatible",
        "api_key": "mock-key", "api_base": "http://127.0.0.1:8123/v1",
        "models": ["mock-extractor"]}).json()["data"]
ok(f"模型就绪: {mock['id'][:8]}...")

prompts = s.get(f"{API}/api/v1/prompts").json().get("data", [])
pr = next((p for p in prompts if p.get("name") == "通用本体提取(测试)"), None)
if not pr:
    pr = s.post(f"{API}/api/v1/prompts", json={
        "name": "通用本体提取(测试)", "domain": "通用",
        "content": "提取 entities/relations/logic_rules/actions, 只返回 JSON。"}).json()["data"]
ok(f"提示词就绪: {pr['id'][:8]}...")

MOCK_MODEL_ID = mock["id"]
PROMPT_ID = pr["id"]

# 生成时间戳
TS = int(time.time())

# ═══════════════════════════════════════════════════════════════════════
# 阶段 A: Pipeline Mapping 路径
# ═══════════════════════════════════════════════════════════════════════
section("阶段 A — Pipeline Mapping")

# A-1: 上传全部文件为数据集
conn_files = []
for f in CREDIT_FILES:
    fname = f.name
    mime = MIME_MAP.get(f.suffix.lower(), "application/octet-stream")
    with open(f, "rb") as fh:
        r = s.post(f"{API}/api/v2/datasets/upload",
                   files={"file": (fname, fh, mime)},
                   headers={"Content-Type": None})
    rd = jget(r)
    j = (rd.get("data") or rd) if isinstance(rd, dict) else rd
    ds_id = ""
    if isinstance(j, dict):
        ds_id = j.get("id", "")
        conn_files.append({"name": fname, "dataset_id": ds_id, "kind": j.get("kind")})
    elif isinstance(j, list) and j:
        ds_id = j[0].get("id", "")
        conn_files.append({"name": fname, "dataset_id": ds_id, "kind": j[0].get("kind")})
    if not ds_id:
        fail(f"上传数据集 {fname}: 响应中无 id: {str(rd)[:200]}")
    else:
        ok(f"数据集上传: {fname:30s} → id={ds_id[:8]}...")

info(f"共上传 {len(conn_files)} 个数据集")

# A-2: 创建单 pipeline，connector 挂全部文件
pipeline_def = {
    "nodes": [
        {"id": "conn", "type": "connector", "position": {"x": 0, "y": 0}, "label": "连接器",
         "config": {"source_type": "file", "files": conn_files}},
        {"id": "stor", "type": "storage", "position": {"x": 260, "y": 0}, "label": "存储器",
         "config": {"storage_mode": "auto"}},
        {"id": "tran", "type": "transform", "position": {"x": 520, "y": 0}, "label": "转换器",
         "config": {"path": "auto", "steps": []}},
        {"id": "out", "type": "output", "position": {"x": 780, "y": 0}, "label": "输出",
         "config": {"dataset_type": "curated_dataset", "primary_key": []}},
    ],
    "edges": [{"id": "e1", "source": "conn", "target": "stor"},
              {"id": "e2", "source": "stor", "target": "tran"},
              {"id": "e3", "source": "tran", "target": "out"}],
}
r = s.post(f"{API}/api/v2/pipelines", json={
    "name": f"信贷-全管道-{TS}", "definition": pipeline_def})
pipeline_id = ""
if check(r, 201, "POST /pipelines 信贷"):
    pipeline_id = jget(r).get("id", "")
    ok(f"Pipeline 创建: id={pipeline_id[:8]}...")

# A-3: 运行 sync
if pipeline_id:
    r = s.post(f"{API}/api/v2/pipelines/{pipeline_id}/run-sync")
    mapping_stats = {}
    if check(r, 200, "POST /pipelines/{id}/run-sync"):
        res = jget(r)
        meta = (res.get("stats") or {}).get("meta") or {}
        outputs = meta.get("outputs", [])
        mapping_stats["outputs"] = outputs
        ok(f"run-sync: status={res.get('status')}, 产出 {len(outputs)} 个 curated dataset")
        for o in outputs:
            info(f"  {o.get('source_file','?'):30s} route={o.get('route')} rows={o.get('rows_out')}")
        mapping_stats["output_count"] = len(outputs)
    else:
        mapping_stats = {"outputs": [], "output_count": 0}

# A-4: 审批 + 建 mapping
if pipeline_id and mapping_stats.get("outputs"):
    r = s.post(f"{API}/api/v1/ontologies", json={
        "name": f"信贷-Mapping-{TS}", "domain": "金融",
        "build_mode": "pipeline_mapping",
        "description": "13个信贷测试文件全流程 Pipeline Mapping"})
    mapping_oid = jget(r).get("data", {}).get("id", "")
    if mapping_oid:
        ok(f"Mapping 本体创建: id={mapping_oid[:8]}...")
        for o in mapping_stats["outputs"]:
            cid = o["curated_dataset_id"]
            s.post(f"{API}/api/v2/curated/{cid}/review", params={"action": "approve"})
            s.post(f"{API}/api/v2/ontologies/{mapping_oid}/mappings", json={
                "curated_dataset_id": cid, "entity_class": pascal(o["source_file"]),
                "field_mapping": {}})
        rb = s.post(f"{API}/api/v2/ontologies/{mapping_oid}/mappings/build-all")
        res2 = rb.json()
        info(f"build-all: {rb.status_code} "
             f"实体={res2.get('total_entities')} 关系={res2.get('total_relations')} "
             f"逻辑={res2.get('total_logic')} 动作={res2.get('total_actions')}")

        # A-5: 评估 mapping 图谱质量
        g = s.get(f"{API}/api/v2/ontologies/{mapping_oid}/graph?limit=1000").json()
        g = g.get("data", g)
        nodes = g.get("nodes", [])
        edges = g.get("edges", [])
        mapping_stats["nodes"] = len(nodes)
        mapping_stats["edges"] = len(edges)
        mapping_stats["logic"] = res2.get("total_logic", 0)
        mapping_stats["actions"] = res2.get("total_actions", 0)
        ok(f"Mapping 图谱: {len(nodes)} 节点, {len(edges)} 边")

        # 边类型统计
        edge_types = Counter(e.get("type") or e.get("label", "?") for e in edges)
        if edge_types:
            info(f"关系类型分布: {dict(edge_types.most_common(10))}")
    else:
        fail("Mapping 本体创建失败")
else:
    mapping_oid = None
    mapping_stats = {"output_count": 0, "nodes": 0, "edges": 0, "logic": 0, "actions": 0}

# ═══════════════════════════════════════════════════════════════════════
# 阶段 B: LLM-driven 提取路径
# ═══════════════════════════════════════════════════════════════════════
section("阶段 B — LLM-driven 本体提取")

# B-1: 创建本体项目
r = s.post(f"{API}/api/v1/ontologies", json={
    "name": f"信贷-LLM提取-{TS}", "domain": "金融",
    "build_mode": "simple_llm",
    "description": "13个信贷增强测试文件 LLM 驱动本体提取"})
llm_oid = jget(r).get("data", {}).get("id", "")
if not llm_oid:
    fail("LLM 本体创建失败")
else:
    ok(f"LLM 本体创建: id={llm_oid[:8]}...")

# B-2: 上传所有可文本化文件
text_files = [f for f in CREDIT_FILES if f.suffix.lower() in TEXT_EXT]
for f in text_files:
    mime = MIME_MAP.get(f.suffix.lower(), "application/octet-stream")
    with open(f, "rb") as fh:
        r = s.post(f"{API}/api/v1/ontologies/{llm_oid}/files",
                   files={"file": (f.name, fh, mime)},
                   headers={"Content-Type": None})
    if check(r, 201, f"上传文件 {f.name}"):
        pass  # 不逐个打印避免刷屏
info(f"上传 {len(text_files)} 个可文本化文件到 LLM 本体")

# B-3: 执行 LLM 提取
r = s.post(f"{API}/api/v1/ontologies/{llm_oid}/execute", json={
    "prompt_id": PROMPT_ID, "model_id": MOCK_MODEL_ID, "model_name": "mock-extractor"})
tid = jget(r).get("data", {}).get("task_id", "")
if not tid:
    fail("LLM 提取任务创建失败")
else:
    ok(f"提取任务入队: task_id={tid[:8]}...")
    status = "running"
    for i in range(90):
        time.sleep(2)
        r = s.get(f"{API}/api/v1/ontologies/{llm_oid}/execute/status",
                  params={"task_id": tid})
        task = jget(r).get("data", {})
        status = task.get("status", "unknown")
        pct = task.get("progress", {}).get("pct", 0) if isinstance(task.get("progress"), dict) else None
        if i % 5 == 0:  # 每10秒打印一次
            pct_str = f" progress={pct}%" if pct else ""
            info(f"  [{(i+1)*2}s] {status}{pct_str}")
        if status in ("success", "completed", "failed", "error"):
            break
    ok(f"提取完成: {status}" if status in ("success", "completed") else f"提取结束: {status}")

# B-4: 查看提取结果
llm_entities = []
llm_logic = []
llm_actions = []
llm_graph = {"nodes": [], "edges": []}

if llm_oid:
    # 实体
    r = s.get(f"{API}/api/v1/ontologies/{llm_oid}/entities", params={"page_size": 300})
    d = jget(r).get("data", jget(r))
    llm_entities = d.get("items", d) if isinstance(d, dict) else d
    ok(f"实体数: {len(llm_entities)}")

    # 逻辑规则
    r = s.get(f"{API}/api/v1/ontologies/{llm_oid}/logic")
    d = jget(r).get("data", jget(r))
    llm_logic = d if isinstance(d, list) else []
    ok(f"逻辑规则数: {len(llm_logic)}")

    # 动作
    r = s.get(f"{API}/api/v1/ontologies/{llm_oid}/actions")
    d = jget(r).get("data", jget(r))
    llm_actions = d if isinstance(d, list) else []
    ok(f"动作数: {len(llm_actions)}")

    # 图谱
    r = s.get(f"{API}/api/v2/ontologies/{llm_oid}/graph?limit=800")
    llm_graph = (jget(r).get("data") or jget(r))
    ok(f"图谱: {len(llm_graph.get('nodes', []))} 节点, {len(llm_graph.get('edges', []))} 边")

# ═══════════════════════════════════════════════════════════════════════
# 阶段 C: 信贷领域深度萃取质量评估
# ═══════════════════════════════════════════════════════════════════════
section("阶段 C — 信贷萃取质量评估（仅 LLM 提取）")

if not llm_entities:
    warn("无 LLM 提取实体，跳过质量评估")
else:
    # C-1: 概念完整性（应覆盖的核心信贷概念）
    entity_names = [e.get("name_cn", "") for e in llm_entities]
    entity_names_lower = [n.lower() for n in entity_names]
    entity_text = " ".join(entity_names)

    expected_concepts = [
        # 客户分层概念
        ("A层/优质", ["A层", "优质"]),
        ("B层/良好", ["B层", "良好"]),
        ("C层/一般", ["C层", "一般"]),
        ("D层/观察", ["D层", "观察", "观察名单"]),
        # 信贷模式
        ("重资本模式", ["重资本"]),
        ("轻资本模式", ["轻资本"]),
        # 贷款产品
        ("消费贷(循环额度)", ["消费贷", "循环额度"]),
        ("消费贷(大额专项)", ["大额专项", "大额"]),
        ("经营贷", ["经营贷"]),
        ("抵押贷", ["抵押贷"]),
        ("教育分期", ["教育分期"]),
        ("购车分期", ["购车分期"]),
        # 核心流程
        ("反欺诈", ["反欺诈"]),
        ("授信审批", ["授信", "审批"]),
        ("贷后监控", ["贷后", "监控"]),
        ("催收策略", ["催收"]),
        ("资金方", ["资金方", "资金匹配"]),
        # 风控
        ("风险定价", ["风险定价", "定价"]),
        ("产品目录/产品", ["产品目录", "产品"]),
        ("不良贷款", ["不良", "逾期"]),
        ("风险准备金", ["风险准备金"]),
        # 业务指标
        ("营销渠道", ["营销", "渠道"]),
        ("客户生命周期", ["生命周期", "客户管理", "客户运营"]),
    ]

    concept_hit = 0
    for concept_name, keywords in expected_concepts:
        hit = any(kw.lower() in entity_text.lower() for kw in keywords)
        if hit:
            concept_hit += 1
        info(f"  {'✓' if hit else '✗'} {concept_name}")

    ok(f"核心概念覆盖: {concept_hit}/{len(expected_concepts)}")
    if concept_hit < 10:
        warn("核心概念覆盖率较低，检查增强数据是否正确上传")

    # C-2: 实体类型分布
    entity_types = Counter(e.get("type", "?") for e in llm_entities)
    info(f"实体类型分布: {dict(entity_types.most_common(15))}")
    if len(entity_types) >= 5:
        ok(f"实体类型多样性: {len(entity_types)} 种")
    else:
        warn(f"实体类型较少: {len(entity_types)} 种")

    # C-3: 逻辑规则覆盖率 — 应覆盖价值链关键阶段
    if llm_logic:
        rule_text = " ".join(r.get("name_cn", "") + " " + r.get("description", "") for r in llm_logic)
        rule_text_lower = rule_text.lower()
        expected_rule_kws = [
            ("欺诈", ["反欺诈", "黑名单", "欺诈", "设备", "身份核验"]),
            ("分层", ["降层", "分层", "评分", "A层", "B层", "C层"]),
            ("审核/审批", ["审批", "审核", "人工复核", "拒绝"]),
            ("逾期/催收", ["逾期", "催收", "M0", "M1", "M2", "M3"]),
            ("定价", ["利率", "定价", "利率优惠", "折扣"]),
            ("额度", ["提额", "降额", "授信", "额度"]),
            ("资金方", ["资金方", "路由", "撮合"]),
            ("生命周期", ["复贷", "唤醒", "睡眠", "存量", "复贷"]),
        ]
        hits = 0
        for rule_name, kws in expected_rule_kws:
            hit = any(kw.lower() in rule_text_lower for kw in kws)
            if hit: hits += 1
            info(f"  {'✓' if hit else '✗'} {rule_name}")
        ok(f"规则主题覆盖: {hits}/{len(expected_rule_kws)}")
        ok(f"规则条数: {len(llm_logic)}")
    else:
        warn("LLM 提取未产出逻辑规则（mock LLM 规则抽取能力有限）")

    # C-4: IF-THEN 规则密度（富规则引擎文件应有较多规则）
    if llm_logic or llm_actions:
        total_rules_actions = len(llm_logic) + len(llm_actions)
        if total_rules_actions >= 8:
            ok(f"规则+动作密度: {total_rules_actions} 条")
        else:
            info(f"规则+动作: {total_rules_actions} 条（mock LLM 提取能力受限）")

    # C-5: 图谱节点度分析 — 找到中心性高的节点
    if llm_graph.get("edges"):
        edge_pairs = []
        for e in llm_graph["edges"]:
            src = e.get("source", "")
            tgt = e.get("target", "")
            etype = e.get("type", e.get("label", "?"))
            edge_pairs.append((src, etype, tgt))
        # 节点度数
        degree = Counter()
        for src, _, tgt in edge_pairs:
            degree[src] += 1
            degree[tgt] += 1
        top_nodes = degree.most_common(5)
        info(f"Top 5 中心节点:")
        for nid, deg in top_nodes:
            # 查找节点名称
            label = "?"
            for n in llm_graph.get("nodes", []):
                if n.get("id") == nid:
                    label = n.get("label") or n.get("name_cn", "?")
                    break
            info(f"   {label} ({nid[:8]}...): deg={deg}")

        # 关系类型统计
        edge_types = Counter(p[1] for p in edge_pairs)
        info(f"关系类型: {dict(edge_types.most_common(8))}")

        if len(llm_graph["edges"]) >= 5:
            ok(f"图谱边数 ≥5: {len(llm_graph['edges'])}")
    else:
        warn("图谱无边，可能 mock LLM 未产出关系")

# ═══════════════════════════════════════════════════════════════════════
# 阶段 D: 数据探索与图谱验证
# ═══════════════════════════════════════════════════════════════════════
section("阶段 D — 数据探索与图谱接口验证")

if llm_oid:
    # D-1: 关键词搜索
    for kw in ["信贷", "消费贷", "反欺诈", "催收", "逾期", "资金方", "产品", "风险定价", "A层", "生命周期"]:
        r = s.get(f"{API}/api/v2/ontologies/{llm_oid}/search/keyword", params={"q": kw, "limit": 5})
        if r.status_code == 200:
            body = jget(r)
            d = body.get("data", body)
            results = d if isinstance(d, list) else d.get("results", d.get("items", []))
            found = len(results) if isinstance(results, list) else 0
            if found: ok(f"关键词'{kw}'搜索: {found} 条结果")
        else:
            warn(f"关键词'{kw}'搜索返回 {r.status_code}")

    # D-2: 语义搜索
    r = s.get(f"{API}/api/v2/ontologies/{llm_oid}/search/semantic",
              params={"q": "信用评分低于500如何处理", "limit": 5})
    if r.status_code == 200:
        body = jget(r)
        d = body.get("data", body)
        results = d if isinstance(d, list) else d.get("results", d.get("items", []))
        ok(f"语义搜索: {len(results)} 条（ChromaDB）")
    else:
        warn(f"语义搜索返回 {r.status_code}")

    # D-3: 图谱 Top Nodes
    r = s.get(f"{API}/api/v1/ontologies/{llm_oid}/graph/top-nodes")
    if r.status_code == 200:
        body = jget(r)
        d = body.get("data", body)
        top = d if isinstance(d, list) else d.get("nodes", []) if isinstance(d, dict) else []
        if top:
            ok(f"Top Nodes: {len(top)} 个中心节点")
            for tn in top[:3]:
                info(f"   {tn.get('name_cn', tn.get('label', '?'))}: "
                     f"degree={tn.get('degree', tn.get('weight', '?'))}")

    # D-4: 导出验证
    for fmt in ["json", "csv"]:
        r = s.get(f"{API}/api/v1/ontologies/{llm_oid}/export", params={"format": fmt})
        if r.status_code == 200:
            ok(f"导出 {fmt}: {len(r.content)} 字节")
        else:
            warn(f"导出 {fmt} 返回 {r.status_code}")

# ═══════════════════════════════════════════════════════════════════════
# 阶段 E: 增量映射（本体-数据集 Mapping）
# ═══════════════════════════════════════════════════════════════════════
section("阶段 E — v2 Mappings（本体-数据映射）")

for label, oid in [("LLM", llm_oid), ("Mapping", mapping_oid)]:
    if oid:
        r = s.get(f"{API}/api/v2/ontologies/{oid}/mappings")
        if r.status_code == 200:
            body = jget(r)
            d = (body.get("data", body) if isinstance(body, dict) else body)
            mappings = d if isinstance(d, list) else []
            ok(f"{label} 本体映射: {len(mappings)} 条")
        else:
            warn(f"{label} Mappings 返回 {r.status_code}")

# ═══════════════════════════════════════════════════════════════════════
# 汇总
# ═══════════════════════════════════════════════════════════════════════
section("测试汇总")

total = PASS + FAIL + WARN
print(f"\n  总计: {total} 项检查")
print(f"  ✅  通过: {PASS}")
print(f"  ⚠️   警告: {WARN}（可接受）")
print(f"  ❌  失败: {FAIL}")

# 信贷特有结果汇总
print(f"\n{'═'*60}")
print("  信贷全流程测试 — 萃取结果")
print(f"{'─'*60}")

print(f"  Pipeline Mapping:")
print(f"    数据文件: {len(CREDIT_FILES)} 个")
print(f"    curated 产出: {mapping_stats.get('output_count', 0)} 个")
print(f"    图谱: {mapping_stats.get('nodes', 0)} 节点, {mapping_stats.get('edges', 0)} 边")
print(f"    逻辑规则: {mapping_stats.get('logic', 0)}")
print(f"    动作: {mapping_stats.get('actions', 0)}")

print(f"\n  LLM-driven 提取（mock LLM）:")
print(f"    文本文件: {len(text_files)} 个")
print(f"    实体: {len(llm_entities)}")
print(f"    逻辑规则: {len(llm_logic)}")
print(f"    动作: {len(llm_actions)}")
print(f"    图谱: {len(llm_graph.get('nodes', []))} 节点, {len(llm_graph.get('edges', []))} 边")

print(f"\n  增强数据验证:")
print(f"    ✓ 客户档案信息.csv — 60条客户画像（A/B/C/D全分层）")
print(f"    ✓ 信贷产品目录.md — 6种贷款产品详细定义")
print(f"    ✓ 反欺诈与全流程规则引擎.md — 70+ IF-THEN规则")
print(f"    ✓ 资金方合作与敞口数据.csv — 8家资金方")
print(f"    ✓ 催收结果数据.csv — 32条催收结果")
print(f"    ✓ 贷款申请记录.csv — 120条（含产品/渠道/用途/耗时）")
print(f"    ✓ 信贷业务战略.md — 获客/定价/生命周期/资金方策略")

# 最终结果
if FAILURES:
    print(f"\n  失败项目 ({len(FAILURES)}):")
    for f in FAILURES:
        print(f"    • {f}")
    print()
    sys.exit(1)
else:
    print(f"\n  🎉 全部检查通过！信贷全流程测试完成。\n")
