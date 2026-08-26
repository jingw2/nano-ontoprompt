"""
信贷测试数据增强生成器
生成：客户档案信息, 资金方合作数据, 催收结果数据
更新：贷款申请记录（新增4列）
"""
import csv, random
from pathlib import Path

BASE = Path(__file__).parent
SEED = 42
random.seed(SEED)

# ── 配置 ───────────────────────────────────────────────────────────

PRODUCT_TYPES = ["消费贷(循环额度)", "消费贷(大额专项)", "经营贷", "抵押贷", "教育分期", "购车分期"]
CHANNELS = ["线上广告", "线下推广", "合作导流", "自然流量", "存量复贷"]
PURPOSES = ["日常消费", "装修", "教育", "医疗", "经营周转", "购车"]

# 营销渠道权重
CHANNEL_WEIGHTS = {"线上广告": 35, "线下推广": 15, "合作导流": 20, "自然流量": 15, "存量复贷": 15}
CHANNEL_POOL = [c for c, w in CHANNEL_WEIGHTS.items() for _ in range(w)]

APPROVAL_LU = {
    "系统自动审批": (0.5, 5),
    "系统初审+人工复核": (60, 120),
    "风控专员审核": (200, 400),
    "风控总监审批": (400, 600),
}

# 客户画像模板
CITIES = ["北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京", "西安", "重庆",
          "苏州", "长沙", "郑州", "东莞", "青岛", "沈阳", "宁波", "昆明", "厦门", "福州"]
OCCUPATIONS = [
    ("企业职员", ["互联网/IT", "金融", "制造业", "房地产", "零售贸易", "物流运输"]),
    ("个体户", ["餐饮", "零售", "服务"]),
    ("公务员/事业单位", ["政府", "教育", "医疗", "科研"]),
    ("企业主", ["制造业", "贸易", "科技"]),
    ("自由职业者", ["设计", "媒体", "咨询"]),
    ("蓝领/工人", ["建筑", "制造", "物流"]),
]

# 按信用分层生成对应的职业/行业/收入分布
TIER_PROFILES = {
    "A": {"income_range": (15000, 80000), "work_years": (5, 20), "edu_weights": {"硕士及以上": 30, "本科": 50, "大专": 15, "高中及以下": 5},
          "occ_weights": {"企业职员": 40, "公务员/事业单位": 30, "企业主": 15, "自由职业者": 10, "个体户": 5}},
    "B": {"income_range": (8000, 25000), "work_years": (3, 15), "edu_weights": {"硕士及以上": 15, "本科": 40, "大专": 30, "高中及以下": 15},
          "occ_weights": {"企业职员": 45, "个体户": 20, "公务员/事业单位": 15, "自由职业者": 12, "企业主": 5, "蓝领/工人": 3}},
    "C": {"income_range": (3000, 12000), "work_years": (1, 10), "edu_weights": {"本科": 20, "大专": 35, "高中及以下": 40, "硕士及以上": 5},
          "occ_weights": {"个体户": 30, "蓝领/工人": 25, "企业职员": 25, "自由职业者": 15, "公务员/事业单位": 5}},
    "D": {"income_range": (1500, 6000), "work_years": (0, 5), "edu_weights": {"高中及以下": 60, "大专": 25, "本科": 15},
          "occ_weights": {"蓝领/工人": 35, "自由职业者": 30, "个体户": 20, "企业职员": 10, "公务员/事业单位": 5}},
}

# 逾期催收结果
COLLECTION_METHODS = ["短信提醒", "AI外呼", "电话催收", "委外催收", "法务催收"]

# ── 工具函数 ───────────────────────────────────────────────────────

def weighted_choice(pool):
    """从 (item, weight) 列表中选择"""
    total = sum(w for _, w in pool)
    r = random.random() * total
    cum = 0
    for item, w in pool:
        cum += w
        if r < cum:
            return item
    return pool[-1][0]

def pick_occupation(tier):
    prof = TIER_PROFILES[tier]
    occ_pool = [(o, prof["occ_weights"].get(o, 5)) for o in prof["occ_weights"]]
    occupation = weighted_choice(occ_pool)
    # pick industry
    for name, industries in OCCUPATIONS:
        if name == occupation:
            industry = random.choice(industries)
            break
    else:
        industry = "其他"
    return occupation, industry

def pick_education(tier):
    prof = TIER_PROFILES[tier]
    pool = [(e, prof["edu_weights"].get(e, 10)) for e in prof["edu_weights"]]
    return weighted_choice(pool)

