"""
nano-ontoprompt v2 全业务域端到端集成测试

覆盖 7 个业务域：供应链, HR, 财务, 营销, 医疗, 法律, 教育
测试完整的 Palantir 式五阶段 Pipeline：Connections → Datasets → Transforms → Curated → Ontology Mapping

用法: python test_e2e_all_domains.py [--verbose]
"""
import os, sys, json, time, uuid, urllib.request, urllib.error, io, email

BASE_URL = "http://localhost:8000"
TOKEN = None
RESULTS = {"pass": 0, "fail": 0, "skip": 0, "details": []}
VERBOSE = "--verbose" in sys.argv

# ── Helpers ──────────────────────────────────────────────────────────────────
def api(method, path, body=None, files=None, expected_status=None, timeout=60):
    """Call API endpoint, return (status_code, response_body)."""
    url = f"{BASE_URL}{path}"
    headers = {}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"

    data_bytes = None
    if files:
        # Proper multipart/form-data encoding using email.mime
        boundary = "----TestBoundary" + str(uuid.uuid4())[:12]
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        parts = []
        for field_name, (filename, file_content, content_type) in files.items():
            hdr = f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\nContent-Type: {content_type}'
            parts.append(f"--{boundary}\r\n{hdr}\r\n\r\n".encode())
            parts.append(file_content if isinstance(file_content, bytes) else file_content.encode())
            parts.append(b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        data_bytes = b"".join(parts)
    elif body is not None:
        data_bytes = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            rbody = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            rbody = json.loads(e.read().decode())
        except Exception:
            rbody = {"error": str(e)}
    except Exception as e:
        return 0, {"error": str(e)}

    if expected_status is not None and status != expected_status:
        raise AssertionError(f"Expected HTTP {expected_status}, got {status}: {json.dumps(rbody, ensure_ascii=False)[:500]}")
    return status, rbody


def ok(name, extra=""):
    RESULTS["pass"] += 1
    RESULTS["details"].append({"name": name, "status": "PASS"})
    print(f"  ✅ {name}{extra}")

def fail(name, msg=""):
    RESULTS["fail"] += 1
    RESULTS["details"].append({"name": name, "status": "FAIL", "error": str(msg)})
    print(f"  ❌ {name}: {msg}")

def skip(name, reason=""):
    RESULTS["skip"] += 1
    RESULTS["details"].append({"name": name, "status": "SKIP", "error": str(reason)})
    print(f"  ⏭️  {name}{' — ' + reason if reason else ''}")

def check(name, fn):
    try:
        fn()
        ok(name)
    except AssertionError as e:
        fail(name, str(e))
    except Exception as e:
        fail(name, str(e))


# ── Test Data Paths ──────────────────────────────────────────────────────────
TEST_DATA = os.path.join(os.path.dirname(__file__) or ".", "test_data")
DOMAINS = ["供应链", "HR", "财务", "营销", "医疗", "法律", "教育"]
MIME_MAP = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ".csv": "text/csv", ".md": "text/markdown", ".json": "application/json", ".txt": "text/plain"}

def domain_files(domain):
    d = os.path.join(TEST_DATA, domain)
    if not os.path.isdir(d): return []
    return [os.path.join(d, f) for f in os.listdir(d) if os.path.isfile(os.path.join(d, f))]

def docs_files():
    d = os.path.join(TEST_DATA, "documents")
    if not os.path.isdir(d): return []
    return [os.path.join(d, f) for f in os.listdir(d) if os.path.isfile(os.path.join(d, f))]


# ═══════════════════════════════════════════════════════════════════════════════
# 0. HEALTH CHECK & AUTH
# ═══════════════════════════════════════════════════════════════════════════════
print("=" * 70)
print("PART 0: 健康检查 & 认证")
print("=" * 70)

def t_health():
    s, b = api("GET", "/health")
    assert s == 200, f"Status={s}"
    assert b.get("status") == "ok"
    print(f"     DB={b.get('db')}  Neo4j={b.get('neo4j')}  MinIO={b.get('minio')}  Chroma={b.get('chroma')}")

check("Health endpoint", t_health)

def t_login():
    global TOKEN
    s, b = api("POST", "/api/v1/auth/login", {"username": "admin", "password": "admin123"}, expected_status=200)
    TOKEN = b["data"]["access_token"]
    assert TOKEN

check("管理员登录", t_login)

# ═══════════════════════════════════════════════════════════════════════════════
# 1. MODELS & PROMPTS
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 1: Models & Prompts")
print("=" * 70)

