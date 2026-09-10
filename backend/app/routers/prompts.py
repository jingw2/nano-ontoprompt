from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
from app.deps import get_db, get_current_user, require_editor
from app.models.prompt import Prompt
from app.models.extraction_task import ExtractionTask
from app.models.user import User
from app.models.model_config import ModelConfig
from app.schemas.prompt import PromptCreate, PromptUpdate, PromptOut
import uuid

router = APIRouter()

# Shared instruction blocks (Palantir Ontology Functions/Action model) so every
# domain template asks the LLM for the same typed structure instead of an
# ad-hoc IF-THEN string or a guessed Python function body.
_LOGIC_INSTRUCTIONS = """【逻辑规则 / Functions】按 Palantir Ontology Functions 模型分类，每条规则的 function_type 必须是以下四类之一：
- derived_property（派生属性）：从现有属性实时计算新属性，definition 写计算表达式，如 "delay_minutes = actual_departure - scheduled_departure"
- aggregation（聚合计算）：跨对象的统计聚合，definition 写聚合表达式，如 "AVG(Flight.delay_minutes) GROUP BY airline"
- complex_edit（批量编辑）：触发时对多个对象的批量修改逻辑，definition 描述具体编辑步骤
- external_query（外部查询）：查询外部系统丰富本体，definition 描述查询的系统与内容
禁止输出旧式 IF-THEN 自然语言"公式"文本。每条规则必须填写 linked_entities（1-5个直接相关实体name_cn），不得为空。"""

_ACTION_INSTRUCTIONS = """【动作 / Actions】按 Palantir Ontology Action 模型提取以下四个组件：
- parameters（参数）：类型化输入字段列表，如 [{"name": "字段名", "type": "object_reference|string|number|boolean|enum|date", "description": "说明"}]
- rules（规则）：定义对本体的编辑操作，如 [{"operation": "Modify|Create Object|Delete Object|Create Link|Delete Link", "target": "实体.字段", "value": "取值或来源"}]
- submission_criteria（提交条件）：前置校验表达式列表，不满足则无法提交，如 ["new_flight.status != Cancelled", "current_user.role in [agent, supervisor]"]
- side_effects（副作用）：执行后触发的外部效果，如 [{"type": "Notification|Webhook", "target": "...", "detail": "..."}]
禁止输出旧式 execution_rule 自然语言描述或 function_code Python 代码。每个动作必须填写 linked_entities、linked_logic_names。"""

