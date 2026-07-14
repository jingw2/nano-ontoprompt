"""
纯 API 方式运行三领域简易 LLM 提取，避免 Playwright 浏览器内存开销。
"""
import requests, time, sys, os

API = "http://localhost:8000"
BASE = API + "/api/v1"

# Login
resp = requests.post(f"{BASE}/auth/login", json={"username": "admin", "password": "admin123"})
token = resp.json()["data"]["access_token"]
headers = {"Authorization": f"Bearer {token}"}
print("Login OK")

DOMAINS = {
    "供应链": {"prompt_id": "9dad1123-72eb-4b9b-b5b3-1777c54ca3cd", "dir": "供应链"},
    "医疗":   {"prompt_id": "d9bf7a9a-5313-4be3-b941-88c33f280566", "dir": "医疗"},
    "财务":   {"prompt_id": "bff40feb-6f53-460e-97d1-b5e8d4f4a9be", "dir": "财务"},
}
MODEL_ID = "8f347f97-e844-4d62-b81b-8c655cd3b410"
MODEL_NAME = "deepseek-v4-flash"

ts = int(time.time() * 1000)
results = []

for domain, cfg in DOMAINS.items():
    print(f"\n{'='*60}")
    print(f"  {domain} 简易 LLM 提取")
    print(f"{'='*60}")

    # 1. Create ontology
    resp = requests.post(f"{BASE}/ontologies", json={
        "name": f"API_{domain}_SimpleLLM_{ts}",
        "domain": domain,
        "description": f"API only — {domain} 简易LLM",
        "build_mode": "simple_llm",
    }, headers=headers)
    oid = resp.json()["data"]["id"]
    print(f"  Ontology: {oid[:8]}")

    # 2. Upload files
    domain_dir = cfg["dir"]
    files = sorted([f for f in os.listdir(domain_dir) if os.path.isfile(os.path.join(domain_dir, f))])
    file_ids = []
    for fname in files:
        fpath = os.path.join(domain_dir, fname)
        with open(fpath, "rb") as f:
            resp = requests.post(f"{BASE}/ontologies/{oid}/files",
                headers=headers,
                files={"file": (fname, f, "application/octet-stream")})
        fid = resp.json()["data"]["id"]
        file_ids.append(fid)
    print(f"  Uploaded {len(file_ids)} files")

    # 3. Execute extraction
    resp = requests.post(f"{BASE}/ontologies/{oid}/execute", json={
        "prompt_id": cfg["prompt_id"],
        "model_id": MODEL_ID,
        "model_name": MODEL_NAME,
        "file_ids": file_ids,
        "constraints": [],
    }, headers=headers)
    task_id = resp.json()["data"]["task_id"]
    print(f"  Task: {task_id[:8]}, waiting...")

    # 4. Poll for completion
    start = time.time()
    while time.time() - start < 1800:  # 30 min timeout
        resp = requests.get(
            f"{BASE}/ontologies/{oid}/execute/status?task_id={task_id}",
            headers=headers)
        status_data = resp.json()["data"]
        status = status_data["status"]
        pct = status_data.get("progress", {}).get("pct", 0)

        elapsed = int(time.time() - start)
        mins = elapsed // 60
        secs = elapsed % 60
        sys.stdout.write(f"\r  [{mins:02d}:{secs:02d}] status={status} pct={pct}%   ")
        sys.stdout.flush()

        if status == "completed":
            print("\n  Completed!")
            break
        elif status == "failed":
            print(f"\n  FAILED: {status_data.get('error', '')}")
            break

        time.sleep(10)
    else:
        print(f"\n  TIMEOUT!")
        continue

    # 5. Collect stats
    ents = requests.get(f"{BASE}/ontologies/{oid}/entities", headers=headers)
    ent_count = len(ents.json().get("data", []))

    graph = requests.get(f"{BASE}/ontologies/{oid}/graph?limit=1", headers=headers)
    edge_count = graph.json().get("total_edges", graph.json().get("data", {}).get("total_edges", 0))

    logic = requests.get(f"{BASE}/ontologies/{oid}/logic", headers=headers)
    logic_count = len(logic.json().get("data", []))

    actions = requests.get(f"{BASE}/ontologies/{oid}/actions", headers=headers)
    action_count = len(actions.json().get("data", []))

    print(f"  Stats: entities={ent_count} edges={edge_count} logic={logic_count} actions={action_count}")
    results.append({
        "domain": domain,
        "path": "简易 LLM",
        "entities": ent_count,
        "edges": edge_count,
        "logic": logic_count,
        "actions": action_count,
        "ontology_id": oid[:8],
    })

# Print table
print(f"\n{'='*70}")
print(f"         三领域 简易 LLM 汇总表")
print(f"{'='*70}")
print(f"| {'业务域':<6} | {'路径':<8} | {'实体数':<6} | {'边数':<6} | {'逻辑':<6} | {'动作':<6} |")
print(f"|{'-'*8}|{'-'*10}|{'-'*8}|{'-'*8}|{'-'*8}|{'-'*8}|")
for r in results:
    print(f"| {r['domain']:<6} | {r['path']:<8} | {r['entities']:<6} | {r['edges']:<6} | {r['logic']:<6} | {r['actions']:<6} |")
print(f"{'='*70}")