def generate_customer(uid, tier, credit_score):
    """生成单个客户档案"""
    prof = TIER_PROFILES[tier]
    income_min, income_max = prof["income_range"]
    income = random.randint(income_min, income_max)
    work_years = random.randint(*prof["work_years"])
    age = random.randint(22, 55)
    gender = random.choice(["男", "女"])
    marital = random.choices(["已婚", "未婚", "离异"], weights=[60, 30, 10])[0]
    edu = pick_education(tier)
    occupation, industry = pick_occupation(tier)
    city = random.choice(CITIES)

    has_house = random.choices(["是", "否"], weights=[40, 60] if tier in ("A", "B") else [20, 80])[0]
    has_car = random.choices(["是", "否"], weights=[50, 50] if tier in ("A", "B") else [15, 85])[0]
    social_security = random.randint(0, min(work_years * 12, 180))
    housing_fund = random.randint(0, min(social_security, 120))
    multi_orgs = random.randint(0, 3) if tier == "A" else random.randint(1, 6) if tier == "B" else random.randint(3, 10)
    hist_overdue = 0 if tier == "A" else random.randint(0, 1) if tier == "B" else random.randint(1, 5)

    return {
        "客户ID": uid,
        "姓名": f"客户{uid[4:]}",
        "年龄": age, "性别": gender, "婚姻状况": marital, "学历": edu,
        "职业": occupation, "行业": industry, "城市": city,
        "月收入(元)": income, "工作年限(年)": work_years,
        "房产情况": has_house, "车辆情况": has_car,
        "社保缴纳月数": social_security, "公积金缴纳月数": housing_fund,
        "多头借贷机构数": multi_orgs,
        "历史逾期次数": hist_overdue,
        "信用评分": credit_score,
        "信用分层": tier,
    }

# ── 主程序 ─────────────────────────────────────────────────────────