def t_models():
    s, b = api("GET", "/api/v1/models", expected_status=200)
    assert isinstance(b.get("data"), list)

check("GET /api/v1/models", t_models)

def t_prompts():
    s, b = api("GET", "/api/v1/prompts", expected_status=200)
    data = b.get("data", [])
    assert len(data) >= 7, f"Expected >=7 prompts, got {len(data)}"
    domains_found = {p.get("domain","") for p in data}
    print(f"     提示词 {len(data)} 个, 领域: {sorted(domains_found)}")

check("GET /api/v1/prompts (≥7个业务域)", t_prompts)

def t_create_model():
    m = {"name": "Test-Model-DeepSeek", "provider": "openai", "model_id": "deepseek-chat",
         "api_key": "sk-test-placeholder", "models": ["deepseek-chat"], "is_default": True,
         "base_url": "https://api.deepseek.com/v1"}
    s, b = api("POST", "/api/v1/models", m, expected_status=200)
    assert b.get("id") or b.get("data", {}).get("id")

check("POST /api/v1/models (创建测试模型)", t_create_model)

# ═══════════════════════════════════════════════════════════════════════════════
# 2. CONNECTIONS (数据连接)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 2: Pipelines > Connections")
print("=" * 70)

CONN_IDS = {}; DATASET_IDS = {}

def t_list_conn():
    s, b = api("GET", "/api/v2/connections", expected_status=200)
    assert isinstance(b, list)

check("GET /api/v2/connections", t_list_conn)

for domain in DOMAINS:
    def make_conn(d):
        def _t():
            body = {"name": f"{d}-Test-Connection", "kind": "file", "config": {"sync_mode": "manual"}}
            s, r = api("POST", "/api/v2/connections", body, expected_status=None)
            assert s in (200, 201), f"Expected 200/201, got {s}: {r}"
            CONN_IDS[d] = r.get("id"); assert CONN_IDS[d]
        return _t
    check(f"POST /api/v2/connections ({domain})", make_conn(domain))

# ═══════════════════════════════════════════════════════════════════════════════
# 3. DATASETS (文件上传 & 数据集)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 3: Pipelines > Datasets (文件上传)")
print("=" * 70)

def t_list_ds():
    s, b = api("GET", "/api/v2/datasets", expected_status=200)
    assert isinstance(b, list)

check("GET /api/v2/datasets", t_list_ds)

def upload_file(fpath):
    fname = os.path.basename(fpath)
    ext = os.path.splitext(fname)[1].lower()
    with open(fpath, "rb") as fh:
        content = fh.read()
    return api("POST", "/api/v2/datasets/upload",
               files={"file": (fname, content, MIME_MAP.get(ext, "application/octet-stream"))})

# Upload documents dir
uploaded_total = 0
for fpath in docs_files():
    s, b = upload_file(fpath)
    if s in (200, 201):
        uploaded_total += 1
        ds_id = b.get("data", {}).get("id") or b.get("id")
        if ds_id: DATASET_IDS[os.path.basename(fpath)] = ds_id
check(f"上传 documents 文件 ({uploaded_total}/{len(docs_files())})",
      lambda: None if uploaded_total > 0 else (_ for _ in ()).throw(AssertionError("0 files uploaded")))

# Upload per domain
for domain in DOMAINS:
    files = domain_files(domain)
    if not files: continue
    up_count = [0]
    def make_up(d, flist):
        def _t():
            for fp in flist:
                s, b = upload_file(fp)
                if s in (200, 201):
                    up_count[0] += 1
                    ds_id = b.get("data", {}).get("id") or b.get("id")
                    if ds_id: DATASET_IDS[os.path.basename(fp)] = ds_id
            assert up_count[0] > 0, f"0/{len(flist)} files uploaded for {d}"
            print(f"     ({up_count[0]}/{len(flist)})", end="")
        return _t
    check(f"上传 {domain} ({len(files)} 文件)", make_up(domain, files))

print(f"   总上传数据集: {len(DATASET_IDS)}")

# ═══════════════════════════════════════════════════════════════════════════════
# 4. TRANSFORMS (Pipeline)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 4: Pipelines > Transforms")
print("=" * 70)

PIPE_IDS = {}

def t_list_pipes():
    s, b = api("GET", "/api/v2/pipelines", expected_status=200)
    assert isinstance(b, list)

check("GET /api/v2/pipelines", t_list_pipes)

