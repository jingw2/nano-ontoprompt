-- OntoPrompt Seed Data
-- Run after schema creation to populate test data

-- =====================
-- USERS (3 users)
-- =====================
INSERT INTO users (id, username, email, password_hash, role, is_active, created_at, updated_at) VALUES
  ('u-admin-001', 'admin', 'admin@ontoprompt.local',
   '$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW', -- password: changeme123
   'admin', TRUE, NOW(), NOW()),
  ('u-editor-001', 'editor', 'editor@ontoprompt.local',
   '$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW',
   'editor', TRUE, NOW(), NOW()),
  ('u-viewer-001', 'viewer', 'viewer@ontoprompt.local',
   '$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW',
   'viewer', TRUE, NOW(), NOW());

-- =====================
-- ONTOLOGY PROJECTS (3 projects)
-- =====================
INSERT INTO ontology_projects (id, name, domain, description, version, status, created_by, created_at, updated_at) VALUES
  ('o-supply-001', '供应链知识图谱', '供应链', '基于供应链管理文档构建的本体', 'v1.0', 'created', 'u-admin-001', NOW(), NOW()),
  ('o-medical-001', '医疗本体', '医疗', '慢性病管理领域本体', 'v0.5', 'draft', 'u-editor-001', NOW(), NOW()),
  ('o-finance-001', '财务概念本体', '财务', '财务报表相关概念体系', 'v0.1', 'creating', 'u-admin-001', NOW(), NOW());

-- =====================
-- ENTITIES (8 entities)
-- =====================
INSERT INTO entities (id, ontology_id, name_cn, name_en, type, description, properties, confidence, version, created_at, updated_at) VALUES
  ('e-sup-001', 'o-supply-001', '供应商', 'Supplier', '组织', '提供原材料或服务的外部企业', '{}', 0.95, 'v1.0', NOW(), NOW()),
  ('e-sup-002', 'o-supply-001', '原材料', 'RawMaterial', '物料', '用于生产的基础物料', '{}', 0.92, 'v1.0', NOW(), NOW()),
  ('e-sup-003', 'o-supply-001', '采购订单', 'PurchaseOrder', '单据', '向供应商发出的采购请求文件', '{}', 0.98, 'v1.0', NOW(), NOW()),
  ('e-sup-004', 'o-supply-001', '仓库', 'Warehouse', '地点', '存储物料的场所', '{}', 0.90, 'v1.0', NOW(), NOW()),
  ('e-sup-005', 'o-supply-001', '安全库存', 'SafetyStock', '概念', '防止缺货的最低库存水平', '{}', 0.88, 'v1.0', NOW(), NOW()),
  ('e-med-001', 'o-medical-001', '高血压', 'Hypertension', '疾病', '收缩压≥140mmHg的慢性病', '{}', 0.97, 'v0.5', NOW(), NOW()),
  ('e-med-002', 'o-medical-001', '降压药', 'Antihypertensive', '药物', '用于降低血压的药物类别', '{}', 0.91, 'v0.5', NOW(), NOW()),
  ('e-fin-001', 'o-finance-001', '资产', 'Asset', '财务概念', '企业拥有的经济资源', '{}', 0.99, 'v0.1', NOW(), NOW());

-- =====================
-- RELATIONS (5 relations)
-- =====================
INSERT INTO relations (id, ontology_id, source_entity, target_entity, type, properties, confidence, created_at) VALUES
  ('r-001', 'o-supply-001', 'e-sup-001', 'e-sup-002', '提供', '{}', 0.95, NOW()),
  ('r-002', 'o-supply-001', 'e-sup-003', 'e-sup-001', '发送给', '{}', 0.90, NOW()),
  ('r-003', 'o-supply-001', 'e-sup-002', 'e-sup-004', '存储于', '{}', 0.88, NOW()),
  ('r-004', 'o-supply-001', 'e-sup-005', 'e-sup-002', '约束', '{}', 0.82, NOW()),
  ('r-005', 'o-medical-001', 'e-med-002', 'e-med-001', '治疗', '{}', 0.93, NOW());

-- =====================
-- LOGIC RULES (3 rules)
-- =====================
INSERT INTO logic_rules (id, ontology_id, name_cn, name_en, description, formula, confidence, version, created_at, updated_at, linked_entities) VALUES
  ('l-001', 'o-supply-001', '安全库存触发规则', 'SafetyStockRule', '库存低于阈值时触发采购', 'stock < safety_stock * 0.2 → purchase()', 0.90, 'v1.0', NOW(), NOW(), '[]'),
  ('l-002', 'o-supply-001', '库存上限规则', 'MaxInventoryRule', '防止过度库存', 'inventory <= max_inventory * 1.5', 0.85, 'v1.0', NOW(), NOW(), '[]'),
  ('l-003', 'o-medical-001', '高血压合并糖尿病用药规则', 'HTN_DM_DrugRule', '高血压合并糖尿病首选ARB或ACEI', 'has(HTN) AND has(DM) → prefer(ARB | ACEI)', 0.88, 'v0.5', NOW(), NOW(), '[]');

