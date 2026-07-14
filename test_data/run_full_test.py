#!/usr/bin/env python3
"""
run_full_test.py — Full-flow API test for nano-ontoprompt
Covers 10 steps: Auth → Models → Upload → Dataset Details →
Pipeline → Curated → LLM Extraction → Ontology Tabs → Related → Settings

Usage:
    python test_data/run_full_test.py
    python test_data/run_full_test.py --base-url http://localhost:8000

Prints ✅ for passes, ⚠️ for acceptable empty-state warnings.
Exits with sys.exit(1) on real failures (bad HTTP status, missing required fields).
"""

import sys
import os
import time
import argparse

# ── dependency check ──────────────────────────────────────────────────────────
try:
    import requests
except ImportError:
    print("ERROR: requests library not found. Install with: pip install requests")
    sys.exit(1)

# ── configuration ─────────────────────────────────────────────────────────────
DEFAULT_BASE_URL = "http://localhost:8000"
USERNAME = "admin"
PASSWORD = "admin123"

SUPPLY_CHAIN_DIR = os.path.join(os.path.dirname(__file__), "供应链")
LEGAL_DIR = os.path.join(os.path.dirname(__file__), "法律")

# ── helpers ───────────────────────────────────────────────────────────────────
FAILURES: list[str] = []
WARNINGS: list[str] = []