for domain in DOMAINS[:3]:
    def make_pipe(d):
        def _t():
            files = domain_files(d)
            has_csv = any(f.endswith('.csv') for f in files)
            has_xlsx = any(f.endswith('.xlsx') for f in files)
            has_json = any(f.endswith('.json') for f in files)

            if has_csv or has_xlsx: path = "structured"
            elif has_json: path = "semi_structured"
            else: path = "unstructured"

            body = {"name": f"{d}-Pipeline", "description": f"{d}领域测试",
                    "transform_path": path, "steps": [
                        {"type": "schema_inference", "config": {}},
                        {"type": "cleansing", "config": {"dedup": True}}
                    ], "status": "draft"}
            s, r = api("POST", "/api/v2/pipelines", body, expected_status=200)
            PIPE_IDS[d] = r.get("id"); assert PIPE_IDS[d]
        return _t
    check(f"POST /api/v2/pipelines ({domain})", make_pipe(domain))

if PIPE_IDS:
    pid = list(PIPE_IDS.values())[0]
    def t_get_pipe():
        s, b = api("GET", f"/api/v2/pipelines/{pid}", expected_status=200)
        assert b.get("id") == pid
    check(f"GET /api/v2/pipelines/{{id}}", t_get_pipe)

    def t_list_runs():
        s, b = api("GET", f"/api/v2/pipelines/{pid}/runs", expected_status=200)
        assert isinstance(b, list) or isinstance(b, dict)
    check(f"GET /api/v2/pipelines/{{id}}/runs", t_list_runs)

# ═══════════════════════════════════════════════════════════════════════════════
# 5. CURATED DATASETS
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 5: Pipelines > Curated Datasets")
print("=" * 70)

CURATED_IDS = []

def t_list_curated():
    s, b = api("GET", "/api/v2/curated", expected_status=200)
    items = b if isinstance(b, list) else b.get("data", [])
    assert isinstance(items, list)
    for item in items:
        if item.get("id"):
            CURATED_IDS.append(item["id"])

check("GET /api/v2/curated", t_list_curated)

if CURATED_IDS:
    cid = CURATED_IDS[0]
    def t_curated_detail():
        s, b = api("GET", f"/api/v2/curated/{cid}", expected_status=200)
        assert b.get("id") == cid
    check("GET /api/v2/curated/{id}", t_curated_detail)

    def t_curated_quality():
        s, b = api("GET", f"/api/v2/curated/{cid}/quality", expected_status=200)
        assert isinstance(b, dict)
    check("GET /api/v2/curated/{id}/quality", t_curated_quality)

    def t_curated_review():
        body = {"status": "approved", "notes": "自动化测试审核通过"}
        s, b = api("POST", f"/api/v2/curated/{cid}/review", body, expected_status=200)
        assert b.get("status") == "approved" or b.get("data", {}).get("status") == "approved"
    check("POST /api/v2/curated/{id}/review (审批)", t_curated_review)

# ═══════════════════════════════════════════════════════════════════════════════
# 6. ONTOLOGIES
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 6: Ontologies")
print("=" * 70)

ONT_IDS = {}

def t_list_ont():
    s, b = api("GET", "/api/v1/ontologies", expected_status=200)
    assert isinstance(b.get("data"), list)

check("GET /api/v1/ontologies", t_list_ont)

for domain, build_mode in [("供应链", "simple_llm"), ("HR", "pipeline_mapping"),
                            ("财务", "simple_llm"), ("医疗", "simple_llm")]:
    def make_ont(d, bm):
        def _t():
            body = {"name": f"{d}-知识图谱-Test", "domain": d, "description": f"{d}端到端测试",
                    "build_mode": bm}
            s, r = api("POST", "/api/v1/ontologies", body, expected_status=200)
            oid = r.get("id") or r.get("data", {}).get("id")
            assert oid
            ONT_IDS[f"{d}_{bm}"] = oid
        return _t
    check(f"POST /api/v1/ontologies ({domain}, {build_mode})", make_ont(domain, build_mode))

ont_id = list(ONT_IDS.values())[0] if ONT_IDS else None

# ═══════════════════════════════════════════════════════════════════════════════
# 7. ENTITIES, LOGIC, ACTIONS (v1 + search)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 7: Entities, Logic, Actions")
print("=" * 70)

