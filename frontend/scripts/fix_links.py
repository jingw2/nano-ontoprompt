"""
Post-process an ontology to fill linked_entities and linked_logic_ids.
Strategy:
  1. Exact entity name match in rule/action text
  2. If no exact match, type-keyword match (returns representative entities, max 3 per type)

Usage: python fix_links.py <ontology_id>
       python fix_links.py   (auto-picks latest ontology with entities)
"""
import sys, requests

BASE = "http://localhost:8002/api/v1"
r = requests.post(f"{BASE}/auth/login", json={"username": "admin", "password": "admin123"})
token = r.json()["data"]["access_token"]
H = {"Authorization": f"Bearer {token}"}

if len(sys.argv) > 1:
    oid = sys.argv[1]
else:
    items = requests.get(f"{BASE}/ontologies", headers=H).json()["data"]["items"]
    oid = next(o["id"] for o in items
               if len(requests.get(f"{BASE}/ontologies/{o['id']}/entities", headers=H)
                      .json().get("data", [])) > 5)
    print(f"Auto-selected: {oid}")

entities = requests.get(f"{BASE}/ontologies/{oid}/entities", headers=H).json()["data"]
rules    = requests.get(f"{BASE}/ontologies/{oid}/logic", headers=H).json()["data"]
actions  = requests.get(f"{BASE}/ontologies/{oid}/actions", headers=H).json()["data"]

# Type → Chinese keywords that appear in rule/action text
TYPE_KEYWORDS = {
    "Supplier":  ["供应商", "供货商", "合作伙伴"],
    "Material":  ["物料", "原材料", "辅料", "零部件"],
    "Warehouse": ["库存", "仓库", "仓储"],
    "Product":   ["产品", "成品", "半成品"],
    "Process":   ["流程", "工艺", "工序", "质检"],
    "Document":  ["订单", "采购单", "合同"],
    "Customer":  ["客户", "应收账款"],
    "Category":  ["类别", "分类"],
}
# Reverse: entity → its type
entity_type = {e["name_cn"]: e.get("type", "") for e in entities}
# Group entities by type for representative sampling
from collections import defaultdict
entities_by_type: dict = defaultdict(list)
for e in entities:
    entities_by_type[e.get("type", "")].append(e["name_cn"])

def find_linked_entities(text: str) -> list[str]:
    """Exact name match first; fallback to type-keyword, take up to 3 per matching type."""
    if not text:
        return []
    # 1. Exact name match
    exact = [e["name_cn"] for e in entities if e["name_cn"] and e["name_cn"] in text]
    if exact:
        return exact
    # 2. Type keyword fallback
    result = []
    for etype, keywords in TYPE_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            # Take first 3 entities of this type as representatives
            result.extend(entities_by_type[etype][:3])
    return result

def find_rule_ids(text: str) -> list[str]:
    """Match logic rules whose name partially overlaps with action text."""
    result = []
    for rule in rules:
        name = rule["name_cn"]
        # Use 2-char substrings as keywords (Chinese 2-grams)
        grams = [name[i:i+2] for i in range(len(name)-1)]
        # Count how many grams appear in text
        hits = sum(1 for g in grams if g in text)
        if hits >= 2:
            result.append(rule["id"])
    return result

rule_name_to_id = {r["name_cn"]: r["id"] for r in rules}

# --- Fix logic rules ---
print(f"\n=== Logic Rules ({len(rules)}) ===")
for rule in rules:
    combined = " ".join(filter(None, [rule.get("formula"), rule.get("description"), rule.get("name_cn")]))
    linked = find_linked_entities(combined)
    cur = rule.get("linked_entities") or []
    if set(linked) != set(cur):
        r2 = requests.put(f"{BASE}/ontologies/{oid}/logic/{rule['id']}", headers=H,
                          json={"linked_entities": linked})
        print(f"  ✓ {rule['name_cn']}: {linked[:4]} ({'ok' if r2.ok else r2.status_code})")
    else:
        print(f"  - {rule['name_cn']}: already has {len(linked)} entities")

# --- Fix actions ---
print(f"\n=== Actions ({len(actions)}) ===")
for action in actions:
    combined = " ".join(filter(None, [
        action.get("execution_rule"), action.get("description"), action.get("name_cn")
    ]))
    linked_ents  = find_linked_entities(combined)
    linked_logic = find_rule_ids(combined)

    cur_ents  = action.get("linked_entities") or []
    cur_logic = action.get("linked_logic_ids") or []
    changed = set(linked_ents) != set(cur_ents) or set(linked_logic) != set(cur_logic)
    if changed:
        r2 = requests.put(f"{BASE}/ontologies/{oid}/actions/{action['id']}", headers=H,
                          json={"linked_entities": linked_ents, "linked_logic_ids": linked_logic})
        rule_names = [r["name_cn"] for r in rules if r["id"] in linked_logic]
        print(f"  ✓ {action['name_cn']}:")
        print(f"      entities({len(linked_ents)}): {linked_ents[:4]}")
        print(f"      logic({len(linked_logic)}): {rule_names}")
        print(f"      status: {'ok' if r2.ok else r2.status_code}")
    else:
        print(f"  - {action['name_cn']}: no change")

print("\n✅ Done")
