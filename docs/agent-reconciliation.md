# Agent 和解（Reconciliation）使用说明 / Agent Reconciliation Guide

**中文** | [English](#english)

## 中文：什么是和解？

当一次 Agent 工具/动作执行（Turn 内的非幂等执行）的**最终结果不确定**时，
系统**不会自动重放**，而是生成一条「和解」案件，等待管理员根据外部副作用证据确认结果。
未解决的不确定性永远不能自行选择重放。

### 何时会出现和解案件

- 模型或工具调用的结果丢失（provider 返回了执行，但结果没有回到 Agent）
- 执行围栏（fence）丢失（worker 断连、claim token 失效）
- 返回未知结果 / 结果无法确认

### 如何使用（管理员，`/admin/agent-reconciliations`）

1. 展开案件，查看红色（未决）证据与时间线（仅展示持久化的可观测事件，不含推理链）。
2. 在「提供方证据」中记录外部证据（提供方执行引用、结果、最终失败信息等）。
3. 选择处置方式并提交（每次提交按版本 CAS 校验，冲突时需刷新）：

| 处置 | 含义 | 需要的证据 |
|---|---|---|
| **确认已成功**（`confirm_succeeded`） | 外部副作用**确实发生**，Turn 终结 | 提供方签名/引用的执行结果证据 |
| **确认未执行**（`confirm_failed`） | 外部副作用**未发生**，Turn 终结 | 提供方最终失败引用 |
| **未执行可重试**（`confirm_not_accepted_and_retry`） | 副作用**未被接受**，Turn 以新的派发代号重新排队 | 确凿的未接受证据，且幂等描述符未变化 |

---

<a name="english"></a>

## English: What is reconciliation?

When the **final outcome of a tool/action execution inside an Agent Turn is
unknown**, the system **never auto-replays** — it opens a reconciliation case
for an admin to confirm the result from external side-effect evidence.
Unresolved uncertainty can never select replay on its own.

### When cases appear

- The model/tool call result was lost (the provider ran, but the result never
  came back to the Agent)
- The execution fence was lost (worker disconnected / claim token invalidated)
- An unknown / unconfirmable result was returned

### How to use it (admin, `/admin/agent-reconciliations`)

1. Open a case and inspect the redacted evidence and timeline (only persisted
   observable events — never chain-of-thought).
2. Record external evidence under "Provider evidence" (provider execution
   reference, result, terminal failure, etc.).
3. Choose a disposition and submit (every submission runs a version CAS;
   refresh on conflict):

| Disposition | Meaning | Evidence required |
|---|---|---|
| **Confirmed succeeded** (`confirm_succeeded`) | The external side effect **did happen**; the Turn terminates | Provider-signed/reference execution result evidence |
| **Confirmed not-run** (`confirm_failed`) | The external side effect **did not happen**; the Turn terminates | Provider terminal-failure reference |
| **Not accepted, retry** (`confirm_not_accepted_and_retry`) | The side effect was **not accepted**; the Turn re-queues with a new dispatch generation | Conclusive non-acceptance evidence and an unchanged idempotent descriptor |
