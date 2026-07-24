import json
import re
from typing import Any

def extract_ontology(text: str, prompt_content: str, model_config: dict, model_name: str, retry_count: int = 3) -> dict:
    provider = model_config.get("provider", "openai")
    api_key = model_config.get("api_key", "")
    api_base = model_config.get("api_base")

    messages = [
        {"role": "system", "content": prompt_content},
        {"role": "user", "content": (
            "请从以下文档中尽可能全面地提取本体信息，以JSON格式返回。\n"
            "要求：\n"
            "1. entities 只放概念/类型实体（如供应商分级、产品类别），不要为文中提到的具体公司名、产品名、"
            "物料名等命名实例单独建 entity——这些具体实例请放入 instances 数组\n"
            "2. 关系要密集——每个概念实体至少参与1条关系，重点识别概念间的层级（IS-A、PART-OF）关系\n"
            "3. 逻辑规则直接对应文中的 IF-THEN 条件\n\n"
            f"文档内容：\n\n{text}"
        )},
    ]

    for attempt in range(retry_count):
        try:
            raw = _call_llm(provider, api_key, api_base, model_name, messages)
            return _parse_response(raw)
        except Exception as e:
            if attempt == retry_count - 1:
                raise
    return {}


def infer_relations(entities: list, existing_relations: list, text: str,
                    model_config: dict, model_name: str) -> list:
    """Second-pass relation inference: find IS-A / PART-OF / INSTANCE-OF links the first pass missed."""
    if len(entities) < 3:
        return []

    provider  = model_config.get("provider", "openai")
    api_key   = model_config.get("api_key", "")
    api_base  = model_config.get("api_base")

    # Build entity snapshot (limit to 50 to keep prompt manageable)
    entity_lines = "\n".join(
        f"- {e.get('name_cn','?')} ({e.get('type','?')}): {(e.get('description') or '')[:60]}"
        for e in entities[:50]
    )
    existing_set = {
        (r.get("source"), r.get("type"), r.get("target"))
        for r in existing_relations
        if r.get("source") and r.get("target")
    }

    system_prompt = (
        "你是本体关系补全专家。给定已提取实体列表和原始文档，找出实体间遗漏的层级和关联关系。\n\n"
        "关系类型（只能使用以下类型，全部英文大写）：\n"
        "  IS-A、PART-OF、INSTANCE-OF、SUPPLIES、STORES、PROCESSES、TREATS、CAUSES、"
        "TRIGGERS、DEPENDS_ON、PRODUCES、HAS_STATUS、GOVERNED_BY、ASSIGNED_TO\n\n"
        "重点寻找：\n"
        "1. IS-A：A 是 B 的一种（如 销售费用 IS-A 费用）\n"
        "2. PART-OF：A 是 B 的组成部分（如 流动资产 PART-OF 资产）\n"
        "3. INSTANCE-OF：A 是 B 的具体实例（如 华为供应链 INSTANCE-OF S级战略客户）\n"
        "4. TRIGGERS：A 触发或发起 B（如 采购申请 TRIGGERS 采购订单）\n"
        "5. DEPENDS_ON：A 依赖 B（如 出货 DEPENDS_ON 库存）\n\n"
        "要求：\n"
        "- 禁止使用\"关联\"等模糊中文关系类型，必须从上方列表中选择语义明确的英文类型\n"
        "- 只输出新发现的关系，不要重复已有关系\n"
        "- source 和 target 必须是实体列表中的 name_cn\n"
        "- 每对实体最多一条关系\n"
        "- 至少找 15 条，最多 50 条\n\n"
        '返回 JSON（不要有其他文字）：{"relations": [{"source": "A", "target": "B", "type": "IS-A", "confidence": 0.85}]}'
    )
    user_msg = (
        f"已提取实体：\n{entity_lines}\n\n"
        f"文档节选：\n{text[:4000]}"
    )

    try:
        raw = _call_llm(provider, api_key, api_base, model_name,
                        [{"role": "system", "content": system_prompt},
                         {"role": "user", "content": user_msg}])
        parsed = _parse_response(raw)
        candidates = parsed.get("relations", []) if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else [])

        new_rels = []
        for r in candidates:
            if not isinstance(r, dict):
                continue
            key = (r.get("source"), r.get("type"), r.get("target"))
            if key[0] and key[2] and key not in existing_set:
                new_rels.append(r)
                existing_set.add(key)
        return new_rels
    except Exception:
        return []  # relation inference failure is non-fatal


def _call_llm(provider: str, api_key: str, api_base: str | None, model: str, messages: list, json_mode: bool = True) -> str:
    # Stable seed for reproducibility: derived from message content so same input → same seed.
    import hashlib as _hashlib, json as _json
    try:
        _seed_src = _json.dumps([m.get("content", "")[:500] for m in messages], ensure_ascii=False, sort_keys=True)
        _seed = int(_hashlib.md5(_seed_src.encode()).hexdigest()[:8], 16)
    except Exception:
        _seed = 42

    if provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model, max_tokens=8192, temperature=0,
            system=messages[0]["content"],
            messages=[{"role": "user", "content": messages[1]["content"] + ("\n\n```json\n{" if json_mode else "")}],
        )
        return ("{" + resp.content[0].text) if json_mode else resp.content[0].text
    else:
        import openai
        kwargs = {"api_key": api_key}
        if api_base:
            kwargs["base_url"] = api_base
        client = openai.OpenAI(**kwargs)
        create_kwargs: dict = {"model": model, "messages": messages, "timeout": 300, "max_tokens": 65536,
                               "temperature": 0, "seed": _seed}
        if json_mode:
            create_kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = client.chat.completions.create(**create_kwargs)
        except Exception:
            # seed not supported by all providers — retry without it
            create_kwargs.pop("seed", None)
            resp = client.chat.completions.create(**create_kwargs)
        return resp.choices[0].message.content or ""



def _parse_response(raw: str) -> dict:
    if not raw:
        raise ValueError("Empty LLM response")

    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    text = raw.strip()
    text = re.sub(r'^```(?:json)?\s*\n?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\n?```\s*$', '', text).strip()

    # Remove control characters that are illegal inside JSON strings
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)

    # Fast path: well-formed JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try json_repair (handles unescaped quotes, truncated output, etc.)
    try:
        from json_repair import repair_json
        repaired = repair_json(text)
        result = json.loads(repaired)
        if isinstance(result, dict):
            return result
    except Exception:
        pass

    # Last resort: slice from first { to last } and try again
    start, end = text.find('{'), text.rfind('}')
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Cannot parse LLM response as JSON: {raw[:300]}")
