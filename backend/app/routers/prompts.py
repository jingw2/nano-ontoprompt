from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
from app.deps import get_db, get_current_user
from app.models.prompt import Prompt
from app.models.extraction_task import ExtractionTask
from app.models.user import User
from app.models.model_config import ModelConfig
from app.schemas.prompt import PromptCreate, PromptUpdate, PromptOut
import uuid

router = APIRouter()

_ACTION_SCHEMA_EXAMPLE = '''"function_code": "def action_name(context: dict) -> dict:\\n    # context contains relevant entity data and runtime parameters\\n    value = context.get(\\'key\\', 0)\\n    if value < context.get(\\'threshold\\', 100):\\n        return {\\'status\\': \\'triggered\\', \\'message\\': \\'Action executed\\'}\\n    return {\\'status\\': \\'skipped\\', \\'message\\': \\'Condition not met\\'}"'''

BUILTIN_PROMPTS = [
    {"name": "通用本体提取", "domain": "其他", "content": """你是一个本体工程专家。请从以下文档中提取本体信息。

实体类型参考：Organization（组织）、Product（产品）、Material（物料）、Category（类别）、Document（文档/订单）、Process（流程）、Facility（设施）、Concept（概念）

关系类型参考：IS-A、PART-OF、INSTANCE-OF、supply、stores、processes、关联

每个实体必须填写 properties（最多3个关键属性，不得为空）。
每条逻辑规则必须填写 linked_entities（关联实体name_cn列表，不得为空）。
每个动作必须填写 linked_entities、linked_logic_names、以及 function_code（Python实现函数）。

function_code 格式：def action_en_name(context: dict) -> dict，实现实际业务逻辑，不要只写注释。

返回JSON，不要有多余文字：
{
  "entities": [{"name_cn": "实体中文名", "name_en": "EntityEnglish", "type": "Organization|Product|Material|Category|Document|Process|Facility|Concept", "description": "描述", "properties": {"属性名": "值"}, "confidence": 0.9}],
  "relations": [{"source": "实体A的name_cn", "target": "实体B的name_cn", "type": "IS-A|PART-OF|INSTANCE-OF|supply|stores|processes|关联", "confidence": 0.85}],
  "logic_rules": [{"name_cn": "规则名", "name_en": "RuleName", "formula": "IF 条件 THEN 结论", "description": "规则描述", "confidence": 0.9, "linked_entities": ["实体name_cn"]}],
  "actions": [{"name_cn": "动作名", "name_en": "ActionName", "execution_rule": "触发条件及执行逻辑", "description": "动作描述", "confidence": 0.9, "linked_entities": ["实体name_cn"], "linked_logic_names": ["逻辑规则name_cn"], "function_code": "def action_name(context: dict) -> dict:\\n    return {'status': 'ok'}"}]
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

【逻辑规则】提取IF-THEN形式业务规则。
每条规则必须填写 linked_entities，从已提取实体中选1-5个直接相关实体的name_cn，不得为空。

【动作】提取可执行业务动作。每个动作必须填写：
- linked_entities（涉及实体的name_cn列表）
- linked_logic_names（触发该动作的逻辑规则name_cn列表）
- function_code：完整的 Python 函数，函数名为 name_en 的 snake_case，签名 (context: dict) -> dict，实现实际业务逻辑，不要只写注释或 pass。

function_code 示例（供应链）：
def trigger_purchase_request(context: dict) -> dict:
    material = context.get('material_name', '')
    current_stock = context.get('current_stock', 0)
    safety_stock = context.get('safety_stock', 0)
    if current_stock < safety_stock:
        order_id = f"PO-{material[:4].upper()}-AUTO"
        return {'status': 'triggered', 'order_id': order_id,
                'message': f'{material} 当前库存 {current_stock}，低于安全库存 {safety_stock}，采购申请已创建'}
    return {'status': 'skipped', 'message': f'{material} 库存充足，无需触发采购'}

返回JSON，不要有多余文字：
{
  "entities": [
    {"name_cn": "中文名", "name_en": "EnglishName", "type": "Supplier|Product|Material|Warehouse|Document|Category|Process", "description": "描述", "properties": {"属性1": "值", "属性2": "值"}, "confidence": 0.95}
  ],
  "relations": [
    {"source": "实体A的name_cn", "target": "实体B的name_cn", "type": "supply|IS-A|PART-OF|INSTANCE-OF|stores|processes|关联", "confidence": 0.85}
  ],
  "logic_rules": [
    {"name_cn": "规则名", "name_en": "RuleName", "formula": "IF 条件 THEN 结论", "description": "描述", "confidence": 0.9, "linked_entities": ["实体name_cn_1", "实体name_cn_2"]}
  ],
  "actions": [
    {"name_cn": "动作名", "name_en": "ActionName", "execution_rule": "触发条件及执行逻辑", "description": "描述", "confidence": 0.9, "linked_entities": ["实体name_cn_1"], "linked_logic_names": ["逻辑规则name_cn"], "function_code": "def action_name(context: dict) -> dict:\\n    val = context.get('key', 0)\\n    if val < context.get('threshold', 100):\\n        return {'status': 'triggered', 'message': '已触发'}\\n    return {'status': 'skipped', 'message': '条件未满足'}"}
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

【逻辑规则】：诊断规则、用药禁忌、剂量规则（IF-THEN形式）。
每条规则必须填写 linked_entities（1-5个直接相关实体name_cn），不得为空。

【动作】：开具处方、触发检查、发送提醒、更新病历等。每个动作必须填写：
- linked_entities（涉及实体的name_cn列表）
- linked_logic_names（触发该动作的逻辑规则name_cn列表）
- function_code：完整 Python 函数，签名 (context: dict) -> dict，实现实际逻辑，不要只写注释或 pass。

function_code 示例：
def prescribe_medication(context: dict) -> dict:
    patient_id = context.get('patient_id', '')
    drug_name = context.get('drug_name', '')
    dosage = context.get('dosage', '标准剂量')
    allergy_list = context.get('allergy_list', [])
    if drug_name in allergy_list:
        return {'status': 'blocked', 'message': f'患者对 {drug_name} 过敏，禁止开具'}
    return {'status': 'prescribed', 'patient_id': patient_id,
            'drug': drug_name, 'dosage': dosage, 'message': '处方已开具'}

返回JSON，不要有多余文字：
{
  "entities": [{"name_cn": "创伤性应激障碍", "name_abbr": "PTSD", "name_en": "Post-Traumatic Stress Disorder", "type": "Disease", "description": "描述", "properties": {"属性名": "值"}, "confidence": 0.9}],
  "relations": [{"source": "实体A的name_cn", "target": "实体B的name_cn", "type": "treats|causes|IS-A|PART-OF|INSTANCE-OF|关联", "confidence": 0.85}],
  "logic_rules": [{"name_cn": "规则名", "name_en": "RuleName", "formula": "IF 条件 THEN 结论", "description": "描述", "confidence": 0.9, "linked_entities": ["实体name_cn_1", "实体name_cn_2"]}],
  "actions": [{"name_cn": "动作名", "name_en": "ActionName", "execution_rule": "触发条件及执行逻辑", "description": "描述", "confidence": 0.9, "linked_entities": ["实体name_cn_1"], "linked_logic_names": ["逻辑规则name_cn"], "function_code": "def action_name(context: dict) -> dict:\\n    val = context.get('key', '')\\n    return {'status': 'ok', 'value': val}"}]
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

【逻辑规则】：会计准则、预警规则、审批规则（IF-THEN形式）。
每条规则必须填写 linked_entities（1-5个直接相关实体name_cn），不得为空。

【动作】：触发对账、生成报表、发起付款审批、库存补货等。每个动作必须填写：
- linked_entities（涉及实体的name_cn列表）
- linked_logic_names（触发该动作的逻辑规则name_cn列表）
- function_code：完整 Python 函数，签名 (context: dict) -> dict，实现实际逻辑，不要只写注释或 pass。

function_code 示例：
def trigger_payment_approval(context: dict) -> dict:
    amount = context.get('amount', 0)
    vendor = context.get('vendor_name', '')
    approver = 'CFO' if amount > 500000 else '财务总监' if amount > 100000 else '财务主管'
    return {'status': 'pending_approval', 'vendor': vendor, 'amount': amount,
            'approver': approver, 'message': f'付款申请 ¥{amount:,} 已提交至 {approver} 审批'}

返回JSON，不要有多余文字：
{
  "entities": [{"name_cn": "财务概念", "name_en": "ConceptName", "type": "Asset|Liability|Revenue|Expense|Document|Category|Process", "description": "描述", "properties": {"属性名": "值"}, "confidence": 0.9}],
  "relations": [{"source": "概念A", "target": "概念B", "type": "IS-A|PART-OF|INSTANCE-OF|supply|关联", "confidence": 0.85}],
  "logic_rules": [{"name_cn": "规则名", "name_en": "RuleName", "formula": "IF 条件 THEN 结论", "description": "描述", "confidence": 0.9, "linked_entities": ["实体name_cn_1", "实体name_cn_2"]}],
  "actions": [{"name_cn": "动作名", "name_en": "ActionName", "execution_rule": "触发条件及执行逻辑", "description": "描述", "confidence": 0.9, "linked_entities": ["实体name_cn_1"], "linked_logic_names": ["逻辑规则name_cn"], "function_code": "def action_name(context: dict) -> dict:\\n    val = context.get('key', 0)\\n    return {'status': 'triggered', 'value': val}"}]
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
每条逻辑规则必须填写 linked_entities（1-5个直接相关实体name_cn），不得为空。
每个动作必须填写 linked_entities、linked_logic_names 及 function_code（完整Python函数，不要只写注释）。

function_code 示例：
def trigger_customer_win_back(context: dict) -> dict:
    customer_id = context.get('customer_id', '')
    days_inactive = context.get('days_inactive', 0)
    ticket_count = context.get('last_month_tickets', 0)
    if days_inactive >= 14 and ticket_count > 3:
        return {'status': 'triggered', 'customer_id': customer_id,
                'action': 'winback_campaign', 'message': f'客户 {customer_id} 已 {days_inactive} 天未登录且工单异常，已启动挽回流程'}
    return {'status': 'skipped', 'message': '未达触发条件'}

返回JSON，不要有多余文字：
{
  "entities": [{"name_cn": "实体名", "name_en": "EntityName", "type": "Category|Organization|Product|Process|Concept|Document", "description": "描述", "properties": {"属性名": "值"}, "confidence": 0.9}],
  "relations": [{"source": "实体A的name_cn", "target": "实体B的name_cn", "type": "IS-A|PART-OF|INSTANCE-OF|supply|关联", "confidence": 0.85}],
  "logic_rules": [{"name_cn": "规则名", "name_en": "RuleName", "formula": "IF 条件 THEN 结论", "description": "描述", "confidence": 0.9, "linked_entities": ["实体name_cn_1", "实体name_cn_2"]}],
  "actions": [{"name_cn": "动作名", "name_en": "ActionName", "execution_rule": "触发条件及执行逻辑", "description": "描述", "confidence": 0.9, "linked_entities": ["实体name_cn_1"], "linked_logic_names": ["逻辑规则name_cn"], "function_code": "def action_name(context: dict) -> dict:\\n    val = context.get('key', 0)\\n    return {'status': 'triggered', 'value': val}"}]
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
每条逻辑规则必须填写 linked_entities（1-5个直接相关实体name_cn），不得为空。
每个动作必须填写 linked_entities、linked_logic_names 及 function_code（完整Python函数，不要只写注释）。

function_code 示例：
def trigger_retention_review(context: dict) -> dict:
    employee_id = context.get('employee_id', '')
    consecutive_c = context.get('consecutive_c_quarters', 0)
    market_gap_pct = context.get('salary_market_gap_pct', 0)
    wait_months = context.get('promotion_wait_months', 0)
    reasons = []
    if consecutive_c >= 2: reasons.append(f'连续{consecutive_c}季度绩效C')
    if market_gap_pct > 20: reasons.append(f'薪资低于市场P50超{market_gap_pct}%')
    if wait_months > 30: reasons.append(f'晋升等待{wait_months}个月')
    if reasons:
        return {'status': 'triggered', 'employee_id': employee_id,
                'reasons': reasons, 'message': 'Retention Review 已启动: ' + '、'.join(reasons)}
    return {'status': 'skipped', 'message': '未达触发条件'}

返回JSON，不要有多余文字：
{
  "entities": [{"name_cn": "实体名", "name_en": "EntityName", "type": "Organization|Category|Concept|Process|Document", "description": "描述", "properties": {"属性名": "值"}, "confidence": 0.9}],
  "relations": [{"source": "实体A的name_cn", "target": "实体B的name_cn", "type": "IS-A|PART-OF|INSTANCE-OF|关联", "confidence": 0.85}],
  "logic_rules": [{"name_cn": "规则名", "name_en": "RuleName", "formula": "IF 条件 THEN 结论", "description": "描述", "confidence": 0.9, "linked_entities": ["实体name_cn_1", "实体name_cn_2"]}],
  "actions": [{"name_cn": "动作名", "name_en": "ActionName", "execution_rule": "触发条件及执行逻辑", "description": "描述", "confidence": 0.9, "linked_entities": ["实体name_cn_1"], "linked_logic_names": ["逻辑规则name_cn"], "function_code": "def action_name(context: dict) -> dict:\\n    val = context.get('key', 0)\\n    return {'status': 'triggered', 'value': val}"}]
}"""},
    {"name": "法律本体提取", "domain": "法律", "content": """从法律文档中提取法律概念、主体、权利义务关系，返回JSON，不要有多余文字：
{
  "entities": [{"name_cn": "法律概念", "name_en": "LegalConcept", "type": "Subject|Right|Obligation|Document|Concept", "description": "描述", "confidence": 0.9}],
  "relations": [{"source": "主体A", "target": "概念B", "type": "IS-A|PART-OF|关联", "confidence": 0.85}],
  "logic_rules": [{"name_cn": "规则名", "name_en": "RuleName", "formula": "IF 条件 THEN 法律后果", "description": "描述", "confidence": 0.9}],
  "actions": []
}"""},
    {"name": "教育本体提取", "domain": "教育", "content": """从教育文档中提取课程、知识点、能力等概念及关系，返回JSON，不要有多余文字：
{
  "entities": [{"name_cn": "教育概念", "name_en": "EduConcept", "type": "Course|Knowledge|Skill|Category|Process", "description": "描述", "confidence": 0.9}],
  "relations": [{"source": "概念A", "target": "概念B", "type": "IS-A|PART-OF|prerequisite|关联", "confidence": 0.85}],
  "logic_rules": [{"name_cn": "规则名", "name_en": "RuleName", "formula": "IF 条件 THEN 结论", "description": "描述", "confidence": 0.9}],
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
def create_prompt(body: PromptCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
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
def update_prompt(prompt_id: str, body: PromptUpdate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    p = db.query(Prompt).filter(Prompt.id == prompt_id).first()
    if not p:
        raise HTTPException(404, "Not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    db.commit(); db.refresh(p)
    return {"data": PromptOut.model_validate(p).model_dump()}

@router.delete("/{prompt_id}", status_code=204)
def delete_prompt(prompt_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    p = db.query(Prompt).filter(Prompt.id == prompt_id).first()
    if not p:
        raise HTTPException(404, "Not found")
    db.query(ExtractionTask).filter(ExtractionTask.prompt_id == prompt_id).update(
        {ExtractionTask.prompt_id: None}, synchronize_session=False
    )
    db.delete(p)
    db.commit()
    db.delete(p); db.commit()

@router.post("/generate-template")
def generate_prompt_template(
    domain: str = Query(..., description="业务域"),
    style: str = Query("ontology_extraction", description="提示词风格"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Use LLM to generate a prompt template for a given business domain"""
    from app.services.llm_service import _call_llm
    from app.services.encryption_service import decrypt

    model_cfg = db.query(ModelConfig).first()
    if not model_cfg:
        raise HTTPException(400, "No model configured. Please add a model in the Models page first.")

    provider = model_cfg.provider
    api_key = decrypt(model_cfg.api_key_encrypted or "")
    api_base = model_cfg.api_base
    models_list = model_cfg.models or []
    model_name = models_list[0] if models_list else ""
    if not model_name:
        raise HTTPException(400, "Model name not configured.")

    system_msg = (
        "你是一个本体工程专家，擅长为不同业务域设计 LLM 提取提示词。"
        "根据用户指定的业务域，生成一个完整的本体提取 Prompt。"
        "Prompt 需要：1) 列出该域典型实体类型；2) 列出关系类型；3) 要求提取逻辑规则和动作；"
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
