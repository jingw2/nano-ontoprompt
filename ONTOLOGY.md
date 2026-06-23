# 构建 Palantir Foundry Ontology 风格系统指南

本文是为 AI Agent / 企业应用构建 Foundry Ontology 风格系统的架构指南。  
目标读者：准备从零搭建该架构，或将现有业务系统迁移到此模式的工程师。

---

## 目录

1. [为什么要用这个架构](#1-为什么要用这个架构)
2. [五个核心概念](#2-五个核心概念)
3. [开始之前：三个关键决策](#3-开始之前三个关键决策)
4. [数据层设计](#4-数据层设计)
5. [Object 系统](#5-object-系统)
6. [Function / Action 系统](#6-function--action-系统)
7. [Governance 治理层](#7-governance-治理层)
8. [Graph / Links 系统](#8-graph--links-系统)
9. [Event Bus](#9-event-bus)
10. [Ontology Manager（运营侧）](#10-ontology-manager运营侧)
11. [实施顺序建议](#11-实施顺序建议)
12. [测试策略](#12-测试策略)
13. [常见陷阱](#13-常见陷阱)
14. [与传统架构的对比](#14-与传统架构的对比)
15. [扩展场景：基于对话的知识提取与推理](#15-扩展场景基于对话的知识提取与推理)
    - [场景描述](#场景描述)
    - [Foundry 对齐：Reference vs Operational 分层](#foundry-对齐reference-vs-operational-分层)
    - [双图协作：如何使用 patient_graph 与 clinical_kb](#双图协作如何使用-patient_graph-与-clinical_kb)
    - [Operational 层：Object 设计（patient_graph）](#object-设计)
    - [Operational 层：Link 设计](#link-设计)
    - [Observation Function 设计](#observation-function-设计)
    - [对话 → 图的提取机制](#对话--图的提取机制)
    - [症状规范化：canonical_id 对齐 SNOMED-CT](#症状规范化对齐-clinical_kb-的-canonical_id)
    - [推理层：图 → 筛查结论](#推理层图--筛查结论)
    - [对话引导：三层漏斗](#对话引导如何有针对性地问问题)
    - [三层漏斗的三个盲区](#三层漏斗的三个盲区)
    - [临床知识图：Objects / Links 设计](#临床知识图objects--links-设计)
    - [文档提取产物：logic_rules / actions 如何落位](#文档提取产物logic_rules--actions-如何落位)
    - [存储选型：MongoDB vs 图数据库](#存储选型mongodb-还是图数据库)
    - [两图分离与反哺架构](#两图分离与反哺架构)
    - [生产级上线 Checklist](#生产级上线-checklist)
    - [nano-ontoprompt 与筛查场景的分工](#nano-ontoprompt-与筛查场景的分工)
16. [S1 扩展：多模态采集与分流路径建模](#16-s1-扩展多模态采集与分流路径建模)
    - [ScreeningSession 状态机](#screeningsession-状态机)
    - [新增 Object 设计](#新增-object-设计s1-operational-层)
    - [Link 设计（S1 专用）](#link-设计s1-专用)
    - [Function 设计（S1 专用）](#function-设计s1-专用)
    - [降级规则](#降级规则)
    - [Event Schema](#event-schemascreeningsubmitted--screeningcrisis)
    - [与 patient_graph 的关系](#与-patient_graph-的关系)
17. [S4 扩展：病历草稿工作流建模](#17-s4-扩展病历草稿工作流建模)
    - [两个状态机](#两个状态机encounterstession--medicalrecorddraft)
    - [新增 Object 设计（S4）](#新增-object-设计s4-operational-层)
    - [Link 设计（S4 专用）](#link-设计s4-专用)
    - [Function 设计（S4 专用）](#function-设计s4-专用)
    - [HIS 写回路径](#encounterdraftarchive-的-his-写回路径)
    - [Event Schema（S4）](#event-schemaemrtnote)
    - [Diff 视图的数据依据](#diff-视图的数据依据)
    - [生产上线硬约束](#生产上线硬约束s4)
18. [Actor 体系与租户隔离](#18-actor-体系与租户隔离)
    - [7 类角色定义](#7-类角色定义)
    - [扩展权限矩阵](#扩展权限矩阵)
    - [authorizeFunction() 实现模式](#authorizefunction-实现模式)
    - [tenant_id 贯穿全链路](#tenant_id-贯穿全链路)
19. [S2 扩展：干预方案建模](#19-s2-扩展干预方案建模)
    - [两个状态机](#两个状态机careplan--caretask)
    - [新增 Object 设计（S2）](#新增-object-设计s2-operational-层)
    - [Link 设计（S2 专用）](#link-设计s2-专用)
    - [Function 设计（S2 专用）](#function-设计s2-专用)
    - [Event Schema（S2）](#event-schemas2)
    - [S1 → S2 完整数据流](#s1--s2-完整数据流)

---

## 1. 为什么要用这个架构

**适合的场景**：

- AI Agent 需要操作多种业务实体（创建订单、发消息、安排会议……）
- 操作有治理需求：部分操作需要用户二次确认，金额类操作要明确授权
- 需要审计：谁在什么时间通过 AI 做了什么操作
- 副作用复杂：一个操作触发多个下游（推通知、写日志、更新图谱……）

**不适合的场景**：

- 纯 CRUD 后台，没有 AI 介入
- 操作数量少（< 5 个），用 if-else 就够
- 没有治理需求，所有操作 AI 可以直接执行

**核心价值**：把「AI 能做什么」建模成一等公民，统一入口、统一校验、统一审计，副作用解耦。

---

## 2. 五个核心概念

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   Object ──Link──► Object        Function  ─────────────►  │
│     │                              │  ▲                     │
│     │                           validate  confirm           │
│     ▼                              │  │                     │
│  Object Store               Governance Layer                │
│  (属性投影)                         │                       │
│                                    ▼                        │
│                               Event Bus ──► Side Effects    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Object
系统里的业务实体。每个 Object 有：
- **类型**（ObjectType）：`Person`、`Order`、`Meeting`、`Document`……
- **ID**：在同类型内唯一
- **属性**（Properties）：投影快照，存在 Object Store，不是权威数据

ObjectRef = `{type, id}` 是跨系统的通用引用。

### Link
Object 之间的类型化关系，存图数据库。  
例：Person `--CREATED-->` Order，Person `--KNOWS-->` Person。  
Link 有方向、类型、权重（强度），随交互递增、定期衰减。

### Function（= Action Type）
绑定到某个 ObjectType 的有名字的操作，是 AI 可以调用的最小单元。  
每个 Function 有：名字、绑定类型、参数定义、治理级别（ConfirmLevel）。  
**定义**由 Ontology Manager 管理（可不发版修改），**实现**由开发者写代码注册。

### Governance
Function 执行前的治理流程：校验 → 确认 → 执行 → 审计。  
ConfirmLevel 决定是直接执行还是需要用户二次确认。

### Event Bus
Function 执行成功后的副作用通过事件总线解耦，不直接调用下游。  
订阅者（推通知、写日志、触发工作流）与主流程完全分离。

---

## 3. 开始之前：三个关键决策

在写代码之前，这三个问题必须想清楚，后期改动代价很大。

### 决策 1：你的 Object 类型是什么？

列出业务里所有需要被 AI 操作的实体，给每个实体一个 PascalCase 的类型名。

```
常见错误：Object 类型定义太细或太粗
- 太细：把 OrderItem 单独建 Object，但 AI 从不单独操作它 → 不必要
- 太粗：把 User 和 Customer 合并，但它们在 AI 操作中有不同权限 → 后期拆分很痛
```

**原则**：AI 会直接引用（作为参数传入 Function）的实体才需要建 Object 类型。

### 决策 2：你的 Function 边界在哪里？

Function 是「用户意图」的最小完整单元，不是 API endpoint 的简单映射。

```
错误示例：
  createOrder + addOrderItem + confirmOrder → 三个 Function
  
正确示例：
  person.placeOrder（包含商品列表）→ 一个 Function，内部分步执行
```

**原则**：一个用户意图 = 一个 Function。用户不会说「先帮我 createOrder，再 addItem……」。

### 决策 3：ConfirmLevel 如何划分？

```
AutoExecute     — AI 直接执行，无需确认
                  适用：读操作、可撤销的轻量写（如创建草稿）

PreviewConfirm  — 推确认卡片，用户点击后执行
                  适用：影响他人、不可撤销、有明显副作用的操作

ExplicitConfirm — 需明确授权
                  适用：涉及金钱、删除数据、权限变更
```

**原则**：宁可确认级别高一点，误触发的成本远高于多一次点击的摩擦。

### 决策 4：数据采集的 Consent 边界是什么？（医疗 / 健康场景必选）

在健康类场景中，Observation Function 的 AutoExecute 有一个前提：用户已对该类数据的采集明确 consent。「静默写入，用户无感知」成立的条件是「用户已知晓并同意」，而不是「界面上没有弹窗」。

```
数据类型                  Consent 要求               Observation Function 可用？
──────────────────────    ──────────────────────     ──────────────────────────
症状 / 情绪提及           单独 consent（敏感数据）    consent 签署后 ✓
生活事件提及              单独 consent               consent 签署后 ✓
危机信号检测              强制启用，可不单独询问      始终 ✓（安全优先）
推断的诊断倾向            需明确告知用户会做推断      consent 范围内才写 RiskIndicator
```

**原则**：Consent 不是「在 T&C 里埋一句话」，而是用户能理解「我说的内容会被结构化记录并用于健康评估」。Consent 范围决定 AutoExecute 边界，超出范围的写入操作应降级为 PreviewConfirm 或不写。

---

## 4. 数据层设计

三张核心表，职责严格分离：

### `ontology_objects`（Object Store）

所有 Object 类型共用一张表，用 `ref.type` 区分。**这是投影缓存，不是权威数据源。**

```
{
  _id:         ObjectId          // cursor 分页必须有
  ref: {
    type:      string            // ObjectType
    id:        string
  }
  properties:  object            // 类型特定属性，map[string]any
  create_time: ISODate
  update_time: ISODate
}
```

索引：`{ "ref.type": 1, "ref.id": 1 }` 唯一索引。

> **重要**：`ontology_objects` 不替代业务表。  
> 权威数据（如订单金额、用户余额）仍在各自业务表，Object Store 只是读路径的投影快照。

**写入策略**：
- 核心对象（如 Meeting、Order）：**write-through**，创建/更新时立即同步写入
- 辅助对象（如 User、Group）：**lazy cache**，首次通过 Resolver 访问时写入

### `graph_edges`（Link Store）

```
{
  _id:          ObjectId         // cursor 分页必须有
  from_id:      string
  from_type:    string
  to_id:        string
  to_type:      string
  rel_type:     string           // KNOWS / CREATED / MEMBER_OF ...
  weight:       float64          // 关系强度，随交互递增
  last_event_at: ISODate
  updated_at:   ISODate
}
```

三个索引：
1. `(from_id, rel_type, to_type)` — 正向查询
2. `(to_id, rel_type)` — 反向查询
3. `(from_id, to_id, rel_type)` 唯一 — Upsert 去重

### `function_runs`（审计表，也叫 `action_runs`）

```
{
  actor_id:    string
  function:    string            // canonical function name
  params:      object
  summary:     string            // 人类可读的操作描述
  status:      string            // pending | confirmed | executed | failed | cancelled
  detail:      string
  created_at:  ISODate
}
```

### `ontology_function_configs`（Ontology Manager 配置表）

```
{
  name:          string          // 必须与代码常量一致
  object_type:   string
  description:   string          // LLM 看到的描述
  confirm_level: int             // 0/1/2
  params:        array
}
```

---

## 5. Object 系统

### ObjectRef 设计

所有对象都用统一的 `ObjectRef{Type, ID}` 表示。Function 的参数和返回值里都用 ObjectRef，不用裸 ID：

```go
// ✓ 类型化引用，LLM 下一轮 tool call 可以直接传入
Output: {"order": ObjectRef{Type: "Order", ID: "ord_123"}}

// ✗ 裸 ID，调用方不知道类型
Output: {"order_id": "ord_123"}
```

### Resolver：联邦读路径

Resolver 聚合多个数据源，对上层屏蔽读路径复杂性：

```
Resolver.Get(ObjectRef{Type: "Order", ID: "ord_123"})
  ├── 查 Object Store（cache hit → 直接返回）
  └── miss → 查权威业务表 → 写回 Object Store → 返回
```

**Resolver 不是 Object Store 的 CRUD 层**，它是联邦读入口。权威写入走 Function，不走 Resolver。

### 投影与权威数据

```
权威数据        Object Store（投影）     Resolver
orders 表  ──►  ontology_objects    ◄──  Resolver.Get("Order")
users 表   ──►  ontology_objects    ◄──  Resolver.Get("Person")
```

写路径：Function 执行 → 写权威表 → 写 Object Store（同步）  
读路径：Resolver.Get → 先查 Object Store → miss 时查权威表并回填

---

## 6. Function / Action 系统

### 定义与实现分离

这是整个架构最重要的分离原则：

| | 定义（元数据） | 实现（执行逻辑） |
|---|---|---|
| **内容** | name、description、params、confirmLevel | 具体的业务代码 |
| **维护方** | Ontology Manager（非技术人员可操作） | 开发者写代码 |
| **变更** | 不用发版 | 必须发版 |
| **存储** | `ontology_function_configs` | 代码注册表 |

```go
// 代码只注册执行逻辑 → Runner
func init() {
    // 第一段：Runner（执行逻辑，无元数据）
    registerRunner("person.placeOrder", (*Client).fnPersonPlaceOrder)
    registerRunner("order.cancel",      (*Client).fnOrderCancel)
    // ...

    // 第二段：种入代码侧默认定义（Ontology Manager 兜底，固定写法）
    for _, def := range defaultFunctionDefs {
        defByName[def.Name] = def
    }
}

// 元数据的代码侧默认值（Ontology Manager 未配置时的兜底）
var defaultFunctionDefs = []FunctionDef{
    {
        Name:         "person.placeOrder",
        ObjectType:   "Person",
        Description:  "以当前用户身份下单。",
        ConfirmLevel: PreviewConfirm,
        Params:       []ParamDef{{Name: "items", ...}, {Name: "address", ...}},
    },
}

// 服务启动时从 DB 加载并覆盖默认值（有 DB 配置则用 DB，无则用默认值）
ontology.LoadFunctionDefinitions(ctx, functionConfigStore)
```

### 命名规范

格式：`ObjectType.methodName`（camelCase），如：
- `person.placeOrder`
- `order.cancel`
- `document.summarize`

LLM tool name 中 `.` 替换为 `__`（避免特殊字符）：`person__placeOrder`。

### Function 执行的完整写路径

每个 Function 的 runner 内部应遵循固定顺序：

```
1. 写权威数据（业务表）
2. PutObject()  → Object Store 投影
3. writeLinks() → Graph 边
4. Events.Publish() → 事件总线（副作用由订阅者处理）
5. return FunctionResult{Complete, Output{ObjectRef}}
```

### 向后兼容：别名

重命名 Function 时，旧名通过别名表映射，不需要迁移存量数据：

```go
var functionAliases = map[string]string{
    "create_order": "person.placeOrder",  // 旧名 → 新名
}
```

`CanonicalFunctionName("create_order")` → `"person.placeOrder"` 自动解析。

### Composite Function（合并意图）

一个用户意图可能包含「创建 + 操作」两步，建议合并为一个 Function：

```go
// meeting.invite 无 meeting_id 时 → 先建会议再邀请（Composite）
// 关键：建会议时压制 IM 推送，避免用户看到两张卡片
if compositeCreate {
    in.NotifyIM = false   // 压制 MeetingCreated 的通知
    meetingID = createMeeting(in)
}
inviteMeeting(meetingID, NotifyIM: true)  // 统一由邀请事件推通知
```

### Observation Function（对话提取型）

除了用户主动触发的 Action Function，还有一种由 **LLM 从对话内容中静默识别并写入**的 Function，称为 Observation Function。

| | Action Function | Observation Function |
|---|---|---|
| **触发方** | 用户意图（「帮我创建会议」） | LLM 识别（「我最近睡不好」→ 记录症状） |
| **用户感知** | 有确认卡片或明确反馈 | 静默，不打断对话 |
| **ConfirmLevel** | 按业务需要设定 | 始终 `AutoExecute` |
| **典型场景** | 企业协作、电商、工单 | 健康筛查、用户画像、情绪追踪 |

Observation Function 的写路径和普通 Function 完全相同（写 Object Store → 写 Graph），区别只在触发方式：LLM 在对话中间自动调用，不等待用户显式指令。

---

## 7. Governance 治理层

### ConfirmLevel 枚举

```
AutoExecute     = 0   直接执行
                       适用：对话提取（Observation Function）、读操作、低风险轻量写
                       健康场景前提：用户已对该类数据采集明确 consent（见决策 4）

PreviewConfirm  = 1   推确认卡片，等用户点击
                       适用：影响他人、不可撤销、有明显副作用的操作

ExplicitConfirm = 2   需明确授权
                       适用：涉及金钱、删除数据、权限变更

ClinicalReview  = 3   写入待审核队列，需持证临床人员 sign-off 后才生效
                       适用：生成筛查结论、更新 RiskIndicator 级别、修改 clinical_kb 内容
                       行为：写 function_runs(status=pending_review)，
                             不向用户展示结论，触发临床审核通知
                       注意：sign-off 完成前用户侧只看到「评估进行中」
```

### 执行流

```
Executor.Execute(Input)
    │
    ├── AutoExecute ──────────────────────► Validate → runFunction → Audit(executed)
    │
    └── PreviewConfirm / ExplicitConfirm
            │
            ├─ Validate()  失败 ──────────► Audit(failed) → 返回错误提示
            │
            └─ Validate()  成功
                    │
                    ├─ function_runs.CreatePending()   写审计记录(status=pending)
                    ├─ Event: ActionConfirmRequested   推确认卡片给用户
                    └─ 返回 {PendingConfirm: true, RunID}
                                    │
                                    │ 用户点确认
                                    ▼
                          Executor.ConfirmRun(runID)
                              ├─ 验证归属 + 状态
                              ├─ Validate() 再校验（防时效性问题）
                              └─ runFunction → Audit(confirmed → executed)
```

### Validate 分层

校验分两层，职责严格分离：

```
validateParams()   — 参数完整性（必填项、格式）
authorizeFunction() — 权限（actor 是否有权操作这个 Object）
```

不要在 authorizeFunction 里做参数校验，也不要在 validateParams 里查数据库。

### BuildSummary

每个需要确认的 Function 都要实现 `BuildSummary`，生成人类可读的确认文案：

```go
case "order.cancel":
    orderID := parseStringParam(params["order_id"])
    return fmt.Sprintf("取消订单 %s", orderID)
```

这是用户在确认卡片上看到的内容，直接影响用户体验。

---

## 8. Graph / Links 系统

### 什么时候建边

不是所有关系都需要进图。建边的标准：**这个关系会被 AI 用来推荐或做上下文推理吗？**

```
值得建边：
  Person --KNOWS--> Person     用于推荐联系人
  Person --CREATED--> Order    用于「我的订单」查询
  Person --MEMBER_OF--> Group  用于群消息权限校验

不必建边：
  Order --HAS_ITEM--> OrderItem  AI 不会基于这个做推理
```

### 权重设计

```
权重 += delta  在每次相关事件发生时递增
权重 *= factor 定期衰减（如每周 ×0.95）

delta 参考：
  发一条消息：+0.05
  共同参与会议：+0.10
  加入同一个群：+0.01（对每对成员）
  创建实体：+1.0
```

### 何时写边

```
直接写（在 Function runner 里）：
  创建类操作 → 立即写 CREATED / INVITED_TO 等强关系边

通过 Webhook 写（依赖外部系统回调）：
  消息发送成功 → AfterSendSingleMsg webhook → RecordMessageExchanged
  用户加入群组 → AfterJoinGroup webhook → RecordGroupJoin
```

不要在事件订阅者里写边——订阅者负责「发出操作」，边在外部系统确认操作完成后才写（webhook），避免「消息还在队列里就写了 KNOWS 边」的问题。

---

## 9. Event Bus

### 核心原则

Function 执行成功后，所有副作用通过事件总线发出，**不直接调用下游**：

```go
// ✓ 解耦：订阅者失败不影响主流程
Events.Publish(ctx, "order.placed", OrderPlacedPayload{...})

// ✗ 直接调用：通知系统失败会导致整个 Function 失败
notificationService.Send(ctx, ...)
```

### 事件设计

每个事件一个 Payload 结构体，字段明确：

```go
type OrderPlacedPayload struct {
    Actor   Ref      // {type: "Person", id: "..."}
    Order   Ref      // {type: "Order", id: "..."}
    Items   []string
    Amount  float64
    NotifyIM bool    // 是否推 IM 通知（composite 路径控制双推问题用）
}
```

`NotifyIM bool` 是控制 Composite Function 避免双推的关键字段。

### 订阅者职责

每个订阅者只做一件事：

```
MeetingCreated → 推 IM 会议卡片
OrderPlaced    → 推支付通知
ActionConfirmRequested → 推确认卡片
TextMessageRequested   → 调 IM API 发消息
```

---

## 10. Ontology Manager（运营侧）

### 三个层次，按需建设

**层次 1：DB overlay，不发版改元数据（0.5 天）**  
实现 `ontology_function_configs` 表 + `LoadFunctionDefinitions`。  
运营直接改 DB，description/confirmLevel 生效不发版。无 UI。

**层次 2：只读 API，可视化查询（0.5 天）**  
```
GET /ontology/v1/objects?type=Order&limit=20&cursor=
GET /ontology/v1/objects/:type/:id/links
GET /ontology/v1/functions
GET /ontology/v1/function-runs?status=pending
```
现有数据接口（ListObjects、ListEdges、LLMTools）都已就绪，只需加路由层。

**层次 3：完整 UI（1-2 周）**  
- 对象浏览器：按类型浏览、查看属性和关联边
- 图关系视图：表格视图（1天）/ 力导向图（3天）
- 函数管理：在线编辑 description/confirmLevel/params
- 审计日志：function_runs 查询

触发条件：**有非工程师角色需要独立操作这些配置时**再建层次 3，不要提前。

---

## 11. 实施顺序建议

```
Week 1：核心数据层
  ├── 定义 ObjectType 和 ObjectRef
  ├── 创建 ontology_objects 表 + Store 接口（Upsert/Get/List）
  ├── 创建 graph_edges 表 + 接口（Upsert/GetTopByFrom/List）
  └── 实现 Resolver（联邦读，lazy cache）

Week 2：Function 系统
  ├── FunctionDef + FunctionRegistry（registerRunner + defaultFunctionDefs + LoadFunctionDefinitions）
  ├── 实现前 2-3 个核心 Function（包含完整写路径：DB → Store → Graph → Event）
  ├── validateFunction（参数校验 + 权限校验）
  └── LLMTools() 导出

Week 3：Governance 层
  ├── ConfirmLevel 枚举
  ├── Executor（validate → confirm → run → audit）
  ├── function_runs 表
  ├── BuildSummary（各函数的确认文案）
  └── Event Bus + 第一批订阅者

Week 4+：完善
  ├── 补齐剩余 Function
  ├── alias 兼容层
  ├── 测试覆盖
  └── Ontology Manager API（按需）
```

**不要等所有 Function 都想清楚再开始**。先做 2 个最核心的，把写路径跑通，再扩展。

---

## 12. 测试策略

### 单元测试：用内存 Fake 替代 DB

关键组件都应该有接口（Interface），便于测试时替换：

```go
// 用 fakeEdges 替代真实 Mongo，记录所有 Upsert 调用
type fakeEdges struct {
    calls []struct{ from, to, rel string }
}
func (f *fakeEdges) Upsert(_ context.Context, from, to, rel string, _ float64) error {
    f.calls = append(f.calls, struct{...}{from, to, rel})
    return nil
}
// 实现接口的其他方法...
```

### 必测场景

| 场景 | 要点 |
|---|---|
| nil guard | Graph/Store 为 nil 时不 panic，返回空结果 |
| 参数透传 | from/to/rel 正确传到底层 |
| 过滤逻辑 | 各 Filter 字段独立生效 |
| 分页边界 | 结果数 == limit → NextCursor 为空；> limit → 截断 + NextCursor 非空 |
| alias 解析 | 旧名 → 新名，自身 → 自身，均正确 |
| LLMTools 完整性 | 所有已注册函数都出现在 tools 列表里 |
| Composite 路径 | NotifyIM=false 时不触发 IM 推送 |

### 不需要在单元测试里覆盖的

- Webhook 触发路径（依赖外部系统，做 E2E 测试）
- Mongo cursor 的分页正确性（做集成测试）
- 事件订阅者的 IM 推送（做集成测试）

---

## 13. 常见陷阱

### 陷阱 1：switch 用字符串字面量而不是常量

```go
// ❌ 重命名函数后 silent break，编译不报错
case "create_order":

// ✓ 常量跟着重命名走
case FuncPersonPlaceOrder:
```

重命名 Function 时，全局 grep 字符串字面量，不要只改常量定义。

### 陷阱 2：Object Store 当权威数据源用

```go
// ❌ 从 ontology_objects 读订单金额做业务判断
order := objectStore.Get("Order", id)
if order.Properties["amount"].(float64) > 1000 { ... }

// ✓ 从权威业务表读
order, _ := orderRepo.Get(ctx, id)
if order.Amount > 1000 { ... }
```

Object Store 只用于 AI 上下文构建和 Ontology Manager 浏览，不用于业务逻辑判断。

### 陷阱 3：Function 粒度太细

```go
// ❌ 用户意图被拆成多个 Function，AI 需要多轮 tool call
createOrder()  →  addItem()  →  addItem()  →  submitOrder()

// ✓ 一个意图一个 Function
person.placeOrder(items, address)
```

### 陷阱 4：在事件订阅者里写 Graph 边

```go
// ❌ 消息还在队列里就写了 KNOWS 边
bus.Subscribe("message.requested", func(payload) {
    im.SendMsg(...)
    graph.RecordMessage(from, to)  // ← 不要
})

// ✓ 等 IM 服务端确认发送成功（webhook 回调）再写边
func handleAfterSendSingleMsg(payload) {
    graph.RecordMessage(from, to)  // ← 在这里写
}
```

### 陷阱 5：FunctionResult.Output 返回裸 ID

```go
// ❌ 调用方不知道 ID 的类型，LLM 下一轮无法直接引用
Output: {"order_id": "ord_123"}

// ✓ 返回 ObjectRef，LLM 可以作为参数直接传入后续 Function
Output: {"order": ObjectRef{Type: "Order", ID: "ord_123"}}
```

### 陷阱 6：Record 结构体没有 `_id` 字段

`ontology_objects` 和 `graph_edges` 的结构体必须有 `_id` 字段，否则 cursor 分页需要额外一次查询：

```go
type Record struct {
    OID        primitive.ObjectID `bson:"_id,omitempty"`  // ← 必须有
    // ...
}
// cursor = recs[limit-1].OID.Hex()  直接取，无需额外查询
```

---

## 14. 与传统架构的对比

| 问题 | 传统做法 | Foundry Ontology |
|---|---|---|
| AI 能做什么 | 散落在各 API handler 里 | 统一在 Function Registry，LLMTools() 导出 |
| 参数校验 | 每个接口各自写 | validateFunction 集中管理 |
| 权限控制 | 散点 if-else | authorizeFunction 统一策略 |
| 二次确认 | 前端硬编码，后端无感知 | ConfirmLevel 声明式配置，Executor 统一处理 |
| 审计 | 有的记、有的不记 | function_runs 全量记录 |
| 副作用 | 直接调用，顺序耦合 | Event Bus，订阅者独立 |
| AI 上下文 | 每次请求 prompt 塞入数据 | Resolver 按需查，Graph 提供关系上下文 |
| 改操作描述 | 改代码发版 | Ontology Manager 修改 DB 记录 |

---

## 15. 扩展场景：基于对话的知识提取与推理

本节以**精神健康筛查**为例，展示如何在对话型场景中应用 Foundry Ontology 架构。  
这类场景的核心特征是：知识来源于**非结构化对话**，而不是用户的显式操作。

### 场景描述

用户与 LLM 进行开放式对话，LLM 在对话过程中识别临床相关信息，将其结构化写入图谱。随着多次对话积累，系统能够推理出用户的症状模式、风险等级和可能的关注方向，辅助临床筛查。

### Foundry 对齐：Reference vs Operational 分层

双图分离（clinical_kb / patient_graph）指的是**存储与 ACL 分离**，不是 Ontology 概念分裂。在 Foundry 语义下，这是一个 Ontology、两类 Object：

```
┌─ Reference Objects（clinical_kb，稳定，只读）──────────────────┐
│  ClinicalDisorder, ClinicalSymptom, DiagnosticCriterion, Scale │
│  ClinicalRule  { formula, triggers_function }                  │
│    --APPLIES_TO--> ClinicalSymptom / ClinicalDisorder          │
│  ✗ 没有用户实例                                                 │
│  ✗ 没有 Function runner（不可直接执行）                          │
└────────────────────────────────────────────────────────────────┘
                              ↑ 只读 consult
                              │
┌─ Operational Objects（patient_graph，动态）────────────────────┐
│  Person, SymptomReport, Session, LifeEvent, RiskIndicator      │
│  Function 只绑在这一层：                                         │
│    person.recordSymptom()           Observation，写 SymptomReport │
│    person.flagCrisisSignal()        危机响应                     │
│    person.getAssessmentFocus()      读状态，驱动对话              │
│    person.generateScreeningReport() 读状态 + consult ClinicalRule │
└────────────────────────────────────────────────────────────────┘

跨层 Link（Ontology 内正式关系，存储可分离）：
  SymptomReport --REFERS_TO--> ClinicalSymptom { canonical_id }
  Person --REPORTS--> SymptomReport
```

#### 为什么 Function 不能绑在 clinical 实体上

Foundry 的动态循环是 **Operational Object 状态变化 → Function 执行 → Event Bus**：

| Foundry 预期 | 临床场景对应 |
|---|---|
| Product 目录（稳定） | ClinicalDisorder、ClinicalSymptom（clinical_kb） |
| Order（动态，每笔在变） | Person、SymptomReport（patient_graph） |
| `order.ship()` 绑在 Order 上 | `person.flagCrisisSignal()` 绑在 Person 上 |
| 补货规则（库存 < X） | ClinicalRule（指南条文，consult 用） |

clinical_kb 稳定不是缺陷——Reference Layer 本来就不参与「变」。**动态驱动 Action 的能力在 patient_graph 的 Operational Object 上**，不在 clinical 实体上。

#### 文档提取的 logic_rules / actions ≠ Foundry Function

nano-ontoprompt 从文档提取的两类产物，在 Ontology 里应区分落位：

| 提取产物 | 本质 | Ontology 落位 | 是否可执行 |
|---|---|---|---|
| `logic_rules` | 声明式临床约束（IF…THEN…） | `ClinicalRule`，绑定 Reference Object | 否，只供 consult |
| `actions` | 指南描述的响应（「触发紧急随访」） | Function **元数据**（description、触发条件） | 否，runner 在代码注册 |
| Foundry Function |  imperative 操作（写图、告警、审计） | 绑 `Person` / `Session`，代码注册 runner | 是 |

提取阶段的价值：**自动生成 ClinicalRule 草稿 + Function 元数据**，不是把 `function_code` 绑在 Disorder 实体上当 Foundry Action 用。

#### Event Bus 驱动的规则评估（Foundry 标准模式）

```
用户说话
  → person.recordSymptom()              // Person 上的 Observation Function
  → 写 SymptomReport + REPORTS 边       // patient_graph 状态变化
  → Event Bus: SymptomReported

订阅者 ClinicalRuleEvaluator（event subscriber，不是 Function）：
  1. 读 Person 当前 SymptomReports
  2. 沿 REFERS_TO 找到 ClinicalSymptom
  3. 查 ClinicalRule（clinical_kb，只读）
  4. IF 命中 → 调用 person.flagCrisisSignal()  // 仍是 Person 上的 Function
  → 写 RiskIndicator → Event Bus → 人工告警
```

规则评估是 **Event 订阅者 consult Reference Layer**，不是 clinical 实体「拥有」Action。clinical 稳定、patient 动态，Foundry 循环在 Operational 层闭环。

#### 与企业场景的类比

```
供应链：
  Product（Reference，稳定） + 补货规则（Policy）
  PurchaseOrder（Operational，动态） + placeOrder Function
  规则命中 → 调用 createPurchaseRequest()，不是 Product 实体执行 Action

精神健康筛查：
  ClinicalSymptom（Reference，稳定） + ClinicalRule（Policy）
  SymptomReport（Operational，动态） + recordSymptom / flagCrisisSignal Function
  规则命中 → 调用 person.flagCrisisSignal()，不是 Disorder 实体执行 Action
```

### 双图协作：如何使用 patient_graph 与 clinical_kb

一句话：**patient_graph 存 Operational Object（用户事实）；clinical_kb 存 Reference Object（医学知识）。** 二者同属一个 Ontology，存储与 ACL 分离；跨层通过 `SymptomReport --REFERS_TO--> ClinicalSymptom` 关联，规则评估由 Event Bus 驱动。

#### 心智模型

```
用户说话（自由文本）
        │
        ▼
   规范化 canonical_id  ←── 查 clinical_kb 的 Symptom 词表（内存索引）
        │
        ▼
   写入 patient_graph  ←── 「小王报告了失眠，持续 3 周」
        │
        ▼
   读 patient_graph    ←── 当前用户有哪些症状、缺什么维度
        │
        ▼
   查 clinical_kb      ←── 这些症状组合对应哪些疾病方向
        │
        ▼
   应用层决策          ←── 下一步问什么 / 风险等级 / 生成报告
        │
        ▼
   注入 LLM prompt
```

#### 完整 walkthrough：用户小王的三次对话

**背景：clinical_kb 已离线建好（与用户无关，全系统只读）**

从 140+ 医学文档 + SNOMED-CT 批量提取，存 TuGraph：

```
Disorder「抑郁症」
  --HAS_SYMPTOM--> snomed:366979004 情绪低落  { prevalence: 0.92, required: true }
  --HAS_SYMPTOM--> snomed:193462001 失眠      { prevalence: 0.78 }
  --HAS_SYMPTOM--> snomed:247441003 兴趣减退  { prevalence: 0.85, required: true }
  --DIAGNOSED_BY--> 「持续 ≥ 2 周」
  --ASSESSED_BY--> PHQ-9
```

**第 1 次对话：收集事实 → 写 patient_graph**

用户：「最近心情很差，晚上也睡不好。」

```
① LLM 调用 person.recordSymptom（用户无感知）
② 规范化（查 clinical_kb Symptom 词表，非整图遍历）：
   「心情很差」→ snomed:366979004（情绪低落）
   「睡不好」  → snomed:193462001（失眠）
③ 写入 patient_graph（MongoDB）：
   Person(小王) --REPORTS--> SymptomReport{canonical_id: snomed:366979004, raw_text: "心情很差"}
     { first_reported_at: 今天, severity: moderate, confidence: 0.88 }
   SymptomReport --REFERS_TO--> ClinicalSymptom{snomed:366979004}
   Person(小王) --REPORTS--> SymptomReport{canonical_id: snomed:193462001, raw_text: "睡不好"}
   Session(本次) --CAPTURED--> 上述 SymptomReport
   Session(本次) --ASSESSED--> Dimension{PHQ-9, item_1 情绪低落} ✓
   Session(本次) --ASSESSED--> Dimension{PHQ-9, item_3 睡眠} ✓
```

每轮结束后读 patient_graph → `getAssessmentFocus()`：

```
emerging_clusters: ["depression_possible"]
coverage_gaps:      ["duration"]   ← PHQ-9 关键项缺失
next_focus:         "depression_possible"
```

跨图推理（第 1 次）：patient 侧 `[情绪低落, 失眠]` + clinical 侧 Disorder 匹配 → 抑郁方向优先，但 duration 缺失 → LLM 自然引导「这种情况持续多久了」。

**第 2 次对话（1 周后）：跨会话积累**

用户：「差不多有一个月了，对什么都提不起兴趣。」

```
更新 情绪低落边：last_reported_at = 今天, reported_count = 2, duration ≥ 4 周 ✓
新增 兴趣减退：snomed:247441003
Coverage：PHQ-9 item_2 ✓，duration gap 关闭 ✓
```

跨图推理：patient 侧 3 症状 + 持续 ≥ 4 周；clinical 侧抑郁症 HAS_SYMPTOM 覆盖 3 项、诊断标准「≥ 2 周」已满足 → `confirmed_clusters: ["depression_possible"]`，继续补 PHQ-9 剩余维度。

**第 3 次对话：生成筛查报告**

调用 `person.generateScreeningReport()`（只读，不写图）：

| 步骤 | 查哪张图 | 做什么 |
|---|---|---|
| 症状持续性 | patient_graph | `REPORTS` 边，`last_reported_at - first_reported_at ≥ 2周` |
| PHQ-9 评分 | patient_graph | 已覆盖 Dimension → 映射条目分值 |
| 时序关联 | patient_graph | `LifeEvent --PRECEDED--> Symptom` |
| 症状簇 | patient_graph | 用户症状间 `CO_OCCURS_WITH` |
| 疾病匹配 | clinical_kb | canonical_ids 与 Disorder HAS_SYMPTOM 交集 |
| 诊断标准 | clinical_kb | `DIAGNOSED_BY` 是否满足 |
| 输出报告 | 应用层 | 合并结果，注入 prompt 或返回结构化报告 |

#### 三种用法与时机

**用法 1：写 patient_graph（每轮对话）**

LLM 识别临床信息 → Observation Function 写入。clinical_kb 仅提供 Symptom 词表做规范化（embedding 召回 + LLM 推理），不参与存储用户数据。

**用法 2：读 patient_graph（每轮对话后）**

`getAssessmentFocus()` 查已有症状、Coverage 缺口、症状簇、持续时间 → 决定下一问什么。此阶段不查 clinical_kb。

**用法 3：跨图推理（方向判断 + 出报告）**

```
patient_graph 取出 canonical_ids
        +
clinical_kb  查 Disorder 匹配、诊断标准、量表推荐、共现模式
        ↓
应用层合并 → 排序 → 注入 prompt 或生成报告
```

#### 分工速查

| 问题 | 查哪张图 |
|---|---|
| 用户说了什么症状？ | patient_graph |
| 症状持续多久？ | patient_graph（边的时间戳） |
| PHQ-9 哪些题还没覆盖？ | patient_graph（Dimension 边） |
| 「失眠 + 兴趣减退」通常提示什么病？ | clinical_kb |
| 抑郁症需要持续几周？ | clinical_kb（DiagnosticCriterion） |
| 接下来该用 PHQ-9 还是 GAD-7？ | clinical_kb + patient_graph（当前信号） |
| 用户 PII 存在哪？ | 只在 patient_graph |

#### 易混淆点

- **一个 Ontology，两类 Object**：分离的是 MongoDB/TuGraph 存储，不是概念模型。Link 类型（如 `REFERS_TO`）在 Ontology 层统一定义。
- **clinical_kb 不记用户**：只有 Reference Object，不知道「小王是谁」。
- **Function 只绑 Person/Session**：文档提取的 `actions` 是 Function 元数据，不是绑在 Disorder 上的 executable。
- **ClinicalRule ≠ Function**：规则是 consult 用的指南条文；Function 是 Person 上的 runner。
- **规范化 = 写 REFERS_TO 边**：口语 → `canonical_id` → SymptomReport 关联 ClinicalSymptom，是正式 Link，不是应用层 ad-hoc 映射。
- **对话引导靠 Operational 层**：Coverage 缺口、duration 缺失等，查 patient_graph 即可，不必查 clinical_kb。
- **反哺是离线回路**：patient_graph 聚合 → clinical_staging → 审核 → 更新 clinical_kb，不影响当次对话。

### Object 设计

Operational 层（patient_graph）Object。**注意：`SymptomReport` 是用户的一次症状报告实例，不是 clinical_kb 里的 `ClinicalSymptom`。**

| Object | 说明 | 关键属性 |
|---|---|---|
| `Person` | 用户，跨会话持久存在 | user_id, created_at |
| `SymptomReport` | 用户报告的症状实例 | canonical_id, raw_text, severity, phq9_item |
| `LifeEvent` | 生活事件 | name, event_type（loss/trauma/transition）, occurred_at |
| `Behavior` | 行为模式 | name, behavior_type（avoidance/substance/sleep）|
| `Session` | 一次对话会话 | started_at, ended_at, summary |
| `RiskIndicator` | 风险指标 | level（low/medium/high/urgent）, basis（推理依据摘要）|
| `Dimension` | 量表评估维度 | scale（PHQ-9/GAD-7/PCL-5）, item_id, item_name |

`SymptomReport.canonical_id` 用于建立 `REFERS_TO` 边，指向 clinical_kb 的 `ClinicalSymptom`。时间戳（`first_reported_at` / `last_reported_at`）放在 `Person --REPORTS--> SymptomReport` 边上。

`Dimension` 是 Coverage Tracking 的核心——每个量表的每个条目是一个 `Dimension` Object，`Session --ASSESSED--> Dimension` 边记录本次会话覆盖了哪些维度。

### Link 设计

**关系边**（记录客观关系，跨会话稳定）：

```
Person --REPORTS-->           SymptomReport   用户报告了某症状（边上有时间戳、severity）
SymptomReport --REFERS_TO-->  ClinicalSymptom  指向 clinical_kb 规范症状（跨层 Link）
Person --EXPERIENCED-->       LifeEvent       经历了某生活事件
Person --EXHIBITS-->          Behavior        表现出某行为模式
Person --HAS_RISK-->          RiskIndicator   被推理出的风险指标
LifeEvent --PRECEDED-->       SymptomReport   事件早于症状（时序因果）
SymptomReport --CO_OCCURS_WITH--> SymptomReport  两个症状报告共现
```

**会话边**（记录单次会话的观察，粒度更细）：

```
Session --CAPTURED-->  SymptomReport  本次会话识别到的症状
Session --CAPTURED-->  LifeEvent      本次会话识别到的生活事件
Session --ASSESSED-->  Dimension      本次会话覆盖了哪些量表维度
```

**边上的关键字段**（与通用架构的 weight 不同，需要时间维度）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `first_reported_at` | timestamp | 首次报告时间 |
| `last_reported_at` | timestamp | 最近一次报告时间 |
| `reported_count` | int | 跨会话报告次数 |
| `severity` | enum | mild / moderate / severe |
| `confidence` | float | LLM 识别置信度，< 0.6 不直接写主图 |
| `evidence` | string | LLM 识别依据片段（非原始对话），供人工审核 |

症状持续时间是临床标准的核心（PHQ-9 要求持续两周以上），必须通过 `first_reported_at` / `last_reported_at` 追踪，不能用 `weight` 代替。

### Observation Function 设计

| Function | ConfirmLevel | 说明 |
|---|---|---|
| `person.recordSymptom()` | AutoExecute（consent 范围内） | 症状写入，静默 |
| `person.recordLifeEvent()` | AutoExecute（consent 范围内） | 生活事件写入，静默 |
| `person.recordBehavior()` | AutoExecute（consent 范围内） | 行为模式写入，静默 |
| `person.recordEmotionState()` | AutoExecute（consent 范围内） | 情绪状态写入，静默 |
| `person.flagCrisisSignal()` | AutoExecute（安全旁路，不受 consent 约束） | 危机信号，即时触发 |
| `person.generateScreeningReport()` | ClinicalReview | 筛查结论，需医生 sign-off 才呈现 |
| `person.updateRiskLevel(urgent)` | AutoExecute | 紧急风险升级 + 即时告警 |
| `person.updateRiskLevel(非紧急)` | ClinicalReview | 非紧急风险评级变更，需临床确认 |

`evidence` 字段存 LLM 识别的依据片段，供人工审核用，不存原始用户对话。

#### FHIR 资源对照（对接 EHR 时参考）

| Foundry Object | FHIR 资源 | 关键字段对应 |
|---|---|---|
| `Person` | `Patient` | user_id → Patient.identifier |
| `SymptomReport` | `Observation` | canonical_id → Observation.code（SNOMED 编码） |
| `Session` | `Encounter` | started_at → Encounter.period.start |
| `Dimension` | `QuestionnaireResponse` | PHQ-9 item → QuestionnaireResponse.item |
| `RiskIndicator` | `RiskAssessment` / `Flag` | level → RiskAssessment.prediction |
| `generateScreeningReport()` | `DiagnosticReport` | 标准报告格式，EHR 可直接接收 |

**何时需要 FHIR 对齐**：
- 筛查结果要传给医院信息系统 → 必须输出 FHIR JSON
- 使用 CDS Hooks（HL7 标准）接入临床决策支持 → Function 触发点映射到 Hook 事件
- 无 EHR 对接需求 → 当前 Foundry Object 设计足够，本表仅供参考

### 对话 → 图的提取机制

**方式一：实时提取（推荐）**

在系统 Prompt 中指示 LLM：

> 在对话过程中，当你识别到以下信息时，调用对应的 Observation Function（这些调用对用户不可见）：
> - 用户提到症状、情绪或身体不适 → `person.recordSymptom`
> - 用户提到重大生活事件 → `person.recordLifeEvent`
> - 用户描述行为模式变化 → `person.recordBehavior`

LLM 在正常回复用户的同时，并行调用这些函数写图。

**方式二：会话结束后批量提取**

会话结束时，将完整对话记录送给专用提取 LLM，输出结构化数据后批量写图。  
适合作为实时提取的补充验证，或在实时提取置信度不足时兜底。

**两种方式对比**：

| | 实时提取 | 批量提取 |
|---|---|---|
| 延迟 | 无延迟，对话中即写入 | 会话结束后写入 |
| 上下文 | 只有当前轮对话 | 完整会话上下文，准确性更高 |
| 适用 | 实时反馈场景 | 准确性要求高的临床场景 |
| 推荐 | 两者组合使用 | — |

### 症状规范化：对齐 clinical_kb 的 canonical_id

LLM 从对话中提取的是自由文本（「心情很差」「脑子转不动」），patient_graph 写入的却是 `canonical_id`（`symptom:mood_depression`）。两者之间需要一个**规范化步骤**，否则跨图匹配无从进行。

#### canonical_id 的来源：直接用 SNOMED-CT，不从文档提取

**SNOMED-CT**（全球最全临床术语库，35万+ 概念）已经包含大量症状，每个概念有唯一数字 ID：

```
366979004 → 情绪低落（Depressed mood）
193462001 → 失眠（Insomnia）
247592009 → 注意力难集中（Poor concentration）
247441003 → 兴趣减退（Loss of interest）
```

Symptom 节点直接从 SNOMED-CT 导入（Ontology 类型名 `ClinicalSymptom`）：

```
ClinicalSymptom { canonical_id: "snomed:366979004", name: "情绪低落" }
ClinicalSymptom { canonical_id: "snomed:193462001", name: "失眠" }
```

**SNOMED-CT 和文档各自提供什么：**

| | SNOMED-CT | 140+ 文档 |
|---|---|---|
| 症状词表（canonical_id） | ✓ 直接导入 | 不需要重复提取 |
| Disorder → Symptom 关系 | 部分有，不完整 | ✓ 需要提取 |
| 各症状在该病中的 prevalence | ✗ 没有 | ✓ 需要提取 |
| DSM-5 诊断标准条目 | ✗ 没有 | ✓ 需要提取 |
| 危险因素、治疗方案 | ✗ 没有 | ✓ 需要提取 |

SNOMED-CT 解决「症状叫什么、ID 是什么」，文档解决「这个病和这些症状是什么关系、关系有多强」。

#### 规范化流程：Embedding 召回 + LLM 推理

低置信度匹配失败是 Embedding 模型的语言理解问题，不应该用人工维护映射表来补。正确分工：**Embedding 负责缩小候选范围，LLM 负责临床推理判断**。

```
用户说「感觉脑子像生锈了」

① Embedding 召回 top-5 候选（内存索引，< 10ms）：
   symptom:concentration_difficulty   0.61
   symptom:cognitive_slowing          0.58
   symptom:brain_fog                  0.54
   ...

② LLM 从候选中临床推理：
   「脑子像生锈了」→ 认知迟缓、注意力下降
   → canonical_id: symptom:concentration_difficulty ✓
```

LLM 有临床语言理解能力，能处理口语化、比喻性、方言表达，Embedding 无法做到这一点。

写入 patient_graph：

```json
{
  "canonical_id": "symptom:concentration_difficulty",
  "raw_text":     "感觉脑子像生锈了",
  "match_confidence": 0.91
}
```

`raw_text` 保留原始表达用于溯源，不参与推理匹配。

#### Symptom 词表缓存

clinical_kb 极少变动，服务启动时预加载所有 Symptom 节点到内存索引，不需要每次查 TuGraph：

```
启动时：
  TuGraph clinical_kb → 拉取全部 ClinicalSymptom{canonical_id, name, embedding}
  → 写入内存索引（FAISS 或余弦相似度列表）

每轮对话提取时：
  自由文本 → Embedding 召回候选 → LLM 推理 → canonical_id
```

#### review 队列只拦截临床歧义

低置信度 ≠ 需要人工。绝大多数情况 LLM 推理后可直接写入，进 review 队列的只有 **LLM 判断为临床上存在多种解读** 的情况：

```
「活着没意思」
  → 抑郁情绪？存在主义表达？自杀意念？
  → LLM 无法仅凭文本判断 → 进 review 队列
  → 同时触发：追问上下文 / 危机检测旁路
```

人工审核做的是**临床判断**，不是文本到标签的映射维护。review 队列的量应该极少——只有连临床医生都需要更多信息才能判断的表达。

### 推理层：图 → 筛查结论

图积累足够信息后，推理由**只读 Function** 完成（查图不写图）：

```
person.generateScreeningReport()
```

内部查询逻辑示例：

```
1. 症状持续性检查
   查：Person --REPORTS--> SymptomReport，filter: last_reported_at > 2周前
   → 持续两周以上的症状列表

2. PHQ-9 映射
   将症状列表映射到 PHQ-9 条目，累计评分

3. 时序关联
   查：LifeEvent --PRECEDED--> Symptom，时间差 < 6个月
   → 识别可能的触发事件

4. 症状共现模式
   查：Symptom --CO_OCCURS_WITH--> Symptom
   → 识别症状簇（焦虑簇、抑郁簇……）

5. 输出结构化报告
   → {risk_level, symptom_clusters, possible_concerns, evidence_sessions}
```

### 对话引导：如何有针对性地问问题

精神健康涉及几十种可能的方向，不能所有问题都问一遍。核心设计原则是：**图的当前状态驱动下一个问题的方向**，而不是预设固定问卷顺序。

#### 分层筛查：宽进窄出

```
第一层：开放式主诉（1-2 轮）
  「最近有什么让你感到困扰的事情吗？」
  从回答中提取初始信号，写入图

第二层：广谱快筛（只问 2-3 个问题，覆盖主要方向）
  根据初始信号，选 PHQ-2 / GAD-2 / PC-PTSD-5 等超短量表
  → 正筛的方向进入第三层，未触发的方向不问

第三层：定向深挖（只针对正筛方向）
  PHQ-9（抑郁）/ GAD-7（焦虑）/ PCL-5（PTSD）…
  → 只问与当前症状簇相关的维度
```

#### 图状态驱动路由

每轮对话后调用只读 Function 查询图的当前覆盖状态：

```
person.getAssessmentFocus()
→ 返回：
  {
    confirmed_clusters: ["depression_possible"],  // 有足够证据
    emerging_clusters:  ["anxiety_signals"],       // 有信号但证据不足
    unexplored:         ["trauma", "substance"],   // 尚未涉及
    coverage_gaps:      ["duration", "severity"],  // 已知症状缺少关键维度
    next_focus:         "anxiety_signals"          // 建议下一步方向
  }
```

返回结果注入系统 prompt：

```
当前评估状态：
- 用户报告了情绪低落和睡眠问题，持续时间未知
- 焦虑方向有初步信号，尚未深入探索
- 创伤和物质使用方向尚未涉及

下一步：自然引导用户谈谈最近是否容易紧张或担心。
不要提问卷，不要列清单，像普通对话一样探索。
```

#### 症状簇 → 方向映射

图里的症状共现模式决定激活哪个评估方向：

```
情绪低落 + 兴趣减退 + 疲乏          → 激活 PHQ-9（抑郁）
容易紧张 + 控制不住担心 + 躯体不适   → 激活 GAD-7（焦虑）
闪回 / 回避 / 创伤事件提及           → 激活 PCL-5（创伤）
情绪高涨 + 睡眠减少 + 冲动（周期性） → 激活双相筛查
多方向同时正筛                       → 优先风险最高，记录共病可能
```

#### Coverage Tracking

每个量表的每个维度都在图里追踪是否已覆盖（存在 `Session --ASSESSED--> Dimension` 边）：

```
PHQ-9 覆盖状态：
  ✓ 情绪低落（已从对话中提取）
  ✓ 兴趣减退（已从对话中提取）
  ✗ 睡眠问题（空缺 → 下一步补充）
  ✗ 疲乏、自我评价、注意力（空缺）
```

**已从对话自然提取到的维度不重复问**，只对空缺维度定向引导。

#### 引导方式

```
深度探索（开放式）：
  「你提到最近睡不好，是难以入睡、容易醒，还是两者都有？」
  → 用于对话中已出现的线索，继续深挖

覆盖补充（温和直接）：
  「除了睡眠，你最近食欲和精力方面有变化吗？」
  → 用于评估必要维度（如持续时间、严重程度）尚未在对话中出现时
```

能从自然对话中提取的不主动问；对于临床必须的维度（如症状持续时长），在对话中未出现时温和地直接问。

### 三层漏斗的三个盲区

漏斗解决的是「下一步问哪个方向」，以下三块不在漏斗职责范围内，需要独立设计。

#### 盲区 1：危机信号不能走漏斗

自伤 / 自杀意念的检测必须独立于漏斗路由，不能等抑郁方向「正筛」后才触发。

```
设计要求：
  - 独立的 Crisis Observation Function，常驻每一轮对话
  - 只要提取到危机关键词（自杀、活着没意思、不想活……）
    → 立即触发 person.flagCrisisSignal()，绕过漏斗路由
  - 写入图边：Person --RISK--> RiskIndicator{level: "urgent"}
  - 上层系统监听该边写入事件，触发人工介入流程

实现方式：
  在 LLM 系统 prompt 中始终注入危机检测指令（不依赖漏斗状态）：
  「如果对话中出现任何自伤或自杀相关信号，立即调用
   person.flagCrisisSignal，不要等待其他评估完成。」
```

PHQ-9 第9题（关于自杀/自伤的念头）也应作为独立规则，在任何会话中只要未覆盖就优先补充，不受漏斗三层顺序约束。

#### 盲区 2：Observation Function 提取失败的兜底

漏斗路由的准确性完全依赖 LLM 从自然对话中正确提取症状信号。提取层是整个设计最脆弱的环节。

**失败场景**：用户说「最近压力有点大」，LLM 未将其映射到任何症状 Object，图里没有写入，导致第二层无方向可激活，漏斗卡死在第一层。

```
兜底策略：

1. 提取置信度标注
   每个提取结果带 confidence 字段（0.0–1.0）
   confidence < 0.6 → 写入待审核队列，不直接驱动路由

2. 不确定时保守扩展方向
   若 getAssessmentFocus() 返回 confirmed_clusters 为空
   且 emerging_clusters 也为空
   → 默认激活情绪低落 + 焦虑两个方向（覆盖最高频场景）
   宁可多问一个方向，不要漏掉

3. 显式提取验证（开发阶段）
   记录每次 Observation Function 的输入（对话片段）和输出（写入的 Object/边）
   对照人工标注，持续评估提取准确率
   准确率 < 80% 时不开放自动路由
```

#### 盲区 3：多方向共病的优先级排序

文档中「多方向同时正筛 → 优先风险最高」需要一个明确的规则，否则实现时无从判断。

```
优先级从高到低（建议参考 NICE 指南排序）：

1. 急性危机（自伤/自杀信号）      ← 独立旁路，见盲区 1
2. PTSD / 创伤后应激障碍          ← PCL-5 正筛
3. 抑郁（伴有自伤倾向信号）        ← PHQ-9 正筛 + 项目9有风险
4. 双相障碍可能                   ← 情绪高涨周期 + 抑郁交替
5. 抑郁（无自伤信号）             ← PHQ-9 正筛
6. 焦虑障碍                      ← GAD-7 正筛
7. 其他方向                      ← 按症状信号强度排序
```

**共病时的实现规则**：

```go
// 在 getAssessmentFocus() 内部，对多个正筛方向按优先级排序
// 返回优先级最高的方向作为 next_focus
// 其余方向进入 pending_clusters，下一轮跟进

type AssessmentFocus struct {
    ConfirmedClusters []string // 已有足够证据
    EmergingClusters  []string // 信号存在，证据不足
    Unexplored        []string // 尚未涉及
    CoverageGaps      []string // 已知方向缺少的维度
    NextFocus         string   // 当前优先级最高方向
    PendingClusters   []string // 多方向共病时，排队等待的方向
}
```

深入当前最高优先级方向，在其 Coverage Tracking 完成（或置信度饱和）后，再切换到下一个 pending 方向，避免在多个方向间来回跳跃让用户困惑。

### 与企业协作场景的关键差异

| 维度 | 企业协作（如会议邀请） | 精神健康筛查 |
|---|---|---|
| Function 触发 | 用户显式意图 | LLM 识别，用户无感知 |
| 写入频率 | 低，一次会话几次 | 高，每轮对话可能多次 |
| 边的核心属性 | weight（关系强度） | 时间戳 + 置信度 + 严重程度 |
| 跨会话积累 | 非核心 | 核心（症状持续时间是临床依据） |
| 推理目标 | 推荐联系人 / 操作权限 | 症状模式 / 风险等级 |
| 隐私级别 | 普通业务数据 | 高度敏感，需单独加密存储 |

### 实施建议

**先做数据积累，再做推理**

```
阶段 1：打通单次对话提取链路
  - 定义 Symptom / LifeEvent Object 类型
  - 实现 person.recordSymptom / person.recordLifeEvent
  - 验证一次对话能正确写入图

阶段 2：验证跨会话积累
  - 同一 Person 多次对话后，图里的症状记录是否正确合并（Upsert 去重）
  - 时间字段是否正确更新

阶段 3：实现推理层
  - person.generateScreeningReport（只读，查图生成报告）
  - 与标准量表（PHQ-9、GAD-7）对照验证准确性

阶段 4：置信度与审核机制
  - 低置信度识别 → 写入待审核队列，不直接进主图
  - 提供人工审核界面确认或拒绝
```

**不要跳过阶段 2 直接做推理**。跨会话积累的数据质量决定推理准确性，数据错了推理结果没有意义。

### 临床知识图：Objects / Links 设计

Reference 层（`clinical_kb`）存储从医学文档中提取的**结构化医学知识**，与 Operational 层的 SymptomReport 完全不同。

#### Objects

| Object | 说明 | 关键属性 |
|---|---|---|
| `ClinicalDisorder` | 疾病 / 障碍 | name, category, icd10_code, dsm5_code |
| `ClinicalSymptom` | 症状（规范化） | canonical_id, name, category（情绪/躯体/认知/行为）|
| `DiagnosticCriterion` | 诊断标准条目 | standard（DSM-5/ICD-11）, criterion_text, time_requirement |
| `ClinicalRule` | 临床规则（指南条文） | formula, description, triggers_function, confidence |
| `RiskFactor` | 危险因素 | name, factor_type（biological/psychological/social）|
| `Treatment` | 治疗方案 | name, treatment_type（medication/therapy/physical）|
| `Scale` | 评估量表 | name, full_name, item_count, score_range |

`ClinicalSymptom.canonical_id` 是跨层桥梁——Operational 层 `SymptomReport` 通过 `REFERS_TO` 边引用它。`ClinicalRule.triggers_function` 指向 Operational 层 Person 上注册的 Function 名（如 `person.flagCrisisSignal`），规则本身不可执行。

#### Links

**诊断相关：**

```
ClinicalDisorder --HAS_SYMPTOM-->       ClinicalSymptom     该病的典型症状
ClinicalDisorder --DIAGNOSED_BY-->      DiagnosticCriterion 诊断标准条目
ClinicalDisorder --COMORBID_WITH-->     ClinicalDisorder    常见共病（双向）
ClinicalSymptom  --INDICATES-->         ClinicalDisorder    症状反向指向疾病
ClinicalRule     --APPLIES_TO-->         ClinicalSymptom     规则适用的症状
ClinicalRule     --APPLIES_TO-->         ClinicalDisorder    规则适用的疾病
```

**病因与风险：**

```
RiskFactor --INCREASES_RISK-->  ClinicalDisorder    危险因素 → 疾病
ClinicalDisorder --HAS_RISK_FACTOR--> RiskFactor    疾病 → 危险因素（反向查询用）
```

**评估与治疗：**

```
ClinicalDisorder --ASSESSED_BY-->       Scale               推荐评估量表
ClinicalDisorder --TREATED_BY-->        Treatment           推荐治疗方案
Scale            --MEASURES-->           ClinicalDisorder    量表适用的疾病
```

#### 边上的关键字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `prevalence` | float | 该症状 / 因素在此病中的出现率（初始来自文献，后由反哺更新）|
| `required` | bool | 是否为必要诊断条件 |
| `source_doc` | string | 来源文档文件名（溯源用）|
| `extraction_confidence` | float | LLM 提取置信度 |
| `last_updated_at` | timestamp | 最近一次被反哺更新的时间 |

#### 从 140+ 文档批量提取的 pipeline

两步构建，不混在一起：

```
Step 1：导入 SNOMED-CT 症状子集（一次性）
  从 SNOMED-CT 导出精神健康相关 Clinical Finding 概念
  → 批量写入 TuGraph clinical_kb 的 ClinicalSymptom 节点
  → canonical_id = SNOMED 概念 ID，无需人工定义

Step 2：从 140+ 文档提取关系和元数据（每次文档更新时）
  for each document:
    1. 按章节切分（概述 / 病因 / 诊断标准 / 辅助检查 / 治疗 / 量表……）
    2. LLM 提取结构化 JSON：
       {
         disorder:    "勃起功能障碍",
         icd11_code:  "F52.2",
         symptoms: [
           { snomed_id: "xxx", name: "勃起障碍", prevalence: 1.0, required: true }
         ],
         diagnostic_criteria: [
           { standard: "DSM-5", text: "持续≥3个月，≥75%性活动中出现" }
         ],
         risk_factors: [
           { name: "糖尿病", factor_type: "biological" },
           { name: "抗抑郁药(SSRIs)", factor_type: "pharmacological" }
         ],
         treatments:    ["PDE5抑制剂", "认知行为治疗", "真空勃起装置"],
         scales:        ["IIEF-5", "EHS", "PHQ-9"],
         comorbidities: ["焦虑症", "抑郁症"]
       }
    3. symptoms 里的 snomed_id 与 Step 1 已有节点匹配，建立边
       ClinicalDisorder --HAS_SYMPTOM--> ClinicalSymptom（SNOMED 节点）
    4. 无法匹配的症状（SNOMED-CT 未收录）→ 新建 ClinicalSymptom 节点，标记来源为文档
```

**去重策略**：`ClinicalSymptom` 以 `snomed_id` 为主键 Upsert；`ClinicalDisorder --HAS_SYMPTOM--> ClinicalSymptom` 边已存在则更新 `prevalence`，不存在则新建。

### 文档提取产物：logic_rules / actions 如何落位

nano-ontoprompt 从文档提取的四类产物，在 Foundry Ontology 中落位如下：

| 提取 JSON 字段 | Ontology 落位 | 存储层 | 说明 |
|---|---|---|---|
| `entities` | Reference Object（ClinicalDisorder 等） | clinical_kb | 医学概念节点 |
| `relations` | Reference Link（HAS_SYMPTOM 等） | clinical_kb | 概念间关系 |
| `logic_rules` | **ClinicalRule** | clinical_kb | 指南条文，`--APPLIES_TO-->` 绑定 Reference Object |
| `actions` | **Function 元数据** | Ontology Manager DB | 描述 + 触发条件，**不是** clinical 侧 executable |

#### logic_rules → ClinicalRule

```
提取：
  { "name_cn": "GAD诊断规则",
    "formula": "IF 焦虑症状持续 duration > 6个月 AND 无法控制 THEN 符合GAD",
    "linked_entities": ["广泛性焦虑障碍", "焦虑症状"] }

落位：
  ClinicalRule {
    formula: "IF ...",
    triggers_function: null,           // 纯诊断约束，不触发 Function
    linked_entities: [Disorder_id, Symptom_id]
  }
  ClinicalRule --APPLIES_TO--> ClinicalDisorder(GAD)
  ClinicalRule --APPLIES_TO--> ClinicalSymptom(焦虑)
```

```
提取：
  { "name_cn": "自杀危机干预规则",
    "formula": "IF 症状==自杀意念 OR 风险==高 THEN 立即干预",
    "linked_entities": ["自杀意念"] }

落位：
  ClinicalRule {
    formula: "IF ...",
    triggers_function: "person.flagCrisisSignal"   // 指向 Person Function
  }
  ClinicalRule --APPLIES_TO--> ClinicalSymptom(自杀意念)
```

#### actions → Function 元数据（绑 Person，不绑 Disorder）

```
提取：
  { "name_cn": "触发紧急随访",
    "execution_rule": "IF 检测到自杀风险 THEN 立即通知",
    "function_code": "def trigger_emergency_followup(context): ...",
    "linked_logic_names": ["自杀危机干预规则"] }

落位：
  ① Ontology Manager（FunctionDef）：
     {
       name: "person.flagCrisisSignal",
       objectType: "Person",
       description: "触发紧急随访（来源：触发紧急随访）",
       confirmLevel: AutoExecute
     }
  ② 代码注册 runner（发版）：
     registerRunner("person.flagCrisisSignal", fnFlagCrisisSignal)
  ③ ClinicalRule.triggers_function = "person.flagCrisisSignal"
  ④ 提取的 function_code 仅作 runner 实现参考，不存入 clinical_kb
```

#### 运行时：谁驱动谁

```
SymptomReported（Event）
  → ClinicalRuleEvaluator（subscriber）
      读 Person 的 SymptomReports
      沿 REFERS_TO 找 ClinicalSymptom
      匹配 ClinicalRule
      IF rule.triggers_function 非空
        → 调用 Person Function（Foundry 标准路径）
  → function_runs 审计
  → Event Bus 副作用（告警、随访……）
```

**不应做的**：把 `function_code` 绑在 ClinicalDisorder 实体上，等 patient 数据去「映射触发」——这违反 Foundry 的 Operational Object 驱动模型。

### 存储选型：MongoDB 还是图数据库

#### 默认选择：MongoDB 足够

`ontology_objects`（节点）+ `graph_edges`（边）都存 MongoDB。

选 MongoDB 的原因：
- Object 的 `properties` 是 schema-free 的，Person / SymptomReport / LifeEvent 属性结构完全不同，BSON document 天然适配
- 边上的扩展字段（`severity`、`confidence`……）同理，不同 `rel_type` 的边属性结构不一样
- 精神健康筛查的图查询基本是 **1-2 跳**（「找某 Person 的所有 SymptomReport」「SymptomReport 的 REFERS_TO ClinicalSymptom」），MongoDB 完全够用

#### 何时引入图数据库（Neo4j / TuGraph）

出现以下需求时再引入，不要提前引入：

- 需要 3 跳以上路径查询（如「从创伤事件出发，沿因果链找所有关联风险指标」）
- 需要图算法（PageRank、社区发现、最短路径）
- `graph_edges` 查询成为性能瓶颈（通常在千万级边以上）

#### 引入图数据库后的架构（仅针对 patient_graph）

引入图数据库后，**不替换 MongoDB，而是叠加**：

```
patient_graph 写入路径：
  应用 → MongoDB（ontology_objects + graph_edges）← Source of Truth
                   ↓ Change Stream 异步同步
                TuGraph（patient_graph 实例）← 深度查询层

patient_graph 读取路径：
  1-2 跳查询       → 直接查 MongoDB（延迟低）
  深度路径 / 图算法 → 查 TuGraph
```

**关键原则**：此规则仅适用于 patient_graph。MongoDB 是患者数据的 Source of Truth，TuGraph 是查询加速层，通过 Change Stream 异步同步，最终一致（通常延迟 < 1s）。

**clinical_kb 不适用此规则**：临床知识图从文档批量提取，TuGraph 本身就是它的 Source of Truth，无需在 MongoDB 中保留副本。两者各自的主存不同：

```
patient_graph：MongoDB 主存 → TuGraph 只读副本
clinical_kb：  TuGraph 主存（直接写入，不经 MongoDB）
```

不选择「图数据库作为 patient_graph 主存」的原因：图数据库的节点 properties 通常是强类型的，不适合 schema-free 的 Object 属性；而且 Object 节点的文档式查询（按属性过滤）仍需要 MongoDB，无法完全去掉。

| 方案 | 适用阶段 | 运维复杂度 |
|---|---|---|
| 纯 MongoDB | 初期，< 百万级边，1-2 跳查询 | 低 |
| MongoDB 主存 + 图数据库只读层 | 需要深度图查询或图算法时 | 中 |
| 图数据库作为主存 | 不推荐（属性灵活性差） | 高 |

### 两图分离与反哺架构

#### 分离的是存储与 ACL，不是 Ontology 概念

两个图实例，各自 Source of Truth 不同，但 **Object 类型、Link 类型、Function Registry 在同一套 Ontology 定义中**：

```
Ontology 定义（统一）
  ├── Reference ObjectTypes：ClinicalDisorder, ClinicalSymptom, ClinicalRule …
  ├── Operational ObjectTypes：Person, SymptomReport, Session …
  ├── LinkTypes：REFERS_TO, REPORTS, HAS_SYMPTOM, APPLIES_TO …
  └── FunctionDefs：person.recordSymptom, person.flagCrisisSignal …（只绑 Operational）

物理存储（分离）
  ├── clinical_kb（TuGraph）← Reference Object 实例
  └── patient_graph（MongoDB）← Operational Object 实例
```

#### 为什么患者图和知识图必须是两个独立图实例

两个图，各自有各自的 Source of Truth，存储层不共享：

```
clinical_kb（TuGraph 实例）
  Source of Truth：TuGraph 本身
  来源：140+ 文档批量提取，极少更新
  访问：全系统只读

patient_graph（TuGraph 实例）
  Source of Truth：MongoDB（ontology_objects + graph_edges）
  来源：对话实时写入，所有用户共用一个实例
  访问：按 user_id 隔离，ACL 由应用层负责
```

`patient_graph` 内部用 `user_id` 属性隔离不同用户，不为每个用户创建独立图实例（运维不可行）。

分离的三个原因：

| | clinical_kb | patient_graph |
|---|---|---|
| 数据性质 | 医学参考知识，类似教科书 | 用户 PII，高度敏感 |
| 更新频率 | 极低，文档更新时才重建 | 高，每轮对话写入 |
| Source of Truth | TuGraph | MongoDB |

#### 跨层查询：REFERS_TO 作为正式 Link

两图存储分离，跨层关联是 Ontology 内的 `SymptomReport --REFERS_TO--> ClinicalSymptom`，评估在 Event Bus 层完成：

```
1. 查 patient_graph（Operational）：
   user_123 的 SymptomReports → canonical_ids

2. 查 clinical_kb（Reference）：
   ClinicalSymptom 匹配 → ClinicalDisorder 覆盖度
   ClinicalRule 匹配 → triggers_function

3. Event subscriber 调用 Person Function（若规则命中）
```

staging 反哺时也直接写 TuGraph clinical_kb，不经过 MongoDB：

```
clinical_staging（MongoDB）→ 审核通过 → 直接更新 TuGraph clinical_kb
```

#### 患者数据 → 知识库的反哺回路

核心原则：**患者个体数据永远不直接写入 clinical_kb，中间必须经过匿名聚合和人工审核。**

```
┌─────────────────┐   定期聚合 job    ┌──────────────────┐
│  patient_graph  │ ────────────────▶ │ clinical_staging │
│  (原始个体数据)  │   匿名统计结果     │  (待审核发现)     │
└─────────────────┘                   └────────┬─────────┘
                                               │ 临床医生审核确认
                                               ▼
                                      ┌──────────────────┐
                                      │   clinical_kb    │
                                      │  (知识图谱主库)   │
                                      └──────────────────┘
```

`clinical_staging` 是质量门控层，防止噪声数据污染知识库。

#### 四类可反哺的内容

**① 边权重校准**

`clinical_kb` 中 `Disorder --HAS_SYMPTOM--> Symptom` 的 `prevalence` 初始值来自文献。真实数据可以将其收敛到你的用户群真实分布：

```
聚合查询（patient_graph）：
  被识别为 depression_possible 的用户中，同时报告 insomnia 的比例？

→ 更新 clinical_kb（经审核后）：
  Depression --HAS_SYMPTOM--> Insomnia { prevalence: 0.73 }
  （从文献值 0.65 → 真实观测值 0.73）
```

**② 发现文献中没有的症状共现模式**

```
挖掘 patient_graph：
  symptom:social_withdrawal 与 symptom:appetite_change 共现率 81%
  但 clinical_kb 中不存在这条 CO_OCCURS_WITH 边

→ 写入 clinical_staging，标记「待临床验证的新发现」
→ 临床审核通过后合并进 clinical_kb
```

**③ 诊断准确率反馈**

```
系统推荐：user_123 可能是 GAD（置信度 0.78）
临床医生确认：正确

→ staging 记录（匿名化，只保留 canonical_id）：
  {
    recommended:  "GAD",
    confirmed:    true,
    symptom_ids:  ["symptom:worry", "symptom:tension"],
    confidence:   0.78
  }

→ 积累足够样本后，调整匹配阈值和权重
```

**④ 风险因素真实预测力验证**

文献描述的危险因素在你的用户群中实际相关性如何？真实数据可以对 `RiskFactor` 重要性排序，使推理结果更贴近实际人群。

#### staging 层的数据结构

```
clinical_staging 集合（MongoDB）：
{
  finding_type:  "co_occurrence" | "prevalence_update" | "diagnostic_feedback",
  status:        "pending" | "approved" | "rejected",
  created_at:    timestamp,
  reviewed_by:   string,        // 临床医生 ID
  reviewed_at:   timestamp,
  payload: {
    // finding_type 决定具体结构
    from_canonical_id: "symptom:social_withdrawal",
    to_canonical_id:   "symptom:appetite_change",
    observed_rate:     0.81,
    sample_size:       342,     // 样本量，不含任何个人信息
  }
}
```

`sample_size` 是门槛过滤条件：样本量不足（如 < 50）的发现不进入 staging，避免小样本噪声。

### 生产级上线 Checklist

双图分离架构**可以作为精神科健康筛查的生产级底层骨架**，但架构设计 ≠ 可上线产品。以下 checklist 按优先级排列，是上线前必须逐项过关的门槛，而非可选优化。

#### 产品定位（上线前必须明确）

```
✓ 定位：精神健康筛查辅助 / 智能分诊引导
✗ 禁止：输出「你得了 XX 病」类诊断结论

输出格式：
  - 风险信号（low / moderate / high / urgent）
  - 量表覆盖度（PHQ-9 item 3/9 已覆盖）
  - 建议关注方向（「建议进一步评估抑郁相关维度」）
  - 证据链（哪些症状、持续多久、来自哪几次会话）
  - 免责声明（非诊断工具，最终决策由持证临床人员做出）
```

#### 一、架构与工程（P0）

| # | 检查项 | 过关标准 |
|---|---|---|
| 1 | patient_graph 最小闭环 | `recordSymptom` → MongoDB 写入 → `getAssessmentFocus` 可读 |
| 2 | 跨会话 Upsert | 同一 Person 多次对话，症状边正确合并（`reported_count`、时间戳更新） |
| 3 | canonical_id 规范化 | 口语 → SNOMED canonical_id，保留 `raw_text` 溯源 |
| 4 | clinical_kb 版本管理 | 知识图有版本号，更新可回滚，变更有 changelog |
| 5 | 跨图推理在应用层 | patient canonical_ids + clinical_kb Disorder 匹配，不跨库 join |
| 6 | function_runs 全量审计 | 每次 Observation / 推理调用有完整记录 |
| 7 | user_id 隔离 | patient_graph 所有查询强制带 user_id 过滤 |

TuGraph 对 patient_graph **不是 P0**——MongoDB 1-2 跳查询足够，图数据库仅在深度路径查询成为瓶颈时再引入。

#### 二、临床有效性（P0，最大风险）

| # | 检查项 | 过关标准 |
|---|---|---|
| 8 | 对话提取准确率 | 人工标注 ≥ 100 例对话，Symptom 提取 F1 ≥ 0.80 才开放自动路由 |
| 9 | 规范化准确率 | 口语 → canonical_id 映射，临床专家抽检 ≥ 50 例，准确率 ≥ 0.90 |
| 10 | clinical_kb 临床审核 | 每条 Disorder–Symptom 关系经持证精神科医师 sign-off |
| 11 | 量表对照验证 | 系统 PHQ-9 估计分 vs 用户自填 PHQ-9，Pearson r ≥ 0.75 |
| 12 | 方向判断验证 | `possible_concerns` 排序 vs 临床医生判断，Top-3 命中率 ≥ 0.70 |
| 13 | 提取监控 | 生产环境持续采样，提取准确率下降 > 5% 自动告警 |

文档已指出：**提取层是整个设计最脆弱的环节**（见「三层漏斗的三个盲区 · 盲区 2」）。架构再完美，提取错了推理就没有意义。

#### 三、安全与危机响应（P0，一票否决）

| # | 检查项 | 过关标准 |
|---|---|---|
| 14 | 危机独立旁路 | `flagCrisisSignal()` 不依赖漏斗路由，每轮常驻检测 |
| 15 | 规则引擎兜底 | LLM 不可用或超时时，关键词规则层仍可触发危机告警 |
| 16 | 人工 escalation SLA | urgent 级别 → 5 分钟内人工坐席响应或转接危机热线 |
| 17 | PHQ-9 第 9 项优先 | 自伤/自杀意念维度未覆盖时，不受漏斗顺序约束，优先补充 |
| 18 | 危机误报/漏报复盘 | 每月统计 crisis 触发率、人工确认率、漏报案例 |
| 19 | 降级策略 | LLM 整体不可用时，系统仍可完成基础量表引导（非纯对话模式） |

仅靠 prompt 注入危机检测指令**不够**——生产必须有独立规则层 + 人工 escalation + 第三方热线集成。

#### 四、合规与隐私（P0）

| # | 检查项 | 过关标准 |
|---|---|---|
| 20 | 知情同意（Consent） | 用户明确 consent：数据类型、保留期、AI 推断声明、非诊断免责 |
| 20a | Consent 与 AutoExecute 挂钩 | Observation Function 上线前确认 consent 覆盖该数据类型（见决策 4） |
| 20b | Consent 版本管理 | 已 consent 版本有记录；consent 内容变更需重新获取同意 |
| 21 | 数据加密 | patient_graph 静态加密（AES-256）+ 传输 TLS 1.2+ |
| 22 | 访问审计 | 谁、何时、为何访问了哪个用户的数据，日志不可篡改 |
| 23 | 数据留存与删除 | 明确保留期限；用户撤回 consent 后 30 天内删除 PII |
| 24 | 跨境评估 | 若 LLM 调用境外 API，患者对话出境需完成合规评估 |
| 25 | 等保 / 分级 | 按《数据安全法》完成医疗健康数据分类分级 |
| 26 | 器械监管评估（SaMD） | 安全措辞：「识别到相关信号，建议咨询专业医生」「评估工具，非诊断」。以下措辞会触发监管：「诊断」「筛查结果表明你患有X」「建议用药」 |

双图分离**有助于**合规（clinical_kb 无 PII），但不替代上述机制。

#### 五、运维与可靠性（P1）

| # | 检查项 | 过关标准 |
|---|---|---|
| 27 | clinical_kb 热更新 | 知识图更新不影响正在进行的对话会话 |
| 28 | Symptom 词表内存索引 | 服务启动预加载，规范化延迟 < 50ms（P99） |
| 29 | 推理延迟 | `getAssessmentFocus()` P99 < 200ms |
| 30 | review 队列工作台 | 临床歧义 case 有人工审核界面，SLA ≤ 24h |
| 31 | 模型版本管理 | LLM 模型切换有 A/B 对比，可一键回滚 |
| 32 | 灾难恢复 | MongoDB 每日备份，RPO ≤ 24h，RTO ≤ 4h |

#### 六、反哺回路（P2，上线后迭代）

| # | 检查项 | 过关标准 |
|---|---|---|
| 33 | staging 质量门控 | 样本量 < 50 的发现不进入 staging |
| 34 | 反哺审核流程 | 临床医生 approve/reject，reject 有原因记录 |
| 35 | prevalence 更新可追溯 | 每次 clinical_kb 边权重变更关联 staging 记录 |

反哺是离线优化回路，**不影响当次对话**，可在核心功能稳定后再启用。

#### 上线决策矩阵

| 维度 | 设计文档现状 | 生产要求 | 优先级 |
|---|---|---|---|
| 架构分层 | ✅ 完整 | 按文档实现 | P0 |
| 临床知识质量 | ⚠️ 依赖 LLM 提取 | 专家 sign-off + 版本管理 | P0 |
| 对话提取准确率 | ⚠️ 最大未知数 | F1 ≥ 0.80 + 持续监控 | P0 |
| 危机响应 | ⚠️ 有设计，无完整闭环 | 规则兜底 + 人工 SLA | P0 |
| 合规隐私 | ⚠️ 仅提及 | 加密 + 审计 + consent | P0 |
| 临床验证 | ❌ 未涉及 | 量表对照 + 方向判断验证 | P0 |
| 工程实现 | ❌ 待落地 | 最小闭环 + 四阶段实施 | P0 |
| 反哺优化 | ✅ 设计完整 | 上线后迭代 | P2 |

#### 推荐实施顺序

```
Phase 0（4-6 周）：最小闭环
  recordSymptom / recordLifeEvent → MongoDB
  getAssessmentFocus → 驱动对话
  flagCrisisSignal → 规则兜底 + 告警

Phase 1（4-6 周）：临床验证
  100 例标注对话 → 提取准确率评估
  clinical_kb v1 → 精神科医生审核
  PHQ-9 对照实验

Phase 2（4-6 周）：合规 + 灰度
  加密 / 审计 / consent 流程
  小范围灰度（≤ 500 用户）
  危机响应 SLA 验证

Phase 3（持续）：规模化 + 反哺
  开放自动路由（提取 F1 ≥ 0.80 后）
  clinical_staging 反哺回路
  多量表扩展（GAD-7、PCL-5……）
```

**Phase 0 的提取准确率数字，比 storage 选型更决定是否上线。**

### nano-ontoprompt 与筛查场景的分工

精神健康筛查是**在线多轮医患对话**；nano-ontoprompt 是**离线文档 → 结构化知识**的构建工具。二者相关，但不是同一个系统、同一个运行时。

#### 构建期 vs 运行期

| | 构建期（nano-ontoprompt） | 运行期（筛查对话系统） |
|---|---|---|
| **时机** | 离线，文档更新时 | 在线，每轮对话 |
| **输入** | 140+ 医学指南 PDF/DOCX | 用户自然语言 |
| **输出** | clinical_kb（概念、关系、规则） | patient_graph（SymptomReport、Session） |
| **Foundry** | 不需要 Function / Event Bus | Person Function + Event Bus |
| **产品形态** | 临床知识库构建 | Foundry Operational Ontology |

```
构建期（一次性 / 低频）：
  医学文档 → nano-ontoprompt 提取 → 临床专家审核 → 导出 clinical_kb

运行期（每轮对话）：
  用户说话 → Person Function 写 patient_graph
          → RuleEvaluator consult clinical_kb（只读查询）
          → LLM 生成下一问 / 危机告警
```

**对话运行时不会**打开 Entity Tab、不会对用户对话再跑文档提取。  
**对话运行时仍会** consult 构建期产出的 clinical_kb（症状规范化、方向匹配、危机规则）。

#### nano-ontoprompt 在筛查里的价值

| 能力 | 筛查场景 | 说明 |
|---|---|---|
| 实体 / 关系 / 知识图谱 Tab | ✅ 必要 | clinical_kb 构建 |
| 逻辑规则 Tab | ✅ 必要 | 落位为 ClinicalRule，运行时 consult |
| LLM 提取 + AI 审查 | ✅ 必要 | 批量结构化 + 质量门控 |
| 动作 Tab | ⚠️ 改用法 | 映射为 Person Function 元数据，不是 KB executable |
| 导出 JSON / TuGraph | ✅ 必要 | 交付给筛查系统只读加载 |
| 多轮对话 / patient_graph | ❌ 不在本平台 | 需另建筛查运行时 |

#### 提取的「医疗本体」是什么

提取结果**适合**筛查，但角色是 **clinical_kb 原料**，不是可执行 Foundry 本体：

- **是**：带 schema 的结构化知识库（Disease、Symptom、HAS_SYMPTOM、ClinicalRule）
- **不是**：多轮对话引擎；不是绑在 Disease 上的 Action 运行时
- **不必叫**「可执行医疗本体」；更准确的产品名是 **「临床知识库（Clinical KB）」**

#### 筛查运行时实际 consult 什么

```
lookupSymptom("睡不着")     → clinical_kb → snomed:193462001
matchDisorders([...ids])    → clinical_kb → [抑郁症, 双相…]
getClinicalRules(symptom)   → clinical_kb → triggers_function?
getAssessmentFocus()        → patient_graph only（不查 KB）
person.recordSymptom()      → patient_graph only（写）
```

#### 能否不做文档提取

| 方案 | 适用 |
|---|---|
| SNOMED 症状 + 手工几十条 ClinicalRule | 量表驱动的小范围筛查 |
| 仅 PHQ-9/GAD-7 条目，无疾病图谱 | 极简 MVP，失去方向匹配与共病推理 |
| 140+ 文档 + nano-ontoprompt 批量提取 | 有大规模指南库时的正确路径 |

没有 clinical_kb，筛查仍可跑（LLM + 量表），但失去规范化词表、疾病方向匹配、可审计的危机规则链。

#### 系统边界（推荐部署）

```
┌─────────────────────────────┐     导出 / API      ┌─────────────────────────────┐
│  nano-ontoprompt            │ ──────────────────▶ │  clinical_kb 服务（只读）     │
│  临床知识库构建              │                     │  TuGraph + Symptom 向量索引   │
│  实体 / 关系 / 规则 / 审查   │                     └──────────────┬──────────────┘
└─────────────────────────────┘                                    │ consult
                                                                   ▼
                                                    ┌─────────────────────────────┐
                                                    │  筛查对话系统（运行时）        │
                                                    │  patient Ontology            │
                                                    │  Person Function + Event Bus │
                                                    │  对话 Agent + RuleEvaluator  │
                                                    └─────────────────────────────┘
```

两个系统通过 **clinical_kb 导出接口** 连接，不共享数据库，不共用运行时。

#### 常见误解

| 误解 | 实际 |
|---|---|
| 提取的本体在对话里直接用 | 只在构建期用；运行时 consult 导出后的 KB |
| 362 条实体驱动每一轮提问 | 驱动对话的是 patient_graph + Coverage；KB 做方向/consult |
| 动作 Tab 的 Function 绑 Disease 执行 | Action 元数据 → 绑 Person 的 FunctionDef |
| 筛查不需要 nano-ontoprompt | 需要 **KB 构建能力**；可以是本平台，也可以是 SNOMED + 手工替代 |
| 对话中应再跑 LLM 文档提取 | 对话中是 Observation Function 写 patient_graph，不是文档提取 |

---

## 16. S1 扩展：多模态采集与分流路径建模

Section 15 描述的是「LLM 从自由对话中静默识别症状」的 Observation Function 模式。S1（全民心理健康智能分级筛查与分流系统）是另一种触发模式：**用户主动提交结构化答题、上传音视频，系统对每路信号独立打分后融合输出**，没有「LLM 从对话中推断」这一步。两种模式可以共存（S1 筛查结束后进入对话随访），但建模完全不同，需要独立设计。

### ScreeningSession 状态机

S1 的核心对象是 `ScreeningSession`（筛查会话），不是通用的 `Session`。状态机：

```
created
  │
  ▼
collecting              — 采集中（答题 + 各模态上传）
  │
  ▼
scoring                 — 模型打分与多模态融合决策
  │
  ├── completed         — 正常完成，写入 ScreeningResult + TriageRecommendation
  │
  └── crisis_escalated  — 危机并行触发（会话可同时标 completed）
             │
             ▼
       CrisisEvent 写入 + outbox_event → 外部通知链路
```

`crisis_escalated` 是并行路径（PRD BR-02），不是终态的替换：危机触发后会话继续 scoring，前端走危机流程，ScreeningResult 仍写入以备回溯。

### 新增 Object 设计（S1 Operational 层）

| Object | 说明 | 关键属性 |
|---|---|---|
| `ScreeningSession` | 一次多模态评估会话 | status, modalities_mask, consent_version, source（mini_program/app/web）|
| `ModalityCapture` | 单路模态采集资产 | modality_type（text/audio/video/scale）, asset_uri, quality_score, status（uploaded/scored/failed）|
| `ScreeningResult` | 多模态融合决策输出 | risk_level（low/moderate/high/urgent）, confidence, explain_json, triage_path |
| `TriageRecommendation` | 分流路径推荐 | path_type（self_help/community_followup/outpatient/crisis_channel）, resource_refs[], strategy_version |
| `CrisisEvent` | 危机检测命中事件 | trigger_type（keyword/threshold/model）, rule_id, modality_source, notify_status, escalation_chain |

**`ScreeningResult.explain_json` 结构（US-A2 AC1 要求 ≥1 条可解释要点）：**

```json
{
  "risk_level": "moderate",
  "confidence": 0.82,
  "key_signals": [
    { "modality": "scale", "signal": "PHQ-9 第3题（睡眠）得分≥2", "weight": 0.4 },
    { "modality": "text",  "signal": "频繁提及睡眠困难与情绪低落", "weight": 0.3 },
    { "modality": "audio", "signal": "语速减慢，声调偏低",          "weight": 0.3 }
  ],
  "explanation_text": "您近期在情绪和睡眠方面有一些值得关注的信号，建议进一步评估。",
  "disclaimer": "本评估为筛查辅助工具，不构成医学诊断。",
  "low_confidence": false
}
```

`explanation_text` 必须使用患者可读语言，不含医疗承诺表述。`disclaimer` 强制输出，不可省略。模型超时降级时 `low_confidence: true`。

### Link 设计（S1 专用）

```
Person           --INITIATED-->    ScreeningSession     用户发起一次筛查会话
ScreeningSession --HAS_ASSET-->    ModalityCapture      会话包含的各路模态资产
ScreeningSession --PRODUCED-->     ScreeningResult      会话输出的融合结果
ScreeningResult  --RECOMMENDS-->   TriageRecommendation 结果推荐的分流路径
ScreeningSession --TRIGGERED-->    CrisisEvent          危机检测命中事件（并行）
Person           --COMPLETED-->    ScreeningSession     历史筛查记录（权重衰减适用）
```

### Function 设计（S1 专用）

S1 的 Function **不是 Observation Function**——触发方是用户显式操作，结果对用户可见，不是 LLM 静默识别。

| Function | ConfirmLevel | 说明 |
|---|---|---|
| `screening.captureModality()` | AutoExecute（需 consent_scope） | 注册模态资产元数据并上传对象存储；未 consent 的模态返回 `consent_required`，不静默跳过 |
| `screening.submitResponse()` | AutoExecute | 提交量表答题，写 ScreeningSession |
| `screening.runFusion()` | AutoExecute（系统调用） | 触发多模态融合评分，写 ScreeningResult；模型超时时回退规则引擎并标记 `low_confidence: true` |
| `screening.recommendTriage()` | AutoExecute | 读 ScreeningResult + 策略配置（app_config）→ 写 TriageRecommendation；本函数不处理危机路径 |
| `screening.flagCrisis()` | AutoExecute（bypass consent，常驻独立） | 任意模态触发危机信号时调用；写 CrisisEvent → outbox_event；1 分钟内完成（PRD KPI），危机路径覆盖常规分流 |
| `screening.getHistory()` | AutoExecute（只读） | 返回该 Person 历史会话与脱敏结果 |

**`screening.captureModality()` 的 Consent 检查（与 Observation Function 的区别）：**

```
Observation Function：会话开始前已有全局 consent → 对话中静默执行
screening.captureModality()：实时检查对应模态的 consent scope

用户点击「开始语音采集」
  → checkConsent(scope="voice_capture")
  → 未 consent → 立即返回 {consent_required: true, missing_scope: "voice_capture"}
  → 前端弹出单独 consent 说明弹窗
  → 用户同意 → 更新 ConsentRecord → 重新调用 captureModality()
  → 用户拒绝 → modalities_mask |= AUDIO_UNAVAIL，降级继续
```

### 降级规则

PRD AC-1.03 要求：模态缺失时仍完成会话，`modalities_mask` 与 UI 提示一致。

```
模态      不可用原因         降级行为                                         modalities_mask
────────  ─────────────────  ───────────────────────────────────────────────  ───────────────────
audio     用户拒绝权限       仅量表+文本，页面提示「本次未使用语音分析」         AUDIO_UNAVAIL
audio     上传超时           回退规则引擎，explain_json 标 low_confidence       AUDIO_TIMEOUT
video     用户拒绝权限       仅量表+文本+音频（若已授权）                        VIDEO_UNAVAIL
video     光线/质量不达标     quality_score < 阈值 → 标 VIDEO_QUALITY，不参与融合 VIDEO_QUALITY
全模态    网络异常           仅量表（3分钟最小可行路径），会话仍完成              ALL_FALLBACK
```

`modalities_mask` 写入 ScreeningSession，前端根据 mask 值渲染对应提示（不可出现 mask=0 但 UI 提示模态缺失的不一致）。

### Event Schema：screening.submitted / screening.crisis

**`screening.submitted`**（会话正常完成时发布，S2 方案生成引擎消费）：

```json
{
  "event_type":      "screening.submitted",
  "session_id":      "ss_abc123",
  "user_id":         "u_xxx",
  "risk_level":      "moderate",
  "triage_path":     "outpatient",
  "modalities":      ["scale", "text", "audio"],
  "modalities_mask": 0,
  "confidence":      0.82,
  "low_confidence":  false,
  "occurred_at":     "2026-06-03T10:05:00Z"
}
```

**`screening.crisis`**（危机检测命中时发布，与会话完成事件并行，不互相等待）：

```json
{
  "event_type":   "screening.crisis",
  "session_id":   "ss_abc123",
  "user_id":      "u_xxx",
  "rule_id":      "crisis_keyword_v3",
  "trigger_type": "keyword",
  "modality":     "text",
  "crisis_type":  "suicidal_ideation",
  "occurred_at":  "2026-06-03T10:03:12Z"
}
```

**两事件的并行关系：**

```
答题过程中触发危机关键词
  → screening.flagCrisis()      → 发布 screening.crisis（即时，不等 scoring）
  → 前端：危机浮层全屏弹出，不可被普通广告遮挡（PRD US-A3 AC2）
  → 会话继续 scoring
  → screening.runFusion()
  → 会话状态 = crisis_escalated（+ completed，并行写）
  → 发布 screening.submitted（risk_level=urgent, triage_path=crisis_channel）

后端：两事件独立消费，互不阻塞
危机通知服务：只消费 screening.crisis，不等 screening.submitted
S2 方案引擎：只消费 screening.submitted，不处理危机逻辑
```

### 与 patient_graph 的关系

S1 ScreeningSession 是独立于 patient_graph Session 的对象，两者触发方式和数据结构完全不同：

| | S1 ScreeningSession | patient_graph Session（对话随访）|
|---|---|---|
| 触发方 | 用户主动进入筛查流程 | 开放对话，LLM 驱动 |
| 主要产出 | ScreeningResult + TriageRecommendation | SymptomReport + Coverage 状态 |
| Function 类型 | 用户显式触发，结果对用户可见 | Observation Function，静默写图 |
| 多模态 | 音频/视频/量表/文本，各路独立打分 | 仅文本，LLM 整体理解 |
| 与 clinical_kb 关系 | ScreeningResult 用 canonical_id 关联症状簇 | SymptomReport --REFERS_TO--> ClinicalSymptom |

两者通过 `Person` 节点关联：同一 Person 既可有 ScreeningSession，也可有对话 Session。报告层合并时以 ScreeningSession 的结构化结果为优先，对话 Session 的 SymptomReport 作为时序补充。

---

## 17. S4 扩展：病历草稿工作流建模

S4（临床医患对话智能叙事系统）的核心流程是：  
**录音 → ASR 流式转写 → 医疗实体识别 → 病历草稿生成 → 医生编辑/确认 → HIS 归档**

建模的三个难点：
1. **BR-01 状态机**：AI 生成内容进入正式 EMR 前须人工确认，confirmed 后 AI 不可自动覆盖
2. **HIS 写回跨系统边界**：归档步骤需要调外部 HIS，通过 Event Bus 解耦而非 Function 内直接调用
3. **ExplicitConfirm 而非 ClinicalReview**：医生确认自己的诊间内容，是同一人的显式操作，不需要第三方 sign-off

### 两个状态机：EncounterSession / MedicalRecordDraft

**EncounterSession（诊间会话）：**

```
created
  │
  ▼  encounter.start()
active                     — 实时 ASR 流式转写中
  │
  ├── paused               — encounter.pause()，可 resume() 恢复
  │       └──► active
  │
  ▼  encounter.end()
completed                  — 触发 encounter.generateDraft()
```

**MedicalRecordDraft（病历草稿）：**

```
draft                      — AI 生成，强制显示 AI 水印，不可归档
  │
  │  [医生在 diff 视图中编辑]  edit_count++，source 字段更新为 manual
  │
  ▼  encounter.confirmDraft()    ExplicitConfirm（医生显式点击签发）
confirmed                  — 医师责任区；AI 不可再自动覆盖（AC-4.02）
  │                           再次修改须走修订/追加，不能覆盖本行
  │
  ▼  encounter.archiveDraft()    ExplicitConfirm（仅 confirmed 状态可触发）
archived                   — 已推送 HIS/EMR；写审计；30 年不可删除（BR-04）
```

### 新增 Object 设计（S4 Operational 层）

| Object | PRD 库表 | 关键属性 |
|---|---|---|
| `EncounterSession` | encounter_session | status, clinician_id, patient_id, started_at, ended_at, audio_asset_uri |
| `TranscriptSegment` | encounter_transcript_segment | seq（严格递增）, text, speaker（clinician/patient）, entities_json, started_ms, ended_ms |
| `MedicalRecordDraft` | note_draft | status, template_id, content_json, model_version, ai_watermark, edit_count, confirmed_by, confirmed_at, archived_at |

**`content_json` 结构**（对齐《病历书写基本规范》门诊记录与 WS 445.2）：

```json
{
  "template_id":   "mental_health_outpatient_v2",
  "model_version": "claude-sonnet-4-6-20251001",
  "generated_at":  "2026-06-03T10:30:00Z",
  "sections": {
    "chief_complaint":    { "value": "情绪低落伴失眠3个月", "source": "asr_llm", "confidence": 0.91 },
    "present_illness":    { "value": "...",               "source": "asr_llm", "confidence": 0.85 },
    "past_history":       { "value": "...",               "source": "asr_llm", "confidence": 0.80 },
    "allergy_history":    { "value": "无已知药物过敏",     "source": "asr_llm", "confidence": 0.70 },
    "physical_exam":      { "value": "...",               "source": "manual",  "confidence": 1.0  },
    "mental_status_exam": { "value": "...",               "source": "asr_llm", "confidence": 0.88 },
    "diagnosis_text":     { "value": "广泛性焦虑障碍",    "source": "asr_llm", "confidence": 0.82,
                            "icd10": ["F41.1"] },
    "treatment_plan":     { "value": "...",               "source": "asr_llm", "confidence": 0.75 }
  }
}
```

`source` 字段区分 `asr_llm`（AI 生成）和 `manual`（医生手动填写），驱动 diff 视图高亮。  
`allergy_history` 不可省略——无过敏须显式写 `"无已知药物过敏"`，不能留空（用药安全要求）。

### Link 设计（S4 专用）

```
Clinician           --CONDUCTED-->    EncounterSession    医生主持本次诊间会话
Patient             --ATTENDED-->     EncounterSession    患者参与本次会话
EncounterSession    --HAS_SEGMENT-->  TranscriptSegment   按 seq 有序的转写片段
EncounterSession    --PRODUCED-->     MedicalRecordDraft  会话生成的病历草稿
Clinician           --CONFIRMED-->    MedicalRecordDraft  医生确认签发（confirmDraft 后写边）
MedicalRecordDraft  --REFERENCES-->   TranscriptSegment   草稿各段引用的原文 seq（diff 溯源）
```

`MedicalRecordDraft --REFERENCES--> TranscriptSegment` 是 diff 视图的数据基础：前端通过这组边拿到「草稿段落 → 原始转写片段」的对应关系并对照展示。

### Function 设计（S4 专用）

S4 的 Actor 是 **Clinician**（B 端医生站），不是 C 端 Person。

| Function | ConfirmLevel | 说明 |
|---|---|---|
| `encounter.start()` | AutoExecute | 创建 EncounterSession，开始接收 ASR 流 |
| `encounter.pause()` | AutoExecute | 暂停录音 → paused |
| `encounter.resume()` | AutoExecute | 恢复录音 → active |
| `encounter.end()` | AutoExecute | 结束会话 → completed；触发 generateDraft() |
| `encounter.submitSegment()` | AutoExecute（系统调用） | ASR 服务每完成一段后写入 TranscriptSegment；seq 严格递增（AC-4.01） |
| `encounter.extractEntities()` | AutoExecute（系统调用） | NLU 识别后更新 TranscriptSegment.entities_json；风险信号旁路触发告警 |
| `encounter.generateDraft()` | AutoExecute（系统调用） | LLM 生成 MedicalRecordDraft（status=draft），写 ai_watermark=true + model_version；发布 `emr.note.draft_ready` |
| `encounter.confirmDraft()` | **ExplicitConfirm** | 医生逐段核对后显式签发；→ confirmed；写 confirmed_by + confirmed_at；此后 AI 不可自动覆盖 |
| `encounter.archiveDraft()` | **ExplicitConfirm** | 推送 HIS/EMR；→ archived；仅 confirmed 状态可触发 |

**为什么 `confirmDraft()` 用 ExplicitConfirm 而非 ClinicalReview：**

```
ClinicalReview  — 需要另一个角色（远程临床坐席）在不同系统里 sign-off
                  适用：C 端用户不应直接看到的结论（如 S1 筛查报告）

ExplicitConfirm — 同一个 Clinician 对自己诊间草稿显式签发
                  要求：必须有物理点击操作，后端强制执行（US-D3 AC2）
                  不能：页面停留一段时间后自动归档
```

### `encounter.archiveDraft()` 的 HIS 写回路径

HIS 适配层是 Event Bus 的订阅者，Function 主流程不直接调用 HIS API：

```
encounter.archiveDraft(draft_id)
    │
    ├── 前置检查：status == confirmed（否则返回错误，不执行）
    │
    ├── 写权威数据：note_draft.status → archived，archived_at = now()
    │
    ├── 写 Graph 边：Clinician --CONFIRMED--> MedicalRecordDraft
    │
    ├── 发布事件（不直接调 HIS）：
    │       emr.note.archive_requested { draft_id, content_json, clinician_id, patient_id }
    │           ↓
    │       HIS 适配订阅者：content_json → HIS 接口格式 → 调用 HIS 写入 API
    │
    ├── 写 function_runs 审计：actor_id, action, resource_id, timestamp（US-D3 AC2）
    │
    └── return FunctionResult{ Complete, Output{ ObjectRef("MedicalRecordDraft", draft_id) } }
```

HIS 调用失败不回滚 `archived` 状态（已写入平台侧），但触发告警并写补偿记录，由运维手动重推。

### Event Schema（emr.note.*）

**`emr.note.draft_ready`**（草稿生成后，通知医生工作台）：

```json
{
  "event_type":     "emr.note.draft_ready",
  "draft_id":       "nd_xyz789",
  "session_id":     "es_abc456",
  "clinician_id":   "c_001",
  "patient_id":     "p_999",
  "template_id":    "mental_health_outpatient_v2",
  "model_version":  "claude-sonnet-4-6-20251001",
  "sections_count": 8,
  "occurred_at":    "2026-06-03T10:31:00Z"
}
```

**`emr.note.archive_requested`**（归档请求，HIS 适配订阅者消费）：

```json
{
  "event_type":   "emr.note.archive_requested",
  "draft_id":     "nd_xyz789",
  "clinician_id": "c_001",
  "patient_id":   "p_999",
  "content_json": { "...": "..." },
  "confirmed_at": "2026-06-03T10:35:00Z",
  "occurred_at":  "2026-06-03T10:36:00Z"
}
```

### Diff 视图的数据依据

PRD 幻觉防护要求对话原文与生成段落对照展示，数据来源是 `REFERENCES` 边：

```
草稿段落 "present_illness"
  --REFERENCES--> TranscriptSegment{seq: 3}, TranscriptSegment{seq: 7}, TranscriptSegment{seq: 12}

前端渲染：
  左侧（原文）   TranscriptSegment.text 按 seq 拼接（speaker 标注）
  右侧（草稿）   content_json.present_illness.value
  高亮规则：     source == "asr_llm" → 蓝色（AI 生成，待核对）
                 source == "manual"  → 无高亮（医生已手动填写）

医生修改右侧后：
  content_json.present_illness.source = "manual"
  note_draft.edit_count++
  高亮消失 → 视觉上表示该段已核对
```

### 生产上线硬约束（S4）

| 约束来源 | 具体要求 | ONTOLOGY.md 对应 |
|---|---|---|
| BR-01 | AI 生成进正式 EMR 前须人工确认 | `confirmDraft()` ExplicitConfirm → confirmed 状态 |
| BR-04 | 病历保留 30 年 | archived 状态不可删除，归档写 function_runs |
| AC-4.01 | transcript 按 seq 有序可拼接 | TranscriptSegment.seq 严格递增，写入时校验 |
| AC-4.02 | confirmed 后不可被 AI 自动覆盖 | `generateDraft()` 执行前检查 status ≠ confirmed |
| US-D3 AC1 | 草稿须标 AI 水印 | note_draft.ai_watermark=true + model_version |
| US-D3 AC2 | 确认/归档产生审计记录 | function_runs 全量写入，不可抵赖 |
| WS 445.2 | allergy_history 不可省略 | content_json.allergy_history 必填，无过敏须显式填写 |
| 《电子病历管理规范》 | AI 生成与医师修改内容须可区分 | content_json.sections[].source 字段 |

---

## 18. Actor 体系与租户隔离

当前 ONTOLOGY.md 只有 `Person`（C 端）和 `Clinician`（B 端医生）两类 Actor。PRD 定义了 7 类角色，`authorizeFunction()` 必须知道「谁在操作」才能做数据范围校验（仅靠角色名称不够，还需要「这个角色是否有权访问这条具体数据」）。`tenant_id` 是全平台横切约束，每个写操作、每条查询都必须携带。

### 7 类角色定义

PRD 的权限模型是 RBAC（角色-权限-数据范围），一个 User 账号可以同时持有多个角色（如医生兼任质控）。

| 角色常量 | 说明 | 主要端 | 数据范围 |
|---|---|---|---|
| `PUBLIC_USER` | 公众，C 端，可匿名筛查 | C 端 | 仅自己的数据 |
| `PATIENT` | 有治疗关系的患者，经咨询师/医生指派后激活 | C 端 | 仅自己的数据 |
| `GUARDIAN` | 家属/监护人，代理未成年人操作 | C 端 | 被监护人的数据（需授权记录） |
| `COUNSELOR` | 咨询师/治疗师，管理 S2 方案 | B 端 | 仅 **指派** 给自己的用户 |
| `CLINICIAN` | 医生（精神科为主），S4 诊间叙事 | B 端 | 仅 **授权** 的患者 |
| `QUALITY_CONTROLLER` | 质控人员，事中/事后质控 | B 端 | 脱敏聚合报表，不可见个体 PII |
| `ADMIN` | 管理员，配置与审计 | 管理后台 | 全租户（所有操作写审计） |

`PUBLIC_USER` 和 `PATIENT` 可能是同一自然人：用户匿名完成 S1 筛查（`PUBLIC_USER`），咨询师分配方案后账号绑定为 `PATIENT`（写入 patient_link，建立治疗关系）。两个角色通过 EMPI 关联，Object 不合并。跨子系统的患者 ID 对齐（S1 匿名 ID → S2 患者 ID → S4 诊间患者记录）依赖平台 EMPI 服务（PRD D-03），属于信息科与 HIS 厂商协商范围，不在本文设计。

### 扩展权限矩阵

在 PRD 原始矩阵基础上，覆盖 Sections 15–17 所有 Function：

| Function | PUBLIC | PATIENT | GUARDIAN | COUNSELOR | CLINICIAN | QC | ADMIN |
|---|---|---|---|---|---|---|---|
| `screening.captureModality` | ✓ | ✓ | ✓ 代理 | ✗ | ✗ | ✗ | ✗ |
| `screening.submitResponse` | ✓ | ✓ | ✓ 代理 | ✗ | ✗ | ✗ | ✗ |
| `screening.flagCrisis` | 系统 | 系统 | 系统 | 系统 | 系统 | ✗ | ✗ |
| `person.recordSymptom`（Observation） | ✓ consent | ✓ consent | ✗ | ✗ | ✗ | ✗ | ✗ |
| `person.generateScreeningReport` | ✗ | ✗ | ✗ | ✓ 指派 | ✓ 授权 | 脱敏 | 审计 |
| `plan.generate` | ✗ | ✗ | ✗ | 系统触发 | 系统触发 | ✗ | ✗ |
| `plan.confirm / adjust` | ✗ | ✗ | ✗ | ✓ 指派 | ✓ 授权 | ✗ | ✗ |
| `task.complete / feedback.submit` | ✗ | ✓ 自己 | ✓ 代理 | ✗ | ✗ | ✗ | ✗ |
| `encounter.start/end/pause/resume` | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ |
| `encounter.confirmDraft` | ✗ | ✗ | ✗ | ✗ | ✓ 自己的会话 | ✗ | ✗ |
| `encounter.archiveDraft` | ✗ | ✗ | ✗ | ✗ | ✓ 自己的会话 | ✗ | ✗ |

### authorizeFunction() 实现模式

校验分两层，顺序不可颠倒：

```
authorizeFunction(actor, fn, params):

  第一层：角色检查（纯内存，无 DB 查询）
    if !roleAllowed(actor.Roles, fn.Name) → return ErrForbidden("role not permitted")

  第二层：数据范围检查（按 function 逐 case，走 Resolver cache）
    case "person.generateScreeningReport", "plan.confirm", "plan.adjust":
      if actor.HasRole(COUNSELOR) && !isAssigned(actor.ID, params.person_id)
        → ErrForbidden("not assigned")
      if actor.HasRole(CLINICIAN) && !isAuthorized(actor.ID, params.person_id)
        → ErrForbidden("not authorized")

    case "encounter.confirmDraft", "encounter.archiveDraft":
      draft   = resolver.Get("MedicalRecordDraft", params.draft_id)
      session = resolver.Get("EncounterSession", draft.SessionID)
      if session.ClinicianID != actor.ID → ErrForbidden("not your session")

    case "task.complete", "feedback.submit":
      task = resolver.Get("CareTask", params.task_id)
      plan = resolver.Get("CarePlan", task.PlanID)
      if plan.SubjectUserID != actor.UserID → ErrForbidden("not your plan")
```

`roleAllowed()` 查内存角色表，无 DB 查询；数据范围检查走 Resolver（cache hit 无额外 DB 查询）。两层都失败时返回 403，不区分「角色不对」还是「数据不属于你」——防止信息泄露。

### tenant_id 贯穿全链路

PRD AC-P.01：任意业务写操作均可关联 `tenant_id`；跨租户访问返回 403。`tenant_id` 从认证层（JWT claims）提取，**不允许客户端在请求体中传入**。

**ontology_objects 写入：**

```json
{
  "tenant_id":  "hospital_a",
  "ref":        { "type": "ScreeningSession", "id": "ss_abc" },
  "properties": { "..." }
}
```

索引扩展：原有 `{ "ref.type": 1, "ref.id": 1 }` 唯一索引前加 `tenant_id`，  
变为 `{ "tenant_id": 1, "ref.type": 1, "ref.id": 1 }` 唯一。

**graph_edges 写入：**

```json
{
  "tenant_id": "hospital_a",
  "from_id":   "u_001",  "from_type": "Person",
  "to_id":     "ss_abc", "to_type":   "ScreeningSession",
  "rel_type":  "INITIATED"
}
```

三个原有索引全部加 `tenant_id` 前缀：  
`(tenant_id, from_id, rel_type, to_type)` / `(tenant_id, to_id, rel_type)` / `(tenant_id, from_id, to_id, rel_type)` 唯一。

**function_runs 写入：**

```json
{
  "tenant_id": "hospital_a",
  "actor_id":  "c_001",
  "function":  "encounter.confirmDraft",
  "params":    { "draft_id": "nd_xyz" },
  "status":    "executed"
}
```

**Executor 层隔离（禁止客户端传入 tenant_id）：**

```go
func (e *Executor) Execute(ctx Context, input Input) Result {
    tenantID := ctx.TenantID()   // 只从 JWT/Session 提取，不读 input.Params
    if tenantID == "" {
        return Result{Err: ErrUnauthorized("missing tenant")}
    }
    actor := e.resolver.GetUser(ctx, tenantID, input.ActorID)
    // 后续所有 DB 查询强制携带 tenantID
}
```

---

## 19. S2 扩展：干预方案建模

S2（循证精准干预方案智能匹配与动态个性化优化平台）是 S1 筛查的下游：`screening.submitted` 事件触发方案草案生成，咨询师确认后推送给患者执行，患者反馈驱动疗效闭环。

### 两个状态机：CarePlan / CareTask

**CarePlan（干预方案）：**

```
draft              — AI 生成结构化草案，咨询师/医生未确认
  │
  ▼  plan.confirm()    ExplicitConfirm
active             — 已激活，任务按计划推送
  │
  ├── paused       — plan.pause()，暂停推送（如患者住院）
  │       └──► active  plan.resume()
  │
  ├── completed    — plan.complete()，疗程正常结束
  └── discontinued — plan.discontinue()，中途终止（原因必填）
```

**CareTask（单个任务实例）：**

```
scheduled          — 已排期，未到 due_at
  │
  ▼  （时间到达）
due                — 到期，等待患者操作
  │
  ├── completed    — task.complete()    AutoExecute
  ├── skipped      — task.skip()        AutoExecute
  └── overdue      — 系统定时检查，超期触发提醒（FR-2.07）
```

### 新增 Object 设计（S2 Operational 层）

| Object | PRD 库表 | 关键属性 |
|---|---|---|
| `CarePlan` | care_plan | status, subject_user_id, owner_id（counselor/clinician）, version, source_screening_id |
| `CarePlanItem` | care_plan_item | item_type（exercise/reading/scale/medication_reminder）, payload_json, evidence_refs[], sequence |
| `CareTask` | care_task | plan_id, item_id, due_at, status, completed_at, reminder_count |
| `CareFeedback` | care_feedback | plan_id, feedback_type（scale/subjective/side_effect）, payload_json, submitted_at |

**`CarePlanItem.payload_json` 结构（含强制字段）：**

```json
{
  "title":          "每日正念练习",
  "description":    "使用 App 内引导音频，每次 10 分钟",
  "frequency":      "daily",
  "duration_weeks": 4,
  "evidence_refs":  ["clinical_kb:rule_mindfulness_gad7"],
  "advisory_label": "建议性质，非医嘱"
}
```

`advisory_label` 在所有 `CarePlanItem` 中强制输出（PRD 硬规则：干预文案须在 UI/API 层一致展示建议性质，不可误解为医嘱）。

**`plan.adjust()` 版本管理：**

```
plan.adjust() 执行时：
  旧 CarePlanItems  → 保留（标记 version=n，历史存档）
  新 CarePlanItems  → 插入（version=n+1）
  CarePlan.version  → 递增

查询当前方案：filter version == latest
查询历史方案：按 version 列出（疗效回溯、责任追溯）
```

### Link 设计（S2 专用）

```
ScreeningResult  --TRIGGERED-->   CarePlan        筛查结果触发方案生成
Counselor        --OWNS-->        CarePlan        咨询师/医生负责该方案
Patient          --ENROLLED_IN--> CarePlan        患者参与该方案
CarePlan         --CONTAINS-->    CarePlanItem    方案包含的措施行（sequence 有序）
CarePlanItem     --GENERATES-->   CareTask        措施行生成的任务实例
Patient          --COMPLETED-->   CareTask        患者完成任务（关系强度 +1.0）
Patient          --SUBMITTED-->   CareFeedback    患者提交反馈
CareFeedback     --EVALUATES-->   CarePlan        反馈关联方案（疗效闭环溯源）
```

`ScreeningResult --TRIGGERED--> CarePlan` 是 S1→S2 闭环的关键链路，`source_screening_id` 同时写入 CarePlan 属性，双向可查。

### Function 设计（S2 专用）

| Function | Actor | ConfirmLevel | 说明 |
|---|---|---|---|
| `plan.generate()` | 系统（消费 screening.submitted） | AutoExecute | AI 生成 CarePlan（status=draft）+ CarePlanItems；所有 item 强制写 advisory_label |
| `plan.confirm()` | Counselor / Clinician | ExplicitConfirm | 确认草案 → active；发布 plan.generated 事件通知患者 |
| `plan.adjust()` | Counselor / Clinician | PreviewConfirm | 修改活跃方案（变更说明必填），版本递增，旧版本保留 |
| `plan.pause()` | Counselor / Clinician | AutoExecute | 暂停任务推送 |
| `plan.resume()` | Counselor / Clinician | AutoExecute | 恢复推送 |
| `plan.discontinue()` | Counselor / Clinician | PreviewConfirm | 中途终止，原因必填（审计留存） |
| `task.complete()` | Patient / Guardian | AutoExecute | 患者标记任务完成；发布 plan.task_completed |
| `task.skip()` | Patient / Guardian | AutoExecute | 跳过任务 |
| `feedback.submit()` | Patient / Guardian | AutoExecute | 提交量表复测/主观感受/副作用；触发疗效分析 |
| `plan.escalateCrisis()` | 系统（消费反馈事件） | AutoExecute | 反馈含危机信号 → 写 CrisisEvent，复用 S1 危机通道 |

**`plan.confirm()` 用 ExplicitConfirm 而非 ClinicalReview 的原因：**  
咨询师/医生确认的是自己指派的方案（同一人的操作），不需要第三方 sign-off；但必须是显式点击，不能 AutoExecute——一旦确认，任务推送即开始，不可自动撤回。

### Event Schema（S2）

**`plan.generated`**（方案激活后通知患者 C 端）：

```json
{
  "event_type":      "plan.generated",
  "plan_id":         "cp_001",
  "subject_user_id": "u_xxx",
  "owner_id":        "counselor_001",
  "risk_level":      "moderate",
  "items_count":     5,
  "source_session":  "ss_abc123",
  "occurred_at":     "2026-06-03T11:00:00Z"
}
```

**`plan.task_completed`**（驱动依从性统计和预测模型）：

```json
{
  "event_type":      "plan.task_completed",
  "task_id":         "ct_abc",
  "plan_id":         "cp_001",
  "subject_user_id": "u_xxx",
  "item_type":       "exercise",
  "completed_at":    "2026-06-03T08:30:00Z",
  "delay_hours":     2
}
```

`delay_hours`（实际完成 vs due_at 的偏差）是依从性预测模型（FR-2.07）的核心输入特征。

### S1 → S2 完整数据流

```
S1 筛查结束
  → screening.submitted { risk_level=moderate, session_id, user_id }
  → S2 消费者：plan.generate()（AutoExecute，系统调用）
  → CarePlan{status=draft} + CarePlanItems（advisory_label 强制）
  → plan.draft_ready 事件 → 通知咨询师工作台

咨询师审核草案
  → plan.confirm()（ExplicitConfirm）
  → CarePlan{status=active}
  → plan.generated 事件 → 通知患者 C 端「您有新方案」
  → CareTask 按 due_at 排期，推送提醒（FR-2.07 频控）

患者执行
  → task.complete()（AutoExecute）→ plan.task_completed 事件
  → feedback.submit()（AutoExecute）→ 疗效分析
      → 若 feedback 含危机信号
          → plan.escalateCrisis()（AutoExecute）
          → 写 CrisisEvent → 复用 S1 screening.crisis 通道（告警 + 升级）

咨询师动态优化
  → plan.adjust()（PreviewConfirm，变更说明必填）
  → version 递增，旧版本保留
  → plan.adjusted 事件 → 通知患者方案已更新
```
