"""Update supply chain prompt to include linked_entities and linked_logic_names."""
import requests

BASE = "http://localhost:8002/api/v1"
r = requests.post(f"{BASE}/auth/login", json={"username": "admin", "password": "admin123"})
token = r.json()["data"]["access_token"]
H = {"Authorization": f"Bearer {token}"}

NEW_CONTENT = (
    "你是供应链领域本体工程专家。从文档中提取完整的供应链本体。\n\n"
    "【实体类型】请识别以下所有类型的实体，每个概念都应提取：\n"
    "- 供应商(Supplier)：企业、供货商、合作伙伴\n"
    "- 产品(Product)：成品、半成品、具体商品\n"
    "- 物料(Material)：原材料、辅料、零部件\n"
    "- 仓库(Warehouse)：仓储中心、配送中心、物流节点\n"
    "- 采购订单(Document)：采购单、合同、订单等单据\n"
    "- 类别(Category)：产品分类（如电子类别、化学类别）、供应商分类\n"
    "- 工艺流程(Process)：生产工艺、采购流程、质检流程、物流流程\n"
    "- 客户(Customer)：购买方、系统集成商、制造企业\n\n"
    "【关系类型】尽可能多地提取（source和target必须是已提取实体的name_cn）：\n"
    "- supply/供应；IS-A/属于；PART-OF/包含；INSTANCE-OF；stores/存储；processes/处理；关联\n\n"
    "【逻辑规则】提取IF-THEN形式的业务规则。linked_entities填写该规则涉及的实体name_cn列表。\n\n"
    "【动作】提取可执行的业务动作。linked_entities填涉及的实体name_cn列表；linked_logic_names填触发此动作的规则name_cn列表。\n\n"
    "返回JSON，不要有多余文字：\n"
    "{\n"
    "  \"entities\": [{\"name_cn\": \"中文名\", \"name_en\": \"EnglishName\", \"type\": \"Supplier|Product|Material|Warehouse|Document|Category|Process|Customer\", \"description\": \"描述\", \"confidence\": 0.95}],\n"
    "  \"relations\": [{\"source\": \"实体A的name_cn\", \"target\": \"实体B的name_cn\", \"type\": \"supply|IS-A|PART-OF|INSTANCE-OF|stores|processes|关联\", \"confidence\": 0.85}],\n"
    "  \"logic_rules\": [{\"name_cn\": \"规则名\", \"name_en\": \"RuleName\", \"formula\": \"IF 条件 THEN 结论\", \"description\": \"描述\", \"linked_entities\": [\"实体name_cn\"], \"confidence\": 0.9}],\n"
    "  \"actions\": [{\"name_cn\": \"动作名\", \"name_en\": \"ActionName\", \"execution_rule\": \"触发条件及执行逻辑\", \"description\": \"描述\", \"linked_entities\": [\"实体name_cn\"], \"linked_logic_names\": [\"规则name_cn\"], \"confidence\": 0.9}]\n"
    "}"
)

prompts = requests.get(f"{BASE}/prompts", headers=H).json()["data"]
p = next(x for x in prompts if x["name"] == "供应链本体提取")
r2 = requests.put(f"{BASE}/prompts/{p['id']}", headers=H, json={"content": NEW_CONTENT})
print(f"Updated: {r2.ok}, {len(NEW_CONTENT)} chars")