BUILTIN_PROMPTS = [
    {"name": "通用本体提取", "domain": "其他", "content": """你是一个本体工程专家。请从以下文档中提取本体信息。

实体类型参考：Organization（组织）、Product（产品）、Material（物料）、Category（类别）、Document（文档/订单）、Process（流程）、Facility（设施）、Concept（概念）

关系类型参考：IS-A、PART-OF、INSTANCE-OF、supply、stores、processes、关联

提取纪律（务必遵守）：
- 只提取与文档主题直接相关的核心概念，跳过偶然提及与噪音（口号、版权行、纯数字段落、文件名）
- 每个实体必须填写一句基于本文档的 description（用于消歧）；同一概念出现多种写法（含中英文并列写法）时只保留一个规范实体，禁止为同义写法建重复实体
- 关系类型必须语义明确（优先 IS-A、PART-OF、INSTANCE-OF、supply、stores、processes），禁止使用"关联"等模糊类型
- 每条关系的 source/target 必须取自已提取实体的 name_cn，禁止引用未提取的实体
- 重点识别概念间的层级关系（IS-A、PART-OF）

每个实体必须填写 properties（最多3个关键属性，不得为空）。

""" + _LOGIC_INSTRUCTIONS + "\n\n" + _ACTION_INSTRUCTIONS + """

返回JSON，不要有多余文字：
{
  "entities": [{"name_cn": "实体中文名", "name_en": "EntityEnglish", "type": "Organization|Product|Material|Category|Document|Process|Facility|Concept", "description": "描述", "properties": {"属性名": "值"}, "confidence": 0.9}],
  "relations": [{"source": "实体A的name_cn", "target": "实体B的name_cn", "type": "IS-A|PART-OF|INSTANCE-OF|supply|stores|processes|关联", "confidence": 0.85}],
  "logic_rules": [{"name_cn": "规则名", "name_en": "RuleName", "function_type": "derived_property", "definition": "派生属性 = 表达式", "description": "规则描述", "confidence": 0.9, "linked_entities": ["实体name_cn"]}],
  "actions": [{"name_cn": "动作名", "name_en": "ActionName", "parameters": [{"name": "target", "type": "object_reference", "description": "操作目标"}], "rules": [{"operation": "Modify", "target": "实体.字段", "value": "新值"}], "submission_criteria": ["前置条件表达式"], "side_effects": [{"type": "Notification", "target": "相关方", "detail": "通知内容"}], "description": "动作描述", "confidence": 0.9, "linked_entities": ["实体name_cn"], "linked_logic_names": ["逻辑规则name_cn"]}]
}"""},
    {"name": "供应链本体提取", "domain": "供应链", "content": """你是供应链领域本体工程专家。从文档中提取完整的供应链本体。

【实体类型】请识别以下所有类型的实体：
- 供应商(Supplier)：企业、供货商、合作伙伴
- 产品(Product)：成品、半成品、具体商品
- 物料(Material)：原材料、辅料、零部件
- 仓库(Warehouse)：仓储中心、配送中心、物流节点
- 采购订单(Document)：采购单、合同、订单等单据
- 类别(Category)：产品分类、供应商分类
- 工艺流程(Process)：生产工艺、采购流程、质检流程

每个实体必须填写 properties 字段（最多3个最重要的属性，如评级、产能、交货周期），不得省略。

【关系类型】尽可能多地提取：supply、IS-A、PART-OF、INSTANCE-OF、stores、processes、关联

""" + _LOGIC_INSTRUCTIONS + "\n\n" + _ACTION_INSTRUCTIONS + """

供应链领域示例：
- Logic: {"name_cn": "安全库存预警", "name_en": "SafetyStockAlert", "function_type": "derived_property", "definition": "stock_gap = safety_stock - current_stock", "description": "库存低于安全线时的差值", "confidence": 0.9, "linked_entities": ["物料", "仓库"]}
- Action: {"name_cn": "自动创建采购申请", "name_en": "TriggerPurchaseRequest", "parameters": [{"name": "material", "type": "object_reference", "description": "触发补货的物料"}], "rules": [{"operation": "Create Object", "target": "PurchaseOrder", "value": "material=material, qty=safety_stock-current_stock"}], "submission_criteria": ["material.current_stock < material.safety_stock"], "side_effects": [{"type": "Notification", "target": "采购负责人", "detail": "已为{material}生成补货申请"}], "description": "库存低于安全库存时自动创建采购申请", "confidence": 0.9, "linked_entities": ["物料"], "linked_logic_names": ["安全库存预警"]}

返回JSON，不要有多余文字：
{
  "entities": [
    {"name_cn": "中文名", "name_en": "EnglishName", "type": "Supplier|Product|Material|Warehouse|Document|Category|Process", "description": "描述", "properties": {"属性1": "值", "属性2": "值"}, "confidence": 0.95}
  ],
  "relations": [
    {"source": "实体A的name_cn", "target": "实体B的name_cn", "type": "supply|IS-A|PART-OF|INSTANCE-OF|stores|processes|关联", "confidence": 0.85}
  ],
  "logic_rules": [
    {"name_cn": "规则名", "name_en": "RuleName", "function_type": "derived_property|aggregation|complex_edit|external_query", "definition": "表达式或描述", "description": "描述", "confidence": 0.9, "linked_entities": ["实体name_cn_1", "实体name_cn_2"]}
  ],
  "actions": [
    {"name_cn": "动作名", "name_en": "ActionName", "parameters": [{"name": "字段名", "type": "object_reference", "description": "说明"}], "rules": [{"operation": "Modify", "target": "实体.字段", "value": "取值"}], "submission_criteria": ["前置条件"], "side_effects": [{"type": "Notification", "target": "相关方", "detail": "通知内容"}], "description": "描述", "confidence": 0.9, "linked_entities": ["实体name_cn_1"], "linked_logic_names": ["逻辑规则name_cn"]}
  ]
}"""},
    {"name": "医疗本体提取", "domain": "医疗", "content": """你是医疗领域本体工程专家。从文档中提取完整的医疗健康本体。

【文件名与主疾病实体】（重要）
每段文档以「【来源文件】{文件名}」开头。文件名通常形如：
  1、创伤性应激障碍 (PTSD).docx
  1、广泛性焦虑障碍 (GAD).docx
即：{序号}、{中文病名} ({英文缩写}).扩展名

对每个文档段，必须提取 1 个 type=Disease 的主疾病实体，中文名与英文缩写**以该段来源文件名为准**：
1. 去掉扩展名（.docx 等）及前导序号（如「1、」）
2. name_cn：仅保留纯中文病名，不得含英文、括号或缩写（✗ 创伤性应激障碍（PTSD） → ✓ 创伤性应激障碍）
3. name_abbr：括号内纯英文字母缩写（如 PTSD、GAD、OCD），与 name_cn、name_en 并列；若无英文缩写则省略
4. 若括号内为中文别名而非英文缩写（如「场所恐惧症（广场恐惧症）」），name_abbr 留空，可将别名写入 properties.alias
5. name_en：填正文中的完整英文疾病名；正文未给出时可据医学常识补全。**禁止**用缩写填 name_en
6. 文件名无法解析出单一病名时（如「治疗焦虑障碍常用药物表.docx」、.json），不强制创建主 Disease，按正文提取即可
7. 主疾病 name_cn 必须与文件名解析结果一致，勿用正文中的近义称呼替代（如文件名是「创伤性」则不得改成「创伤后」）

【实体类型】识别以下所有类型：
- Disease（疾病）：诊断名称、病症、综合征
- Drug（药物）：药品、化合物、制剂
- Symptom（症状）：体征、症候、检查指标异常
- Treatment（治疗方案）：疗法、手术、康复方案
- Facility（医疗机构）：医院、科室、诊所
- Category（分类）：疾病分类、药物分类
- Process（医疗流程）：诊疗流程、用药流程、手术流程
- RiskFactor(风险因素): 年龄、遗传、肥胖、吸烟、高盐饮食
- Pathogenesis(发病机制): 疾病的发病机制、病理生理过程
- Subtype(亚型): 疾病的亚型、变异型、并发症
- Scale(量表): 疾病量表、评分量表、诊断量表
- Examination(检查): 检查项目、检查方法、检查指标
- NonDrugTreatment(非药物治疗): 非药物治疗、物理治疗、心理治疗
- FAQ(常见问题): 疾病常见问题、疾病常见疑问
- DiagnosisCriteria(诊断标准): 诊断标准
- FollowupQuestion(随访问题): 随访问题
- DiversionRule(转诊规则): 转诊规则
- ClinicalFocus(临床重点): 临床重点

每个实体必须填写 properties（最多3个关键属性，如发病率、副作用、适应症），不得为空。

【关系类型】：treats（治疗）、causes（引起）、IS-A（属于）、PART-OF（包含）、INSTANCE-OF、关联

""" + _LOGIC_INSTRUCTIONS + "\n\n" + _ACTION_INSTRUCTIONS + """

医疗领域示例：
- Logic: {"name_cn": "药物过敏禁忌判定", "name_en": "AllergyContraindication", "function_type": "complex_edit", "definition": "若 drug_name 命中患者 allergy_list，将处方状态置为 blocked 并记录禁忌原因", "description": "开具处方前的过敏禁忌校验", "confidence": 0.9, "linked_entities": ["药物", "患者"]}
- Action: {"name_cn": "开具处方", "name_en": "PrescribeMedication", "parameters": [{"name": "patient", "type": "object_reference", "description": "患者"}, {"name": "drug", "type": "object_reference", "description": "拟开具药物"}], "rules": [{"operation": "Create Object", "target": "Prescription", "value": "patient=patient, drug=drug"}], "submission_criteria": ["drug not in patient.allergy_list"], "side_effects": [{"type": "Notification", "target": "患者", "detail": "处方已开具"}], "description": "为患者开具药物处方", "confidence": 0.9, "linked_entities": ["药物", "患者"], "linked_logic_names": ["药物过敏禁忌判定"]}

返回JSON，不要有多余文字：
{
  "entities": [{"name_cn": "创伤性应激障碍", "name_abbr": "PTSD", "name_en": "Post-Traumatic Stress Disorder", "type": "Disease", "description": "描述", "properties": {"属性名": "值"}, "confidence": 0.9}],
  "relations": [{"source": "实体A的name_cn", "target": "实体B的name_cn", "type": "treats|causes|IS-A|PART-OF|INSTANCE-OF|关联", "confidence": 0.85}],
  "logic_rules": [{"name_cn": "规则名", "name_en": "RuleName", "function_type": "derived_property|aggregation|complex_edit|external_query", "definition": "表达式或描述", "description": "描述", "confidence": 0.9, "linked_entities": ["实体name_cn_1", "实体name_cn_2"]}],
  "actions": [{"name_cn": "动作名", "name_en": "ActionName", "parameters": [{"name": "字段名", "type": "object_reference", "description": "说明"}], "rules": [{"operation": "Modify", "target": "实体.字段", "value": "取值"}], "submission_criteria": ["前置条件"], "side_effects": [{"type": "Notification", "target": "相关方", "detail": "通知内容"}], "description": "描述", "confidence": 0.9, "linked_entities": ["实体name_cn_1"], "linked_logic_names": ["逻辑规则name_cn"]}]
}"""},
    {"name": "财务本体提取", "domain": "财务", "content": """你是财务领域本体工程专家。从文档中提取完整的财务会计本体。

【实体类型】识别以下所有类型：
- Asset（资产）：固定资产、流动资产、无形资产
- Liability（负债）：短期负债、长期负债、应付账款
- Revenue（收入）：主营收入、其他收入、利息收入
- Expense（费用）：成本、运营费用、管理费用
- Document（凭证/报表）：资产负债表、利润表、现金流量表、采购单、发票
- Category（科目分类）：会计科目、成本中心、利润中心
- Process（财务流程）：对账流程、结账流程、报销流程、采购流程

每个实体必须填写 properties（最多3个关键属性，如金额、占比、周转率），不得为空。

【关系提取要求】**必须提取至少15条关系**，重点覆盖：
1. PART-OF 资产结构：流动资产/非流动资产/无形资产 PART-OF 资产；应收账款/存货/货币资金 PART-OF 流动资产；固定资产/长期投资 PART-OF 非流动资产
2. PART-OF 负债结构：流动负债/非流动负债 PART-OF 负债；应付账款/短期借款 PART-OF 流动负债
3. IS-A 费用类型：营业成本/销售费用/管理费用/研发费用/财务费用/所得税 IS-A 费用
4. 关联 利润链：营业收入→毛利润→营业利润→利润总额→净利润（用"关联"类型）
5. PART-OF 报表：各科目 PART-OF 其所属报表（资产负债表/利润表/现金流量表）
6. supply 采购流程：采购订单→入库单→仓库存货之间的流转关系

""" + _LOGIC_INSTRUCTIONS + "\n\n" + _ACTION_INSTRUCTIONS + """

财务领域示例：
- Logic: {"name_cn": "大额付款分级审批人", "name_en": "PaymentApproverTier", "function_type": "derived_property", "definition": "approver = CFO if amount > 500000 else 财务总监 if amount > 100000 else 财务主管", "description": "按付款金额推导审批人", "confidence": 0.9, "linked_entities": ["付款申请"]}
- Action: {"name_cn": "发起付款审批", "name_en": "TriggerPaymentApproval", "parameters": [{"name": "payment", "type": "object_reference", "description": "付款申请"}], "rules": [{"operation": "Modify", "target": "Payment.status", "value": "pending_approval"}], "submission_criteria": ["payment.amount > 0"], "side_effects": [{"type": "Notification", "target": "approver", "detail": "付款申请待审批"}], "description": "提交付款申请进入审批流程", "confidence": 0.9, "linked_entities": ["付款申请"], "linked_logic_names": ["大额付款分级审批人"]}

返回JSON，不要有多余文字：
{
  "entities": [{"name_cn": "财务概念", "name_en": "ConceptName", "type": "Asset|Liability|Revenue|Expense|Document|Category|Process", "description": "描述", "properties": {"属性名": "值"}, "confidence": 0.9}],
  "relations": [{"source": "概念A", "target": "概念B", "type": "IS-A|PART-OF|INSTANCE-OF|supply|关联", "confidence": 0.85}],
  "logic_rules": [{"name_cn": "规则名", "name_en": "RuleName", "function_type": "derived_property|aggregation|complex_edit|external_query", "definition": "表达式或描述", "description": "描述", "confidence": 0.9, "linked_entities": ["实体name_cn_1", "实体name_cn_2"]}],
  "actions": [{"name_cn": "动作名", "name_en": "ActionName", "parameters": [{"name": "字段名", "type": "object_reference", "description": "说明"}], "rules": [{"operation": "Modify", "target": "实体.字段", "value": "取值"}], "submission_criteria": ["前置条件"], "side_effects": [{"type": "Notification", "target": "相关方", "detail": "通知内容"}], "description": "描述", "confidence": 0.9, "linked_entities": ["实体name_cn_1"], "linked_logic_names": ["逻辑规则name_cn"]}]
}"""},
    {"name": "营销本体提取", "domain": "其他", "content": """你是营销领域本体工程专家。从文档中提取完整的营销与客户运营本体。

【实体类型】识别以下所有类型：
- Category（客户分层/分类）：S级战略客户、A级重点客户、B级成长客户、C级长尾客户等客户分层概念
- Organization（组织/合作方）：代理商、系统集成商、咨询公司、竞争对手企业
- Product（产品/服务）：产品版本（基础版/专业版/企业版）、增值模块、定价包
- Process（流程/渠道）：营销渠道（SEM/内容营销/社交媒体/邮件营销/展会）、销售漏斗阶段
- Concept（营销概念）：健康度评分、续约率、CAC、NPS、ARR、GMV、Win Rate等指标
- Document（报告/规则）：营销策略文档、客户成功手册、SLA协议

【关系提取要求】**必须提取至少20条关系**，重点覆盖：
1. IS-A 层级：各客户等级 IS-A 客户分层基类（例：A级重点客户 IS-A 客户）
2. IS-A 层级：各营销渠道 IS-A 营销渠道（例：搜索引擎营销 IS-A 营销渠道）
3. INSTANCE-OF：具体企业 INSTANCE-OF 客户分层（例：华为供应链 INSTANCE-OF S级战略客户）
4. IS-A 层级：各产品版本 IS-A 产品线（例：基础版 IS-A 产品）
5. IS-A 层级：竞争对手企业 IS-A 竞争对手（例：用友U8C IS-A 竞争对手）
6. PART-OF：销售漏斗各阶段 PART-OF 销售漏斗
7. 关联：渠道与获客、产品与客户群、健康度与续约之间的关联

每个实体必须填写 properties（最多3个关键属性，如占比、合同额、转化率），不得为空。

""" + _LOGIC_INSTRUCTIONS + "\n\n" + _ACTION_INSTRUCTIONS + """

营销领域示例：
- Logic: {"name_cn": "客户流失风险评分", "name_en": "ChurnRiskScore", "function_type": "aggregation", "definition": "AVG(days_inactive, ticket_count) GROUP BY customer_id", "description": "综合不活跃天数与工单量计算流失风险", "confidence": 0.9, "linked_entities": ["客户"]}
- Action: {"name_cn": "触发客户挽回流程", "name_en": "TriggerCustomerWinBack", "parameters": [{"name": "customer", "type": "object_reference", "description": "目标客户"}], "rules": [{"operation": "Create Object", "target": "WinbackCampaign", "value": "customer=customer"}], "submission_criteria": ["customer.days_inactive >= 14", "customer.last_month_tickets > 3"], "side_effects": [{"type": "Notification", "target": "客户成功经理", "detail": "已启动挽回流程"}], "description": "客户长期不活跃且工单异常时启动挽回流程", "confidence": 0.9, "linked_entities": ["客户"], "linked_logic_names": ["客户流失风险评分"]}

返回JSON，不要有多余文字：
{
  "entities": [{"name_cn": "实体名", "name_en": "EntityName", "type": "Category|Organization|Product|Process|Concept|Document", "description": "描述", "properties": {"属性名": "值"}, "confidence": 0.9}],
  "relations": [{"source": "实体A的name_cn", "target": "实体B的name_cn", "type": "IS-A|PART-OF|INSTANCE-OF|supply|关联", "confidence": 0.85}],
  "logic_rules": [{"name_cn": "规则名", "name_en": "RuleName", "function_type": "derived_property|aggregation|complex_edit|external_query", "definition": "表达式或描述", "description": "描述", "confidence": 0.9, "linked_entities": ["实体name_cn_1", "实体name_cn_2"]}],
  "actions": [{"name_cn": "动作名", "name_en": "ActionName", "parameters": [{"name": "字段名", "type": "object_reference", "description": "说明"}], "rules": [{"operation": "Modify", "target": "实体.字段", "value": "取值"}], "submission_criteria": ["前置条件"], "side_effects": [{"type": "Notification", "target": "相关方", "detail": "通知内容"}], "description": "描述", "confidence": 0.9, "linked_entities": ["实体name_cn_1"], "linked_logic_names": ["逻辑规则name_cn"]}]
}"""},
    {"name": "HR本体提取", "domain": "其他", "content": """你是人力资源领域本体工程专家。从文档中提取完整的HR与人才管理本体。

【实体类型】识别以下所有类型：
- Organization（组织单元）：集团总部、业务部门（产品研发部/销售与市场/客户成功部/供应链运营/财务与法务/人力资源）
- Category（职级/分类）：技术职级（P4/P5/P6/P7/P8）、岗位序列、招聘渠道类型
- Concept（岗位/角色）：AI算法工程师、客户成功经理、销售总监、管理培训生等具体岗位
- Process（HR流程）：入职培训、绩效评估、晋升流程、离职流程、招聘流程
- Document（制度/规则）：劳动合同、竞业限制协议、个人发展计划(IDP)、薪酬制度

【关系提取要求】**必须提取至少20条关系**，重点覆盖：
1. PART-OF 组织架构：各业务部门 PART-OF 集团总部
2. IS-A 职级体系：P4/P5/P6/P7/P8 各自 IS-A 技术职级
3. 晋升关系（关联）：P4→P5→P6→P7→P8 晋升链路（用"关联"类型）
4. IS-A 招聘渠道：内部推荐/BOSS直聘/猎头/校园招聘/领英 IS-A 招聘渠道
5. IS-A 岗位：AI算法工程师/客户成功经理/销售总监 IS-A 岗位
6. PART-OF 绩效维度：OKR完成度/技术质量/团队协作 PART-OF 研发绩效评估; 配额完成率/漏斗健康度/客户满意度 PART-OF 销售绩效评估
7. 关联：岗位与所属部门之间的关联

每个实体必须填写 properties（最多3个关键属性，如员工数、薪资范围、转化率），不得为空。

""" + _LOGIC_INSTRUCTIONS + "\n\n" + _ACTION_INSTRUCTIONS + """

HR领域示例：
- Logic: {"name_cn": "保留面谈触发因素", "name_en": "RetentionReviewFactors", "function_type": "complex_edit", "definition": "连续两季度绩效C，或薪资低于市场P50超20%，或晋升等待超30个月时，汇总触发原因", "description": "识别需要发起保留面谈的员工", "confidence": 0.9, "linked_entities": ["员工"]}
- Action: {"name_cn": "启动保留面谈", "name_en": "TriggerRetentionReview", "parameters": [{"name": "employee", "type": "object_reference", "description": "目标员工"}], "rules": [{"operation": "Create Object", "target": "RetentionReview", "value": "employee=employee"}], "submission_criteria": ["employee.consecutive_c_quarters >= 2 || employee.salary_market_gap_pct > 20 || employee.promotion_wait_months > 30"], "side_effects": [{"type": "Notification", "target": "HRBP", "detail": "已为{employee}启动保留面谈"}], "description": "命中保留风险因素时启动面谈流程", "confidence": 0.9, "linked_entities": ["员工"], "linked_logic_names": ["保留面谈触发因素"]}

返回JSON，不要有多余文字：
{
  "entities": [{"name_cn": "实体名", "name_en": "EntityName", "type": "Organization|Category|Concept|Process|Document", "description": "描述", "properties": {"属性名": "值"}, "confidence": 0.9}],
  "relations": [{"source": "实体A的name_cn", "target": "实体B的name_cn", "type": "IS-A|PART-OF|INSTANCE-OF|关联", "confidence": 0.85}],
  "logic_rules": [{"name_cn": "规则名", "name_en": "RuleName", "function_type": "derived_property|aggregation|complex_edit|external_query", "definition": "表达式或描述", "description": "描述", "confidence": 0.9, "linked_entities": ["实体name_cn_1", "实体name_cn_2"]}],
  "actions": [{"name_cn": "动作名", "name_en": "ActionName", "parameters": [{"name": "字段名", "type": "object_reference", "description": "说明"}], "rules": [{"operation": "Modify", "target": "实体.字段", "value": "取值"}], "submission_criteria": ["前置条件"], "side_effects": [{"type": "Notification", "target": "相关方", "detail": "通知内容"}], "description": "描述", "confidence": 0.9, "linked_entities": ["实体name_cn_1"], "linked_logic_names": ["逻辑规则name_cn"]}]
}"""},
    {"name": "法律本体提取", "domain": "法律", "content": """从法律文档中提取法律概念、主体、权利义务关系，返回JSON，不要有多余文字：
{
  "entities": [{"name_cn": "法律概念", "name_en": "LegalConcept", "type": "Subject|Right|Obligation|Document|Concept", "description": "描述", "confidence": 0.9}],
  "relations": [{"source": "主体A", "target": "概念B", "type": "IS-A|PART-OF|关联", "confidence": 0.85}],
  "logic_rules": [{"name_cn": "规则名", "name_en": "RuleName", "function_type": "derived_property|aggregation|complex_edit|external_query", "definition": "法律条件与后果的表达式或描述", "description": "描述", "confidence": 0.9}],
  "actions": []
}"""},
    {"name": "教育本体提取", "domain": "教育", "content": """从教育文档中提取课程、知识点、能力等概念及关系，返回JSON，不要有多余文字：
{
  "entities": [{"name_cn": "教育概念", "name_en": "EduConcept", "type": "Course|Knowledge|Skill|Category|Process", "description": "描述", "confidence": 0.9}],
  "relations": [{"source": "概念A", "target": "概念B", "type": "IS-A|PART-OF|prerequisite|关联", "confidence": 0.85}],
  "logic_rules": [{"name_cn": "规则名", "name_en": "RuleName", "function_type": "derived_property|aggregation|complex_edit|external_query", "definition": "表达式或描述", "description": "描述", "confidence": 0.9}],
  "actions": []
}"""},
]