def step(n: str, title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  STEP {n}: {title}")
    print(f"{'='*60}")


def ok(msg: str) -> None:
    print(f"  ✅  {msg}")


def warn(msg: str) -> None:
    print(f"  ⚠️   {msg}")
    WARNINGS.append(msg)


def fail(msg: str) -> None:
    print(f"  ❌  {msg}")
    FAILURES.append(msg)


def check_status(resp: requests.Response, expected: int, label: str) -> bool:
    if resp.status_code != expected:
        fail(f"{label} — expected HTTP {expected}, got {resp.status_code}: {resp.text[:200]}")
        return False
    return True


def require_field(obj: dict, field: str, label: str) -> bool:
    if field not in obj:
        fail(f"{label} — missing required field '{field}' in response")
        return False
    return True


def get_json(resp: requests.Response) -> dict:
    try:
        return resp.json()
    except Exception:
        return {}


# ── test driver ───────────────────────────────────────────────────────────────

def main(base_url: str) -> None:
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 1: Auth
    # ─────────────────────────────────────────────────────────────────────────
    step("1", "Auth — POST /api/v1/auth/login")

    resp = session.post(f"{base_url}/api/v1/auth/login",
                        json={"username": USERNAME, "password": PASSWORD})
    if not check_status(resp, 200, "login"):
        print("\nFATAL: Cannot authenticate. Check credentials and that the server is running.")
        sys.exit(1)

    body = get_json(resp)
    token = body.get("data", {}).get("access_token")
    if not token:
        fail("login response missing data.access_token")
        sys.exit(1)

    ok(f"Logged in as '{USERNAME}', got JWT token")
    session.headers.update({"Authorization": f"Bearer {token}"})

    # Verify /profile
    resp = session.get(f"{base_url}/api/v1/auth/profile")
    if check_status(resp, 200, "GET /profile"):
        profile = get_json(resp).get("data", {})
        ok(f"Profile: username={profile.get('username')}, role={profile.get('role')}")
    else:
        fail("Could not fetch profile after login")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 2: Models — GET /api/v1/models
    # ─────────────────────────────────────────────────────────────────────────
    step("2", "Models — GET /api/v1/models")

    resp = session.get(f"{base_url}/api/v1/models")
    if not check_status(resp, 200, "GET /api/v1/models"):
        warn("Models endpoint failed — continuing without model test")
    else:
        data = get_json(resp).get("data", [])
        if len(data) == 0:
            warn("No model configs found. Add a model in Settings → Models to enable LLM tests.")
        else:
            model = data[0]
            ok(f"Found {len(data)} model(s). First: name={model.get('name')}, provider={model.get('provider')}")
            # Test first model connection
            model_id = model.get("id")
            if model_id:
                resp2 = session.post(f"{base_url}/api/v1/models/{model_id}/test")
                if resp2.status_code == 200:
                    ok(f"Model connection test passed for '{model.get('name')}'")
                else:
                    warn(f"Model connection test failed (HTTP {resp2.status_code}) — API key may not be set")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 3: File Upload — POST /api/v2/datasets/upload (CSV files from 供应链/)
    # ─────────────────────────────────────────────────────────────────────────
    step("3", "File Upload — POST /api/v2/datasets/upload (供应链 CSV files)")

    csv_files = []
    if os.path.isdir(SUPPLY_CHAIN_DIR):
        csv_files = [
            f for f in os.listdir(SUPPLY_CHAIN_DIR)
            if f.lower().endswith(".csv")
        ]
    else:
        warn(f"供应链 directory not found at {SUPPLY_CHAIN_DIR}")

    uploaded_dataset_ids: list[str] = []

    if not csv_files:
        warn("No CSV files found in test_data/供应链/ — skipping upload test")
    else:
        ok(f"Found {len(csv_files)} CSV file(s) in 供应链/: {csv_files}")
        for fname in csv_files:
            fpath = os.path.join(SUPPLY_CHAIN_DIR, fname)
            with open(fpath, "rb") as f:
                resp = session.post(
                    f"{base_url}/api/v2/datasets/upload",
                    files={"file": (fname, f, "text/csv")},
                    headers={"Content-Type": None},
                )
            if check_status(resp, 201, f"upload {fname}"):
                body = get_json(resp)
                ds = body.get("data", {})
                ds_id = ds.get("id")
                if ds_id:
                    uploaded_dataset_ids.append(ds_id)
                    ok(f"Uploaded '{fname}' → dataset id={ds_id}, kind={ds.get('kind')}")
                else:
                    fail(f"Upload response for '{fname}' missing data.id")
            else:
                warn(f"Upload failed for '{fname}' — non-fatal, continuing")

    # Verify GET /api/v2/datasets list
    resp = session.get(f"{base_url}/api/v2/datasets")
    if check_status(resp, 200, "GET /api/v2/datasets"):
        datasets = get_json(resp)
        if not isinstance(datasets, list):
            fail("GET /api/v2/datasets should return a list")
        else:
            ok(f"Dataset list returned {len(datasets)} total dataset(s)")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 3b: Dataset Details — schema + stats
    # ─────────────────────────────────────────────────────────────────────────
    step("3b", "Dataset Details — /schema and /stats for uploaded datasets")

    if not uploaded_dataset_ids:
        warn("No uploaded datasets to inspect — using first dataset from list if available")
        resp = session.get(f"{base_url}/api/v2/datasets")
        if resp.status_code == 200:
            all_ds = get_json(resp)
            if isinstance(all_ds, list) and all_ds:
                uploaded_dataset_ids = [all_ds[0]["id"]]
            else:
                warn("No datasets in system — skipping Step 3b")

    for ds_id in uploaded_dataset_ids[:2]:  # check first 2
        # schema
        resp = session.get(f"{base_url}/api/v2/datasets/{ds_id}/schema")
        if check_status(resp, 200, f"GET /api/v2/datasets/{ds_id}/schema"):
            schema = get_json(resp)
            if "dataset_id" not in schema:
                fail(f"Schema response missing 'dataset_id' for {ds_id}")
            elif "columns" not in schema:
                fail(f"Schema response missing 'columns' for {ds_id}")
            else:
                ok(f"Schema for {ds_id}: {len(schema['columns'])} column(s)")

        # stats
        resp = session.get(f"{base_url}/api/v2/datasets/{ds_id}/stats")
        if check_status(resp, 200, f"GET /api/v2/datasets/{ds_id}/stats"):
            stats = get_json(resp)
            for field in ("dataset_id", "row_count", "column_count", "null_rates", "version_count"):
                require_field(stats, field, f"stats for {ds_id}")
            ok(f"Stats for {ds_id}: rows={stats.get('row_count')}, cols={stats.get('column_count')}, versions={stats.get('version_count')}")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 4: Pipeline Run
    # ─────────────────────────────────────────────────────────────────────────
    step("4", "Pipeline Run — POST /api/v2/pipelines + run")

    pipeline_id: str | None = None
    run_id: str | None = None

    if not uploaded_dataset_ids:
        warn("No datasets available — skipping pipeline creation")
    else:
        source_ds_id = uploaded_dataset_ids[0]
        resp = session.post(
            f"{base_url}/api/v2/pipelines",
            json={
                "name": "test-pipeline-run-full",
                "source_dataset_id": source_ds_id,
                "route": "A",
                "spec": {},
            },
        )
        if check_status(resp, 201, "POST /api/v2/pipelines"):
            body = get_json(resp)
            pipeline_id = body.get("id")
            ok(f"Created pipeline id={pipeline_id}, status={body.get('status')}")

            # Run the pipeline
            resp2 = session.post(f"{base_url}/api/v2/pipelines/{pipeline_id}/run")
            if check_status(resp2, 200, f"POST /api/v2/pipelines/{pipeline_id}/run"):
                run_body = get_json(resp2)
                run_id = run_body.get("run_id")
                ok(f"Pipeline run triggered: run_id={run_id}, status={run_body.get('status')}")

                # Poll run status for up to 30s
                if run_id:
                    for i in range(15):
                        time.sleep(2)
                        resp3 = session.get(f"{base_url}/api/v2/pipelines/runs/{run_id}")
                        if resp3.status_code == 200:
                            run_status = get_json(resp3).get("status", "unknown")
                            if run_status in ("completed", "done", "success"):
                                ok(f"Pipeline run completed (status={run_status}) after ~{(i+1)*2}s")
                                break
                            elif run_status in ("failed", "error"):
                                warn(f"Pipeline run finished with status={run_status} (may be expected if no worker)")
                                break
                            # still running/pending — keep polling
                        else:
                            warn(f"GET /api/v2/pipelines/runs/{run_id} returned {resp3.status_code}")
                            break
                    else:
                        warn(f"Pipeline run still pending/running after 30s — Celery worker may not be running")

    # List pipelines
    resp = session.get(f"{base_url}/api/v2/pipelines")
    if check_status(resp, 200, "GET /api/v2/pipelines"):
        pls = get_json(resp)
        ok(f"Pipeline list: {len(pls)} total pipeline(s)")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 5: Curated Review
    # ─────────────────────────────────────────────────────────────────────────
    step("5", "Curated Review — GET /api/v2/curated, quality, review")

    resp = session.get(f"{base_url}/api/v2/curated")
    curated_list = []
    if check_status(resp, 200, "GET /api/v2/curated"):
        curated_list = get_json(resp)
        if not isinstance(curated_list, list):
            fail("GET /api/v2/curated should return a list")
        elif len(curated_list) == 0:
            warn("No curated datasets found — pipeline may not have produced curated output yet")
        else:
            ok(f"Found {len(curated_list)} curated dataset(s)")
            first = curated_list[0]
            cid = first.get("id")
            ok(f"First curated: id={cid}, status={first.get('status')}, quality={first.get('quality_score')}")

            # Quality report
            resp2 = session.get(f"{base_url}/api/v2/curated/{cid}/quality")
            if check_status(resp2, 200, f"GET /api/v2/curated/{cid}/quality"):
                qr = get_json(resp2)
                ok(f"Quality report keys: {list(qr.keys())}")

            # Review — approve (only if status is pending_review to avoid re-reviewing)
            if first.get("status") in ("pending_review", "pending"):
                resp3 = session.post(
                    f"{base_url}/api/v2/curated/{cid}/review",
                    params={"action": "approve", "notes": "auto-approved by test script"},
                )
                if resp3.status_code == 200:
                    ok(f"Review submitted: {get_json(resp3)}")
                else:
                    warn(f"Review submit returned HTTP {resp3.status_code} — may require old curated table row")
            else:
                warn(f"Curated dataset already reviewed (status={first.get('status')}) — skipping approve")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 6: Simple LLM Extraction — create ontology, upload PDF, trigger extraction
    # ─────────────────────────────────────────────────────────────────────────
    step("6", "Simple LLM Extraction — create ontology + upload legal PDF + extract")

    ontology_id: str | None = None
    extraction_task_id: str | None = None

    # Create ontology with simple_llm build_mode
    resp = session.post(
        f"{base_url}/api/v1/ontologies",
        json={
            "name": "test-legal-ontology-run-full",
            "domain": "法律",
            "description": "Auto-created by run_full_test.py",
            "build_mode": "simple_llm",
        },
    )
    if resp.status_code == 409:
        # Already exists — fetch it
        warn("Ontology 'test-legal-ontology-run-full' already exists — reusing")
        resp2 = session.get(f"{base_url}/api/v1/ontologies", params={"name": "test-legal-ontology-run-full"})
        if resp2.status_code == 200:
            items = get_json(resp2).get("data", {}).get("items", [])
            if items:
                ontology_id = items[0]["id"]
                ok(f"Reusing existing ontology id={ontology_id}")
    elif check_status(resp, 201, "POST /api/v1/ontologies"):
        body = get_json(resp)
        ontology_id = body.get("data", {}).get("id")
        ok(f"Created ontology id={ontology_id}")

    if ontology_id:
        # Upload a legal PDF file
        legal_files = []
        if os.path.isdir(LEGAL_DIR):
            legal_files = [
                f for f in os.listdir(LEGAL_DIR)
                if f.lower().endswith(".pdf")
            ]

        uploaded_file_id: str | None = None
        if not legal_files:
            warn(f"No PDF files in test_data/法律/ — trying .docx files")
            if os.path.isdir(LEGAL_DIR):
                legal_files = [f for f in os.listdir(LEGAL_DIR) if f.lower().endswith(".docx")]

        if not legal_files:
            warn("No legal documents found — skipping file upload for extraction")
        else:
            fname = legal_files[0]
            fpath = os.path.join(LEGAL_DIR, fname)
            ext = os.path.splitext(fname)[1].lower()
            mime_map = {
                ".pdf": "application/pdf",
                ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".csv": "text/csv",
            }
            mime = mime_map.get(ext, "application/octet-stream")

            with open(fpath, "rb") as f:
                resp2 = session.post(
                    f"{base_url}/api/v1/ontologies/{ontology_id}/files",
                    params={"ontology_id": ontology_id},
                    files={"file": (fname, f, mime)},
                    headers={"Content-Type": None},
                )
            if check_status(resp2, 201, f"upload {fname} to ontology"):
                file_body = get_json(resp2).get("data", {})
                uploaded_file_id = file_body.get("id")
                ok(f"Uploaded '{fname}' → file id={uploaded_file_id}")

        # Trigger extraction (requires model + file)
        resp3 = session.get(f"{base_url}/api/v1/models")
        models_for_extract = get_json(resp3).get("data", []) if resp3.status_code == 200 else []

        if not models_for_extract:
            warn("No model configured — skipping extraction trigger")
        elif not uploaded_file_id:
            warn("No file uploaded — skipping extraction trigger")
        else:
            model_cfg = models_for_extract[0]
            model_id = model_cfg.get("id")
            model_name = (model_cfg.get("models") or [""])[0]
            file_ids = [uploaded_file_id] if uploaded_file_id else []

            # Fetch a prompt_id for the extraction request
            resp_prompts = session.get(f"{base_url}/api/v1/prompts")
            prompt_id = None
            if resp_prompts.status_code == 200:
                prompts_data = get_json(resp_prompts)
                pts = prompts_data.get("data", prompts_data) if isinstance(prompts_data, dict) else prompts_data
                if isinstance(pts, list) and pts:
                    prompt_id = pts[0].get("id")
            if not prompt_id:
                warn("No prompts found — skipping extraction trigger")
            else:
                resp4 = session.post(
                    f"{base_url}/api/v1/ontologies/{ontology_id}/execute",
                    params={"ontology_id": ontology_id},
                    json={
                        "model_id": model_id,
                        "model_name": model_name,
                        "prompt_id": prompt_id,
                        "file_ids": file_ids,
                        "constraints": [],
                    },
                )
                extraction_task_id = None
                if check_status(resp4, 200, f"POST execute ontology"):
                    body4 = get_json(resp4)
                    extraction_task_id = body4.get("data", {}).get("task_id")
                    ok(f"Extraction queued: task_id={extraction_task_id}")

                # Poll extraction status for up to 60s
                if extraction_task_id:
                    for i in range(20):
                        time.sleep(3)
                        resp5 = session.get(
                            f"{base_url}/api/v1/ontologies/{ontology_id}/execute/status",
                            params={"ontology_id": ontology_id, "task_id": extraction_task_id},
                        )
                        if resp5.status_code == 200:
                            task_data = get_json(resp5).get("data", {})
                            status = task_data.get("status", "unknown")
                            if status in ("done", "completed", "success"):
                                ok(f"Extraction completed after ~{(i+1)*3}s")
                                break
                            elif status in ("failed", "error"):
                                warn(f"Extraction status={status} — may be expected without valid LLM key")
                                break
                        else:
                            warn(f"Extraction status endpoint returned {resp5.status_code}")
                            break
                    else:
                        warn("Extraction still running after 60s — continuing")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 8: Ontology Tabs — entities, logic, actions, graph, export
    # ─────────────────────────────────────────────────────────────────────────
    step("8", "Ontology Tabs — entities, logic, actions, graph, export")

    # Use first available ontology
    check_ontology_id: str | None = ontology_id

    if not check_ontology_id:
        resp = session.get(f"{base_url}/api/v1/ontologies")
        if resp.status_code == 200:
            items = get_json(resp).get("data", {}).get("items", [])
            if items:
                check_ontology_id = items[0]["id"]
                ok(f"Using existing ontology id={check_ontology_id} for tab checks")
            else:
                warn("No ontologies found — skipping tab checks")
        else:
            warn("Cannot list ontologies — skipping tab checks")

    if check_ontology_id:
        oid = check_ontology_id

        # Entities
        resp = session.get(
            f"{base_url}/api/v1/ontologies/{oid}/entities",
            params={"ontology_id": oid},
        )
        if check_status(resp, 200, "GET entities"):
            entities = get_json(resp).get("data", [])
            ok(f"Entities tab: {len(entities)} entity/entities")
        else:
            fail(f"Entities tab returned {resp.status_code}")

        # Logic
        resp = session.get(
            f"{base_url}/api/v1/ontologies/{oid}/logic",
            params={"ontology_id": oid},
        )
        if check_status(resp, 200, "GET logic"):
            rules = get_json(resp).get("data", [])
            ok(f"Logic tab: {len(rules)} rule(s)")

        # Actions
        resp = session.get(
            f"{base_url}/api/v1/ontologies/{oid}/actions",
            params={"ontology_id": oid},
        )
        if check_status(resp, 200, "GET actions"):
            actions = get_json(resp).get("data", [])
            ok(f"Actions tab: {len(actions)} action(s)")

        # Graph
        resp = session.get(
            f"{base_url}/api/v1/ontologies/{oid}/graph",
            params={"ontology_id": oid},
        )
        if check_status(resp, 200, "GET graph"):
            graph_data = get_json(resp).get("data", {})
            nodes = graph_data.get("nodes", [])
            edges = graph_data.get("edges", [])
            ok(f"Graph tab: {len(nodes)} nodes, {len(edges)} edges")

        # Export (json format)
        resp = session.get(
            f"{base_url}/api/v1/ontologies/{oid}/export",
            params={"ontology_id": oid, "format": "json"},
        )
        if check_status(resp, 200, "GET export json"):
            ok(f"Export (json) returned {len(resp.content)} bytes")
        else:
            warn(f"Export returned HTTP {resp.status_code}")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 9: /related endpoint for first entity
    # ─────────────────────────────────────────────────────────────────────────
    step("9", "/related endpoint — first entity's related logic + actions")

    if check_ontology_id:
        oid = check_ontology_id
        resp = session.get(
            f"{base_url}/api/v1/ontologies/{oid}/entities",
            params={"ontology_id": oid},
        )
        if resp.status_code == 200:
            entities = get_json(resp).get("data", [])
            if not entities:
                warn("No entities in ontology — skipping /related test")
            else:
                first_entity_id = entities[0]["id"]
                resp2 = session.get(
                    f"{base_url}/api/v1/ontologies/{oid}/entities/{first_entity_id}/related",
                    params={"ontology_id": oid},
                )
                if check_status(resp2, 200, f"GET /related for entity {first_entity_id}"):
                    related = get_json(resp2)
                    if "logic" not in related or "actions" not in related:
                        fail("/related response missing 'logic' or 'actions' fields")
                    else:
                        ok(f"/related for entity '{entities[0].get('name_cn')}': "
                           f"{len(related['logic'])} logic rule(s), {len(related['actions'])} action(s)")
        else:
            warn("Could not list entities for /related test")
    else:
        warn("No ontology available — skipping /related test")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 10: Settings — prompts, confidence rules, user management,
    #          generate-template, connections
    # ─────────────────────────────────────────────────────────────────────────
    step("10", "Settings — prompts, confidence rules, users, connections")

    # 10a: Prompts list
    resp = session.get(f"{base_url}/api/v1/prompts")
    if check_status(resp, 200, "GET /api/v1/prompts"):
        prompts = get_json(resp).get("data", [])
        ok(f"Prompts: {len(prompts)} prompt(s) in DB")

    # 10b: Builtin prompt templates
    resp = session.get(f"{base_url}/api/v1/prompts/templates")
    if check_status(resp, 200, "GET /api/v1/prompts/templates"):
        templates = get_json(resp).get("data", [])
        if len(templates) == 0:
            fail("No builtin prompt templates returned")
        else:
            ok(f"Builtin prompt templates: {len(templates)} template(s)")

    # 10c: Confidence rules
    resp = session.get(f"{base_url}/api/v1/settings/rules")
    if check_status(resp, 200, "GET /api/v1/settings/rules"):
        rules = get_json(resp).get("data", [])
        if len(rules) == 0:
            warn("No confidence rules found — may not have been seeded yet")
        else:
            ok(f"Confidence rules: {len(rules)} rule(s) (e.g. {rules[0].get('rule_key')}={rules[0].get('rule_value')})")

    # 10d: User management (requires admin role)
    resp = session.get(f"{base_url}/api/v1/users")
    if resp.status_code == 403:
        warn("GET /api/v1/users returned 403 — current user lacks admin role")
    elif check_status(resp, 200, "GET /api/v1/users"):
        users = get_json(resp).get("data", [])
        ok(f"User management: {len(users)} user(s)")

    # 10e: Generate prompt template (requires model to be configured)
    resp3 = session.get(f"{base_url}/api/v1/models")
    has_model = (resp3.status_code == 200 and len(get_json(resp3).get("data", [])) > 0)
    if not has_model:
        warn("Skipping generate-template test — no model configured")
    else:
        resp = session.post(
            f"{base_url}/api/v1/prompts/generate-template",
            params={"domain": "供应链", "style": "ontology_extraction"},
        )
        if resp.status_code == 200:
            tpl = get_json(resp)
            if "content" in tpl and len(tpl["content"]) > 10:
                ok(f"generate-template returned {len(tpl['content'])} chars for domain '供应链'")
            else:
                warn(f"generate-template response: {tpl}")
        elif resp.status_code == 400:
            warn(f"generate-template returned 400 — LLM not reachable or key invalid: {resp.text[:100]}")
        else:
            warn(f"generate-template returned HTTP {resp.status_code}")

    # 10f: Connections list
    resp = session.get(f"{base_url}/api/v2/connections")
    if check_status(resp, 200, "GET /api/v2/connections"):
        conns = get_json(resp)
        if not isinstance(conns, list):
            fail("GET /api/v2/connections should return a list")
        elif len(conns) == 0:
            warn("No connections configured")
        else:
            ok(f"Connections: {len(conns)} connection(s)")
            # Test sync on first connection
            conn_id = conns[0].get("id")
            if conn_id:
                resp2 = session.post(f"{base_url}/api/v2/connections/{conn_id}/sync")
                if resp2.status_code == 200:
                    ok(f"Connection sync triggered for id={conn_id}")
                else:
                    warn(f"Connection sync returned {resp2.status_code}")

    # ─────────────────────────────────────────────────────────────────────────
    # Summary
    # ─────────────────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}")

    if WARNINGS:
        print(f"\n  ⚠️  Warnings ({len(WARNINGS)}):")
        for w in WARNINGS:
            print(f"      - {w}")

    if FAILURES:
        print(f"\n  ❌  Failures ({len(FAILURES)}):")
        for f_msg in FAILURES:
            print(f"      - {f_msg}")
        print(f"\n  Test FAILED with {len(FAILURES)} failure(s).")
        sys.exit(1)
    else:
        print(f"\n  ✅  All required checks passed!")
        if WARNINGS:
            print(f"      ({len(WARNINGS)} warning(s) — see above; these are acceptable for empty/unconfigured state)")
        sys.exit(0)


# ── entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Full-flow API test for nano-ontoprompt")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Backend base URL (default: {DEFAULT_BASE_URL})",
    )
    args = parser.parse_args()
    main(args.base_url)