if ont_id:
    def t_entities_list():
        s, b = api("GET", f"/api/v1/ontologies/{ont_id}/entities", expected_status=200)
        assert isinstance(b.get("data"), list)
    check("GET entities (v1)", t_entities_list)

    def t_create_entity():
        body = {"name_cn": "测试组织", "name_en": "TestOrg", "type": "Organization",
                "description": "E2E测试", "properties": {"region": "北京"}, "confidence": 0.95}
        s, r = api("POST", f"/api/v1/ontologies/{ont_id}/entities", body, expected_status=200)
        eid = r.get("id") or r.get("data", {}).get("id")
        assert eid
    check("POST entity", t_create_entity)

    def t_entities_search():
        s, b = api("GET", f"/api/v1/ontologies/{ont_id}/entities?q=测试&type=Organization", expected_status=200)
        assert isinstance(b.get("data"), list)
    check("GET entities?q=&type= (搜索过滤)", t_entities_search)

    def t_logic_list():
        s, b = api("GET", f"/api/v1/ontologies/{ont_id}/logic", expected_status=200)
        assert isinstance(b.get("data"), list)
    check("GET logic (v1)", t_logic_list)

    def t_actions_list():
        s, b = api("GET", f"/api/v1/ontologies/{ont_id}/actions", expected_status=200)
        assert isinstance(b.get("data"), list)
    check("GET actions (v1)", t_actions_list)

    def t_create_logic():
        body = {"name_cn": "测试规则", "name_en": "TestRule", "formula": "IF x THEN y",
                "description": "E2E测试", "confidence": 0.9, "linked_entities": ["测试组织"]}
        s, r = api("POST", f"/api/v1/ontologies/{ont_id}/logic", body, expected_status=200)
    check("POST logic", t_create_logic)

    def t_create_action():
        body = {"name_cn": "测试动作", "name_en": "TestAction", "execution_rule": "ON event DO action",
                "description": "E2E测试", "confidence": 0.9, "linked_entities": ["测试组织"],
                "linked_logic_names": ["测试规则"],
                "function_code": "def test_action(ctx: dict) -> dict: return {'ok': True}"}
        s, r = api("POST", f"/api/v1/ontologies/{ont_id}/actions", body, expected_status=200)
    check("POST action", t_create_action)
else:
    skip("Entities/Logic/Actions", "无可用本体")

# ═══════════════════════════════════════════════════════════════════════════════
# 8. GRAPH (Neo4j)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 8: Graph API (Neo4j)")
print("=" * 70)

if ont_id:
    def t_graph():
        s, b = api("GET", f"/api/v2/ontologies/{ont_id}/graph", expected_status=200)
        assert isinstance(b, dict)
    check("GET graph (Neo4j)", t_graph)

    def t_cypher():
        s, b = api("POST", f"/api/v2/ontologies/{ont_id}/graph/cypher",
                   {"query": "MATCH (n) RETURN n LIMIT 3"}, expected_status=200)
        assert isinstance(b, dict)
    check("POST graph/cypher", t_cypher)

    def t_nl_query():
        s, b = api("POST", f"/api/v2/ontologies/{ont_id}/graph/nl-query",
                   {"question": "有哪些实体？"}, expected_status=200)
        assert isinstance(b, dict)
    check("POST graph/nl-query (NL→Cypher)", t_nl_query)

    def t_neighbors():
        s, b = api("GET", f"/api/v2/ontologies/{ont_id}/graph/neighbors/dummy?depth=1", expected_status=200)
        assert isinstance(b, dict)
    check("GET graph/neighbors/{node_id}", t_neighbors)

    def t_communities():
        s, b = api("GET", f"/api/v2/ontologies/{ont_id}/graph/communities", expected_status=200)
        assert isinstance(b, dict)
    check("GET graph/communities", t_communities)
else:
    skip("Graph API", "无可用本体")

# ═══════════════════════════════════════════════════════════════════════════════
# 9. SEARCH
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 9: Search API")
print("=" * 70)

if ont_id:
    def t_search_kw():
        s, b = api("POST", f"/api/v2/ontologies/{ont_id}/search",
                   {"query": "组织", "mode": "keyword"}, expected_status=200)
        assert isinstance(b, dict)
    check("POST search (keyword)", t_search_kw)

    def t_search_semantic():
        s, b = api("POST", f"/api/v2/ontologies/{ont_id}/search",
                   {"query": "供应链上下游", "mode": "semantic"}, expected_status=200)
        assert isinstance(b, dict)
    check("POST search (semantic)", t_search_semantic)
else:
    skip("Search API", "无可用本体")