# 1. 读取现有贷款申请记录
loans_path = BASE / "贷款申请记录.csv"
with open(loans_path, encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    original_loans = list(reader)

print(f"读取原始贷款记录: {len(original_loans)} 条")

# 2. 获取唯一的借款人ID列表（用于生成客户档案）
unique_users = []
seen = set()
for row in original_loans:
    uid = row["借款人ID"]
    if uid not in seen:
        seen.add(uid)
        unique_users.append((uid, row["信用分层"], int(row["信用评分"])))

print(f"去重借款人: {len(unique_users)} 个")

# 3. 生成客户档案（取前60个借款人）
profile_users = unique_users[:min(60, len(unique_users))]
profiles = []
for uid, tier, score in profile_users:
    profiles.append(generate_customer(uid, tier, score))

profile_path = BASE / "客户档案信息.csv"
pf_headers = ["客户ID","姓名","年龄","性别","婚姻状况","学历","职业","行业","城市",
              "月收入(元)","工作年限(年)","房产情况","车辆情况","社保缴纳月数",
              "公积金缴纳月数","多头借贷机构数","历史逾期次数","信用评分","信用分层"]
with open(profile_path, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=pf_headers)
    w.writeheader()
    w.writerows(profiles)
print(f"生成客户档案: {len(profiles)} 条 → {profile_path.name}")

# 4. 为贷款记录新增4列并写出
# 确定客户分层到职业的映射（用于判断是否为个体户/企业主→经营贷）
tier_occ_map = {}
for p in profiles:
    tier_occ_map[p["客户ID"]] = (p["信用分层"], p["职业"], p["月收入(元)"])

def assign_product(amount, tier, purpose=None, occupation=None):
    """基于金额、分层和用途分配产品类型"""
    amt = amount
    if amt == 0:
        return ""
    # 教育目的 → 教育分期
    if purpose == "教育":
        return "教育分期"
    if purpose == "购车":
        return "购车分期"
    # 个体户/企业主 → 偏向经营贷
    if occupation in ("个体户", "企业主") and amt >= 50000:
        return "经营贷"
    if amt > 200000:
        return "抵押贷"
    if amt > 150000:
        return random.choices(["经营贷", "抵押贷"], weights=[60, 40])[0]
    if amt > 50000:
        if tier in ("A", "B"):
            return "消费贷(大额专项)"
        else:
            return "消费贷(循环额度)"
    # <= 50000
    return "消费贷(循环额度)"

def assign_channel(tier, purpose):
    if purpose in ("教育", "购车"):
        return "合作导流"
    if tier == "A":
        return random.choices(["合作导流", "自然流量", "存量复贷"], weights=[30, 30, 40])[0]
    return random.choice(CHANNEL_POOL)

def assign_purpose(amount, tier, product, occupation=None):
    if product == "教育分期":
        return "教育"
    if product == "购车分期":
        return "购车"
    if product == "经营贷":
        return "经营周转"
    if amount > 100000:
        return random.choices(["经营周转", "装修", "购车"], weights=[40, 30, 30])[0]
    if amount > 30000:
        return random.choices(["装修", "日常消费", "医疗", "教育"], weights=[30, 25, 25, 20])[0]
    return random.choice(["日常消费", "医疗"])

def assign_approval_time(approval_method):
    if approval_method in APPROVAL_LU:
        lo, hi = APPROVAL_LU[approval_method]
        return round(random.uniform(lo, hi), 1)
    return 999

def assign_repayment_status(loan_id, user):
    """基于用户信用分层和历史逾期生成还款状态"""
    # 此函数不在贷款记录中使用，留作参考
    pass

enhanced = []
for row in original_loans:
    amount = int(row["申请金额"])
    tier = row["信用分层"]
    uid = row["借款人ID"]
    occ_info = tier_occ_map.get(uid, (tier, "", 0))
    occupation = occ_info[1]

    purpose = assign_purpose(amount, tier, "", occupation)
    product = assign_product(amount, tier, purpose, occupation)
    channel = assign_channel(tier, purpose)
    approval_time = assign_approval_time(row["审批方式"])

    row["产品类型"] = product
    row["营销渠道"] = channel
    row["贷款用途"] = purpose
    row["审批耗时(分钟)"] = approval_time
    enhanced.append(row)

headers_out = ["申请编号","借款人ID","申请日期","申请金额","信用分层","信用评分",
               "授信额度","审批结果","审批方式","资金方","产品类型","营销渠道",
               "贷款用途","审批耗时(分钟)"]
with open(loans_path, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=headers_out)
    w.writeheader()
    w.writerows(enhanced)
print(f"更新贷款申请记录: {len(enhanced)} 条 (新增4列: 产品类型/营销渠道/贷款用途/审批耗时)")

# 5. 生成资金方合作数据
fund_partners = [
    {
        "资金方名称": "众邦银行",
        "合作模式": "重资本", "总授信额度(亿)": 50, "已用敞口(亿)": 38.5,
        "使用率": "77.0%", "风险准备金覆盖率": "4.2%",
        "审批通过率": "68.5%", "平均放款时效(小时)": 2.3, "状态": "正常"
    },
    {
        "资金方名称": "南京银行",
        "合作模式": "重资本", "总授信额度(亿)": 80, "已用敞口(亿)": 72.0,
        "使用率": "90.0%", "风险准备金覆盖率": "3.8%",
        "审批通过率": "72.1%", "平均放款时效(小时)": 1.8, "状态": "预警(敞口即将满额)"
    },
    {
        "资金方名称": "苏宁消费金融",
        "合作模式": "轻资本", "总授信额度(亿)": 30, "已用敞口(亿)": 18.2,
        "使用率": "60.7%", "风险准备金覆盖率": "—",
        "审批通过率": "58.3%", "平均放款时效(小时)": 3.5, "状态": "正常"
    },
    {
        "资金方名称": "锦程消费金融",
        "合作模式": "轻资本", "总授信额度(亿)": 25, "已用敞口(亿)": 22.8,
        "使用率": "91.2%", "风险准备金覆盖率": "—",
        "审批通过率": "62.4%", "平均放款时效(小时)": 2.9, "状态": "预警(敞口即将满额)"
    },
    {
        "资金方名称": "浙商银行",
        "合作模式": "混合型", "总授信额度(亿)": 60, "已用敞口(亿)": 41.0,
        "使用率": "68.3%", "风险准备金覆盖率": "4.5%",
        "审批通过率": "74.2%", "平均放款时效(小时)": 1.5, "状态": "正常"
    },
    {
        "资金方名称": "马上消费金融",
        "合作模式": "轻资本", "总授信额度(亿)": 20, "已用敞口(亿)": 9.6,
        "使用率": "48.0%", "风险准备金覆盖率": "—",
        "审批通过率": "55.6%", "平均放款时效(小时)": 4.1, "状态": "正常"
    },
    {
        "资金方名称": "江南农商银行",
        "合作模式": "重资本", "总授信额度(亿)": 40, "已用敞口(亿)": 33.2,
        "使用率": "83.0%", "风险准备金覆盖率": "3.5%",
        "审批通过率": "65.8%", "平均放款时效(小时)": 2.7, "状态": "正常"
    },
    {
        "资金方名称": "邮储银行(合作洽谈中)",
        "合作模式": "轻资本", "总授信额度(亿)": 40, "已用敞口(亿)": 0,
        "使用率": "0%", "风险准备金覆盖率": "—",
        "审批通过率": "—", "平均放款时效(小时)": 0, "状态": "系统对接中"
    },
]

fund_path = BASE / "资金方合作与敞口数据.csv"
fund_headers = list(fund_partners[0].keys())
with open(fund_path, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=fund_headers)
    w.writeheader()
    w.writerows(fund_partners)
print(f"生成资金方数据: {len(fund_partners)} 条 → {fund_path.name}")

# 6. 生成催收结果数据
# 从还款流水中找到逾期记录
repay_path = BASE / "还款流水.csv"
with open(repay_path, encoding="utf-8-sig") as f:
    repay_reader = csv.DictReader(f)
    repay_records = list(repay_reader)
print(f"读取还款流水: {len(repay_records)} 条")

# 找到逾期记录
overdue_records = [r for r in repay_records if int(r.get("逾期天数", 0)) > 0]
print(f"逾期记录: {len(overdue_records)} 条")

# 构建借款人的职业/分层映射
user_tier_map = {row["借款人ID"]: row["信用分层"] for row in original_loans}

collection_results = []
collection_method_by_days = {
    (1, 30): ["短信提醒", "AI外呼"],
    (31, 60): ["电话催收"],
    (61, 90): ["委外催收"],
    (91, 9999): ["委外催收", "法务催收"],
}

for i, rec in enumerate(overdue_records[:40]):  # 取前40条逾期
    days = int(rec.get("逾期天数", 0))
    loan_id = rec.get("贷款编号", "")
    uid = rec.get("借款人ID", "")

    # 根据逾期天数确定催收方式
    for (lo, hi), methods in collection_method_by_days.items():
        if lo <= days <= hi:
            method = random.choice(methods)
            break
    else:
        method = "委外催收"

    tier = user_tier_map.get(uid, "C")
    was_recovered = random.random() < (0.3 if days > 90 else 0.6 if days > 60 else 0.75)
    is_lost = random.random() < 0.15 if days > 60 else random.random() < 0.05

    if was_recovered:
        recovery_ratio = random.uniform(0.3, 1.0)
        result = "全额结清" if recovery_ratio >= 0.99 else "部分还款"
    elif is_lost:
        result = "核销"
        recovery_ratio = 0
    else:
        result = "在催"
        recovery_ratio = 0

    amount = int(rec.get("应还金额", 0))
    recovered_amount = round(amount * recovery_ratio, 2)

    stage = "M3+" if days > 90 else "M2" if days > 60 else "M1" if days > 30 else "M0"

    collection_results.append({
        "催收编号": f"CL-2026-{i+1:05d}",
        "贷款编号": loan_id,
        "借款人ID": uid,
        "逾期天数": days,
        "逾期阶段": stage,
        "催收方式": method,
        "催收人员": "系统自动" if method in ("短信提醒", "AI外呼") else f"催收员{random.randint(1,30):03d}",
        "是否失联": "是" if is_lost else "否",
        "催收结果": result,
        "回收金额(元)": recovered_amount,
        "应还金额(元)": amount,
        "回收率": f"{recovery_ratio*100:.1f}%",
    })

collection_path = BASE / "催收结果数据.csv"
cl_headers = ["催收编号","贷款编号","借款人ID","逾期天数","逾期阶段","催收方式",
              "催收人员","是否失联","催收结果","回收金额(元)","应还金额(元)","回收率"]
with open(collection_path, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=cl_headers)
    w.writeheader()
    w.writerows(collection_results)
print(f"生成催收结果: {len(collection_results)} 条 → {collection_path.name}")

# 7. 概要统计
print(f"\n{'='*50}")
print(f"增强完成！文件清单：")
print(f"  ✓ 新增: 反欺诈与全流程规则引擎.md")
print(f"  ✓ 新增: 信贷产品目录.md")
print(f"  ✓ 新增: 客户档案信息.csv ({len(profiles)}条)")
print(f"  ✓ 新增: 资金方合作与敞口数据.csv ({len(fund_partners)}条)")
print(f"  ✓ 新增: 催收结果数据.csv ({len(collection_results)}条)")
print(f"  ✓ 更新: 贷款申请记录.csv ({len(enhanced)}条, 新增4列)")
print(f"  ↑ 建议同步更新: 信贷业务战略.md (补充获客/产品/资金方策略)")