-- =====================
-- ACTIONS (3 actions)
-- =====================
INSERT INTO actions (id, ontology_id, name_cn, name_en, description, execution_rule, function_code, linked_entities, linked_logic_ids, confidence, version, created_at, updated_at) VALUES
  ('a-001', 'o-supply-001', '触发采购申请', 'TriggerPurchaseRequest', '自动创建采购申请', 'WHEN stock_low THEN create_PR()', '', '["e-sup-001","e-sup-002"]', '["l-001"]', 0.88, 'v1.0', NOW(), NOW()),
  ('a-002', 'o-supply-001', '供应商绩效评估', 'SupplierEvaluation', '季度评估供应商', 'SCHEDULE quarterly → evaluate_supplier()', '', '["e-sup-001"]', '[]', 0.82, 'v1.0', NOW(), NOW()),
  ('a-003', 'o-medical-001', '高血压用药推荐', 'HTNDrugRecommendation', '基于患者情况推荐用药', 'IF patient.HTN AND patient.DM THEN recommend(ARB)', '', '["e-med-001","e-med-002"]', '["l-003"]', 0.85, 'v0.5', NOW(), NOW());

-- =====================
-- PROMPTS (6 prompts)
-- =====================
INSERT INTO prompts (id, name, domain, content, version, created_by, created_at, updated_at) VALUES
  ('p-001', '通用本体提取', '其他', '你是本体工程专家，提取JSON格式本体：{"entities":[],"relations":[],"logic_rules":[],"actions":[]}', 'v1.0', 'u-admin-001', NOW(), NOW()),
  ('p-002', '供应链本体提取', '供应链', '从供应链文档提取供应商、物料、仓库等实体和关系，返回JSON', 'v1.0', 'u-admin-001', NOW(), NOW()),
  ('p-003', '医疗本体提取', '医疗', '从医疗文档提取疾病、药物、症状等实体及诊疗规则，返回JSON', 'v1.0', 'u-admin-001', NOW(), NOW()),
  ('p-004', '财务本体提取', '财务', '从财务文档提取会计概念、财务规则，返回JSON', 'v1.0', 'u-admin-001', NOW(), NOW()),
  ('p-005', '法律本体提取', '法律', '从法律文档提取法律概念、权利义务关系，返回JSON', 'v1.0', 'u-admin-001', NOW(), NOW()),
  ('p-006', '教育本体提取', '教育', '从教育文档提取课程、知识点、能力要求，返回JSON', 'v1.0', 'u-admin-001', NOW(), NOW()),
  ('p-007', '精神科知识图谱提取', '医疗',
'你是精神科/心理健康领域的医学知识图谱抽取专家。请从文档中提取结构化知识图谱，严格按照以下JSON格式输出，不要输出任何额外文字或注释。

## 输出格式

{
  "entities": [...],
  "relations": [...],
  "logic_rules": [],
  "actions": []
}

## 实体类型（entities）

每个实体结构：
{ "name_cn": "实体中文名", "name_en": "English name（如有）", "type": "实体类型", "description": "一句话描述", "properties": {}, "confidence": 0.90 }

### 实体类型与 properties 字段说明

Disease（疾病）— 文档描述的主体疾病
  properties: { "definition": "疾病定义", "epidemiology_summary": "流行病学摘要", "treatment_principles": "治疗原则", "follow_up": "随访方案", "prognosis": "预后", "prevention": "预防措施", "patient_education": "患者教育要点" }

Symptom（症状）— 疾病的临床表现症状
  properties: { "category": "症状分类（如情感症状/认知症状/躯体症状）", "source_text": "原文摘录" }

RiskFactor（危险因素）— 发病相关危险因素
  properties: { "category": "因素类别（遗传/环境/社会心理/生物等）", "source_text": "原文摘录" }

Pathogenesis（发病机制）— 发病原因和病理机制要点
  properties: { "source_text": "原文摘录" }

Subtype（疾病亚型）— 疾病分型/亚类
  properties: { "criteria": "分型依据", "source_text": "原文摘录" }

Scale（量表）— 诊断或评估量表
  properties: { "full_name": "量表全称", "purpose": "用途", "items_or_dimensions": "条目或维度说明", "cutoff_or_scoring": "评分标准或截断值" }

Examination（检查项目）— 辅助检查
  properties: { "purpose": "检查目的", "key_findings": "关键异常表现" }

DiagnosisCriteria（诊断标准）— 诊断标准条目，name_cn 使用"系统-代码"格式如"DSM-5-F32"
  properties: { "system": "DSM-5或ICD-11等", "code": "诊断编码", "summary": "核心标准摘要", "full_text": "标准全文或核心条款" }

Drug（药物）— 药物治疗
  properties: { "drug_class": "药物类别", "indications": "适应症", "notes": "注意事项", "contraindications_raw": "禁忌证原文" }

NonDrugTreatment（非药物治疗）— 心理治疗、物理治疗等
  properties: { "treatment_type": "治疗类型（如CBT/ECT/rTMS等）", "description": "治疗方法描述", "contraindications_raw": "禁忌证原文" }

FAQ（常见问答）— 医患常见问答，name_cn 使用问题核心词（不超过15字）
  properties: { "question": "完整问题", "answer": "完整答案" }

## 关系类型（relations）

每条关系结构：
{ "source": "source实体的name_cn", "target": "target实体的name_cn", "type": "关系类型", "confidence": 0.90 }

关系类型清单（source类型 → target类型）：
- HAS_SYMPTOM         Disease → Symptom          疾病具有该症状
- HAS_RISK_FACTOR     Disease → RiskFactor        疾病具有该危险因素
- HAS_PATHOGENESIS    Disease → Pathogenesis      疾病具有该发病机制
- HAS_SUBTYPE         Disease → Subtype           疾病具有该亚型
- DIAGNOSED_BY_SCALE  Disease → Scale             疾病用该量表诊断/评估
- NEEDS_EXAM          Disease → Examination       疾病需要该检查
- MEETS_CRITERIA      Disease → DiagnosisCriteria 疾病符合该诊断标准
- TREATED_BY_DRUG     Disease → Drug              疾病使用该药物治疗
- TREATED_BY_NON_DRUG Disease → NonDrugTreatment  疾病使用该非药物治疗
- DIFFERENTIAL_WITH   Disease → Disease           疾病需与该病鉴别诊断
- COMORBID_WITH       Disease → Disease           疾病与该病常见共病
- HAS_FAQ             Disease → FAQ               疾病关联该常见问答

## 抽取规则

1. 所有 name_cn 必须来自原文真实表述，严禁凭空编造或按常识补充
2. 原文中以①②③或1.2.3.编号罗列的条目必须逐条拆分为独立实体，不得合并
3. source_text 摘录能支持该条目的原文句子，用于溯源
4. confidence 赋值：原文明确表述 0.90~0.98，有一定推断 0.70~0.89，不确定 0.50~0.69
5. 关系中 source 和 target 必须与 entities 中某个 name_cn 完全一致
6. logic_rules 和 actions 固定输出空数组',
  'v1.0', 'u-admin-001', NOW(), NOW());

