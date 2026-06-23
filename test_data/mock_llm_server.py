#!/usr/bin/env python3
"""本地 OpenAI 兼容 Mock LLM — 用于无真实 API Key 时验证简易提取路径。

启发式从文档文本中提取实体/关系/逻辑规则/动作,返回 nano-ontoprompt
期望的 JSON 结构。仅验证链路机械正确性,不代表真实 LLM 提取质量。

用法: python test_data/mock_llm_server.py  (监听 8123)
"""
import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8123

ORG_RE = re.compile(r'[一-龥]{2,10}(?:公司|集团|科技|物流|铝业|五金|包装|原材料|医院|药业|银行|事务所|大学|学院)')
HEADING_RE = re.compile(r'^#{1,4}\s+[\d\.\s]*(.+?)\s*$', re.MULTILINE)
IF_THEN_RE = re.compile(r'IF\s+(.+?)\s+THEN\s+(.+?)(?=\n|$)', re.IGNORECASE)
IF_COMMA_RE = re.compile(r'^\s*[-*]?\s*IF\s+([^,，]+?)[,，]\s*(.+)$', re.IGNORECASE | re.MULTILINE)
ACTION_VERB_RE = re.compile(r'(通知|触发|审批|发送|上报|冻结|暂停|启动|提交)([一-龥A-Za-z]{2,12})')


def heuristic_extract(text: str) -> dict:
    entities, seen = [], set()

    for m in ORG_RE.findall(text):
        if m not in seen and len(seen) < 12:
            seen.add(m)
            entities.append({"name_cn": m, "name_en": "", "type": "Organization",
                             "description": f"文档中提到的组织: {m}", "properties": {}, "confidence": 0.9})

    headings = [h.strip() for h in HEADING_RE.findall(text) if 2 <= len(h.strip()) <= 20]
    for h in headings[:10]:
        if h not in seen:
            seen.add(h)
            entities.append({"name_cn": h, "name_en": "", "type": "Concept",
                             "description": f"文档章节概念: {h}", "properties": {}, "confidence": 0.85})

    relations = []
    concepts = [e["name_cn"] for e in entities if e["type"] == "Concept"]
    orgs = [e["name_cn"] for e in entities if e["type"] == "Organization"]
    for i in range(len(concepts) - 1):
        relations.append({"source": concepts[i], "target": concepts[i + 1], "type": "related_to"})
    for o in orgs:
        if concepts:
            relations.append({"source": o, "target": concepts[0], "type": "involved_in"})

    logic_rules = []
    rules = IF_THEN_RE.findall(text) + [
        m for m in IF_COMMA_RE.findall(text)
        if not re.search(r'\bTHEN\b', m[0] + m[1], re.IGNORECASE)
    ]
    for i, (cond, act) in enumerate(rules[:10], 1):
        logic_rules.append({"name_cn": f"规则{i}: {cond.strip()[:24]}", "name_en": f"rule_{i}",
                            "description": f"IF {cond.strip()} THEN {act.strip()}",
                            "formula": f"IF {cond.strip()[:40]}", "linked_entities": concepts[:1],
                            "confidence": 0.8})

    actions, seen_act = [], set()
    for verb, obj in ACTION_VERB_RE.findall(text):
        name = f"{verb}{obj}"
        if name not in seen_act and len(seen_act) < 8:
            seen_act.add(name)
            actions.append({"name_cn": name, "name_en": "", "description": f"文档中的动作: {name}",
                            "trigger_condition": "", "linked_entities": concepts[:1], "confidence": 0.75})

    return {"entities": entities, "relations": relations, "logic_rules": logic_rules, "actions": actions}


def heuristic_infer_relations(user_content: str) -> dict:
    """Generate IS-A / PART-OF mock relations from entity list in infer_relations prompt."""
    entity_block = ""
    for line in user_content.split("\n"):
        if line.startswith("- "):
            entity_block += line + "\n"
    # Extract name_cn from lines like "- 名称 (type): desc"
    names = []
    for line in entity_block.strip().split("\n"):
        m = re.match(r'^- (.+?) \(', line)
        if m:
            names.append(m.group(1).strip())
    # Pair adjacent entities with IS-A
    relations = []
    for i in range(min(len(names) - 1, 8)):
        relations.append({"source": names[i], "target": names[i + 1], "type": "IS-A", "confidence": 0.75})
    return {"relations": relations}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        user_content = ""
        for msg in body.get("messages", []):
            if msg.get("role") == "user":
                user_content = msg.get("content", "")

        if "提取本体信息" in user_content:
            result = heuristic_extract(user_content)
        elif "已提取实体：" in user_content:
            result = heuristic_infer_relations(user_content)
        else:
            result = {"relations": []}

        resp = {
            "id": "mock-1", "object": "chat.completion", "created": 0, "model": "mock-extractor",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": json.dumps(result, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        data = json.dumps(resp, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"Mock LLM listening on :{PORT}")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