# ═══════════════════════════════════════════════════════════════════════════════
# 10. ONTOLOGY MAPPING
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 10: Ontology Mapping")
print("=" * 70)

if ont_id:
    def t_mapping_suggest():
        s, b = api("POST", f"/api/v2/ontologies/{ont_id}/mapping/suggest",
                   {"dataset_ids": list(DATASET_IDS.values())[:3] if DATASET_IDS else []},
                   expected_status=200)
        assert isinstance(b, dict)
    check("POST mapping/suggest", t_mapping_suggest)

    def t_get_mapping():
        s, b = api("GET", f"/api/v2/ontologies/{ont_id}/mapping", expected_status=200)
        assert isinstance(b, (dict, list))
    check("GET mapping", t_get_mapping)

    def t_apply_mapping():
        body = {"mappings": [
            {"entity_type": "TestEntity", "property_mappings": [{"column": "name", "property": "name_cn"}]}
        ]}
        s, b = api("POST", f"/api/v2/ontologies/{ont_id}/mapping/apply", body, expected_status=200)
        assert isinstance(b, dict)
    check("POST mapping/apply", t_apply_mapping)
else:
    skip("Mapping API", "无可用本体")

# ═══════════════════════════════════════════════════════════════════════════════
# 11. INCREMENTAL UPDATE
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 11: Incremental Update")
print("=" * 70)

def t_inc_status():
    s, b = api("GET", "/api/v2/incremental/status", expected_status=200)
    assert isinstance(b, (dict, list))
check("GET /api/v2/incremental/status", t_inc_status)

# ═══════════════════════════════════════════════════════════════════════════════
# 12. EXPORT
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 12: Export API")
print("=" * 70)

if ont_id:
    for fmt in ["json", "yaml", "csv"]:
        def make_exp(f):
            def _t():
                s, b = api("GET", f"/api/v1/ontologies/{ont_id}/export?format={f}", expected_status=200)
            return _t
        check(f"GET export?format={fmt}", make_exp(fmt))
else:
    skip("Export", "无可用本体")

# ═══════════════════════════════════════════════════════════════════════════════
# 13. V1 COMPATIBILITY
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 13: v1 Compatibility")
print("=" * 70)

for ep, label in [
    ("/api/v1/overview", "overview"),
    ("/api/v1/settings", "settings"),
]:
    def make_v1(p):
        def _t():
            s, b = api("GET", p, expected_status=200)
            assert isinstance(b, dict)
        return _t
    check(f"GET {p}", make_v1(ep))

# ═══════════════════════════════════════════════════════════════════════════════
# 14. PIPELINES — 完整预处理测试 (preview-step)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("PART 14: Pipeline 高级功能")
print("=" * 70)

def t_preview_step():
    body = {"step_type": "schema_inference", "dataset_id": list(DATASET_IDS.values())[0] if DATASET_IDS else "",
            "config": {}}
    s, b = api("POST", "/api/v2/pipelines/preview-step", body, expected_status=200)
    assert isinstance(b, dict)
check("POST /api/v2/pipelines/preview-step", t_preview_step)

def t_suggest_split():
    body = {"schema": {
        "order_id": "string", "customer_id": "string", "customer_name": "string",
        "product_id": "string", "product_name": "string", "order_date": "date", "amount": "float"
    }, "domain": "供应链"}
    s, b = api("POST", "/api/v2/pipelines/suggest-split", body, expected_status=200)
    assert isinstance(b, dict)
check("POST /api/v2/pipelines/suggest-split (宽表拆分)", t_suggest_split)

# ═══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
t = RESULTS["pass"] + RESULTS["fail"] + RESULTS["skip"]
pct = RESULTS["pass"] / max(t, 1) * 100
print(f"  Total: {t}  |  ✅ Pass: {RESULTS['pass']}  |  ❌ Fail: {RESULTS['fail']}  |  ⏭️ Skip: {RESULTS['skip']}")
print(f"  Pass Rate: {pct:.1f}%")

if RESULTS["fail"]:
    print("\n--- FAILURES ---")
    for d in RESULTS["details"]:
        if d["status"] == "FAIL":
            print(f"  ❌ {d['name']}: {d.get('error','')}")

# Save report
out_path = os.path.join(os.path.dirname(__file__) or ".", "test_results.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(RESULTS, f, ensure_ascii=False, indent=2, default=str)
print(f"\n  Report saved to: {out_path}")

sys.exit(0 if RESULTS["fail"] == 0 else 1)