-- =====================
-- MODEL CONFIGS (3 models)
-- =====================
INSERT INTO model_configs (id, name, api_base, api_key_encrypted, provider, models, created_by, created_at, updated_at) VALUES
  ('m-001', 'OpenAI GPT-4o', NULL, 'sk-test-encrypted', 'openai', '["gpt-4o","gpt-4o-mini"]', 'u-admin-001', NOW(), NOW()),
  ('m-002', 'Claude 3.5', NULL, 'sk-ant-test-encrypted', 'anthropic', '["claude-3-5-sonnet-20241022","claude-3-5-haiku-20241022"]', 'u-admin-001', NOW(), NOW()),
  ('m-003', 'Ollama Local', 'http://localhost:11434/v1', '', 'compatible', '["llama3.2","qwen2.5"]', 'u-admin-001', NOW(), NOW());

-- =====================
-- EXTRACTION TASKS (2 tasks)
-- =====================
INSERT INTO extraction_tasks (id, ontology_id, prompt_id, model_id, status, parameters, progress, error, created_at, updated_at) VALUES
  ('t-001', 'o-supply-001', 'p-002', 'm-001', 'completed',
   '{"model_name":"gpt-4o"}', '{"stage":"done","pct":100}', NULL, NOW(), NOW()),
  ('t-002', 'o-medical-001', 'p-003', 'm-001', 'failed',
   '{"model_name":"gpt-4o"}', '{"stage":"calling LLM","pct":40}', 'API rate limit exceeded', NOW(), NOW());

-- =====================
-- RULES CONFIG (8 rules)
-- =====================
INSERT INTO rules_config (id, rule_key, rule_value, rule_label_cn, rule_label_en, editable, created_at, updated_at) VALUES
  ('rc-001', 'confidence_entity_min', '0.5', '实体最低置信度', 'Entity min confidence', TRUE, NOW(), NOW()),
  ('rc-002', 'confidence_logic_min', '0.6', '逻辑规则最低置信度', 'Logic rule min confidence', TRUE, NOW(), NOW()),
  ('rc-003', 'confidence_action_min', '0.6', '动作最低置信度', 'Action min confidence', TRUE, NOW(), NOW()),
  ('rc-004', 'confidence_relation_min', '0.5', '关系最低置信度', 'Relation min confidence', TRUE, NOW(), NOW()),
  ('rc-005', 'confidence_high_threshold', '0.9', '高置信度阈值', 'High confidence threshold', TRUE, NOW(), NOW()),
  ('rc-006', 'confidence_medium_threshold', '0.7', '中置信度阈值', 'Medium confidence threshold', TRUE, NOW(), NOW()),
  ('rc-007', 'confidence_low_threshold', '0.5', '低置信度阈值', 'Low confidence threshold', TRUE, NOW(), NOW()),
  ('rc-008', 'confidence_display_dashed_below', '0.7', '低于此值显示虚线边', 'Show dashed edge below', TRUE, NOW(), NOW());
