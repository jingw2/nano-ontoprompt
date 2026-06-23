"""
导入 snomed_mental_health.csv 到 nano-ontoprompt 的医疗本体。

用法：
  python import_snomed.py [--csv PATH] [--ontology ONTOLOGY_ID] [--dry-run]

默认值：
  --csv       ../docker/openim/snomed_mental_health.csv
  --ontology  o-medical-001
"""

import csv
import os
import sys
import time
import argparse
import requests

API_BASE = os.getenv("API_BASE", "http://localhost:8000/api/v1")
USERNAME = os.getenv("ONTOLOGY_USERNAME", "admin")
PASSWORD = os.getenv("ONTOLOGY_PASSWORD", "changeme123")

# CSV type → OntoPrompt entity type
TYPE_MAP = {
    "symptom":  "Symptom",
    "disorder": "Disease",
    "indicator": "RiskIndicator",
}

# CSV category → description prefix (辅助可读性)
CATEGORY_LABEL = {
    "depression":       "抑郁",
    "sleep":            "睡眠",
    "anxiety":          "焦虑",
    "cognitive":        "认知",
    "somatic":          "躯体",
    "bipolar":          "双相",
    "schizophrenia":    "精神分裂",
    "personality":      "人格障碍",
    "trauma":           "创伤",
    "neurodevelopmental": "神经发育",
    "risk":             "风险指标",
}


def login() -> str:
    r = requests.post(f"{API_BASE}/auth/login",
                      json={"username": USERNAME, "password": PASSWORD})
    r.raise_for_status()
    return r.json()["data"]["access_token"]


def existing_canonical_ids(token: str, ontology_id: str) -> set:
    r = requests.get(f"{API_BASE}/ontologies/{ontology_id}/entities",
                     headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    items = r.json().get("data", [])
    return {e["canonical_id"] for e in items if e.get("canonical_id")}


def load_csv(path: str) -> list[dict]:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or line.strip() == "":
                continue
            rows.append(line)
    reader = csv.DictReader(rows)
    return list(reader)


def import_entities(csv_path: str, ontology_id: str, dry_run: bool):
    token = login()
    print(f"✓ 登录成功")

    existing = existing_canonical_ids(token, ontology_id)
    print(f"✓ 已有实体 {len(existing)} 条（按 canonical_id 去重）")

    rows = load_csv(csv_path)
    print(f"✓ CSV 读取 {len(rows)} 条")

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    created = skipped = failed = 0

    for row in rows:
        canonical_id = row.get("canonical_id", "").strip()
        if not canonical_id:
            continue

        if canonical_id in existing:
            skipped += 1
            continue

        entity_type = TYPE_MAP.get(row.get("type", "").strip(), "Symptom")
        category    = row.get("category", "").strip()
        name_zh     = row.get("name_zh", "").strip()
        name_en     = row.get("name_en", "").strip()
        snomed_id   = row.get("snomed_id", "").strip()

        payload = {
            "name_cn":      name_zh or name_en,
            "name_en":      name_en,
            "snomed_id":    snomed_id,
            "canonical_id": canonical_id,
            "type":         entity_type,
            "description":  f"{CATEGORY_LABEL.get(category, category)} · {entity_type}",
            "confidence":   0.95,
        }

        if dry_run:
            print(f"  [DRY] {canonical_id}  {name_zh}")
            created += 1
            continue

        r = requests.post(
            f"{API_BASE}/ontologies/{ontology_id}/entities",
            json=payload,
            headers=headers,
        )
        if r.status_code == 201:
            created += 1
            existing.add(canonical_id)
            print(f"  ✓ {canonical_id}  {name_zh}")
        else:
            failed += 1
            print(f"  ✗ {canonical_id}  {r.status_code} {r.text[:80]}", file=sys.stderr)

        time.sleep(0.05)   # 避免瞬间打满 API

    print(f"\n完成：新增 {created}，跳过 {skipped}（已存在），失败 {failed}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv",      default=os.path.join(os.path.dirname(__file__), "snomed_mental_health.csv"))
    parser.add_argument("--ontology", default="o-medical-001")
    parser.add_argument("--dry-run",  action="store_true", help="只打印不写入")
    args = parser.parse_args()

    import_entities(args.csv, args.ontology, args.dry_run)