@router.get("/templates")
def get_builtin_templates(_=Depends(get_current_user)):
    """Return hardcoded builtin prompt templates (not from DB)."""
    return {"data": BUILTIN_PROMPTS}

@router.get("")
def list_prompts(domain: Optional[str] = None, db: Session = Depends(get_db), _=Depends(get_current_user)):
    q = db.query(Prompt)
    if domain:
        q = q.filter(Prompt.domain == domain)
    prompts = q.order_by(Prompt.created_at.desc()).all()
    return {"data": [PromptOut.model_validate(p).model_dump() for p in prompts]}

@router.post("", status_code=201)
def create_prompt(body: PromptCreate, db: Session = Depends(get_db), current_user: User = Depends(require_editor)):
    prompt = Prompt(id=str(uuid.uuid4()), name=body.name, domain=body.domain,
                    content=body.content, version=body.version, created_by=current_user.id)
    db.add(prompt); db.commit(); db.refresh(prompt)
    return {"data": PromptOut.model_validate(prompt).model_dump()}

@router.get("/by-domain/{domain}")
def get_prompts_by_domain(domain: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    prompts = db.query(Prompt).filter(Prompt.domain == domain).all()
    return {"data": [PromptOut.model_validate(p).model_dump() for p in prompts]}

@router.get("/{prompt_id}")
def get_prompt(prompt_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    p = db.query(Prompt).filter(Prompt.id == prompt_id).first()
    if not p:
        raise HTTPException(404, "Not found")
    return {"data": PromptOut.model_validate(p).model_dump()}

@router.put("/{prompt_id}")
def update_prompt(prompt_id: str, body: PromptUpdate, db: Session = Depends(get_db), _=Depends(require_editor)):
    p = db.query(Prompt).filter(Prompt.id == prompt_id).first()
    if not p:
        raise HTTPException(404, "Not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    db.commit(); db.refresh(p)
    return {"data": PromptOut.model_validate(p).model_dump()}

@router.delete("/{prompt_id}", status_code=204)
def delete_prompt(prompt_id: str, db: Session = Depends(get_db), _=Depends(require_editor)):
    p = db.query(Prompt).filter(Prompt.id == prompt_id).first()
    if not p:
        raise HTTPException(404, "Not found")
    db.query(ExtractionTask).filter(ExtractionTask.prompt_id == prompt_id).update(
        {ExtractionTask.prompt_id: None}, synchronize_session=False
    )
    db.delete(p)
    db.commit()

@router.post("/generate-template")
def generate_prompt_template(
    domain: str = Query(..., description="业务域"),
    style: str = Query("ontology_extraction", description="提示词风格"),
    db: Session = Depends(get_db),
    current_user=Depends(require_editor),
):
    """Use LLM to generate a prompt template for a given business domain"""
    from app.services.llm_service import _call_llm
    from app.services.model_callers.extraction import resolve_llm_caller, ModelVersionUnavailableError

    model_cfg = db.query(ModelConfig).first()
    if not model_cfg:
        raise HTTPException(400, "No model configured. Please add a model in the Models page first.")

    try:
        kwargs = resolve_llm_caller(db, model_cfg.id)
    except ModelVersionUnavailableError:
        raise HTTPException(409, "MODEL_VERSION_UNAVAILABLE: configure an active immutable model version first.")

    provider = kwargs["provider"]
    api_key = kwargs["api_key"]
    api_base = kwargs["api_base"]
    model_name = kwargs["model"]
    if not model_name:
        raise HTTPException(400, "Model name not configured.")

    system_msg = (
        "你是一个本体工程专家，擅长为不同业务域设计 LLM 提取提示词。"
        "根据用户指定的业务域，生成一个完整的本体提取 Prompt。"
        "Prompt 需要：1) 列出该域典型实体类型；2) 列出关系类型；3) 要求按 Palantir Ontology "
        "Functions 模型（derived_property/aggregation/complex_edit/external_query）提取逻辑规则，"
        "按 Palantir Action 模型（parameters/rules/submission_criteria/side_effects）提取动作；"
        "4) 规定返回 JSON 格式（entities/relations/logic_rules/actions）。"
        "只返回 Prompt 文本本身，不要有其他说明。"
    )
    user_msg = f"请为【{domain}】业务域生成本体提取提示词，风格：{style}。"

    try:
        content = _call_llm(provider, api_key, api_base, model_name, [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ], json_mode=False)
        if not isinstance(content, str):
            content = str(content)
    except Exception as e:
        raise HTTPException(500, f"LLM generation failed: {str(e)}")

    return {"domain": domain, "content": content.strip()}
