# Ontology 优化方案：基于 Palantir Design Principles

> 对照 Palantir Foundry Ontology 设计原则对 nano-ontoprompt 的优化分析。
> 参考: [Palantir Best Practices](https://www.palantir.com/docs/foundry/ontology/ontology-best-practices/) · [Structural Guidance](https://www.palantir.com/docs/foundry/ontology/ontology-structural-guidance/) · [Anti-Patterns](https://www.palantir.com/docs/foundry/ontology/ontology-anti-patterns/)

---

## 已确认的反模式（按严重度排序）

| # | 反模式 | 严重度 | 现状 | 对应 Palantir 原则 |
|---|--------|--------|------|-------------------|
| 1 | **Kitchen Sink** | HIGH | Pipeline Mapping 将源列 1:1 映射为属性，列名即属性名，无 curated 过滤，无 struct 分组 | Domain-Driven Design |
| 2 | **无 Object Type 定义** | HIGH | 实体类型只是 `type` 字符串，无独立表定义 property schema、category、描述 | Domain-Driven Design |
| 3 | **双重身份混淆** | MEDIUM | v1 LLM 创建概念级实体，v2 Pipeline 创建实例级实体，存同一张表无区分 | Separate Identity from Observation |
| 4 | **扁平无层次** | MEDIUM | 无接口/继承/组合机制，前端实体以扁平表格展示 | Composition Over Hierarchies |
| 5 | **FK 命名缺乏语义** | LOW | `HAS_SUPPLIER_DATABASE` vs `SUPPLIES`，v1/v2 命名不统一 | Naming Conventions |
| 6 | **无跨本体复用** | LOW | 每个本体完全隔离，无法共享类型定义，同域重复建设 | DRY / Rule of Three |
| 7 | **Golden Hammer** | LOW | Schema contract 规则含 40+ 属性，无降噪机制 | Choose the Right Tool |

---

## Phase 1：基础 — Object Type 定义 + Kitchen Sink 修复

### 1A. 引入 `object_types` 表（独立类型定义）

**新建 `backend/app/models/object_type.py`**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID PK | |
| `ontology_id` | FK → ontology_projects | |
| `name` | String(200) | PascalCase，如 `Supplier`、`PurchaseOrder` |
| `name_cn` | String(200) | 中文名 |
| `description` | Text | |
| `category` | Enum: `domain\|artifact\|conceptual` | 领域实体 vs 数据容器 vs 纯概念 |
| `parent_type_id` | FK → self (nullable) | 为未来继承预留 |
| `property_schema` | JSON | `[{name, type, required, semantic_group, description}]` |
| `is_abstract` | Boolean | 为未来接口预留 |
| `source` | String: `pipeline_mapping\|simple_llm\|manual` | 来源路径 |

**新建 `backend/app/routers/v2/object_types.py`** — CRUD API + Alembic migration

**修改 `backend/app/models/entity.py`** — 新增两列（向后兼容，nullable）:
- `object_type_id` — FK → object_types.id
- `entity_category` — `conceptual` (v1 LLM) | `instance` (v2 Pipeline)

**修改 `backend/app/tasks/extraction.py`** — v1 提取时设置 `entity_category='conceptual'`

**修改 `backend/app/services/v2/mapping/mapping_service.py`** — v2 build 时设置 `entity_category='instance'`

### 1B. 引入 `link_types` 表

**新建 `backend/app/models/link_type.py`**:

| 字段 | 说明 |
|------|------|
| `id`, `ontology_id`, `name`, `description` | |
| `category` | Enum: `semantic\|fk_inferred\|structural` |
| `source_object_type_id`, `target_object_type_id` | nullable 约束 |

**新建 `backend/app/routers/v2/link_types.py`** — CRUD API + migration

**修改 `backend/app/models/relation.py`** — 新增 `link_type_id` (nullable FK)

### 1C. Kitchen Sink 修复

**修改 `backend/app/services/v2/mapping/mapping_service.py`**:
- `build_all()` — 为每个 entity_class 自动创建/更新 ObjectType
- `_normalize_mapping()` — 新增 `semantic_group`（LLM 建议属性分组）
- `_property_metadata()` — 自动检测 `is_technical`（纯 ID 列标记为 hidden）
- 新增 `_infer_entity_category()` — 按名称推断 domain vs artifact

**修改 `backend/app/services/v2/mapping/auto_mapper.py`**:
- `_llm_suggest()` — prompt 要求为相关属性建议 semantic_group（如 `address`、`coordinates`）
- `_rule_based_suggest()` — 同前缀列检测（`address_*` → `address` group）

**实体类别推断规则**:
- `domain`: Supplier, Customer, Product, Disease, Drug, Symptom, Employee, Patient, Order, Contract, Warehouse, Invoice...
- `artifact`: Import, Export, Report, Database, Log, File, Dataset, Table, Record, Summary, Review...

### 1D. 前端 Object Types Tab

**新建 `frontend/src/pages/ontologies/detail/tabs/ObjectTypesTab.tsx`**:
- 类型卡片网格：名称（中英文）、category badge（蓝=domain / 灰=artifact / 紫=conceptual）、实体数、属性数
- 筛选：All | Domain | Artifact | Conceptual
- 点击进入类型详情页

**新建 `frontend/src/pages/ontologies/detail/object-type/ObjectTypeDetailPage.tsx`**:
- 属性按 semantic_group 分组展示
- 每组可编辑（重命名、改类型、增删属性）
- 关联 link_types 列表
- 链接到"查看此类型所有实体"

**修改 `frontend/src/pages/ontologies/detail/OntologyDetailPage.tsx`** — 新增 Object Types tab

### 1E. 前端 Entities Tab / Graph 增强

**修改 `frontend/src/pages/ontologies/detail/tabs/EntitiesTab.tsx`**:
- 新增 Category 列（概念/实例 badge）
- `type` 列链接到 ObjectTypeDetailPage
- 新增筛选：entity_category、object_type

**修改 `frontend/src/pages/ontologies/detail/entity/EntityDetailPage.tsx`**:
- 显示 Object Type 链接 + Category badge
- properties 按 semantic_group 分组渲染

**修改 `frontend/src/pages/ontologies/detail/tabs/GraphTabV2.tsx`**:
- 新增 legend toggle "Show Category" — 节点边框区分 domain/artifact/conceptual

---

## Phase 2：语义关系 + 跨路径对齐 + 降噪

### 2A. 关系命名规范化

**新建 `backend/app/services/v2/mapping/relation_naming.py`**:

```python
FK_NAMING_MAP = {
    "supplier": "SUPPLIED_BY",
    "customer": "PURCHASED_BY",
    "warehouse": "STORED_IN",
    "order": "PART_OF_ORDER",
    "employee": "ASSIGNED_TO",
    "department": "BELONGS_TO",
    "project": "PART_OF_PROJECT",
    "patient": "BELONGS_TO_PATIENT",
    "drug": "PRESCRIBED",
    # fallback: HAS_{ENTITY_CLASS}
}
```

**修改 `mapping_service.py`** — `_detect_fk_columns()` 使用规范化器

### 2B. INSTANCE-OF 链接（v1 概念 ↔ v2 实例）

**修改 `mapping_service.py`** — `build_all()` 新增步骤:
- 同域查找 v1 LLM 本体的概念实体
- 按名称匹配 → 创建 `INSTANCE-OF` 关系
- 需先建立 cross-ontology reference

### 2C. 跨本体引用

**修改 `backend/app/models/ontology.py`** — 新增 `reference_ontology_ids` (JSON)
**修改 InfoTab** — 新增引用本体多选器

### 2D. 规则降噪

**修改 `mapping_service.py`** — noise_score 计算 + 合并重复规则
**修改 LogicTab** — 新增 "Hide boilerplate" 开关

---

## Phase 3：远期

- 3A: `interfaces` → `[Temporal] [Locatable] [Stateful]` 标记
- 3B: GraphTabV2 Tree 布局（优先展示 IS-A/PART-OF/INSTANCE-OF 层级）
- 3C: 跨本体共享类型库（`ontology_kind='shared_library'`）

---

## 核心修改文件汇总

| 文件 | Phase | 类型 |
|------|-------|------|
| `backend/app/models/object_type.py` | P1 | **新建** |
| `backend/app/models/link_type.py` | P1 | **新建** |
| `backend/app/models/entity.py` | P1 | 加列 |
| `backend/app/models/relation.py` | P1 | 加列 |
| `backend/app/models/ontology.py` | P2C | 加列 |
| `backend/app/services/v2/mapping/mapping_service.py` | P1+P2 | 核心扩展 |
| `backend/app/services/v2/mapping/auto_mapper.py` | P1+P2 | prompt 增强 |
| `backend/app/services/v2/mapping/relation_naming.py` | P2 | **新建** |
| `backend/app/tasks/extraction.py` | P1 | 标记 |
| `backend/app/routers/v2/object_types.py` | P1 | **新建** |
| `backend/app/routers/v2/link_types.py` | P1 | **新建** |
| `frontend/.../ObjectTypesTab.tsx` | P1 | **新建** |
| `frontend/.../ObjectTypeDetailPage.tsx` | P1 | **新建** |
| `frontend/.../OntologyDetailPage.tsx` | P1 | tab |
| `frontend/.../EntitiesTab.tsx` | P1 | 增强 |
| `frontend/.../EntityDetailPage.tsx` | P1 | 增强 |
| `frontend/.../GraphTabV2.tsx` | P1 | 增强 |
| `frontend/.../LogicTab.tsx` | P2 | 降噪 |
| `frontend/.../InfoTab.tsx` | P2C | 引用选择 |
